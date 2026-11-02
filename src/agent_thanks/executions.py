"""Record commands run directly by agent-thanks, without invoking a shell.

The caller owns private storage. Records contain raw argv, which can include
secrets, but never copy a child's stdout, stderr, or environment into the log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Callable, Iterable, Sequence
from uuid import uuid4

from .models import Evidence
from .session import (
    OUTCOME_CONFLICT,
    OUTCOME_CORRUPTED,
    OUTCOME_ERROR,
    OUTCOME_MISSING,
    OUTCOME_OK,
    OUTCOME_UNANCHORED,
    OUTCOME_UNKNOWN,
    scan_session_evidence,
)
from .transcripts import load_json, same_path


EXECUTION_LOG_SCHEMA = "agent-thanks/execution-log/1"
_SCHEMA_PREFIX = "agent-thanks/execution-log/"
_TRUNCATED_LOG_PREFIX = re.compile(r'^\s*\{\s*"schema"\s*:\s*"agent-thanks/execution-log/')
_COMMON_FIELDS = frozenset({"schema", "event", "execution_id", "cwd", "argv", "recorded_at"})
PrivateWriter = Callable[[Path, str], None]


def _argv(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if not all(isinstance(item, str) and "\0" not in item for item in value):
        return None
    if not value[0]:
        return None
    return tuple(value)


def execute(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    write_record: PrivateWriter,
) -> int:
    """Run argv directly with inherited stdio and return the child's status.

    ``write_record(path, text)`` must append privately and reject unsafe paths.
    The start must be persisted before the child is launched. A POSIX signal
    returncode stays negative; a CLI may translate it to ``128 - returncode``.
    Launch failures return 127 (not found) or 126 (other OS error). Ctrl+C is
    recorded as an unknown outcome and re-raised for the caller's normal 130.
    """
    arguments = _argv(argv)
    if arguments is None:
        raise ValueError("exec requires a non-empty argv containing strings without null bytes")
    root = cwd.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Execution project directory does not exist: {root}")
    common = {
        "schema": EXECUTION_LOG_SCHEMA,
        "execution_id": uuid4().hex,
        "cwd": str(root),
        "argv": list(arguments),
    }

    def record(event: str, **fields: object) -> None:
        entry = dict(common, event=event, recorded_at=datetime.now(timezone.utc).isoformat())
        entry.update(fields)
        write_record(log_path, json.dumps(entry, ensure_ascii=False) + "\n")

    record("started")
    try:
        result = subprocess.run(list(arguments), cwd=str(root), shell=False, check=False)
    except KeyboardInterrupt:
        try:
            record("interrupted", exit_code=None)
        except OSError:
            # The persisted start still provides no success. Preserve Ctrl+C
            # even if storage becomes unavailable while the child is running.
            pass
        raise
    except OSError as error:
        code = 127 if isinstance(error, FileNotFoundError) else 126
        record("launch_failed", exit_code=code)
        print(f"agent-thanks exec: {error}", file=sys.stderr)
        return code
    if type(result.returncode) is not int:
        raise ValueError("Executed command returned an invalid exit status")
    record("finished", exit_code=result.returncode)
    return result.returncode


def is_execution_log(path: Path) -> bool:
    """Recognize native logs, including damaged or unsupported-schema logs.

    Recognition does not establish validity. A damaged native log must reach
    the strict scanner rather than become trusted plain text through a flag.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.lstrip().startswith("{"):
                    continue
                try:
                    # This permissive parse identifies the format only. Keep
                    # repeated schema keys visible; the evidence reader below
                    # still rejects every duplicate key before promotion.
                    pairs = json.loads(line, object_pairs_hook=lambda value: value)
                except ValueError:
                    if _TRUNCATED_LOG_PREFIX.match(line):
                        return True
                    continue
                if isinstance(pairs, list) and any(
                    key == "schema" and isinstance(value, str) and value.startswith(_SCHEMA_PREFIX)
                    for key, value in pairs
                ):
                    return True
    except OSError:
        return False
    return False


@dataclass(frozen=True)
class _Record:
    number: int
    origin: str
    source: str
    execution_id: str
    cwd: str
    argv: tuple[str, ...]
    event: str
    exit_code: int | None
    timestamp: datetime


def _record(value: object, number: int, origin: str, source: str) -> _Record | None:
    if not isinstance(value, dict) or value.get("schema") != EXECUTION_LOG_SCHEMA:
        return None
    event = value.get("event")
    if not isinstance(event, str) or event not in {"started", "finished", "interrupted", "launch_failed"}:
        return None
    expected = _COMMON_FIELDS if event == "started" else _COMMON_FIELDS | {"exit_code"}
    if value.keys() != expected:
        return None
    identity, cwd = value.get("execution_id"), value.get("cwd")
    argv = _argv(value.get("argv"))
    if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{32}", identity) is None:
        return None
    if not isinstance(cwd, str) or "\0" in cwd or not Path(cwd).is_absolute() or argv is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    code = value.get("exit_code")
    if event == "finished" and type(code) is not int:
        return None
    if event == "interrupted" and code is not None:
        return None
    if event == "launch_failed" and (type(code) is not int or code not in {126, 127}):
        return None
    return _Record(number, origin, source, identity, cwd, argv, event, code, timestamp)


