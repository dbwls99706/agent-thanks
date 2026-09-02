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
from typing import Any, Iterable, Iterator, Mapping

from .models import Evidence
from .session import (
    OUTCOME_ERROR,
    OUTCOME_MISSING,
    OUTCOME_OK,
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
_EXIT_CODE_PATTERN = re.compile(r"(?i)\bexit[ _]?code\b\D{0,3}(\d+)")
_MAX_DEPTH = 32

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


def iter_transcript_records(path: Path) -> Iterator[TranscriptRecord]:
    """Yield (record number, kind, text, outcome).

    Kinds: 'command' for a shell command (outcome is 'ok', 'error', 'unknown',
    or 'missing' when the transcript recorded no result for that call), 'text'
    for agent prose, and 'reference' for everything else that may mention a
    repository. Outcome is None for non-command records.
    """
    records = load_transcript_records(path)
    results = _index_results(records)
    tracked = bool(results)
    for number, record in records:
        for kind, text, outcome in _walk(record, None, 0, results, tracked):
            if text.strip():
                yield number, kind, text, outcome


def scan_transcript_evidence(path: Path, source: str) -> list[tuple[str, Evidence]]:
    """Classify every record of a transcript; labels point at the record number."""
    items: list[tuple[str, Evidence]] = []
    for number, kind, text, outcome in iter_transcript_records(path):
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


HOOK_LOG_STATUSES = {RESULT_OK: OUTCOME_OK, RESULT_ERROR: OUTCOME_ERROR, RESULT_UNKNOWN: OUTCOME_UNKNOWN}


def is_hook_log(path: Path) -> bool:
    """Return True for a structured hook log written by ``agent-thanks hook record``."""
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
                return isinstance(record, dict) and {"command", "status", "basis"} <= set(record)
    except OSError:
        return False
    return False


def scan_hook_log_evidence(path: Path, source: str) -> list[tuple[str, Evidence]]:
    """Classify hook log entries; only entries recorded as successful can count as use."""
    items: list[tuple[str, Evidence]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        command = entry.get("command") if isinstance(entry, dict) else None
        if not isinstance(command, str) or not command.strip():
            continue
        outcome = HOOK_LOG_STATUSES.get(str(entry.get("status")), OUTCOME_UNKNOWN)
        items.extend(
            scan_session_evidence(
                command, f"{source}:{number}", line_labels=False, outcome=outcome, single_statement=True
            )
        )
    return items


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


def _index_results(records: Iterable[tuple[int, Any]]) -> dict[str, str]:
    """Map tool call identifiers to 'ok' or 'error' from recorded results."""
    statuses: dict[str, str] = {}
    for _, record in records:
        for node in _iter_dicts(record, 0):
            entry = _result_of(node)
            if entry is not None:
                statuses[entry[0]] = entry[1]
    return statuses


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


def result_status(value: Any) -> str:
    """Judge a recorded tool result with failure-first semantics.

    Every explicit signal in the value is collected. Any failure signal
    (``is_error`` true, a non-zero exit code, a non-empty ``error`` field, a
    failure status, or an "Exit code: N" line with N != 0) makes the result
    RESULT_ERROR, even when a success signal is also present. Without a failure
    signal, only an exact success signal (``is_error`` equal to ``False``, exit
    code 0, a success status, or an "Exit code: 0" line) makes it RESULT_OK.
    Everything else is RESULT_UNKNOWN, which callers must never treat as
    success.
    """
    failures, successes = _result_signals(value, 0)
    if failures:
        return RESULT_ERROR
    if successes:
        return RESULT_OK
    return RESULT_UNKNOWN


def _result_signals(value: Any, depth: int) -> tuple[int, int]:
    failures = successes = 0
    if depth > _MAX_DEPTH:
        return failures, successes
    if isinstance(value, dict):
        if "is_error" in value:
            flag = value["is_error"]
            if flag is False:
                successes += 1
            elif flag is True or (not isinstance(flag, bool) and bool(flag)):
                failures += 1
        for key in ("exit_code", "exitCode", "returncode", "status_code"):
            code = value.get(key)
            if isinstance(code, int) and not isinstance(code, bool):
                if code == 0:
                    successes += 1
                else:
                    failures += 1
        if value.get("error"):
            failures += 1
        status = value.get("status")
        if isinstance(status, str):
            lowered = status.casefold()
            if lowered in _FAILURE_STATUSES:
                failures += 1
            elif lowered in _SUCCESS_STATUSES:
                successes += 1
        for key in (*_RESULT_TEXT_KEYS, "metadata"):
            if key in value:
                nested_failures, nested_successes = _result_signals(value[key], depth + 1)
                failures += nested_failures
                successes += nested_successes
        return failures, successes
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                return _result_signals(parsed, depth + 1)
        for match in _EXIT_CODE_PATTERN.finditer(value):
            if int(match.group(1)) == 0:
                successes += 1
            else:
                failures += 1
        return failures, successes
    if isinstance(value, list):
        for item in value:
            nested_failures, nested_successes = _result_signals(item, depth + 1)
            failures += nested_failures
            successes += nested_successes
        return failures, successes
    return failures, successes


def _result_of(node: dict[str, Any]) -> tuple[str, str] | None:
    """Pair a recorded result with its call identifier; every format uses the same judge."""
    node_type = node.get("type")
    if node_type == "tool_result" and isinstance(node.get("tool_use_id"), str):
        return node["tool_use_id"], result_status(node)
    if node_type in {"function_call_output", "custom_tool_call_output"} and isinstance(
        node.get("call_id"), str
    ):
        return node["call_id"], result_status(node.get("output"))
    response = node.get("functionResponse")
    if isinstance(response, dict) and isinstance(response.get("id"), str):
        return response["id"], result_status(response.get("response"))
    return None


def _walk(
    node: Any, role: str | None, depth: int, results: dict[str, str], tracked: bool
) -> list[tuple[str, str, str | None]]:
    found: list[tuple[str, str, str | None]] = []
    if depth > _MAX_DEPTH:
        return found
    if isinstance(node, list):
        for item in node:
            found.extend(_walk(item, role, depth + 1, results, tracked))
        return found
    if not isinstance(node, dict):
        return found

    role = _role_of(node, role)
    call = _call_of(node)
    if call is not None:
        name, parameters, call_id = call
        if is_shell_tool(name):
            command = _command_of(parameters)
            if command:
                status = results.get(call_id) if call_id is not None else None
                outcome = HOOK_LOG_STATUSES.get(status or "", OUTCOME_MISSING)
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
            found.extend(_walk(value, role, depth + 1, results, tracked))
    return found


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
