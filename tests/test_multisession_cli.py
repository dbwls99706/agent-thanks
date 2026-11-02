from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.cli import _build_report, build_parser
from agent_thanks.entry import main
from agent_thanks.models import Report
from agent_thanks.transcripts import encode_project_path


def codex_transcript(path: Path, root: Path, repository: str, session: str, modified: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(root)}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": session,
            "arguments": json.dumps({"cmd": f"git clone https://github.com/{repository}.git"}),
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": session,
            "output": json.dumps({"output": "done", "metadata": {"exit_code": 0}}),
        }},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (modified, modified))


def claude_transcript(path: Path, root: Path, repository: str, modified: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"cwd": str(root), "sessionId": path.stem},
        {"type": "assistant", "message": {"role": "assistant", "content": [{
            "type": "tool_use", "id": "clone", "name": "Bash",
            "input": {"command": f"git clone https://github.com/{repository}.git"},
        }]}},
        {"type": "user", "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "clone", "is_error": False, "content": "done",
        }]}},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (modified, modified))


class MultiSessionCliTests(unittest.TestCase):
    @contextmanager
    def project(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / "project"
            root.mkdir()
            codex = home / ".codex" / "sessions"
            claude = home / ".claude" / "projects" / encode_project_path(root)
            codex_transcript(codex / "older.jsonl", root, "used/older", "older", 2)
            codex_transcript(codex / "newer.jsonl", root, "used/newer", "newer", 3)
            codex_transcript(codex / "foreign.jsonl", home / "project-other", "foreign/unused", "foreign", 9)
            claude_transcript(claude / "claude.jsonl", root, "used/claude", 1)
            with patch.dict(os.environ, {
                "CODEX_HOME": str(home / ".codex"),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            }), patch("pathlib.Path.home", return_value=home):
                yield root, codex

    def run_preview(self, root: Path, *arguments: str) -> tuple[int, Report, str]:
        output, error = StringIO(), StringIO()
        report = root / "report.json"
        with redirect_stdout(output), redirect_stderr(error), patch("builtins.input") as ask:
            status = main([
                "thanks", "--repo", str(root), "--offline", "--dry-run",
                "--output", str(report), *arguments,
            ])
        ask.assert_not_called()
        return status, Report.read(report), output.getvalue()

    def test_all_sessions_does_not_inject_newest_agent_or_cross_project_boundaries(self) -> None:
        with self.project() as (root, _codex), patch("agent_thanks.entry._detect_agent") as detect:
            status, report, output = self.run_preview(root, "--all-sessions")
        self.assertEqual(status, 0)
        detect.assert_not_called()
        self.assertEqual(
            {candidate.repository for candidate in report.candidates},
            {"used/older", "used/newer", "used/claude"},
        )
        self.assertTrue(all(candidate.recommended for candidate in report.candidates))
        self.assertNotIn("foreign/unused", output)

    def test_from_limits_agent_collection_while_preserving_all_its_sessions(self) -> None:
        with self.project() as (root, _codex):
            status, report, _output = self.run_preview(root, "--all-sessions", "--from", "codex")
        self.assertEqual(status, 0)
        self.assertEqual(
            {candidate.repository for candidate in report.candidates}, {"used/older", "used/newer"}
        )

    def test_claude_alias_layout_survives_the_cli_resolving_the_project(self) -> None:
        with self.project() as (root, codex):
            home = codex.parent.parent
            intermediate = home / "intermediate"
            intermediate.mkdir()
            alias = intermediate / ".." / "project"
            folder = home / ".claude" / "projects" / encode_project_path(alias)
            claude_transcript(folder / "alias.jsonl", alias, "used/alias", 5)
            foreign = home / ".claude" / "projects" / "foreign" / "foreign.jsonl"
            claude_transcript(foreign, home / "project-other", "foreign/unused", 9)
            status, report, output = self.run_preview(root, "--all-sessions", "--from", "claude-code")
        self.assertEqual(status, 0)
        self.assertEqual(
            {candidate.repository for candidate in report.candidates}, {"used/claude", "used/alias"}
        )
        self.assertTrue(all(candidate.recommended for candidate in report.candidates))
        self.assertNotIn("foreign/unused", output)

    def test_existing_default_still_uses_only_the_newest_matching_session(self) -> None:
        with self.project() as (root, _codex):
            status, report, _output = self.run_preview(root)
        self.assertEqual(status, 0)
        self.assertEqual([candidate.repository for candidate in report.candidates], ["used/newer"])

    def test_explicit_discovered_and_native_paths_are_each_scanned_once(self) -> None:
        with self.project() as (root, codex):
            native = root / ".agent-thanks" / "sessions" / "executions.jsonl"
            native.parent.mkdir(parents=True)
            native.write_text("", encoding="utf-8")
            explicit = codex / "older.jsonl"
            alias = codex / ".." / "sessions" / "older.jsonl"
            args = build_parser().parse_args([
                "scan", "--repo", str(root), "--offline", "--all-sessions", "--from", "codex",
                "--session", str(explicit), "--session", str(alias), "--session", str(native),
            ])
            with patch("agent_thanks.cli.ProjectScanner") as scanner:
                _build_report(args)
            sources = list(scanner.return_value.scan.call_args.args[0])
            self.assertEqual(len(sources), 3)
            self.assertEqual(
                set(sources), {explicit.resolve(), (codex / "newer.jsonl").resolve(), native.resolve()}
            )

    def test_repeated_stdin_source_is_read_once_alongside_matching_sessions(self) -> None:
        class Input(StringIO):
            reads = 0

            def read(self, *args, **kwargs):
                self.reads += 1
                return super().read(*args, **kwargs)

        stream = Input("git clone https://github.com/stdin/unconfirmed.git\n")
        with self.project() as (root, _codex), patch("sys.stdin", stream):
            status, report, output = self.run_preview(
                root, "--all-sessions", "--from", "codex", "--session", "-", "--session", "-"
            )
        self.assertEqual(status, 0)
        self.assertEqual(stream.reads, 1)
        candidates = {candidate.repository: candidate for candidate in report.candidates}
        self.assertTrue(candidates["used/older"].recommended)
        self.assertTrue(candidates["used/newer"].recommended)
        self.assertFalse(candidates["stdin/unconfirmed"].recommended)
        self.assertNotIn("Would star: https://github.com/stdin/unconfirmed", output)


if __name__ == "__main__":
    unittest.main()
