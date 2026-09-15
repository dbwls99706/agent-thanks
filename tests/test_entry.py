from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_thanks.entry import main


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
    @patch("agent_thanks.entry._detect_agent", return_value=None)
    def test_thanks_falls_back_to_project_changes(self, detect, cli_main) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(["thanks", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_called_once()
        cli_main.assert_called_once_with(["run", "--dry-run"])
        self.assertIn("scanning project changes only", error.getvalue())

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
    def test_no_arguments_show_quick_help_without_starting_a_star_flow(self, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main([])

        self.assertEqual(status, 0)
        self.assertIn("Everyday use", output.getvalue())
        cli_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
