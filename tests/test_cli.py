from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.cli import main
from agent_thanks.config import ConfigStore
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


if __name__ == "__main__":
    unittest.main()
