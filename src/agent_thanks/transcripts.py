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
_USER_ROLES = {"user", "human", "tool", "function", "system"}
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
_CODEX_CALL_TYPES = frozenset({"function_call", "custom_tool_call"})
_CODEX_OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
HOOK_LOG_SCHEMA = "agent-thanks/hook-log/1"
HOOK_AGENTS = frozenset({"claude-code", "codex", "gemini"})
PROMOTING_BASES = frozenset({"exit_status", "successful_post_tool_event"})

RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_UNKNOWN = "unknown"
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


def load_transcript_records(path: Path) -> list[tuple[int, Any]]:
    """Return (record number, record) pairs.

    A JSON Lines file numbers records by physical line. A single JSON document
    numbers the items of its message list, or counts as one record.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip("﻿ \t\r\n")
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError:
        document = None

    if document is None:
        records: list[tuple[int, Any]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append((number, json.loads(line)))
            except json.JSONDecodeError:
                continue
        return records
    if isinstance(document, list):
        return list(enumerate(document, start=1))
    if isinstance(document, dict):
        for key in _DOCUMENT_LIST_KEYS:
            value = document.get(key)
            if isinstance(value, list):
                return list(enumerate(value, start=1))
    return [(1, document)]


def iter_transcript_records(
    path: Path, *, authoritative: Mapping[str, "HookStatus"] | None = None
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
    reuses for different calls is a 'conflict' as well.
    """
    records = load_transcript_records(path)
    calls = _index_calls(records)
    results = _index_results(records, calls)
    for number, record in records:
        for kind, text, outcome in _walk(record, None, 0, calls, results, authoritative):
            if text.strip():
                yield number, kind, text, outcome


def scan_transcript_evidence(
    path: Path, source: str, *, authoritative: Mapping[str, "HookStatus"] | None = None
) -> list[tuple[str, Evidence]]:
    """Classify every record of a transcript; labels point at the record number."""
    items: list[tuple[str, Evidence]] = []
    for number, kind, text, outcome in iter_transcript_records(path, authoritative=authoritative):
        label = f"{source}:{number}"
        if kind == "command":
            items.extend(
                scan_session_evidence(
                    text,
                    label,
                    line_labels=False,
                    outcome=outcome or OUTCOME_MISSING,
                    single_statement=True,
                )
            )
        elif kind == "text":
            items.extend(scan_prose_evidence(text, label, line_labels=False))
        else:
            items.extend(scan_reference_evidence(text, label, line_labels=False))
    return items


def transcript_calls(path: Path) -> dict[str, str | None]:
    """Map each tool call id a transcript uses to the shell command behind it.

    The value is ``None`` when the id is reused for different calls or belongs
    to a call that is not a shell command, so a hook log entry with that id can
    never agree with the transcript.
    """
    return {
        call_id: info.command if info is not None else None
        for call_id, info in _index_calls(load_transcript_records(path)).items()
    }


def merge_transcript_calls(maps: Iterable[Mapping[str, str | None]]) -> dict[str, str | None]:
    """Merge call maps from several transcripts; an id they disagree about maps to ``None``."""
    merged: dict[str, str | None] = {}
    for calls in maps:
        for call_id, command in calls.items():
            if call_id in merged and merged[call_id] != command:
                merged[call_id] = None
            else:
                merged.setdefault(call_id, command)
    return merged


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
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return False
                return isinstance(record, dict) and record.get("schema") == HOOK_LOG_SCHEMA
    except OSError:
        return False
    return False


