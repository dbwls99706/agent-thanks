"""Extract executed shell commands and agent prose from coding-agent transcripts.

Transcripts are JSON or JSON Lines files written by terminal coding agents. The
adapter separates three kinds of records:

- commands the agent executed through a known shell tool, carried together with
  the recorded outcome of that call; only a recorded success can make a command
  count as use;
- prose written by the agent, where only an explicit provenance statement counts
  as use; and
- everything else that can mention a repository, such as failed commands, calls
  to other tools, and their parameters, which only ever yields references.

Tool results, user prompts, and hidden reasoning are never treated as actions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, NamedTuple

from .models import Evidence
from .session import (
    OUTCOME_AMBIGUOUS,
    OUTCOME_CORRUPTED,
    OUTCOME_UNANCHORED,
    OUTCOME_CONFLICT,
    OUTCOME_ERROR,
    OUTCOME_MISSING,
    OUTCOME_OK,
    OUTCOME_UNCONFIRMED,
    OUTCOME_UNKNOWN,
    scan_prose_evidence,
    scan_reference_evidence,
    scan_session_evidence,
)


SHELL_TOOLS = frozenset(
    {
        "bash",
        "sh",
        "shell",
        "shell_command",
        "exec_command",
        "execute_command",
        "run_shell_command",
        "run_terminal_cmd",
        "run_command",
        "terminal",
        "powershell",
        "cmd",
    }
)
_SHELL_EXECUTABLES = {
    "bash",
    "sh",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "pwsh",
    "powershell",
    "powershell.exe",
    "cmd",
    "cmd.exe",
}
_SHELL_COMMAND_FLAGS = {"-c", "-lc", "-ic", "-lic", "-cl", "/c", "-command"}
_PARAMETER_KEYS = ("input", "args", "arguments", "parameters", "params")
_ASSISTANT_ROLES = {"assistant", "model", "gemini", "ai"}
_USER_ROLES = {"user", "human", "tool", "function", "system", "developer"}
_ROLE_CONFLICT = "conflict"
_EXIT_KEYS = ("exit_code", "exitCode", "returncode", "status_code")
_STRUCTURED_KEYS = ("metadata", "data")
_OUTPUT_KEYS = {
    "tool_result",
    "output",
    "outputs",
    "stdout",
    "stderr",
    "result",
    "results",
    "response",
    "thinking",
    "summary",
    "signature",
}
_DOCUMENT_LIST_KEYS = ("messages", "history", "items", "turns", "records", "events", "entries")
_EXIT_CODE_PATTERN = re.compile(r"(?i)\b(?:exit[ _]?code|exited with(?: exit)? code)\b\D{0,3}(-?\d+)")
_OUTPUT_MARKER = "Output:"
_MAX_HEADER_LINES = 8
_HEADER_LINE_PATTERN = re.compile(
    r"^(?:[A-Za-z][A-Za-z _-]{0,40}:(?:\s.*)?|Process (?:exited with code -?\d+|running with session ID \S+))\s*$"
)
_MAX_DEPTH = 32
CODEX_SHELL_TOOLS = frozenset({"shell", "exec_command"})
CLAUDE_SHELL_TOOLS = frozenset({"Bash"})
_MAX_SIGNAL_DEPTH = 8
_CODEX_CALL_TYPES = frozenset({"function_call", "custom_tool_call"})
_CODEX_OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
HOOK_LOG_SCHEMA = "agent-thanks/hook-log/1"
HOOK_AGENTS = frozenset({"claude-code", "codex", "gemini"})
# The only (agent, event, tool, basis) combinations whose hook entry may record a
# success. Claude Code fires PostToolUse for Bash after a successful run; Codex
# fires PostToolUse for its canonical Bash tool with the exit status in the
# response. Gemini defines no success signal, so it has no promoting row.
HOOK_PROMOTION_MATRIX = frozenset(
    {
        ("claude-code", "PostToolUse", "Bash", "successful_post_tool_event"),
        ("codex", "PostToolUse", "Bash", "exit_status"),
    }
)

RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_UNKNOWN = "unknown"


class DuplicateKeyError(ValueError):
    """A JSON object repeated a key; ``json.loads`` would silently keep the last value."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise DuplicateKeyError(f"duplicate key {key!r}")
        mapping[key] = value
    return mapping


def load_json(text: str) -> Any:
    """Parse JSON, rejecting objects that repeat a key.

    Every transcript record, hook log entry, hook payload, and JSON-encoded
    result string goes through this loader, so a duplicated ``is_error`` or
    ``exit_code`` can never resolve to whichever value came last.
    """
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
_FAILURE_STATUSES = {
    "failed",
    "failure",
    "error",
    "errored",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
    "aborted",
    "rejected",
}
_SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed", "complete", "done"}
_RESULT_TEXT_KEYS = ("output", "stdout", "result", "content", "message", "text")
_PROJECT_KEYS = ("cwd", "projectRoot", "project_root", "workingDirectory", "working_directory")
_META_SESSION_KEYS = ("id", "session_id", "sessionId", "thread_id", "threadId")
_RECORD_SESSION_KEYS = ("session_id", "sessionId", "thread_id", "threadId")

TranscriptRecord = tuple[int, str, str, str | None]


def canonical_command(command: str) -> str:
    """Return the form in which every source stores and compares a command: outer whitespace removed."""
    return command.strip()


def is_shell_tool(name: object) -> bool:
    """Return True only for tool names known to execute a shell command."""
    return isinstance(name, str) and name.casefold() in SHELL_TOOLS


