from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.entry import main
from agent_thanks.github import GitHubError
from agent_thanks.models import Candidate, Evidence, Report


class CaptureWorkflowTests(unittest.TestCase):
    def test_capture_is_included_without_attesting_to_a_plain_text_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, error = StringIO(), StringIO()
            with patch("agent_thanks.cli.GitHubClient") as github, redirect_stdout(output), redirect_stderr(error):
                with patch("agent_thanks.executions.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                    self.assertEqual(main(["exec", "--repo", str(root), "--", "git", "clone", "https://github.com/used/captured.git"]), 0)
                report = root / "report.json"
                with patch("agent_thanks.entry._detect_agent", return_value=None):
                    self.assertEqual(main(["thanks", "--repo", str(root), "--offline", "--dry-run", "--output", str(report)]), 0)
                github.assert_not_called()
            candidates = Report.read(report).candidates
            self.assertEqual([item.repository for item in candidates], ["used/captured"])
            self.assertTrue(candidates[0].recommended)
            self.assertEqual(candidates[0].evidence[0].kind, "execution_usage")
            self.assertEqual(output.getvalue().count("Would star: https://github.com/used/captured"), 1)
            log = root / ".agent-thanks" / "sessions" / "executions.jsonl"
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)

    def test_failed_capture_cannot_be_promoted_with_trust_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("agent_thanks.executions.subprocess.run", return_value=subprocess.CompletedProcess([], 128)), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["exec", "--repo", str(root), "--", "git", "clone", "https://github.com/unused/failed"]), 128)
            report = root / "report.json"
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch("agent_thanks.entry._detect_agent", return_value=None):
                self.assertEqual(main(["thanks", "--repo", str(root), "--trust-session", "--offline", "--dry-run", "--output", str(report)]), 0)
            self.assertFalse(Report.read(report).candidates[0].recommended)

    def test_exec_preserves_child_stdout_and_nonzero_status_in_a_real_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
            result = subprocess.run([
                sys.executable, "-m", "agent_thanks", "exec", "--repo", str(root), "--",
                sys.executable, "-c", "import sys; sys.stdout.write('child-output'); sys.exit(7)",
            ], cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(result.stdout, "child-output")
            self.assertIn("Execution recorded:", result.stderr)
            records = [json.loads(line) for line in (root / ".agent-thanks" / "sessions" / "executions.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[-1]["exit_code"], 7)

    def test_exec_without_a_command_does_not_create_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            root = Path(directory)
            self.assertEqual(main(["exec", "--repo", str(root), "--"]), 2)
            self.assertFalse((root / ".agent-thanks").exists())

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_unsafe_execution_storage_refuses_to_launch_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".agent-thanks"
            state.mkdir()
            external = root / "external"
            external.mkdir()
            (state / "sessions").symlink_to(external, target_is_directory=True)
            with patch("agent_thanks.executions.subprocess.run") as run, redirect_stderr(StringIO()):
                self.assertEqual(main(["exec", "--repo", str(root), "--", "git", "clone", "https://github.com/used/repo"]), 2)
            run.assert_not_called()
            self.assertEqual(list(external.iterdir()), [])


class ReceiptRecoveryWorkflowTests(unittest.TestCase):
    @patch("agent_thanks.cli.time.sleep", return_value=None)
    @patch("agent_thanks.cli.GitHubClient")
    def test_partial_star_batch_survives_restart_and_undo_only_touches_confirmed_entries(self, client_type, _sleep) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            Report(str(root), "HEAD", [
                Candidate(f"used/{name}", [Evidence("session_usage", "log:1", "Executed", "high", True)])
                for name in ("one", "two")
            ]).write(report)
            client = client_type.return_value
            client.whoami.return_value = "octocat"
            client.is_starred.return_value = False
            client.star.side_effect = [None, GitHubError("request timed out")]
            output = StringIO()
            with patch("agent_thanks.cli._require_interactive_terminal"), patch("builtins.input", side_effect=["y", "y", "y"]), redirect_stdout(output), redirect_stderr(StringIO()):
                self.assertEqual(main(["star", str(report)]), 2)
            self.assertIn("agent-thanks unstar used/one", output.getvalue())
            history = StringIO()
            with redirect_stdout(history):
                self.assertEqual(main(["history", "--repo", str(root), "--json"]), 0)
            receipt = json.loads(history.getvalue())[0]
            self.assertEqual([item["state"] for item in receipt["entries"]], ["starred", "pending"])
            client.is_starred.return_value = True
            with patch("agent_thanks.cli._require_interactive_terminal"), patch("builtins.input", side_effect=["y", "y"]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["undo", "--repo", str(root), "--last"]), 0)
            client.unstar.assert_called_once_with("used/one")
            after = StringIO()
            with redirect_stdout(after):
                self.assertEqual(main(["history", "--repo", str(root), "--json"]), 0)
            self.assertEqual([item["state"] for item in json.loads(after.getvalue())[0]["entries"]], ["undone", "pending"])


if __name__ == "__main__":
    unittest.main()
