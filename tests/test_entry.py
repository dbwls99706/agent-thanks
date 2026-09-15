from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.entry import _detect_agent, main


class EntryPointTests(unittest.TestCase):
    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent", return_value="codex")
    def test_thanks_auto_detects_the_agent_for_the_project(self, detect, cli_main) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(["thanks", "--repo", "/work/project", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_called_once_with(Path("/work/project"))
        cli_main.assert_called_once_with(
            [
                "run",
                "--repo",
                "/work/project",
                "--dry-run",
                "--from",
                "codex",
            ]
        )
        self.assertIn("Detected coding agent: codex", error.getvalue())

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent")
    def test_explicit_agent_is_never_replaced(self, detect, cli_main) -> None:
        status = main(["thanks", "--from", "claude-code", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_not_called()
        cli_main.assert_called_once_with(
            ["run", "--from", "claude-code", "--dry-run"]
        )

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent")
    def test_explicit_session_is_never_replaced(self, detect, cli_main) -> None:
        status = main(["thanks", "--session", "session.jsonl", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_not_called()
        cli_main.assert_called_once_with(
            ["run", "--session", "session.jsonl", "--dry-run"]
        )

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent")
    def test_equals_style_session_is_never_replaced(self, detect, cli_main) -> None:
        status = main(["thanks", "--session=session.jsonl", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_not_called()
        cli_main.assert_called_once_with(
            ["run", "--session=session.jsonl", "--dry-run"]
        )

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent", return_value=None)
    def test_thanks_falls_back_to_project_changes(self, detect, cli_main) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(["thanks", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_called_once()
        cli_main.assert_called_once_with(["run", "--dry-run"])
        self.assertIn("scanning project changes only", error.getvalue())

    def test_detect_agent_prefers_the_newest_matching_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude = root / "claude.jsonl"
            codex = root / "codex.jsonl"
            claude.write_text("{}\n", encoding="utf-8")
            codex.write_text("{}\n", encoding="utf-8")
            os.utime(claude, ns=(1_000_000_000, 1_000_000_000))
            os.utime(codex, ns=(2_000_000_000, 2_000_000_000))

            found = {
                "claude-code": claude,
                "codex": codex,
                "gemini": None,
            }

            with patch(
                "agent_thanks.entry.locate_transcript",
                side_effect=lambda agent, project, home: found[agent],
            ):
                self.assertEqual(_detect_agent(root), "codex")

    @patch("agent_thanks.entry.cli_main", return_value=7)
    @patch("agent_thanks.entry._detect_agent")
    def test_run_remains_a_compatibility_command(self, detect, cli_main) -> None:
        status = main(["run", "--dry-run"])

        self.assertEqual(status, 7)
        detect.assert_not_called()
        cli_main.assert_called_once_with(["run", "--dry-run"])

    @patch("agent_thanks.entry.cli_main")
    def test_top_level_help_presents_thanks_as_the_primary_command(self, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["--help"])

        self.assertEqual(status, 0)
        self.assertIn("agent-thanks thanks", output.getvalue())
        self.assertIn("approve Stars", output.getvalue())
        cli_main.assert_not_called()

    @patch("agent_thanks.entry.cli_main")
    @patch("agent_thanks.entry._detect_agent")
    def test_thanks_help_has_no_detection_side_effects(self, detect, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["thanks", "--help"])

        self.assertEqual(status, 0)
        self.assertIn("usage: agent-thanks thanks", output.getvalue())
        self.assertIn("--from AGENT", output.getvalue())
        detect.assert_not_called()
        cli_main.assert_not_called()

    @patch("agent_thanks.entry.cli_main")
    def test_no_arguments_show_quick_help_without_starting_a_star_flow(self, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main([])

        self.assertEqual(status, 0)
        self.assertIn("Everyday use", output.getvalue())
        cli_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