class HookStatus(NamedTuple):
    """Combined status of one tool call in a hook log, with the exact command it recorded."""

    status: str
    command: str | None


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
    entry is complete: it names a known agent, a promoting basis, a non-empty
    tool call id, and a non-empty command. Anything else is unknown.
    """
    status = entry.get("status")
    if status == RESULT_ERROR:
        return RESULT_ERROR
    call_id = entry.get("tool_call_id")
    command = entry.get("command")
    complete = (
        status == RESULT_OK
        and entry.get("agent") in HOOK_AGENTS
        and entry.get("basis") in PROMOTING_BASES
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
    path: Path, source: str, *, transcript_calls: Mapping[str, str | None] | None = None
) -> list[tuple[str, Evidence]]:
    """Classify hook log entries; only calls whose every entry recorded success can count as use.

    Entries that share a tool call id are judged by their combined status, so a
    call recorded as failed once stays a reference even if a later entry for the
    same call recorded success. An entry without a tool call id never counts.
    When the transcripts scanned alongside the log know the call id
    (``transcript_calls``), the entry counts only if they recorded the identical
    command for it; a disagreement or a reused id is a conflict.
    """
    entries = _hook_log_entries(path)
    combined = _combine_hook_log(entries)
    items: list[tuple[str, Evidence]] = []
    for number, entry in entries:
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        call_id = entry.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            outcome = HOOK_LOG_STATUSES.get(combined[call_id].status, OUTCOME_UNKNOWN)
            if transcript_calls is not None and call_id in transcript_calls:
                recorded = transcript_calls[call_id]
                if recorded is None:
                    outcome = OUTCOME_AMBIGUOUS
                elif recorded != command:
                    outcome = OUTCOME_CONFLICT
        else:
            status = RESULT_ERROR if entry.get("status") == RESULT_ERROR else RESULT_UNKNOWN
            outcome = HOOK_LOG_STATUSES.get(status, OUTCOME_UNKNOWN)
        items.extend(
            scan_session_evidence(
                command, f"{source}:{number}", line_labels=False, outcome=outcome, single_statement=True
            )
        )
    return items


def _hook_log_entries(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """Return the schema-marked entries of a hook log with their line numbers."""
    entries: list[tuple[int, dict[str, Any]]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("schema") == HOOK_LOG_SCHEMA:
            entries.append((number, entry))
    return entries


def _combine_hook_log(entries: Iterable[tuple[int, dict[str, Any]]]) -> dict[str, HookStatus]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for _, entry in entries:
        call_id = entry.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            grouped.setdefault(call_id, []).append(entry)
    return {call_id: combine_hook_entries(items) for call_id, items in grouped.items()}


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
    after normalization. When ``session_id`` is given, the transcript must also
    record that identifier (or carry it in its file name). Nothing falls back
    to "the newest file".
    """
    (directory,) = transcript_locations(agent, cwd, home, environ)
    if agent == "claude-code":
        candidates = _sorted_by_mtime(directory.glob("*.jsonl"))
        if session_id is not None:
            return next((path for path in candidates if path.stem == session_id), None)
        return candidates[0] if candidates else None

    pattern = "*.jsonl" if agent == "codex" else "*.json"
    candidates = _sorted_by_mtime(
        path for path in directory.rglob(pattern) if path.name != "settings.json"
    )
    for candidate in candidates[:200]:
        metadata = transcript_metadata(candidate)
        if metadata.cwd is None or not same_path(metadata.cwd, cwd):
            continue
        if (
            session_id is not None
            and metadata.session_id != session_id
            and not candidate.stem.endswith(session_id)
        ):
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
    return os.path.normcase(os.path.normpath(trimmed)).replace("\\", "/")


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
        document = json.loads(stripped)
    except json.JSONDecodeError:
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
            records.append(json.loads(line))
        except json.JSONDecodeError:
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


def _index_calls(records: Iterable[tuple[int, Any]]) -> dict[str, CallInfo | None]:
    """Map tool call identifiers to the call behind them, read from envelope positions only.

    An identifier that the transcript reuses for different calls (another tool,
    other parameters, or another record kind) maps to ``None``: no result can
    then be attributed to any of them.
    """
    calls: dict[str, CallInfo | None] = {}
    for _, record in records:
        for node in _result_envelopes(record):
            call = _call_of(node)
            if call is None or call[2] is None:
                continue
            name, parameters, call_id = call
            command = _command_of(parameters) if is_shell_tool(name) else None
            info = CallInfo(
                _call_kind(node), name, json.dumps(parameters, sort_keys=True, default=str), command
            )
            if call_id not in calls:
                calls[call_id] = info
            elif calls[call_id] != info:
                calls[call_id] = None
    return calls


