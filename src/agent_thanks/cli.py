from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Sequence

from . import __version__
from .config import CONSENT_MODES, ConfigError, ConfigStore, Settings
from .github import GitHubClient, GitHubError, validate_repository
from .models import Candidate, Report
from .resolver import PackageRepositoryResolver
from .scanner import ProjectScanner, ScanError


DEFAULT_REPORT = ".agent-thanks-report.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-thanks",
        description=(
            "Find open-source repositories meaningfully used during an AI coding "
            "session and thank them using your chosen consent policy."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run",
        help="Scan and apply the saved ask/auto consent policy in one command",
    )
    _add_scan_arguments(run)
    run.add_argument(
        "--mode",
        choices=CONSENT_MODES,
        help="Override the saved consent mode for this run only",
    )
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

    star = commands.add_parser("star", help="Apply a consent policy to a saved report")
    star.add_argument("report", nargs="?", type=Path, default=Path(DEFAULT_REPORT))
    star.add_argument(
        "--repo",
        dest="repositories",
        action="append",
        default=[],
        help="Star only this owner/repo; repeatable",
    )
    star.add_argument(
        "--mode",
        choices=CONSENT_MODES,
        help="Override the saved consent mode for this command only",
    )
    star.add_argument(
        "--yes",
        action="store_true",
        help="Skip prompts and star recommended candidates only",
    )
    star.add_argument(
        "--all",
        action="store_true",
        help="Include low-confidence references; requires --yes",
    )
    star.add_argument("--dry-run", action="store_true", help="Show actions without starring")

    config = commands.add_parser(
        "config",
        help="Choose whether to ask every time or star verified repositories automatically",
    )
    config_group = config.add_mutually_exclusive_group()
    config_group.add_argument(
        "--mode",
        choices=CONSENT_MODES,
        help="Save ask or auto as the default consent mode",
    )
    config_group.add_argument(
        "--show",
        action="store_true",
        help="Show the current consent mode and config path",
    )

    unstar = commands.add_parser("unstar", help="Revoke stars previously granted")
    unstar.add_argument("repositories", nargs="+", metavar="OWNER/REPO")
    unstar.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    unstar.add_argument("--dry-run", action="store_true", help="Show actions without unstarring")

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
        if args.command == "scan":
            return _scan(args)
        if args.command == "review":
            return _review(args.report)
        if args.command == "star":
            return _star(args)
        if args.command == "config":
            return _config(args)
        if args.command == "unstar":
            return _unstar(args)
    except (OSError, ValueError, ConfigError, ScanError, GitHubError) as error:
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
    mode = _resolve_consent_mode(args.mode)
    report = _build_report(args)
    _write_report(report, args.output)
    selected = _select_by_mode(report.candidates, mode)
    return _execute_stars(selected, dry_run=args.dry_run)


def _review(path: Path) -> int:
    report = Report.read(path)
    _print_report(report)
    return 0


def _star(args: argparse.Namespace) -> int:
    if args.all and not args.yes:
        raise ValueError("--all requires --yes to make bulk intent explicit")
    report = Report.read(args.report)
    requested = {validate_repository(item).casefold() for item in args.repositories}

    if requested:
        selected = [item for item in report.candidates if item.repository.casefold() in requested]
        missing = requested - {item.repository.casefold() for item in selected}
        if missing:
            raise ValueError(
                f"Repositories are not present in the report: {', '.join(sorted(missing))}"
            )
    elif args.yes:
        selected = [item for item in report.candidates if args.all or item.recommended]
    else:
        mode = _resolve_consent_mode(args.mode)
        selected = _select_by_mode(report.candidates, mode)

    return _execute_stars(selected, dry_run=args.dry_run)


def _config(args: argparse.Namespace) -> int:
    store = ConfigStore()
    if args.show:
        if store.exists:
            settings = store.load()
            print(f"Consent mode: {settings.consent_mode}")
        else:
            print("Consent mode: ask (safe default; not saved yet)")
        print(f"Config file: {store.path}")
        return 0

    if args.mode:
        settings = Settings(consent_mode=args.mode)
        store.save(settings)
    else:
        settings = _interactive_configuration(store)
    _print_saved_mode(settings, store.path)
    return 0