def is_transcript(path: Path) -> bool:
    """Return True when a file looks like a JSON or JSON Lines transcript."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(64)
    except OSError:
        return False
    return head.lstrip("﻿ \t\r\n").startswith(("{", "["))


class Transcript(NamedTuple):
    """The records of a transcript and whether any non-empty line failed to parse."""

    records: list[tuple[int, Any]]
    corrupted: bool


def load_transcript(path: Path) -> Transcript:
    """Return (record number, record) pairs and whether the file is corrupted.

    A JSON Lines file numbers records by physical line; a line that is not JSON
    marks the transcript corrupted, and a corrupted transcript can prove no
    success. A single JSON document numbers the items of its message list, or
    counts as one record.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip("\ufeff \t\r\n")
    try:
        document = load_json(stripped)
    except ValueError:
        document = None

    if document is None:
        records: list[tuple[int, Any]] = []
        corrupted = False
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append((number, load_json(line)))
            except ValueError:
                corrupted = True
        return Transcript(records, corrupted)
    if isinstance(document, list):
        return Transcript(list(enumerate(document, start=1)), False)
    if isinstance(document, dict):
        for key in _DOCUMENT_LIST_KEYS:
            value = document.get(key)
            if isinstance(value, list):
                return Transcript(list(enumerate(value, start=1)), False)
    return Transcript([(1, document)], False)


def load_transcript_records(path: Path) -> list[tuple[int, Any]]:
    """Return (record number, record) pairs; see ``load_transcript``."""
    return load_transcript(path).records


def iter_transcript_records(
    path: Path,
    *,
    authoritative: Mapping[str, "HookStatus"] | None = None,
    session_calls: Mapping[str, "TranscriptCall"] | None = None,
) -> Iterator[TranscriptRecord]:
    """Yield (record number, kind, text, outcome).

    Kinds: 'command' for a shell command (outcome is 'ok', 'error', 'unknown',
    or 'missing' when the transcript recorded no result for that call), 'text'
    for agent prose, and 'reference' for everything else that may mention a
    repository. Outcome is None for non-command records.

    When ``authoritative`` is given (statuses per tool call id from a hook log),
    it replaces the transcript's own results: a command is 'ok' only if the hook
    log says so for the same call id and the identical command text, a command
    the hook log never saw is 'unconfirmed', and a call whose recorded command
    differs from the hook log's is a 'conflict'. A call id that the transcript
    reuses for different calls is 'ambiguous', and every command of a transcript
    with an unparsable line is 'corrupted'.
    """
    transcript = load_transcript(path)
    calls, positions = _index_calls(transcript.records)
    results = _index_results(transcript.records, calls, positions)
    for number, record in transcript.records:
        # Only text at an envelope position whose role is on the assistant side is
        # agent prose; text anywhere else can only ever be a reference.
        prose_nodes = {id(envelope.node) for envelope in _envelopes(record) if _prose_position_ok(envelope)}
        for kind, text, outcome in _walk(
            record, None, 0, calls, results, authoritative, session_calls, prose_nodes
        ):
            if kind == "command" and transcript.corrupted:
                outcome = OUTCOME_CORRUPTED
            if text.strip():
                yield number, kind, text, outcome


def scan_transcript_evidence(
    path: Path,
    source: str,
    *,
    authoritative: Mapping[str, "HookStatus"] | None = None,
    session_calls: Mapping[str, "TranscriptCall"] | None = None,
) -> list[tuple[str, Evidence]]:
    """Classify every record of a transcript; labels point at the record number.

    ``session_calls`` carries what every transcript scanned together knows about
    each call id, so a failure or a different command recorded for the same call
    in another file of the session demotes this file's command too.
    """
    items: list[tuple[str, Evidence]] = []
    for number, kind, text, outcome in iter_transcript_records(
        path, authoritative=authoritative, session_calls=session_calls
    ):
        label = f"{source}:{number}"
        if kind == "command":
            items.extend(
                scan_session_evidence(
                    text,
                    label,
                    line_labels=False,
                    outcome=outcome or OUTCOME_MISSING,
                    single_statement=True, provenance=False,
                )
            )
        elif kind == "text":
            items.extend(scan_prose_evidence(text, label, line_labels=False))
        else:
            items.extend(scan_reference_evidence(text, label, line_labels=False))
    return items


class TranscriptCall(NamedTuple):
    """What a transcript knows about one tool call id.

    ``command`` is the shell command of its call record (``None`` when the id is
    reused for different calls, belongs to another kind of call, or has no call
    record), ``status`` the failure-first combination of its results, ``recorded``
    whether a call record exists, and ``resulted`` whether any result exists.
    """

    command: str | None
    status: str
    recorded: bool
    resulted: bool


def transcript_calls(path: Path) -> dict[str, TranscriptCall]:
    """Map each tool call id a transcript mentions to its shell command and combined result.

    Ids come from call records and from result records alike, so a failure whose
    call record is missing from a partial transcript still counts. The command is
    ``None`` when the id is reused for different calls, belongs to a call that is
    not a shell command, or has no call record (``recorded`` is False then). The
    status is the failure-first combination of every result recorded for the id,
    or unknown without one.
    """
    transcript = load_transcript(path)
    calls, positions = _index_calls(transcript.records)
    results = _index_results(transcript.records, calls, positions)
    if transcript.corrupted:
        # A corrupted file keeps its failures but can vouch for no success elsewhere.
        results = {
            call_id: RESULT_ERROR if status == RESULT_ERROR else RESULT_UNKNOWN
            for call_id, status in results.items()
        }
    known = {
        call_id: TranscriptCall(
            info.command if info is not None else None,
            results.get(call_id, RESULT_UNKNOWN),
            True,
            call_id in results,
        )
        for call_id, info in calls.items()
    }
    for call_id, status in results.items():
        known.setdefault(call_id, TranscriptCall(None, status, False, True))
    return known


def transcript_identity(path: Path) -> tuple[str, str] | None:
    """Return (session id, normalized project directory) when a transcript records both.

    Only transcripts with a confirmed identity may be merged with each other or
    linked to a hook log; anything else is judged on its own.
    """
    metadata = transcript_metadata(path)
    if metadata.session_id is None or metadata.cwd is None:
        return None
    return metadata.session_id, _normalize_path(metadata.cwd)