def _call_kind(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if node_type in {"tool_use"} | _CODEX_CALL_TYPES:
        return str(node_type)
    if any(isinstance(node.get(wrapper), dict) for wrapper in ("functionCall", "function_call", "function")):
        return "functionCall"
    return "generic"


def _index_results(
    records: Iterable[tuple[int, Any]], calls: Mapping[str, CallInfo | None]
) -> dict[str, str]:
    """Map tool call identifiers to a combined status from recorded result envelopes.

    Only envelope positions are read: a record, its ``payload``, and the items
    of its message ``content`` or ``parts`` lists. Result-shaped objects nested
    inside program output are never indexed. Several results for one call are
    combined with failure first. ``calls`` identifies the call behind each
    result so a string result is interpreted only for the Codex records whose
    envelope is known.
    """
    statuses: dict[str, list[str]] = {}
    for _, record in records:
        for node in _result_envelopes(record):
            entry = _result_of(node, calls)
            if entry is not None:
                statuses.setdefault(entry[0], []).append(entry[1])
    return {call_id: combine_statuses(values) for call_id, values in statuses.items()}


def _result_envelopes(record: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(record, dict):
        return
    yield record
    payload = record.get("payload")
    if isinstance(payload, dict):
        yield payload
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    for container in (
        message.get("content"),
        message.get("parts"),
        record.get("content"),
        record.get("parts"),
    ):
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    yield item


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


def result_status(value: Any, *, codex_envelope: bool = False) -> str:
    """Judge a recorded tool result envelope with failure-first semantics.

    Success signals are accepted only from structured envelope fields: in a
    result object, an ``is_error`` flag equal to ``False`` or an integer exit
    code of 0 (top level or under ``metadata``). A string result yields a
    success signal only when ``codex_envelope`` is set, which callers do only
    for the envelope Codex writes for its own shell tools: a JSON-encoded
    envelope's exit code, or the exit code in the header block that precedes
    the ``Output:`` marker. Program output (``output``, ``stdout``,
    ``content``, ``message``, ``text``) and any other bare text can never create
    a success signal, so a program that prints a success message cannot fake
    one. Failure signals are accepted from anywhere. Any failure makes the
    result RESULT_ERROR; without a failure, an exact success makes it
    RESULT_OK; otherwise RESULT_UNKNOWN.
    """
    failures, successes = _envelope_signals(value, 0, codex_envelope)
    if failures:
        return RESULT_ERROR
    if successes:
        return RESULT_OK
    return RESULT_UNKNOWN


def _envelope_signals(value: Any, depth: int, codex_envelope: bool) -> tuple[int, int]:
    failures = successes = 0
    if depth > 4:
        return failures, successes
    if isinstance(value, dict):
        if "is_error" in value:
            flag = value["is_error"]
            if flag is False:
                successes += 1
            elif flag is True or (not isinstance(flag, bool) and bool(flag)):
                failures += 1
        code = _exit_code(value)
        if code is not None:
            if code == 0:
                successes += 1
            else:
                failures += 1
        if value.get("error"):
            failures += 1
        status = value.get("status")
        if isinstance(status, str) and status.casefold() in _FAILURE_STATUSES:
            failures += 1
        for key in _RESULT_TEXT_KEYS:
            failures += _output_failures(value.get(key), depth + 1)
        return failures, successes
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                # A JSON-encoded envelope: only Codex's own shell tools may prove success with it.
                code = _exit_code(parsed)
                if code is not None:
                    if code != 0:
                        failures += 1
                    elif codex_envelope:
                        successes += 1
                if parsed.get("error"):
                    failures += 1
                status = parsed.get("status")
                if isinstance(status, str) and status.casefold() in _FAILURE_STATUSES:
                    failures += 1
                for key in _RESULT_TEXT_KEYS:
                    failures += _output_failures(parsed.get(key), depth + 1)
                return failures, successes
        header, body = _split_text_envelope(stripped) if codex_envelope else ("", stripped)
        for match in _EXIT_CODE_PATTERN.finditer(header):
            if int(match.group(1)) == 0:
                successes += 1
            else:
                failures += 1
        failures += _output_failures(body, depth + 1)
        return failures, successes
    return failures, successes


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


def _exit_code(mapping: dict[str, Any]) -> int | None:
    """Read a structured exit code from an envelope or its ``metadata`` block."""
    for key in ("exit_code", "exitCode", "returncode", "status_code"):
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    metadata = mapping.get("metadata")
    if isinstance(metadata, dict):
        return _exit_code(metadata)
    return None


def _output_failures(output: Any, depth: int) -> int:
    """Count failure signals inside program output; output never yields success."""
    if depth > 4:
        return 0
    if isinstance(output, str):
        return sum(1 for match in _EXIT_CODE_PATTERN.finditer(output) if int(match.group(1)) != 0)
    if isinstance(output, list):
        return sum(_output_failures(item, depth + 1) for item in output)
    if isinstance(output, dict):
        total = 0
        code = _exit_code(output)
        if code not in (None, 0):
            total += 1
        if output.get("error"):
            total += 1
        for key in _RESULT_TEXT_KEYS:
            total += _output_failures(output.get(key), depth + 1)
        return total
    return 0


def _result_of(
    node: dict[str, Any], calls: Mapping[str, CallInfo | None]
) -> tuple[str, str] | None:
    """Pair a recorded result with its call identifier; every format uses the same judge."""
    node_type = node.get("type")
    if node_type == "tool_result" and isinstance(node.get("tool_use_id"), str):
        return node["tool_use_id"], result_status(node)
    if node_type in _CODEX_OUTPUT_TYPES and isinstance(node.get("call_id"), str):
        # Codex writes the output envelope itself only for its own shell tools, and
        # only a Codex call record proves that this is such a call. Any other string
        # result is program output and never proves success.
        call_id = node["call_id"]
        info = calls.get(call_id)
        codex_envelope = (
            info is not None and info.kind in _CODEX_CALL_TYPES and info.tool in CODEX_SHELL_TOOLS
        )
        return call_id, result_status(node.get("output"), codex_envelope=codex_envelope)
    response = node.get("functionResponse")
    if isinstance(response, dict) and isinstance(response.get("id"), str):
        return response["id"], result_status(response.get("response"))
    return None


def _walk(
    node: Any,
    role: str | None,
    depth: int,
    calls: Mapping[str, CallInfo | None],
    results: dict[str, str],
    authoritative: Mapping[str, HookStatus] | None,
) -> list[tuple[str, str, str | None]]:
    found: list[tuple[str, str, str | None]] = []
    if depth > _MAX_DEPTH:
        return found
    if isinstance(node, list):
        for item in node:
            found.extend(_walk(item, role, depth + 1, calls, results, authoritative))
        return found
    if not isinstance(node, dict):
        return found

    role = _role_of(node, role)
    call = _call_of(node)
    if call is not None:
        if role in _USER_ROLES:
            return found  # call-shaped objects in user or tool content are never actions
        name, parameters, call_id = call
        if is_shell_tool(name):
            command = _command_of(parameters)
            if command:
                outcome = _command_outcome(call_id, command, calls, results, authoritative)
                found.append(("command", command, outcome))
        else:
            parameter_text = _parameter_text(parameters)
            if parameter_text:
                found.append(("reference", parameter_text, None))
        return found

    text = _text_of(node, role)
    if text is not None:
        found.append(("text", text, None))
    for key, value in node.items():
        if key in _OUTPUT_KEYS:
            continue
        if isinstance(value, (dict, list)):
            found.extend(_walk(value, role, depth + 1, calls, results, authoritative))
    return found


def _command_outcome(
    call_id: str | None,
    command: str,
    calls: Mapping[str, CallInfo | None],
    results: dict[str, str],
    authoritative: Mapping[str, HookStatus] | None,
) -> str:
    """Decide what a transcript command may claim; every condition must hold for a success.

    The call needs an identifier that the transcript uses for this call alone.
    With a hook log, the log must hold that identifier with the identical
    command text and a combined ok status. Without one, the transcript's own
    combined result for the identifier must be ok.
    """
    if call_id is None:
        return OUTCOME_UNCONFIRMED if authoritative is not None else OUTCOME_MISSING
    if call_id in calls and calls[call_id] is None:
        return OUTCOME_AMBIGUOUS
    if authoritative is not None:
        entry = authoritative.get(call_id)
        if entry is None:
            return OUTCOME_UNCONFIRMED
        if entry.command is None or entry.command != command:
            return OUTCOME_CONFLICT
        return HOOK_LOG_STATUSES.get(entry.status, OUTCOME_UNCONFIRMED)
    return HOOK_LOG_STATUSES.get(results.get(call_id, ""), OUTCOME_MISSING)


def _role_of(node: dict[str, Any], inherited: str | None) -> str | None:
    role = node.get("role")
    if isinstance(role, str):
        return role.casefold()
    record_type = node.get("type")
    if isinstance(record_type, str) and record_type.casefold() in _ASSISTANT_ROLES | _USER_ROLES:
        return record_type.casefold()
    return inherited


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
            parameters = json.loads(stripped)
        except json.JSONDecodeError:
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
        executable = os.path.basename(parts[0]).casefold()
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