def _resolve_consent_mode(override: str | None) -> str:
    if override is not None:
        return override
    store = ConfigStore()
    if store.exists:
        return store.load().consent_mode
    print("No consent policy has been configured yet.\n")
    return _interactive_configuration(store).consent_mode


def _interactive_configuration(store: ConfigStore) -> Settings:
    print("How should agent-thanks handle repositories with verified, meaningful use?")
    print("  1. Ask every time — show each repository and wait for yes/no (recommended)")
    print("  2. Auto star all — star every verified repository without another prompt")
    print("\nViewed-only and low-confidence repositories are never auto-starred.")

    while True:
        try:
            answer = input("Choose [1/2] (default: 1): ").strip().casefold()
        except EOFError as error:
            raise ConfigError(
                "No interactive input is available. Run "
                "'agent-thanks config --mode ask' or '--mode auto' first."
            ) from error
        if answer in {"", "1", "ask", "a"}:
            settings = Settings(consent_mode="ask")
            break
        if answer in {"2", "auto"}:
            settings = Settings(consent_mode="auto")
            break
        print("Please enter 1 for ask or 2 for auto.")

    store.save(settings)
    return settings


def _print_saved_mode(settings: Settings, path: Path) -> None:
    if settings.consent_mode == "ask":
        print("Saved consent mode: ask — every candidate requires an explicit yes/no.")
    else:
        print(
            "Saved consent mode: auto — all verified, meaningful-use repositories "
            "will be starred automatically."
        )
    print(f"Config file: {path}")


def _select_by_mode(candidates: list[Candidate], mode: str) -> list[Candidate]:
    if mode == "ask":
        print("Consent mode: ask — reviewing repositories one by one.")
        return _interactive_selection(candidates)
    if mode == "auto":
        selected = [candidate for candidate in candidates if candidate.recommended]
        skipped = [candidate for candidate in candidates if not candidate.recommended]
        print(
            "Consent mode: auto — selecting all verified, meaningful-use "
            f"repositories ({len(selected)})."
        )
        if skipped:
            print(
                f"Skipped {len(skipped)} low-confidence reference(s); "
                "use review before starring them explicitly."
            )
        return selected
    raise ConfigError(f"Unsupported consent mode: {mode}")


def _execute_stars(selected: list[Candidate], *, dry_run: bool) -> int:
    if not selected:
        print("No repositories selected.")
        return 0

    action = "Would star" if dry_run else "Starred"
    client = GitHubClient()
    for index, candidate in enumerate(selected):
        if not dry_run:
            client.star(candidate.repository)
            if index + 1 < len(selected):
                time.sleep(0.25)
        print(f"{action}: https://github.com/{candidate.repository}")
    return 0


def _unstar(args: argparse.Namespace) -> int:
    repositories = [validate_repository(item) for item in args.repositories]
    if not args.yes:
        answer = _read_answer(f"Unstar {len(repositories)} repositories? [y/N] ")
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    action = "Would unstar" if args.dry_run else "Unstarred"
    client = GitHubClient()
    for index, repository in enumerate(repositories):
        if not args.dry_run:
            client.unstar(repository)
            if index + 1 < len(repositories):
                time.sleep(0.25)
        print(f"{action}: https://github.com/{repository}")
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
        raise ConfigError(
            "Interactive confirmation requires a terminal. Use '--mode auto', "
            "'--yes', or configure auto mode explicitly."
        ) from error


def _print_summary(report: Report, report_path: Path) -> None:
    recommended = sum(item.recommended for item in report.candidates)
    print(
        f"Found {len(report.candidates)} repository candidate(s); "
        f"{recommended} verified for meaningful use, "
        f"{len(report.unresolved_dependencies)} unresolved dependency mapping(s)."
    )
    print(f"Review: agent-thanks review {report_path}")
    print(f"Apply saved policy: agent-thanks star {report_path}")


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
