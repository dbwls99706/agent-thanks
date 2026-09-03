from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Sequence

from . import __version__
from .exporter import render_markdown
from .github import GitHubClient, GitHubError, validate_repository
from .models import Candidate, Evidence, Report
from .resolver import PackageRepositoryResolver
from .scanner import ProjectScanner, ScanError
from .transcripts import (
    RESULT_ERROR,
    RESULT_OK,
    RESULT_UNKNOWN,
    is_shell_tool,
    HOOK_LOG_SCHEMA,
    HOOK_PROMOTION_MATRIX,
    locate_transcript,
    result_status,
    transcript_locations,
)


DEFAULT_REPORT = ".agent-thanks-report.json"
STATE_DIRECTORY = ".agent-thanks"
POST_TOOL_EVENTS = frozenset({"PostToolUse", "PostToolUseFailure", "AfterTool"})
SESSION_LOG_MAX_AGE_SECONDS = 30 * 24 * 3600
AGENTS = ("claude-code", "codex", "gemini")


class InteractionError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-thanks",
        description=(
            "Find open-source repositories meaningfully used during an AI coding "
            "session and review the evidence before thanking them."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "demo",
        help="Preview the evidence and dry-run flow without credentials or network access",
    )

    run = commands.add_parser(
        "run",
        help="Scan, review, and ask for each eligible Star in one command",
    )
    _add_scan_arguments(run)
    run.add_argument("--dry-run", action="store_true", help="Show actions without starring")

    scan = commands.add_parser(
        "scan",
        help="Create a read-only evidence report without starring",
    )
    _add_scan_arguments(scan)

    review = commands.add_parser(
        "review",
        help="Review a scan report without changing GitHub",
    )
    review.add_argument("report", nargs="?", type=Path, default=Path(DEFAULT_REPORT))

    export = commands.add_parser(
        "export",
        help="Export a report as shareable Markdown without changing GitHub",
    )
    export.add_argument("report", nargs="?", type=Path, default=Path(DEFAULT_REPORT))
    export.add_argument(
        "--output",
        type=Path,
        default=Path("-"),
        help="Markdown path, or '-' for stdout (default: -)",
    )
    export.add_argument(
        "--include-low-confidence",
        action="store_true",
        help="Include a separate section for references that require review",
    )

    star = commands.add_parser(
        "star",
        help="Ask for explicit approval before each eligible Star in a saved report",
    )
    star.add_argument("report", nargs="?", type=Path, default=Path(DEFAULT_REPORT))
    star.add_argument(
        "--repo",
        dest="repositories",
        action="append",
        default=[],
        help="Star only this owner/repo; repeatable",
    )
    star.add_argument("--dry-run", action="store_true", help="Show actions without starring")

    unstar = commands.add_parser("unstar", help="Revoke stars previously granted")
    unstar.add_argument("repositories", nargs="+", metavar="OWNER/REPO")
    unstar.add_argument("--dry-run", action="store_true", help="Show actions without unstarring")

    doctor = commands.add_parser(
        "doctor",
        help="Check the local project and GitHub authentication",
    )
    doctor.add_argument("--repo", type=Path, default=Path.cwd(), help="Project root")

    hook = commands.add_parser(
        "hook",
        help="Entry points for coding-agent hooks; detection only, never a Star",
    )
    hook_commands = hook.add_subparsers(dest="hook_command", required=True)
    record = hook_commands.add_parser(
        "record",
        help=(
            "Append an executed shell command with its recorded outcome to "
            f"{STATE_DIRECTORY}/sessions/<session>.jsonl"
        ),
    )
    record.add_argument("payload", nargs="?", help="Hook JSON; read from stdin when omitted")
    record.add_argument(
        "--from",
        dest="agent",
        choices=AGENTS,
        help="Agent whose hook contract applies when the payload records no result",
    )
    stop = hook_commands.add_parser(
        "stop",
        help="Scan the finished turn and announce verified repositories without starring",
    )
    stop.add_argument("payload", nargs="?", help="Hook JSON; read from stdin when omitted")
    stop.add_argument(
        "--from",
        dest="agent",
        choices=AGENTS,
        help="Locate this agent's transcript when the payload carries no transcript path",
    )
    stop.add_argument("--offline", action="store_true", help="Skip package registry lookups")

    return parser


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Project root")
    parser.add_argument(
        "--base",
        default="HEAD",
        help="Git revision representing the state before the agent worked (default: HEAD)",
    )
    parser.add_argument(
        "--session",
        type=Path,
        action="append",
        default=[],
        help="Agent transcript or log file; repeatable, '-' reads stdin",
    )
    parser.add_argument(
        "--from",
        dest="agent",
        choices=AGENTS,
        help="Scan the newest transcript this coding agent wrote for the project",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_REPORT),
        help=f"Report path, or '-' for stdout with scan (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not query package registries to map packages to repositories",
    )
    parser.add_argument(
        "--trust-session",
        action="store_true",
        help=(
            "Attest that the commands in plain-text session logs completed successfully; "
            "without it those commands stay review-only references"
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "demo":
            return _demo()
        if args.command == "scan":
            return _scan(args)
        if args.command == "review":
            return _review(args.report)
        if args.command == "export":
            return _export(args)
        if args.command == "star":
            return _star(args)
        if args.command == "unstar":
            return _unstar(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "hook":
            return _hook(args)
    except (OSError, ValueError, InteractionError, ScanError, GitHubError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    parser.error("Unknown command")
    return 2


def _build_report(args: argparse.Namespace) -> Report:
    resolver = PackageRepositoryResolver(offline=args.offline)
    sessions = list(args.session)
    if args.agent:
        sessions.append(_locate_agent_transcript(args.agent, args.repo))
    scanner = ProjectScanner(
        args.repo, base=args.base, resolver=resolver, trust_sessions=args.trust_session
    )
    return scanner.scan(sessions)


def _locate_agent_transcript(agent: str, root: Path) -> Path:
    cwd = root.expanduser().resolve()
    transcript = locate_transcript(agent, cwd, Path.home())
    if transcript is None:
        searched = ", ".join(str(path) for path in transcript_locations(agent, cwd, Path.home()))
        raise ValueError(
            f"No {agent} transcript found for {cwd} (searched: {searched}). "
            "Pass the transcript path with --session instead."
        )
    print(f"Transcript: {transcript}", file=sys.stderr)
    return transcript


def _demo() -> int:
    report = Report(
        root="<built-in demo>",
        base=None,
        candidates=[
            Candidate(
                "BehaviorTree/BehaviorTree.CPP",
                [
                    Evidence(
                        kind="session_usage",
                        source="demo-session.jsonl:2",
                        detail="Session ran a repository-use command that completed successfully",
                        confidence="high",
                        meaningful=True,
                    )
                ],
            ),
            Candidate(
                "example/reference-only",
                [
                    Evidence(
                        kind="session_reference",
                        source="demo-session.jsonl:1",
                        detail="Repository was referenced in the session; verify actual reuse",
                        confidence="low",
                        meaningful=False,
                    )
                ],
            ),
            Candidate(
                "ros-navigation/navigation2",
                [
                    Evidence(
                        kind="session_usage",
                        source="demo-session.jsonl:4",
                        detail="Session states that code was adapted from this repository",
                        confidence="high",
                        meaningful=True,
                    )
                ],
            ),
        ],
    )
    print("agent-thanks demo — read-only; no credentials or network requests\n")
    _print_report(report)
    print("\nDry-run actions for verified, meaningful-use repositories:")
    selected = [candidate for candidate in report.candidates if candidate.recommended]
    status = _execute_stars(selected, dry_run=True)
    print("\nNext: agent-thanks run --repo . --dry-run")
    return status


def _write_report(report: Report, output_argument: Path) -> Path:
    output = output_argument.resolve()
    report.write(output)
    print(f"Report: {output}")
    _print_summary(report, output)
    return output


def _scan(args: argparse.Namespace) -> int:
    report = _build_report(args)
    if str(args.output) == "-":
        print(report.to_json(), end="")
    else:
        _write_report(report, args.output)
    return 0


def _run(args: argparse.Namespace) -> int:
    if str(args.output) == "-":
        raise ValueError("run cannot use --output -. Use scan for JSON-only output.")
    report = _build_report(args)
    _write_report(report, args.output)
    eligible = _eligible_candidates(report.candidates)
    _print_ineligible_summary(report.candidates)
    return _review_and_star(eligible, dry_run=args.dry_run)


def _review(path: Path) -> int:
    report = Report.read(path)
    _print_report(report)
    return 0


def _export(args: argparse.Namespace) -> int:
    report = Report.read(args.report)
    markdown = render_markdown(
        report,
        include_low_confidence=args.include_low_confidence,
    )
    if str(args.output) == "-":
        print(markdown, end="")
    else:
        output = args.output.expanduser().resolve()
        output.write_text(markdown, encoding="utf-8")
        print(f"Markdown: {output}")
    return 0


def _star(args: argparse.Namespace) -> int:
    report = Report.read(args.report)
    requested = {validate_repository(item).casefold() for item in args.repositories}

    if requested:
        selected = [item for item in report.candidates if item.repository.casefold() in requested]
        missing = requested - {item.repository.casefold() for item in selected}
        if missing:
            raise ValueError(
                f"Repositories are not present in the report: {', '.join(sorted(missing))}"
            )
        ineligible = [item.repository for item in selected if not item.recommended]
        if ineligible:
            raise ValueError(
                "Cannot star candidates without high-confidence meaningful-use evidence: "
                + ", ".join(sorted(ineligible, key=str.casefold))
            )
    else:
        selected = _eligible_candidates(report.candidates)
        _print_ineligible_summary(report.candidates)

    return _review_and_star(selected, dry_run=args.dry_run)


def _eligible_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return [candidate for candidate in candidates if candidate.recommended]


def _print_ineligible_summary(candidates: list[Candidate]) -> None:
    count = sum(not candidate.recommended for candidate in candidates)
    if count:
        print(
            f"Review only: {count} candidate(s) lack high-confidence "
            "meaningful-use evidence and cannot be starred."
        )


def _review_and_star(selected: list[Candidate], *, dry_run: bool) -> int:
    for candidate in selected:
        validate_repository(candidate.repository)
    if dry_run:
        return _execute_stars(selected, dry_run=True)
    if not selected:
        print("No repositories eligible for starring.")
        return 0

    _require_interactive_terminal("Starring")
    client = GitHubClient()
    print(f"GitHub account: @{client.whoami()}")
    pending = _exclude_existing_stars(selected, client)
    if not pending:
        print("No unstarred repositories require a decision.")
        return 0
    print("Each new Star requires an explicit yes. The default is No.")
    approved = _interactive_selection(pending)
    return _execute_stars(
        approved,
        dry_run=False,
        client=client,
        known_unstarred=True,
    )


def _exclude_existing_stars(
    candidates: list[Candidate], client: GitHubClient
) -> list[Candidate]:
    pending: list[Candidate] = []
    for index, candidate in enumerate(candidates):
        if client.is_starred(candidate.repository):
            print(f"Already starred: https://github.com/{candidate.repository}")
        else:
            pending.append(candidate)
        if index + 1 < len(candidates):
            time.sleep(0.25)
    return pending


def _execute_stars(
    selected: list[Candidate],
    *,
    dry_run: bool,
    client: GitHubClient | None = None,
    known_unstarred: bool = False,
) -> int:
    if not selected:
        print("No repositories selected.")
        return 0

    if dry_run:
        for candidate in selected:
            print(f"Would star: https://github.com/{candidate.repository}")
        return 0

    if client is None:
        raise InteractionError("An authenticated interactive Star session is required.")
    newly_starred: list[str] = []
    try:
        for index, candidate in enumerate(selected):
            if not known_unstarred and client.is_starred(candidate.repository):
                print(f"Already starred: https://github.com/{candidate.repository}")
            else:
                client.star(candidate.repository)
                newly_starred.append(candidate.repository)
                print(f"Starred: https://github.com/{candidate.repository}")
            if index + 1 < len(selected):
                time.sleep(0.25)
    except (GitHubError, OSError):
        if newly_starred:
            print("The batch stopped after a partial update.")
            _print_undo(newly_starred)
        raise
    _print_undo(newly_starred)
    return 0


def _unstar(args: argparse.Namespace) -> int:
    repositories = [validate_repository(item) for item in args.repositories]
    if args.dry_run:
        for repository in repositories:
            print(f"Would unstar: https://github.com/{repository}")
        return 0

    _require_interactive_terminal("Unstarring")
    client = GitHubClient()
    print(f"GitHub account: @{client.whoami()}")
    selected: list[str] = []
    for repository in repositories:
        answer = _read_answer(f"Unstar https://github.com/{repository}? [y/N/q] ")
        if answer in {"q", "quit", "cancel"}:
            print("Cancelled.")
            return 0
        if answer in {"y", "yes"}:
            selected.append(repository)

    if not selected:
        print("No repositories selected.")
        return 0
    answer = _read_answer(f"Proceed with {len(selected)} unstar(s)? [y/N] ")
    if answer not in {"y", "yes"}:
        print("Cancelled.")
        return 0

    for index, repository in enumerate(selected):
        if client.is_starred(repository):
            client.unstar(repository)
            print(f"Unstarred: https://github.com/{repository}")
        else:
            print(f"Not starred: https://github.com/{repository}")
        if index + 1 < len(selected):
            time.sleep(0.25)
    return 0


def _print_undo(repositories: list[str]) -> None:
    if not repositories:
        return
    values = " ".join(repositories)
    print(f"Undo this batch: agent-thanks unstar {values}")


def _hook(args: argparse.Namespace) -> int:
    """Run a hook entry point. Hooks never fail the agent and never touch GitHub."""
    output: dict[str, object] | None = None
    try:
        payload = _hook_payload(args.payload)
        if args.hook_command == "record":
            _hook_record(payload, agent=args.agent)
        else:
            output = _hook_stop(payload, agent=args.agent, offline=args.offline)
    except Exception as error:  # noqa: BLE001 - a hook must never interrupt the agent
        print(f"agent-thanks hook: {error}", file=sys.stderr)
    if output is None and args.agent == "gemini":
        # Gemini CLI parses the standard output of a successful hook as JSON, so a
        # hook with nothing to say answers with an empty object.
        output = {}
    if output is not None:
        print(json.dumps(output))
    return 0


def _hook_payload(raw: str | None) -> dict[str, object]:
    if raw is None:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def _hook_root(payload: dict[str, object]) -> Path:
    cwd = payload.get("cwd")
    root = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    return root.expanduser().resolve()


def _state_directory(root: Path) -> Path:
    state = root / STATE_DIRECTORY
    state.mkdir(exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return state


def _session_id(payload: dict[str, object]) -> str | None:
    for key in ("session_id", "sessionId", "thread-id", "thread_id", "conversation_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_name(session_id: str) -> str:
    """Return a file name for a session id; ids that needed sanitizing get a hash so they never collide."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "session"
    if safe != session_id:
        safe = f"{safe}-{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:12]}"
    return safe


def _session_log(state: Path, session_id: str | None) -> Path:
    directory = state / "sessions"
    directory.mkdir(exist_ok=True)
    return directory / f"{_safe_name(session_id or 'default')}.jsonl"


def _report_path(state: Path, session_id: str | None) -> Path:
    if session_id is None:
        return state / "report.json"
    directory = state / "reports"
    directory.mkdir(exist_ok=True)
    return directory / f"{_safe_name(session_id)}.json"


def _infer_agent(payload: dict[str, object]) -> str | None:
    """Infer only what is safe to infer: a Codex notify payload by its distinctive shape.

    Hook payloads from different agents share ``hook_event_name`` and
    ``transcript_path``, so no agent-specific contract is ever inferred from
    them; that needs an explicit ``--from``.
    """
    kind = payload.get("type")
    if (isinstance(kind, str) and kind.startswith("agent-turn")) or "thread-id" in payload:
        return "codex"
    return None


def _hook_outcome(payload: dict[str, object], agent: str | None) -> tuple[str, str]:
    """Return (status, basis) for a post-tool hook payload.

    A failure always wins: an explicit failure in ``tool_response`` or a Claude
    Code ``PostToolUseFailure`` event records ``error``. A success is recorded
    only through a promoting contract from ``HOOK_PROMOTION_MATRIX``: Codex with
    a ``PostToolUse`` event for its canonical ``Bash`` tool and an explicit exit
    status of 0 in the response, or Claude Code with a ``PostToolUse`` event for
    ``Bash``, whose event fires only after a successful run. A text or JSON
    string response is interpreted as a Codex envelope only with ``--from
    codex``. Every other combination records ``unknown``.
    """
    event = payload.get("hook_event_name")
    tool = payload.get("tool_name") or payload.get("tool")
    status = result_status(payload.get("tool_response"), codex_envelope=agent == "codex")
    if status == RESULT_ERROR:
        return RESULT_ERROR, "tool_response"
    if event == "PostToolUseFailure":
        return RESULT_ERROR, "post_tool_failure_event"
    if status == RESULT_OK and (agent, event, tool, "exit_status") in HOOK_PROMOTION_MATRIX:
        return RESULT_OK, "exit_status"
    if (agent, event, tool, "successful_post_tool_event") in HOOK_PROMOTION_MATRIX:
        return RESULT_OK, "successful_post_tool_event"
    return RESULT_UNKNOWN, "no_result"


def _hook_record(payload: dict[str, object], *, agent: str | None) -> None:
    event = payload.get("hook_event_name")
    if event is not None and event not in POST_TOOL_EVENTS:
        return None  # a pre-tool or unrelated event says nothing about a completed command
    tool_name = payload.get("tool_name") or payload.get("tool")
    tool_input = payload.get("tool_input") or payload.get("input")
    if not is_shell_tool(tool_name) or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    status, basis = _hook_outcome(payload, agent)
    entry = {
        "schema": HOOK_LOG_SCHEMA,
        "agent": agent,
        "event": event,
        "tool": tool_name,
        "session_id": _session_id(payload),
        "tool_call_id": payload.get("tool_use_id") or payload.get("tool_call_id"),
        "command": command,
        "status": status,
        "basis": basis,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    state = _state_directory(_hook_root(payload))
    log = _session_log(state, _session_id(payload))
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return None


def _hook_stop(
    payload: dict[str, object], *, agent: str | None, offline: bool
) -> dict[str, object] | None:
    root = _hook_root(payload)
    state = _state_directory(root)
    session_id = _session_id(payload)
    _prune_state(state)

    # The structured hook log is the authority for actions: the scanner reads its
    # statuses as overrides for the transcript's own results of the same tool
    # calls and demotes whatever the two sources disagree about. The transcript
    # adds prose provenance; a command the hook log never saw stays unconfirmed.
    sources: list[Path] = []
    log = _session_log(state, session_id)
    if log.is_file():
        sources.append(log)
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and Path(transcript).is_file():
        sources.append(Path(transcript))
    else:
        agent = agent or _infer_agent(payload)
        if agent is not None:
            located = locate_transcript(agent, root, Path.home(), session_id=session_id)
            if located is not None:
                sources.append(located)
    if not sources:
        return None

    resolver = PackageRepositoryResolver(offline=offline)
    report = ProjectScanner(root, base="HEAD", resolver=resolver).scan(sources)
    report_path = _report_path(state, session_id)
    report.write(report_path)
    latest = state / "report.json"
    if report_path != latest:
        report.write(latest)

    announced = _load_announced(state / "announced.json")
    key = session_id or "default"
    seen = {item.casefold() for item in announced.get(key, [])}
    fresh = [
        candidate.repository
        for candidate in report.candidates
        if candidate.recommended and candidate.repository.casefold() not in seen
    ]
    if not fresh:
        return None
    announced[key] = sorted(seen | {item.casefold() for item in fresh})
    (state / "announced.json").write_text(
        json.dumps(announced, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"systemMessage": _announcement(fresh, report_path, root)}


def _load_announced(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, list):
        return {"default": [str(item) for item in loaded]}
    if isinstance(loaded, dict):
        return {
            str(key): [str(item) for item in value]
            for key, value in loaded.items()
            if isinstance(value, list)
        }
    return {}


def _prune_state(state: Path) -> None:
    cutoff = time.time() - SESSION_LOG_MAX_AGE_SECONDS
    for directory, pattern in ((state / "sessions", "*.jsonl"), (state / "reports", "*.json")):
        if not directory.is_dir():
            continue
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


def _announcement(repositories: list[str], report_path: Path, root: Path) -> str:
    try:
        shown = report_path.relative_to(root).as_posix()
    except ValueError:
        shown = str(report_path)
    names = ", ".join(repositories)
    return (
        f"agent-thanks: this task shows verified open-source use of {names}. "
        f"Review the evidence and approve Stars in a terminal: agent-thanks star {shown}"
    )


def _doctor(args: argparse.Namespace) -> int:
    print(f"agent-thanks {__version__} doctor")
    issues = 0

    python_version = ".".join(str(value) for value in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        print(f"[ok] Python {python_version}")
    else:  # pragma: no cover - the package cannot import on unsupported Python
        print(f"[!!] Python {python_version}; Python 3.10 or newer is required")
        issues += 1

    if shutil.which("git"):
        print("[ok] Git is available")
    else:
        print("[!!] Git is not available on PATH")
        issues += 1

    root = args.repo.expanduser().resolve()
    if not root.is_dir():
        print(f"[!!] Project directory does not exist: {root}")
        issues += 1
    else:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        ) if shutil.which("git") else None
        if result is not None and result.returncode == 0:
            print(f"[ok] Git project: {root}")
        else:
            print(f"[--] Non-Git project: {root} (current manifests will be scanned)")

    print("[ok] Star policy: interactive approval required for every repository")

    try:
        login = GitHubClient().whoami()
        print(f"[ok] GitHub account: @{login}")
    except GitHubError as error:
        print(f"[!!] GitHub authentication: {error}")
        issues += 1

    if issues:
        print(f"Doctor found {issues} issue(s).")
        return 1
    print("Ready to review and star repositories.")
    return 0


def _interactive_selection(candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []

    selected: list[Candidate] = []
    for candidate in candidates:
        print()
        _print_candidate(candidate)
        answer = _read_answer("Star this repository? [y/N/q] ")
        if answer in {"q", "quit", "cancel"}:
            print("Cancelled.")
            return []
        if answer in {"y", "yes"}:
            selected.append(candidate)

    if not selected:
        return []
    answer = _read_answer(f"Proceed with {len(selected)} star(s)? [y/N] ")
    return selected if answer in {"y", "yes"} else []


def _read_answer(prompt: str) -> str:
    try:
        return input(prompt).strip().casefold()
    except EOFError as error:
        raise InteractionError("Interactive confirmation ended before a decision.") from error


def _require_interactive_terminal(action: str) -> None:
    if not sys.stdin.isatty():
        raise InteractionError(
            f"{action} requires an interactive terminal; piped or unattended "
            "confirmation is not accepted. Use --dry-run for automation."
        )


def _print_summary(report: Report, report_path: Path) -> None:
    recommended = sum(item.recommended for item in report.candidates)
    print(
        f"Found {len(report.candidates)} repository candidate(s); "
        f"{recommended} verified for meaningful use, "
        f"{len(report.unresolved_dependencies)} unresolved dependency mapping(s)."
    )
    print(f"Review: agent-thanks review {report_path}")
    print(f"Export: agent-thanks export {report_path} --output OPEN_SOURCE_USE.md")
    print(f"Approve Stars: agent-thanks star {report_path}")


def _print_report(report: Report) -> None:
    print(f"Scan root: {report.root}")
    print(f"Base: {report.base or 'none (all supported manifests scanned)'}")
    print(f"Generated: {report.generated_at}")
    if not report.candidates:
        print("\nNo GitHub repository candidates found.")
    for candidate in report.candidates:
        print()
        _print_candidate(candidate)

    if report.unresolved_dependencies:
        print("\nUnresolved package-to-repository mappings:")
        for item in report.unresolved_dependencies:
            print(f"  - {item.ecosystem}:{item.package} ({item.source})")


def _print_candidate(candidate: Candidate) -> None:
    marker = "verified" if candidate.recommended else "review"
    print(f"[{marker} | {candidate.confidence}] https://github.com/{candidate.repository}")
    for evidence in candidate.evidence:
        print(f"  - {evidence.detail} ({evidence.source})")


if __name__ == "__main__":
    raise SystemExit(main())
