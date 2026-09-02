from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.cli import main
from agent_thanks.github import GitHubError
from agent_thanks.models import Candidate, Evidence, Report


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.report_path = Path(self.temporary_directory.name) / "report.json"
        self.legacy_config_path = Path(self.temporary_directory.name) / "config.json"
        Report(
            root=self.temporary_directory.name,
            base="HEAD",
            candidates=[
                Candidate(
                    "owner/recommended-one",
                    [Evidence("direct_dependency", "package.json", "Added one", "high", True)],
                ),
                Candidate(
                    "owner/recommended-two",
                    [Evidence("session_usage", "session.log:2", "Used two", "high", True)],
                ),
                Candidate(
                    "owner/review-only",
                    [Evidence("session_reference", "session.log:1", "Viewed", "low", False)],
                ),
            ],
        ).write(self.report_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_dry_run_previews_verified_candidates_without_prompts(self) -> None:
        output = StringIO()
        with patch("builtins.input") as user_input, patch(
            "agent_thanks.cli.GitHubClient"
        ) as client_type, redirect_stdout(output):
            status = main(["star", str(self.report_path), "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("owner/recommended-one", output.getvalue())
        self.assertIn("owner/recommended-two", output.getvalue())
        self.assertNotIn("Would star: https://github.com/owner/review-only", output.getvalue())
        user_input.assert_not_called()
        client_type.assert_not_called()

    def test_low_confidence_candidate_cannot_be_selected_for_star(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(
                [
                    "star",
                    str(self.report_path),
                    "--repo",
                    "owner/review-only",
                    "--dry-run",
                ]
            )

        self.assertEqual(status, 2)
        self.assertIn("without high-confidence meaningful-use evidence", error.getvalue())

    def test_repository_must_exist_in_report(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            status = main(
                ["star", str(self.report_path), "--repo", "owner/missing", "--dry-run"]
            )

        self.assertEqual(status, 2)
        self.assertIn("not present in the report", error.getvalue())

    def test_removed_bulk_star_options_are_rejected(self) -> None:
        for arguments in (["--yes"], ["--all"], ["--mode", "auto"]):
            with self.subTest(arguments=arguments), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["star", str(self.report_path), *arguments])
                self.assertEqual(raised.exception.code, 2)

    def test_removed_config_command_is_rejected(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            main(["config", "--mode", "auto"])
        self.assertEqual(raised.exception.code, 2)

    @patch("agent_thanks.cli.GitHubClient")
    def test_live_star_requires_a_real_interactive_terminal(self, client_type) -> None:
        error = StringIO()
        with patch("agent_thanks.cli.sys.stdin", StringIO()), redirect_stdout(
            StringIO()
        ), redirect_stderr(error):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 2)
        self.assertIn("requires an interactive terminal", error.getvalue())
        self.assertIn("piped or unattended", error.getvalue())
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_every_eligible_repository_requires_a_yes_or_no(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        output = StringIO()

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "n", "y"]
        ) as user_input, redirect_stdout(output):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 3)
        client.star.assert_called_once_with("owner/recommended-one")
        self.assertIn("Each new Star requires an explicit yes", output.getvalue())
        self.assertIn("Review only: 1 candidate", output.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_every_repository_defaults_to_no(self, client_type) -> None:
        client_type.return_value.whoami.return_value = "octocat"
        client_type.return_value.is_starred.return_value = False
        output = StringIO()

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["", ""]
        ), redirect_stdout(output):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 0)
        self.assertIn("No repositories selected.", output.getvalue())
        client_type.return_value.star.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_legacy_auto_config_is_ignored_and_prompts_still_run(self, client_type) -> None:
        self.legacy_config_path.write_text(
            '{"schema_version": 1, "consent_mode": "auto"}\n', encoding="utf-8"
        )
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False

        with patch.dict(
            os.environ, {"AGENT_THANKS_CONFIG": str(self.legacy_config_path)}
        ), patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["n", "n"]
        ) as user_input, redirect_stdout(StringIO()):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 2)
        client.star.assert_not_called()

    def test_run_dry_run_is_noninteractive_and_writes_a_report(self) -> None:
        root = Path(self.temporary_directory.name) / "project"
        root.mkdir()
        (root / "requirements.txt").write_text(
            "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
        )
        report_path = root / "report.json"
        output = StringIO()

        with patch("builtins.input") as user_input, patch(
            "agent_thanks.cli.GitHubClient"
        ) as client_type, redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--repo",
                    str(root),
                    "--offline",
                    "--dry-run",
                    "--output",
                    str(report_path),
                ]
            )

        self.assertEqual(status, 0)
        self.assertTrue(report_path.is_file())
        self.assertIn("Would star: https://github.com/acme/demo", output.getvalue())
        user_input.assert_not_called()
        client_type.assert_not_called()

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

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y"]
        ), redirect_stdout(output), redirect_stderr(error):
            status = main(
                ["star", str(self.report_path), "--repo", "owner/recommended-one"]
            )

        self.assertEqual(status, 2)
        self.assertIn("HTTP 403", error.getvalue())
        self.assertNotIn("Starred:", output.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_existing_star_is_reported_without_mutation(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = True
        output = StringIO()

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input"
        ) as user_input, redirect_stdout(output):
            status = main(
                ["star", str(self.report_path), "--repo", "owner/recommended-one"]
            )

        self.assertEqual(status, 0)
        self.assertIn("GitHub account: @octocat", output.getvalue())
        self.assertIn("Already starred", output.getvalue())
        self.assertIn("No unstarred repositories require a decision", output.getvalue())
        user_input.assert_not_called()
        client.star.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_new_star_prints_exact_undo_command(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        output = StringIO()

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y"]
        ), redirect_stdout(output):
            status = main(
                ["star", str(self.report_path), "--repo", "owner/recommended-one"]
            )

        self.assertEqual(status, 0)
        client.star.assert_called_once_with("owner/recommended-one")
        self.assertIn(
            "Undo this batch: agent-thanks unstar owner/recommended-one",
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

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y", "y"]
        ), redirect_stdout(output), redirect_stderr(error):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 2)
        self.assertIn("partial update", output.getvalue())
        receipt = output.getvalue().split("Undo this batch:")[-1]
        self.assertIn("owner/recommended-one", receipt)
        self.assertNotIn("owner/recommended-two", receipt)
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

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y", "y"]
        ), redirect_stdout(output), redirect_stderr(error):
            status = main(["star", str(self.report_path)])

        self.assertEqual(status, 2)
        self.assertIn("agent-thanks unstar owner/recommended-one", output.getvalue())
        self.assertIn("connection reset", error.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_unstar_skips_repository_without_an_existing_star(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        output = StringIO()

        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y"]
        ), redirect_stdout(output):
            status = main(["unstar", "owner/recommended-one"])

        self.assertEqual(status, 0)
        self.assertIn("Not starred", output.getvalue())
        client.unstar.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_unstar_live_requires_a_real_interactive_terminal(self, client_type) -> None:
        error = StringIO()
        with patch("agent_thanks.cli.sys.stdin", StringIO()), redirect_stderr(error):
            status = main(["unstar", "owner/recommended-one"])

        self.assertEqual(status, 2)
        self.assertIn("requires an interactive terminal", error.getvalue())
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_demo_is_read_only_and_requires_no_authenticated_client(
        self, client_type
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            status = main(["demo"])

        rendered = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("no credentials or network requests", rendered)
        self.assertIn(
            "Would star: https://github.com/BehaviorTree/BehaviorTree.CPP", rendered
        )
        self.assertIn("Would star: https://github.com/ros-navigation/navigation2", rendered)
        self.assertNotIn("Would star: https://github.com/example/reference-only", rendered)
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_unstar_dry_run_never_constructs_an_authenticated_client(
        self, client_type
    ) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["unstar", "owner/recommended-one", "--dry-run"])

        self.assertEqual(status, 0)
        self.assertIn("Would unstar", output.getvalue())
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    @patch("agent_thanks.cli.subprocess.run")
    @patch("agent_thanks.cli.shutil.which", return_value="/usr/bin/tool")
    def test_doctor_reports_ready_account(self, _which, run, client_type) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="true\n", stderr=""
        )
        client_type.return_value.whoami.return_value = "octocat"
        output = StringIO()

        with redirect_stdout(output):
            status = main(["doctor", "--repo", self.temporary_directory.name])

        self.assertEqual(status, 0)
        self.assertIn("GitHub account: @octocat", output.getvalue())
        self.assertIn("interactive approval required", output.getvalue())
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
        client_type.return_value.whoami.side_effect = GitHubError("Authentication required")
        output = StringIO()

        with redirect_stdout(output):
            status = main(["doctor", "--repo", self.temporary_directory.name])

        self.assertEqual(status, 1)
        self.assertIn("GitHub authentication", output.getvalue())
        self.assertIn("Doctor found 1 issue(s).", output.getvalue())



class DemoConsistencyTests(unittest.TestCase):
    def test_bundled_example_reproduces_the_demo_classification(self) -> None:
        from agent_thanks.resolver import PackageRepositoryResolver
        from agent_thanks.scanner import ProjectScanner

        example = Path(__file__).resolve().parent.parent / "examples" / "session.jsonl"
        report = ProjectScanner(
            example.parent, resolver=PackageRepositoryResolver(offline=True)
        ).scan([example])
        classification = {
            item.repository: (item.confidence, item.recommended) for item in report.candidates
        }
        self.assertEqual(
            classification,
            {
                "BehaviorTree/BehaviorTree.CPP": ("high", True),
                "example/reference-only": ("low", False),
                "ros-navigation/navigation2": ("high", True),
            },
        )


if __name__ == "__main__":
    unittest.main()
