from __future__ import annotations

import argparse
from pathlib import Path
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


DEFAULT_REPORT = ".agent-thanks-report.json"


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
    return ProjectScanner(args.repo, base=args.base, resolver=resolver).scan(args.session)


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
                        source="demo-session.log:2",
                        detail="Session shows a substantive repository-use command",
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
                        source="demo-session.log:1",
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
                        source="demo-session.log:3",
                        detail="Session shows a substantive repository-use command",
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
