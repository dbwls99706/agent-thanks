from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.cli import main
from agent_thanks.config import ConfigStore
from agent_thanks.github import GitHubError
from agent_thanks.models import Candidate, Evidence, Report


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.report_path = Path(self.temporary_directory.name) / "report.json"
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        Report(
            root=self.temporary_directory.name,
            base="HEAD",
            candidates=[
                Candidate(
                    "owner/recommended",
                    [Evidence("direct_dependency", "package.json", "Added", "high", True)],
                ),
                Candidate(
                    "owner/review-only",
                    [Evidence("session_reference", "session.log:1", "Viewed", "low", False)],
                ),
            ],
        ).write(self.report_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_yes_dry_run_stars_recommended_only(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["star", str(self.report_path), "--yes", "--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("owner/recommended", output.getvalue())
        self.assertNotIn("owner/review-only", output.getvalue())

    def test_all_requires_yes(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(["star", str(self.report_path), "--all", "--dry-run"])
        self.assertEqual(status, 2)
        self.assertIn("--all requires --yes", error.getvalue())

    def test_all_yes_includes_low_confidence_references(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(
                ["star", str(self.report_path), "--all", "--yes", "--dry-run"]
            )

        self.assertEqual(status, 0)
        self.assertIn("owner/recommended", output.getvalue())
        self.assertIn("owner/review-only", output.getvalue())

    def test_auto_mode_stars_all_verified_repositories_without_prompt(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}):
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["config", "--mode", "auto"]), 0)
            with redirect_stdout(output):
                status = main(["star", str(self.report_path), "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("Consent mode: auto", output.getvalue())
        self.assertIn("Would star: https://github.com/owner/recommended", output.getvalue())
        self.assertNotIn("Would star: https://github.com/owner/review-only", output.getvalue())
        self.assertIn("Skipped 1 low-confidence", output.getvalue())

    def test_ask_mode_prompts_for_every_repository(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}):
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["config", "--mode", "ask"]), 0)
            with patch("builtins.input", side_effect=["y", "n", "y"]), redirect_stdout(output):
                status = main(["star", str(self.report_path), "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("Consent mode: ask", output.getvalue())
        self.assertIn("Would star: https://github.com/owner/recommended", output.getvalue())
        self.assertNotIn("Would star: https://github.com/owner/review-only", output.getvalue())

    def test_ask_mode_defaults_every_repository_to_no(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}):
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["config", "--mode", "ask"]), 0)
            with patch("builtins.input", side_effect=["", ""]), redirect_stdout(output):
                status = main(["star", str(self.report_path), "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("No repositories selected.", output.getvalue())
        self.assertNotIn("Would star:", output.getvalue())

    def test_interactive_config_can_select_auto(self) -> None:
        with patch.dict(os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}), patch(
            "builtins.input", return_value="2"
        ), redirect_stdout(StringIO()):
            status = main(["config"])

        self.assertEqual(status, 0)
        self.assertEqual(ConfigStore(self.config_path).load().consent_mode, "auto")

    def test_run_scans_and_applies_one_time_auto_override(self) -> None:
        root = Path(self.temporary_directory.name) / "project"
        root.mkdir()
        (root / "requirements.txt").write_text(
            "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
        )
        report_path = root / "report.json"
        output = StringIO()

        with patch.dict(
            os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}
        ), redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--repo",
                    str(root),
                    "--offline",
                    "--mode",
                    "auto",
                    "--dry-run",
                    "--output",
                    str(report_path),
                ]
            )

        self.assertEqual(status, 0)
        self.assertTrue(report_path.is_file())
        self.assertIn("Would star: https://github.com/acme/demo", output.getvalue())
        self.assertFalse(
            self.config_path.exists(), "one-time --mode must not change saved settings"
        )

    @patch("agent_thanks.cli.GitHubClient")
    def test_permission_failure_exits_nonzero_without_false_success(
        self, client_type
    ) -> None:
        client_type.return_value.whoami.return_value = "octocat"
        client_type.return_value.is_starred.return_value = False
        client_type.return_value.star.side_effect = GitHubError(
            "GitHub API returned HTTP 403: Resource not accessible"
        )
        output = StringIO()
        error = StringIO()

        with redirect_stdout(output), redirect_stderr(error):
            status = main(["star", str(self.report_path), "--yes"])

        self.assertEqual(status, 2)
        self.assertIn("HTTP 403", error.getvalue())
        self.assertNotIn("Starred:", output.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_existing_star_is_reported_without_mutation(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = True
        output = StringIO()

        with redirect_stdout(output):
            status = main(
                [
                    "star",
                    str(self.report_path),
                    "--repo",
                    "owner/recommended",
                    "--yes",
                ]
            )

        self.assertEqual(status, 0)
        self.assertIn("GitHub account: @octocat", output.getvalue())
        self.assertIn("Already starred", output.getvalue())
        client.star.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_new_star_prints_exact_undo_command(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        output = StringIO()

        with redirect_stdout(output):
            status = main(
                [
                    "star",
                    str(self.report_path),
                    "--repo",
                    "owner/recommended",
                    "--yes",
                ]
            )

        self.assertEqual(status, 0)
        client.star.assert_called_once_with("owner/recommended")
        self.assertIn(
            "Undo this batch: agent-thanks unstar owner/recommended",
            output.getvalue(),
        )

    @patch("agent_thanks.cli.time.sleep", return_value=None)
    @patch("agent_thanks.cli.GitHubClient")
    def test_partial_failure_prints_undo_for_completed_stars(
        self, client_type, _sleep
    ) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        client.star.side_effect = [
            None,
            GitHubError("GitHub API returned HTTP 403: Forbidden"),
        ]
        output = StringIO()
        error = StringIO()

        with redirect_stdout(output), redirect_stderr(error):
            status = main(["star", str(self.report_path), "--all", "--yes"])

        self.assertEqual(status, 2)
        self.assertIn("partial update", output.getvalue())
        self.assertIn(
            "agent-thanks unstar owner/recommended",
            output.getvalue(),
        )
        self.assertNotIn("owner/review-only", output.getvalue().split("Undo this batch:")[-1])
        self.assertIn("HTTP 403", error.getvalue())

    @patch("agent_thanks.cli.time.sleep", return_value=None)
    @patch("agent_thanks.cli.GitHubClient")
    def test_network_failure_prints_undo_for_completed_stars(
        self, client_type, _sleep
    ) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        client.star.side_effect = [None, OSError("connection reset")]
        output = StringIO()
        error = StringIO()

        with redirect_stdout(output), redirect_stderr(error):
            status = main(["star", str(self.report_path), "--all", "--yes"])

        self.assertEqual(status, 2)
        self.assertIn("partial update", output.getvalue())
        self.assertIn(
            "agent-thanks unstar owner/recommended",
            output.getvalue(),
        )
        self.assertNotIn("owner/review-only", output.getvalue().split("Undo this batch:")[-1])
        self.assertIn("connection reset", error.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_unstar_skips_repository_without_an_existing_star(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        output = StringIO()

        with redirect_stdout(output):
            status = main(["unstar", "owner/recommended", "--yes"])

        self.assertEqual(status, 0)
        self.assertIn("Not starred", output.getvalue())
        client.unstar.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_dry_run_never_constructs_an_authenticated_client(self, client_type) -> None:
        with redirect_stdout(StringIO()):
            status = main(["star", str(self.report_path), "--yes", "--dry-run"])

        self.assertEqual(status, 0)
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_unstar_dry_run_never_constructs_an_authenticated_client(
        self, client_type
    ) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["unstar", "owner/recommended", "--yes", "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("Would unstar", output.getvalue())
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    @patch("agent_thanks.cli.subprocess.run")
    @patch("agent_thanks.cli.shutil.which", return_value="/usr/bin/tool")
    def test_doctor_reports_ready_account(
        self, _which, run, client_type
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="true\n", stderr=""
        )
        client_type.return_value.whoami.return_value = "octocat"
        output = StringIO()

        with patch.dict(
            os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}
        ), redirect_stdout(output):
            status = main(["doctor", "--repo", self.temporary_directory.name])

        self.assertEqual(status, 0)
        self.assertIn("GitHub account: @octocat", output.getvalue())
        self.assertIn("Ready to review and star repositories.", output.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    @patch("agent_thanks.cli.subprocess.run")
    @patch("agent_thanks.cli.shutil.which", return_value="/usr/bin/tool")
    def test_doctor_returns_one_when_authentication_is_missing(
        self, _which, run, client_type
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="true\n", stderr=""
        )
        client_type.return_value.whoami.side_effect = GitHubError(
            "Authentication required"
        )
        output = StringIO()

        with patch.dict(
            os.environ, {"AGENT_THANKS_CONFIG": str(self.config_path)}
        ), redirect_stdout(output):
            status = main(["doctor", "--repo", self.temporary_directory.name])

        self.assertEqual(status, 1)
        self.assertIn("GitHub authentication", output.getvalue())
        self.assertIn("Doctor found 1 issue(s).", output.getvalue())


if __name__ == "__main__":
    unittest.main()