def _outcome(records: list[_Record]) -> str:
    starts = [record for record in records if record.event == "started"]
    ends = [record for record in records if record.event != "started"]
    if len(starts) > 1 or len(ends) > 1 or len({record.argv for record in records}) > 1:
        return OUTCOME_CONFLICT
    if not starts:
        return OUTCOME_UNANCHORED
    if not ends:
        return OUTCOME_MISSING
    start, end = starts[0], ends[0]
    if start.origin != end.origin:
        return OUTCOME_UNANCHORED
    if end.number < start.number:
        return OUTCOME_UNANCHORED
    if end.timestamp < start.timestamp:
        return OUTCOME_CONFLICT
    if end.event == "interrupted":
        return OUTCOME_UNKNOWN
    if end.event != "finished" or end.exit_code != 0:
        return OUTCOME_ERROR
    return OUTCOME_OK


def scan_execution_log_evidence(
    path: Path, source: str | None = None, *, root: Path
) -> list[tuple[str, Evidence]]:
    """Read one native execution log using the shared cross-file validator."""
    return scan_execution_logs_evidence([(path, str(path) if source is None else source)], root=root)


def scan_execution_logs_evidence(
    sources: Iterable[tuple[Path, str]], *, root: Path
) -> list[tuple[str, Evidence]]:
    """Read native execution evidence belonging to exactly this project.

    One malformed record makes all commands in the file review-only. Each
    successful command otherwise needs one matching start and later finish in
    that same file, identical argv, an explicit integer zero exit status, and
    no conflicting copies in any other input. Different execution identifiers
    and different projects cannot supply an outcome for each other.
    """
    groups: dict[str, list[_Record]] = {}
    damaged: set[str] = set()
    tainted_ids: set[str] = set()
    seen_files: set[str] = set()
    references: list[tuple[str, tuple[str, ...]]] = []

    def taint_pairs(pairs: list[tuple[str, object]]) -> None:
        # This only removes authority. It never accepts a repeated-key record
        # as evidence: every possibly matching identity is kept conservative.
        if any(key == "cwd" and isinstance(value, str) and same_path(value, root) for key, value in pairs):
            tainted_ids.update(
                value for key, value in pairs
                if key == "execution_id" and isinstance(value, str)
                and re.fullmatch(r"[a-f0-9]{32}", value) is not None
            )

    for path, label in sources:
        path = Path(path)
        origin = str(path.expanduser().resolve())
        if origin in seen_files:
            continue
        seen_files.add(origin)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            damaged.add(origin)
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            source = f"{label}:{number}"
            try:
                value = load_json(line)
            except ValueError:
                damaged.add(origin)
                try:
                    pairs = json.loads(line, object_pairs_hook=lambda value: value)
                except ValueError:
                    continue
                if line.lstrip().startswith("{") and isinstance(pairs, list):
                    taint_pairs(pairs)
                continue
            record = _record(value, number, origin, source)
            if record is None:
                damaged.add(origin)
                if isinstance(value, dict):
                    taint_pairs(list(value.items()))
                    cwd, argv = value.get("cwd"), _argv(value.get("argv"))
                    if isinstance(cwd, str) and argv is not None and same_path(cwd, root):
                        references.append((source, argv))
                continue
            if same_path(record.cwd, root):
                groups.setdefault(record.execution_id, []).append(record)

    items: list[tuple[str, Evidence]] = []

    def scan(source: str, argv: tuple[str, ...], outcome: str) -> None:
        for repository, evidence in scan_session_evidence(
            shlex.join(argv), source, line_labels=False,
            outcome=outcome, single_statement=True, provenance=False,
        ):
            items.append((repository, Evidence(
                kind="execution_usage" if evidence.meaningful else "execution_reference",
                source=evidence.source,
                detail=evidence.detail.replace("Session", "Recorded execution", 1),
                confidence=evidence.confidence,
                meaningful=evidence.meaningful,
            )))

    for identity, records in groups.items():
        corrupted = identity in tainted_ids or any(record.origin in damaged for record in records)
        outcome = OUTCOME_CORRUPTED if corrupted else _outcome(records)
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for record in records:
            key = record.origin, record.argv
            if key not in seen:
                scan(record.source, record.argv, outcome)
                seen.add(key)
    for source, argv in references:
        scan(source, argv, OUTCOME_CORRUPTED)
    return items