def hook_log_identity(path: Path) -> str | None:
    """Return the session id a hook log records, or None when its entries carry none or disagree."""
    identities = {
        entry.get("session_id")
        for _, entry in _hook_log_entries(path).entries
        if isinstance(entry.get("session_id"), str) and entry.get("session_id")
    }
    return next(iter(identities)) if len(identities) == 1 else None


def merge_transcript_calls(maps: Iterable[Mapping[str, TranscriptCall]]) -> dict[str, TranscriptCall]:
    """Merge call maps from several transcripts; disagreeing commands become ``None`` and results combine failure first."""
    commands: dict[str, str | None] = {}
    recorded: dict[str, bool] = {}
    resulted: dict[str, bool] = {}
    statuses: dict[str, list[str]] = {}
    for calls in maps:
        for call_id, call in calls.items():
            if call.recorded:
                if recorded.get(call_id) and commands.get(call_id) != call.command:
                    commands[call_id] = None
                else:
                    commands.setdefault(call_id, call.command)
                recorded[call_id] = True
            statuses.setdefault(call_id, [])
            if call.resulted:
                statuses[call_id].append(call.status)
                resulted[call_id] = True
    return {
        call_id: TranscriptCall(
            commands.get(call_id),
            combine_statuses(values) if values else RESULT_UNKNOWN,
            recorded.get(call_id, False),
            resulted.get(call_id, False),
        )
        for call_id, values in statuses.items()
    }


HOOK_LOG_STATUSES = {RESULT_OK: OUTCOME_OK, RESULT_ERROR: OUTCOME_ERROR, RESULT_UNKNOWN: OUTCOME_UNKNOWN}


