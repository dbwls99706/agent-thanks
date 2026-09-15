from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence

from .cli import main as cli_main
from .transcripts import locate_transcript


AGENTS = ("claude-code", "codex", "gemini")


def _repo_from_args(args: list[str]) -> Path:
    for index, argument in enumerate(args):
        if argument == "--repo" and index + 1 < len(args):
            return Path(args[index + 1])
        if argument.startswith("--repo="):
            return Path(argument.split("=", 1)[1])
    return Path.cwd()


def _has_explicit_session_source(args: list[str]) -> bool:
    return any(
        argument in {"--from", "--session"}
        or argument.startswith("--from=")
        or argument.startswith("--session=")
        for argument in args
    )


def _detect_agent(root: Path) -> str | None:
    project = root.expanduser().resolve()
    matches: list[tuple[int, int, str]] = []
    for priority, agent in enumerate(AGENTS):
        transcript = locate_transcript(agent, project, Path.home())
        if transcript is None:
            continue
        try:
            modified = transcript.stat().st_mtime_ns
        except OSError:
            modified = 0
        matches.append((modified, -priority, agent))
    if not matches:
        return None
    return max(matches)[2]


def _quick_help() -> str:
    return """agent-thanks - find OSS your coding agent used and thank it with a Star

Everyday use:
  agent-thanks thanks              Detect, review, and approve Stars
  agent-thanks thanks --dry-run    Preview the exact repositories first

The thanks command auto-detects the newest Claude Code, Codex, or Gemini
transcript that belongs to the current project. Pass --from or --session to
choose the source explicitly.

Other commands:
  demo      Safe built-in demonstration
  scan      Create an evidence report without touching GitHub
  review    Inspect a saved report
  export    Export a report as Markdown
  star      Review and Star verified repositories from a saved report
  unstar    Revoke Stars previously granted
  doctor    Check the project and GitHub authentication

Advanced and compatibility command:
  run       Same scan-and-review flow as thanks, without auto-detection

Run 'agent-thanks <command> --help' for command-specific options.
"""


def _thanks_help() -> str:
    return """usage: agent-thanks thanks [options]

Find open-source repositories used during the current coding task, show the
evidence, and ask before each eligible GitHub Star.

By default, agent-thanks auto-detects the newest Claude Code, Codex, or Gemini
transcript that belongs to the selected project.

options:
  --repo PATH             Project root (default: current directory)
  --base REVISION         State before the agent worked (default: HEAD)
  --from AGENT            Use claude-code, codex, or gemini explicitly
  --session PATH          Use a transcript or log explicitly; repeatable
  --output PATH           JSON report path (default: .agent-thanks-report.json)
  --offline               Skip package-registry lookups
  --trust-session         Trust commands from plain-text logs as successful
  --dry-run               Show which verified repositories would be offered
  -h, --help              Show this help

Live Stars always require an interactive terminal, one default-No decision per
repository, and a final confirmation.
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args in (["-h"], ["--help"]):
        print(_quick_help(), end="")
        return 0

    if args[0] in {"thanks", "thank"}:
        if any(argument in {"-h", "--help"} for argument in args[1:]):
            print(_thanks_help(), end="")
            return 0

        args[0] = "run"
        if not _has_explicit_session_source(args):
            agent = _detect_agent(_repo_from_args(args))
            if agent is not None:
                args.extend(["--from", agent])
                print(f"Detected coding agent: {agent}", file=sys.stderr)
            else:
                print(
                    "No matching coding-agent transcript found; scanning project changes only.",
                    file=sys.stderr,
                )

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