def is_hook_log(path: Path) -> bool:
    """Return True for a structured hook log written by ``agent-thanks hook record``.

    The first record must carry the hook log schema marker; a JSON Lines file
    that merely happens to have ``command`` and ``status`` keys is not one.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = load_json(line)
                except ValueError:
                    return False
                return isinstance(record, dict) and record.get("schema") == HOOK_LOG_SCHEMA
    except OSError:
        return False
    return False


class HookStatus(NamedTuple):
    """Combined status of one tool call in a hook log, with the exact command it recorded."""

    status: str
    command: str | None


class HookLog(NamedTuple):
    """The schema-marked entries of a hook log and whether any other non-empty line was found."""

    entries: list[tuple[int, dict[str, Any]]]
    corrupted: bool


def load_hook_log_statuses(path: Path) -> dict[str, HookStatus]:
    """Return the combined status and command per tool call id recorded in a hook log."""
    return _combine_hook_log(_hook_log_entries(path))


def merge_hook_statuses(maps: Iterable[Mapping[str, HookStatus]]) -> dict[str, HookStatus]:
    """Merge statuses from several hook logs; a call they disagree about is never ok."""
    merged: dict[str, HookStatus] = {}
    for statuses in maps:
        for call_id, entry in statuses.items():
            if call_id not in merged:
                merged[call_id] = entry
                continue
            previous = merged[call_id]
            status = combine_statuses((previous.status, entry.status))
            if previous.command != entry.command:
                merged[call_id] = HookStatus(RESULT_ERROR if status == RESULT_ERROR else RESULT_UNKNOWN, None)
            else:
                merged[call_id] = HookStatus(status, entry.command)
    return merged


def combine_statuses(statuses: Iterable[str]) -> str:
    """Combine every recorded status of one call: any failure wins, only unanimous success is ok."""
    values = list(statuses)
    if not values:
        return RESULT_UNKNOWN
    if RESULT_ERROR in values:
        return RESULT_ERROR
    if all(value == RESULT_OK for value in values):
        return RESULT_OK
    return RESULT_UNKNOWN


def hook_entry_status(entry: Mapping[str, Any]) -> str:
    """Return the status one hook log entry may contribute.

    A recorded failure always counts. A recorded success counts only when the
    entry's agent, event, tool, and basis form a promoting contract from
    ``HOOK_PROMOTION_MATRIX`` and the entry carries a non-empty tool call id and
    command. Anything else is unknown.
    """
    status = entry.get("status")
    if status == RESULT_ERROR:
        return RESULT_ERROR
    call_id = entry.get("tool_call_id")
    command = entry.get("command")
    contract = (entry.get("agent"), entry.get("event"), entry.get("tool"), entry.get("basis"))
    complete = (
        status == RESULT_OK
        and contract in HOOK_PROMOTION_MATRIX
        and isinstance(call_id, str)
        and bool(call_id)
        and isinstance(command, str)
        and bool(command.strip())
    )
    return RESULT_OK if complete else RESULT_UNKNOWN


def combine_hook_entries(entries: Iterable[Mapping[str, Any]]) -> HookStatus:
    """Combine every hook log entry recorded for one tool call.

    Statuses combine failure first. Entries that disagree about the exact
    command they ran contradict each other, so their combined status is never
    ok and no command is kept for them; a call without a command is never ok.
    """
    items = list(entries)
    status = combine_statuses(hook_entry_status(entry) for entry in items)
    commands: set[str] = set()
    for entry in items:
        command = entry.get("command")
        if isinstance(command, str) and command.strip():
            commands.add(command)
    if len(commands) != 1:
        return HookStatus(RESULT_ERROR if status == RESULT_ERROR else RESULT_UNKNOWN, None)
    return HookStatus(status, next(iter(commands)))


def scan_hook_log_evidence(
    path: Path, source: str, *, transcript_calls: Mapping[str, TranscriptCall] | None = None
) -> list[tuple[str, Evidence]]:
    """Classify hook log entries; only calls whose every entry recorded success can count as use.

    Entries that share a tool call id are judged by their combined status, so a
    call recorded as failed once stays a reference even if a later entry for the
    same call recorded success. An entry without a tool call id never counts, and
    a log with any corrupted line promotes nothing. When the transcripts scanned
    alongside the log know the call id (``transcript_calls``), the entry counts
    only if they recorded the identical command for it and no failure; a
    disagreement or a reused id is a conflict.
    """
    log = _hook_log_entries(path)
    combined = _combine_hook_log(log)
    items: list[tuple[str, Evidence]] = []
    for number, entry in log.entries:
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        call_id = entry.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            outcome = HOOK_LOG_STATUSES.get(combined[call_id].status, OUTCOME_UNKNOWN)
            if transcript_calls is not None and call_id in transcript_calls:
                recorded = transcript_calls[call_id]
                if recorded.recorded and recorded.command is None:
                    outcome = OUTCOME_AMBIGUOUS
                elif recorded.recorded and recorded.command != canonical_command(command):
                    outcome = OUTCOME_CONFLICT
                elif recorded.status == RESULT_ERROR:
                    outcome = OUTCOME_ERROR
        else:
            status = RESULT_ERROR if entry.get("status") == RESULT_ERROR else RESULT_UNKNOWN
            outcome = HOOK_LOG_STATUSES.get(status, OUTCOME_UNKNOWN)
        items.extend(
            scan_session_evidence(
                command,
                f"{source}:{number}",
                line_labels=False,
                outcome=outcome,
                single_statement=True,
                provenance=False,
            )
        )
    return items


def _hook_log_entries(path: Path) -> HookLog:
    """Return the schema-marked entries of a hook log; any other non-empty line marks it corrupted."""
    entries: list[tuple[int, dict[str, Any]]] = []
    corrupted = False
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = load_json(line)
        except ValueError:
            corrupted = True
            continue
        if isinstance(entry, dict) and entry.get("schema") == HOOK_LOG_SCHEMA:
            if isinstance(entry.get("command"), str):
                entry["command"] = canonical_command(entry["command"])
            entries.append((number, entry))
        else:
            corrupted = True
    return HookLog(entries, corrupted)


def _combine_hook_log(log: HookLog) -> dict[str, HookStatus]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for _, entry in log.entries:
        call_id = entry.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            grouped.setdefault(call_id, []).append(entry)
    combined = {call_id: combine_hook_entries(items) for call_id, items in grouped.items()}
    if log.corrupted:
        # A damaged authoritative log keeps its failures but can prove no success.
        combined = {
            call_id: HookStatus(RESULT_ERROR if entry.status == RESULT_ERROR else RESULT_UNKNOWN, entry.command)
            for call_id, entry in combined.items()
        }
    return combined


def encode_project_path(path: Path | str) -> str:
    """Return the directory name a project path receives inside the Claude Code projects folder."""
    return re.sub(r"[\\/:]", "-", str(path))


def agent_home(agent: str, home: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Return the agent's configuration directory, honoring its override variable."""
    environ = os.environ if environ is None else environ
    if agent == "claude-code":
        return Path(environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    if agent == "codex":
        return Path(environ.get("CODEX_HOME") or home / ".codex")
    if agent == "gemini":
        return home / ".gemini"
    raise ValueError(f"Unknown agent: {agent}")


def transcript_locations(
    agent: str, cwd: Path, home: Path, environ: Mapping[str, str] | None = None
) -> list[Path]:
    """Return the directories searched for an agent's transcripts."""
    base = agent_home(agent, home, environ)
    if agent == "claude-code":
        return [base / "projects" / encode_project_path(cwd)]
    if agent == "codex":
        return [base / "sessions"]
    return [base / "tmp"]


def locate_transcript(
    agent: str,
    cwd: Path,
    home: Path,
    environ: Mapping[str, str] | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Find the transcript an agent wrote for exactly this project and session, or None.

    A candidate counts only when the project directory it records equals ``cwd``
    after normalization; a transcript that records no directory cannot be
    confirmed and is skipped, for every agent. When ``session_id`` is given, the
    transcript must record that identifier exactly (Claude Code names the file
    after it and records it inside; Codex and Gemini record it inside); a file
    name never stands in for missing or different metadata. Nothing falls back
    to "the newest file".
    """
    (directory,) = transcript_locations(agent, cwd, home, environ)
    if agent == "claude-code":
        candidates = _sorted_by_mtime(directory.glob("*.jsonl"))
        if session_id is not None:
            candidates = [path for path in candidates if path.stem == session_id]
    else:
        pattern = "*.jsonl" if agent == "codex" else "*.json"
        candidates = _sorted_by_mtime(
            path for path in directory.rglob(pattern) if path.name != "settings.json"
        )
    for candidate in candidates[:200]:
        metadata = transcript_metadata(candidate)
        if metadata.cwd is None or not same_path(metadata.cwd, cwd):
            continue
        if session_id is not None and metadata.session_id != session_id:
            continue
        return candidate
    return None


@dataclass(frozen=True)
class TranscriptMetadata:
    cwd: str | None
    session_id: str | None


def transcript_metadata(path: Path) -> TranscriptMetadata:
    """Return the project directory and session identifier a transcript records about itself."""
    cwd: str | None = None
    session_id: str | None = None
    for record in _head_records(path, limit=25):
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") if record.get("type") == "session_meta" else None
        if isinstance(payload, dict):
            cwd = cwd or _first_string(payload, _PROJECT_KEYS)
            session_id = session_id or _first_string(payload, _META_SESSION_KEYS)
        cwd = cwd or _first_string(record, _PROJECT_KEYS)
        session_id = session_id or _first_string(record, _RECORD_SESSION_KEYS)
        if cwd is not None and session_id is not None:
            break
    return TranscriptMetadata(cwd, session_id)


def same_path(recorded: str, expected: Path | str) -> bool:
    """Compare two directory paths after normalization; no substring matching."""
    return _normalize_path(recorded) == _normalize_path(str(expected))


def _normalize_path(value: str) -> str:
    text = value.strip()
    trimmed = text.rstrip("/\\") or text
    # Hook roots are resolved before state is written, so transcript metadata
    # must use the same physical-path spelling.  This matters on macOS, where
    # temporary directories commonly arrive as /var/... while resolve() uses
    # the /private/var/... target, and on Windows for junctions and 8.3 names.
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(trimmed)))
    return os.path.normcase(os.path.normpath(resolved)).replace("\\", "/")


def _first_string(mapping: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _head_records(path: Path, *, limit: int) -> list[Any]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(262144)
    except OSError:
        return []
    stripped = head.lstrip("\ufeff \t\r\n")
    try:
        document = load_json(stripped)
    except ValueError:
        document = None
    if isinstance(document, dict):
        return [document]
    if isinstance(document, list):
        return document[:limit]
    records: list[Any] = []
    for line in head.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(load_json(line))
        except ValueError:
            continue
        if len(records) >= limit:
            break
    return records


class CallInfo(NamedTuple):
    """A tool call seen at an envelope position: record kind, tool name, parameters, and shell command."""

    kind: str
    tool: str
    parameters: str
    command: str | None


def _iter_envelopes(records: Iterable[tuple[int, Any]]) -> Iterator[tuple[int, Envelope]]:
    """Yield every envelope position of a transcript in order with a running sequence number."""
    position = 0
    for _, record in records:
        for envelope in _envelopes(record):
            position += 1
            yield position, envelope


def _index_calls(
    records: Iterable[tuple[int, Any]],
) -> tuple[dict[str, CallInfo | None], dict[str, int]]:
    """Map tool call identifiers to the call behind them and to the position of that call.

    Only calls at the position their agent writes them are indexed
    (``_call_position_ok``); a call anywhere else, under a user or developer
    role, or under a role that contradicts its record is never an action. An
    identifier that the transcript reuses for different calls (another tool,
    other parameters, or another record kind) maps to ``None``: no result can
    then be attributed to any of them. The position is that of the first
    occurrence, so a success recorded earlier can be told apart from one
    recorded after the call.
    """
    calls: dict[str, CallInfo | None] = {}
    positions: dict[str, int] = {}
    for position, envelope in _iter_envelopes(records):
        call = _call_of(envelope.node)
        if call is None or call[2] is None:
            continue
        kind = _call_kind(envelope.node)
        if not _call_position_ok(envelope, kind):
            continue
        name, parameters, call_id = call
        command = _command_of(parameters) if is_shell_tool(name) else None
        info = CallInfo(kind, name, json.dumps(parameters, sort_keys=True, default=str), command)
        if call_id not in calls:
            calls[call_id] = info
            positions[call_id] = position
        elif calls[call_id] != info:
            calls[call_id] = None
    return calls, positions


def _call_kind(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if node_type in {"tool_use"} | _CODEX_CALL_TYPES:
        return str(node_type)
    if any(isinstance(node.get(wrapper), dict) for wrapper in ("functionCall", "function_call", "function")):
        return "functionCall"
    return "generic"


def _index_results(
    records: Iterable[tuple[int, Any]],
    calls: Mapping[str, CallInfo | None],
    positions: Mapping[str, int],
) -> dict[str, str]:
    """Map tool call identifiers to a combined status from recorded result envelopes.

    Only envelope positions are read. A result proves success only at the
    position its agent writes it (``_result_position_ok``), paired with a call
    of the matching kind and tool, and only after that call; a result anywhere
    else, or before its call, keeps its failure signals but never yields a
    success. Several results for one call are combined with failure first.
    """
    statuses: dict[str, list[str]] = {}
    for position, envelope in _iter_envelopes(records):
        entry = _result_of(envelope, calls)
        if entry is None:
            continue
        call_id, status = entry
        if status == RESULT_OK and positions.get(call_id, position + 1) > position:
            status = RESULT_UNKNOWN
        statuses.setdefault(call_id, []).append(status)
    return {call_id: combine_statuses(values) for call_id, values in statuses.items()}


class Envelope(NamedTuple):
    """One position of a record where an agent writes calls, results, or prose."""

    node: dict[str, Any]
    role: str | None
    position: str  # record, payload, payload_item, message_item, record_item
    record_type: str | None
    message_role: str | None


def _envelopes(record: Any) -> Iterator[Envelope]:
    """Yield the envelope positions of a record with the role that governs each.

    Envelope positions are the record itself, its ``payload`` and the items of
    the payload's ``content``, and the items of the record's message ``content``
    or ``parts`` lists. Every item is judged with its own role when it carries
    one: an item whose role contradicts the message that holds it gets the
    conflict role, which no call, result, or prose may use.
    """
    if not isinstance(record, dict):
        return
    record_type = record.get("type") if isinstance(record.get("type"), str) else None
    role = _role_of(record, None)
    yield Envelope(record, role, "record", record_type, None)
    payload = record.get("payload")
    if isinstance(payload, dict):
        payload_role = _role_of(payload, role)
        yield Envelope(payload, payload_role, "payload", record_type, payload_role)
        for container in (payload.get("content"), payload.get("parts")):
            if isinstance(container, list):
                for item in container:
                    if isinstance(item, dict):
                        yield Envelope(item, _role_of(item, payload_role), "payload_item", record_type, payload_role)
    message = record.get("message") if isinstance(record.get("message"), dict) else None
    if message is not None:
        message_role = _role_of(message, role)
        for container in (message.get("content"), message.get("parts")):
            if isinstance(container, list):
                for item in container:
                    if isinstance(item, dict):
                        yield Envelope(item, _role_of(item, message_role), "message_item", record_type, message_role)
    for container in (record.get("content"), record.get("parts")):
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    yield Envelope(item, _role_of(item, role), "record_item", record_type, role)


def _result_envelopes(record: Any) -> Iterator[tuple[dict[str, Any], str | None]]:
    for envelope in _envelopes(record):
        yield envelope.node, envelope.role


def _call_position_ok(envelope: Envelope, kind: str) -> bool:
    """Positive allowlist: where each agent writes the calls it makes."""
    if kind == "tool_use":  # Claude Code: content item of an assistant message
        return (
            envelope.position == "message_item"
            and envelope.record_type == "assistant"
            and envelope.message_role == "assistant"
            and envelope.role == "assistant"
        )
    if kind in _CODEX_CALL_TYPES:  # Codex: a response item, wrapped or bare, never inside a role
        return envelope.position in {"payload", "record"} and envelope.role is None and (
            envelope.position == "record" or envelope.record_type == "response_item"
        )
    if kind == "functionCall":  # Gemini: a part of a model turn
        return envelope.position == "record_item" and envelope.role == "model"
    return False


def _result_position_ok(envelope: Envelope, node_type: str | None) -> bool:
    """Positive allowlist: where each agent writes the results it records."""
    if node_type == "tool_result":
        return (
            envelope.position == "message_item"
            and envelope.record_type == "user"
            and envelope.message_role == "user"
            and envelope.role == "user"
        )
    if node_type in _CODEX_OUTPUT_TYPES:
        return envelope.position in {"payload", "record"} and envelope.role is None and (
            envelope.position == "record" or envelope.record_type == "response_item"
        )
    if node_type == "functionResponse":
        return envelope.position == "record_item" and envelope.role == "user"
    return False


def _prose_position_ok(envelope: Envelope) -> bool:
    """Positive allowlist: where each agent writes its own prose."""
    if envelope.position == "message_item":  # Claude Code assistant text
        return (
            envelope.record_type == "assistant"
            and envelope.message_role == "assistant"
            and envelope.role == "assistant"
        )
    if envelope.position == "payload_item":  # Codex assistant message text
        return envelope.record_type == "response_item" and envelope.role == "assistant"
    if envelope.position == "record_item":  # Gemini model parts
        return envelope.role == "model"
    if envelope.position == "record":  # Gemini chat records typed as the model
        return envelope.record_type in _ASSISTANT_ROLES and envelope.role in _ASSISTANT_ROLES
    return False


def _iter_dicts(node: Any, depth: int) -> Iterator[dict[str, Any]]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item, depth + 1)


def result_status(value: Any, *, agent: str | None = None) -> str:
    """Judge a recorded tool result envelope with failure-first semantics.

    Failure signals are collected from the whole envelope, at any depth and in
    any field: ``is_error`` or ``isError`` that is anything but exactly
    ``false``, an exit code field that is non-zero or not an integer, a
    non-empty ``error``, a failure ``status`` or a status of an unexpected type,
    and "Exit code: N" text with N != 0, including inside nested JSON strings
    and inside the program output that follows a Codex header. Success is read
    only from the fixed position the named agent writes: for ``claude-code`` a
    top-level ``is_error`` equal to ``False``; for ``codex`` a top-level or
    ``metadata`` ``exit_code`` equal to 0 in a result object or a JSON-encoded
    envelope, or an exit code of 0 in the header block that precedes the
    ``Output:`` marker. Gemini defines no success signal, and with no agent no
    success is ever read. Any failure makes the result RESULT_ERROR; a result
    too deep or too large to scan completely is RESULT_UNKNOWN; without a
    failure, an exact success makes it RESULT_OK; otherwise RESULT_UNKNOWN.
    """
    return result_status_in(value, value, agent=agent)


def result_status_in(record: Any, value: Any, *, agent: str | None = None) -> str:
    """Judge ``value`` as the success position while scanning all of ``record`` for failures.

    ``record`` is the whole result envelope (a Codex output item with its
    ``output``, ``metadata``, and sibling fields; a Gemini ``functionResponse``
    with its ``response``); ``value`` is the one position the agent writes its
    success into. A failure or an incomplete scan anywhere in the record blocks
    a success read from the position.
    """
    failures, complete = _failure_signals(record, 0)
    if failures:
        return RESULT_ERROR
    if not complete:
        return RESULT_UNKNOWN
    return RESULT_OK if _success_signal(value, agent) else RESULT_UNKNOWN


def _success_signal(value: Any, agent: str | None) -> bool:
    if isinstance(value, dict):
        return _agent_success(value, agent)
    if isinstance(value, str) and agent == "codex":
        stripped = value.strip()
        parsed = _parse_json_object(stripped)
        if parsed is not None:
            return _agent_success(parsed, agent)
        header, _ = _split_text_envelope(stripped)
        return any(int(match.group(1)) == 0 for match in _EXIT_CODE_PATTERN.finditer(header))
    return False


def _agent_success(mapping: dict[str, Any], agent: str | None) -> bool:
    """Read the one success field an agent writes, at the one position it writes it."""
    if agent == "claude-code":
        return mapping.get("is_error") is False
    if agent == "codex":
        metadata = mapping.get("metadata")
        codes = [mapping.get("exit_code"), metadata.get("exit_code") if isinstance(metadata, dict) else None]
        return any(isinstance(code, int) and not isinstance(code, bool) and code == 0 for code in codes)
    return False


def _failure_signals(value: Any, depth: int) -> tuple[int, bool]:
    """Count failure signals anywhere in a result; the flag says whether the scan was complete.

    A structure deeper than the scan limit is reported incomplete, and an
    incomplete scan never lets a success through.
    """
    if depth > _MAX_SIGNAL_DEPTH:
        return 0, False
    if isinstance(value, dict):
        total, complete = 0, True
        for key in ("is_error", "isError"):
            if key in value and value[key] is not False:
                total += 1  # true, null, or a type the contract never uses
        for key in _EXIT_KEYS:
            if key in value:
                code = value[key]
                if not isinstance(code, int) or isinstance(code, bool) or code != 0:
                    total += 1
        if value.get("error"):
            total += 1
        if "status" in value and value["status"] is not None:
            status = value["status"]
            if not isinstance(status, str) or status.casefold() in _FAILURE_STATUSES:
                total += 1
        for nested in value.values():
            if isinstance(nested, (dict, list, str)):
                nested_total, nested_complete = _failure_signals(nested, depth + 1)
                total += nested_total
                complete = complete and nested_complete
        return total, complete
    if isinstance(value, list):
        total, complete = 0, True
        for item in value:
            nested_total, nested_complete = _failure_signals(item, depth + 1)
            total += nested_total
            complete = complete and nested_complete
        return total, complete
    if isinstance(value, str):
        stripped = value.strip()
        parsed = _parse_json_value(stripped)
        if parsed is not None:
            return _failure_signals(parsed, depth + 1)
        total = sum(1 for match in _EXIT_CODE_PATTERN.finditer(value) if int(match.group(1)) != 0)
        # A string that looks like JSON but does not parse (a duplicate key, a
        # truncation) hides whatever it carried: the scan is incomplete.
        complete = not stripped.startswith(("{", "["))
        # Program output after a header may itself be JSON with an error inside.
        for line in stripped.splitlines():
            line_value = _parse_json_value(line.strip())
            if line_value is not None:
                nested_total, nested_complete = _failure_signals(line_value, depth + 1)
                total += nested_total
                complete = complete and nested_complete
            elif line.strip().startswith(("{", "[")):
                complete = False
        return total, complete
    return 0, True


def _parse_json_object(text: str) -> dict[str, Any] | None:
    value = _parse_json_value(text)
    return value if isinstance(value, dict) else None


def _parse_json_value(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse a JSON object or array; anything else, including duplicate keys, is None."""
    if not text.startswith(("{", "[")):
        return None
    try:
        parsed = load_json(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _split_text_envelope(text: str) -> tuple[str, str]:
    """Split a text result into its header and the program output that follows.

    Codex writes a header block (``Exit code: N``, or ``Chunk ID`` / ``Wall
    time`` / ``Process exited with code N`` lines) and then an ``Output:`` line
    before the program output. The header is the lines before the first
    ``Output:`` line when that line is among the first few and every line before
    it is a header field; a text without such a header is all program output.
    Success is read from the header alone, because a program can print anything
    after the marker but nothing before it.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines[:_MAX_HEADER_LINES]):
        if line.strip() == _OUTPUT_MARKER:
            if all(_HEADER_LINE_PATTERN.match(item.strip()) for item in lines[:index]):
                return "\n".join(lines[:index]), "\n".join(lines[index + 1 :])
            break
    return "", text


def _result_of(envelope: Envelope, calls: Mapping[str, CallInfo | None]) -> tuple[str, str] | None:
    """Pair a recorded result with its call identifier; every format uses the same judge.

    A result proves success only at the position its agent writes it: a Claude
    Code ``tool_result`` inside a user message paired with a ``tool_use`` call of
    Claude Code's own ``Bash`` tool; a Codex output item, wrapped in a response
    item or bare, paired with a Codex call record of a Codex shell tool; a Gemini
    ``functionResponse`` in a user turn paired with a ``functionCall``. A result
    anywhere else, or paired with a call of another kind or tool, keeps its
    failure signals but never yields a success.
    """
    node = envelope.node
    node_type = node.get("type")
    if node_type == "tool_result" and isinstance(node.get("tool_use_id"), str):
        call_id = node["tool_use_id"]
        info = calls.get(call_id)
        placed = (
            _result_position_ok(envelope, node_type)
            and _call_kind_is(info, {"tool_use"})
            and info.tool in CLAUDE_SHELL_TOOLS
        )
        return call_id, result_status(node, agent="claude-code" if placed else None)
    if node_type in _CODEX_OUTPUT_TYPES and isinstance(node.get("call_id"), str):
        call_id = node["call_id"]
        info = calls.get(call_id)
        placed = (
            _result_position_ok(envelope, node_type)
            and _call_kind_is(info, _CODEX_CALL_TYPES)
            and info.tool in CODEX_SHELL_TOOLS
        )
        return call_id, result_status_in(node, node.get("output"), agent="codex" if placed else None)
    response = node.get("functionResponse")
    if isinstance(response, dict) and isinstance(response.get("id"), str):
        call_id = response["id"]
        placed = _result_position_ok(envelope, "functionResponse") and _call_kind_is(calls.get(call_id), {"functionCall"})
        return call_id, result_status_in(node, response.get("response"), agent="gemini" if placed else None)
    return None


def _call_kind_is(info: CallInfo | None, kinds: frozenset[str] | set[str]) -> bool:
    return info is not None and info.kind in kinds


def _walk(
    node: Any,
    role: str | None,
    depth: int,
    calls: Mapping[str, CallInfo | None],
    results: dict[str, str],
    authoritative: Mapping[str, HookStatus] | None,
    session_calls: Mapping[str, TranscriptCall] | None,
    prose_nodes: frozenset[int] | set[int],
) -> list[tuple[str, str, str | None]]:
    found: list[tuple[str, str, str | None]] = []
    if depth > _MAX_DEPTH:
        return found
    if isinstance(node, list):
        for item in node:
            found.extend(_walk(item, role, depth + 1, calls, results, authoritative, session_calls, prose_nodes))
        return found
    if not isinstance(node, dict):
        return found

    role = _role_of(node, role)
    call = _call_of(node)
    if call is not None:
        if role in _USER_ROLES or role == _ROLE_CONFLICT:
            return found  # call-shaped objects in user or tool content, or under conflicting roles, are never actions
        name, parameters, call_id = call
        if is_shell_tool(name):
            command = _command_of(parameters)
            if command:
                info = CallInfo(
                    _call_kind(node), name, json.dumps(parameters, sort_keys=True, default=str), command
                )
                outcome = _command_outcome(call_id, info, calls, results, authoritative, session_calls)
                found.append(("command", command, outcome))
        else:
            parameter_text = _parameter_text(parameters)
            if parameter_text:
                found.append(("reference", parameter_text, None))
        return found

    text = _text_of(node, role)
    if text is not None:
        found.append(("text" if id(node) in prose_nodes else "reference", text, None))
    for key, value in node.items():
        if key in _OUTPUT_KEYS:
            continue
        if isinstance(value, (dict, list)):
            found.extend(_walk(value, role, depth + 1, calls, results, authoritative, session_calls, prose_nodes))
    return found


def _command_outcome(
    call_id: str | None,
    info: CallInfo,
    calls: Mapping[str, CallInfo | None],
    results: dict[str, str],
    authoritative: Mapping[str, HookStatus] | None,
    session_calls: Mapping[str, TranscriptCall] | None = None,
) -> str:
    """Decide what a transcript command may claim; every condition must hold for a success.

    The call needs an identifier that was indexed at the position its agent
    writes calls, with exactly this call's fingerprint and for this call alone.
    Every transcript of the session scanned together must agree on the command
    behind the identifier and record no failure for it. With a hook log, the log
    must hold the identifier with the identical command text and a combined ok
    status. Without one, the combined result for the identifier must be ok.
    """
    if call_id is None:
        return OUTCOME_UNCONFIRMED if authoritative is not None else OUTCOME_MISSING
    if call_id not in calls:
        return OUTCOME_UNANCHORED
    indexed = calls[call_id]
    if indexed is None or indexed != info:
        return OUTCOME_AMBIGUOUS
    own = results.get(call_id)
    if session_calls is not None and call_id in session_calls:
        session = session_calls[call_id]
        if session.recorded and (
            session.command is None or canonical_command(session.command) != canonical_command(info.command)
        ):
            return OUTCOME_AMBIGUOUS
        if session.resulted:
            own = combine_statuses([status for status in (own, session.status) if status is not None])
    if authoritative is not None:
        entry = authoritative.get(call_id)
        if entry is None:
            return OUTCOME_UNCONFIRMED
        if entry.command is None or canonical_command(entry.command) != canonical_command(info.command):
            return OUTCOME_CONFLICT
        if own == RESULT_ERROR:
            return OUTCOME_ERROR
        return HOOK_LOG_STATUSES.get(entry.status, OUTCOME_UNCONFIRMED)
    return HOOK_LOG_STATUSES.get(own or "", OUTCOME_MISSING)


def _role_of(node: dict[str, Any], inherited: str | None) -> str | None:
    """Return the role governing a node: its own role, else the inherited one.

    A node whose own role belongs to a different class (user side or assistant
    side) than the inherited role is contradictory; it and everything under it
    get the conflict role, which never records an action or a success.
    """
    own: str | None = None
    role = node.get("role")
    if isinstance(role, str):
        own = role.casefold()
    else:
        record_type = node.get("type")
        if isinstance(record_type, str) and record_type.casefold() in _ASSISTANT_ROLES | _USER_ROLES:
            own = record_type.casefold()
    if inherited == _ROLE_CONFLICT:
        return _ROLE_CONFLICT
    if own is not None and inherited is not None:
        own_class, inherited_class = _role_class(own), _role_class(inherited)
        if own_class and inherited_class and own_class != inherited_class:
            return _ROLE_CONFLICT
    return own if own is not None else inherited


def _role_class(role: str) -> str | None:
    if role in _USER_ROLES:
        return "user"
    if role in _ASSISTANT_ROLES:
        return "assistant"
    return None


def _call_of(node: dict[str, Any]) -> tuple[str, Any, str | None] | None:
    """Return (tool name, parameters, call id) for any recognized tool call."""
    node_type = node.get("type")
    name = node.get("name")
    if node_type == "tool_use" and isinstance(name, str):
        return name, node.get("input"), _identifier(node.get("id"))
    if node_type in {"function_call", "custom_tool_call"} and isinstance(name, str):
        parameters = node.get("arguments") if "arguments" in node else node.get("input")
        return name, parameters, _identifier(node.get("call_id"), node.get("id"))
    for wrapper in ("functionCall", "function_call", "function"):
        inner = node.get(wrapper)
        if isinstance(inner, dict) and isinstance(inner.get("name"), str):
            parameters = next((inner[key] for key in _PARAMETER_KEYS if key in inner), None)
            return inner["name"], parameters, _identifier(inner.get("id"), node.get("id"), node.get("call_id"))
    if (
        isinstance(name, str)
        and node_type in (None, "tool_call", "toolCall", "function")
        and any(key in node for key in _PARAMETER_KEYS)
    ):
        parameters = next(node[key] for key in _PARAMETER_KEYS if key in node)
        return name, parameters, _identifier(node.get("id"), node.get("call_id"))
    return None


def _identifier(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _command_of(parameters: Any) -> str | None:
    if isinstance(parameters, str):
        stripped = parameters.strip()
        if not stripped.startswith(("{", "[")):
            return stripped or None
        try:
            parameters = load_json(stripped)
        except ValueError:
            return None
    if not isinstance(parameters, dict):
        return None
    for key in ("command", "cmd", "script"):
        if key in parameters:
            return _command_string(parameters[key])
    return None


def _command_string(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list) and value:
        parts = [str(item) for item in value]
        executable = parts[0].casefold()  # a path-qualified shell is not the shell
        if (
            len(parts) >= 3
            and executable in _SHELL_EXECUTABLES
            and parts[1].casefold() in _SHELL_COMMAND_FLAGS
        ):
            return parts[2] if len(parts) == 3 else shlex.join(parts[2:])
        return shlex.join(parts)
    return None


def _parameter_text(parameters: Any) -> str:
    if isinstance(parameters, str):
        return parameters
    if isinstance(parameters, (dict, list)):
        return json.dumps(parameters, ensure_ascii=False)
    return ""


def _text_of(node: dict[str, Any], role: str | None) -> str | None:
    if role in _USER_ROLES:
        return None
    node_type = node.get("type")
    text = node.get("text")
    if node_type in {"text", "output_text"} and isinstance(text, str):
        return text
    if node_type is None and isinstance(text, str) and "name" not in node:
        return text
    content = node.get("content")
    if isinstance(content, str) and (
        role in _ASSISTANT_ROLES or node_type in _ASSISTANT_ROLES
    ):
        return content
    return None


def _newest(paths: Iterable[Path]) -> Path | None:
    candidates = _sorted_by_mtime(paths)
    return candidates[0] if candidates else None


def _sorted_by_mtime(paths: Iterable[Path]) -> list[Path]:
    files = [path for path in paths if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
