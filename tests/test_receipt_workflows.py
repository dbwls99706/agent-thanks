from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks import cli, receipts
from agent_thanks.models import Candidate, Evidence, Report


class ReceiptWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "한글 project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_report(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Report(
            # A saved report's JSON metadata must not choose the write target.
            root="/untrusted/report/metadata",
            base="HEAD",
            candidates=[Candidate("owner/one", [
                Evidence("session_usage", "session.jsonl:1", "Successful clone", "high", True)
            ])],
        ).write(path)
        return path

    def confirmed_receipt(self, *, account: str = "octocat", include_pending: bool = False):
        directory = cli._receipt_directory(self.project, create=True)
        repositories = ["owner/one", "owner/unconfirmed"] if include_pending else ["owner/one"]
        receipt = receipts.begin_receipt(
            directory, account, self.project, repositories, write=cli._private_write
        )
        return receipts.update_receipt(
            directory, receipt, "owner/one", "starred", write=cli._private_write
        )

    @patch("agent_thanks.cli.GitHubClient")
    def test_hook_report_stars_are_recorded_in_the_enclosing_project(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        for relative in (".agent-thanks/report.json", ".agent-thanks/reports/session.json"):
            with self.subTest(relative=relative):
                report = self.write_report(self.project / relative)
                before = set((self.project / ".agent-thanks" / "receipts").glob("*.json"))
                with patch("agent_thanks.cli._require_interactive_terminal"), patch(
                    "builtins.input", side_effect=["y", "y"]
                ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    status = cli.main(["star", str(report)])
                self.assertEqual(status, 0)
                directory = self.project / ".agent-thanks" / "receipts"
                created = set(directory.glob("*.json")) - before
                self.assertEqual(len(created), 1)
                receipt = receipts.read_receipt(directory, created.pop().stem, read=cli._private_read)
                self.assertEqual(receipt.root, str(self.project.resolve()))
                self.assertEqual(receipt.confirmed_repositories, ["owner/one"])
                self.assertFalse((report.parent / ".agent-thanks").exists())

    @patch("agent_thanks.cli.GitHubClient")
    def test_downloaded_report_can_use_an_explicit_project_for_its_receipt(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        report = self.write_report(self.root / "downloads" / "report.json")
        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y"]
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = cli.main(["star", str(report), "--project", str(self.project)])
        self.assertEqual(status, 0)
        self.assertEqual(len(list((self.project / ".agent-thanks" / "receipts").glob("*.json"))), 1)
        self.assertFalse((report.parent / ".agent-thanks").exists())

    @patch("agent_thanks.cli.GitHubClient")
    def test_confirmation_save_errors_preserve_the_exact_undo_command(self, client_type) -> None:
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = False
        report = self.write_report(self.project / "report.json")
        for error in (RuntimeError("receipt storage refused"), ValueError("invalid receipt storage")):
            with self.subTest(error=type(error).__name__):
                client.star.reset_mock()
                output, errors = StringIO(), StringIO()
                with patch("agent_thanks.cli._require_interactive_terminal"), patch(
                    "builtins.input", side_effect=["y", "y"]
                ), patch("agent_thanks.cli.receipts.update_receipt", side_effect=error), redirect_stdout(
                    output
                ), redirect_stderr(errors):
                    status = cli.main(["star", str(report)])
                self.assertEqual(status, 2)
                client.star.assert_called_once_with("owner/one")
                self.assertIn("Undo this batch: agent-thanks unstar owner/one", output.getvalue())
                self.assertIn(str(error), errors.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_unavailable_receipt_writes_stop_undo_before_delete(self, client_type) -> None:
        receipt = self.confirmed_receipt()
        client = client_type.return_value
        client.whoami.return_value = "octocat"
        client.is_starred.return_value = True
        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input", side_effect=["y", "y"]
        ), patch("agent_thanks.cli.receipts.update_receipt", side_effect=OSError("read-only storage")), redirect_stdout(
            StringIO()
        ), redirect_stderr(StringIO()):
            status = cli.main(["undo", "--repo", str(self.project), "--receipt", receipt.id])
        self.assertEqual(status, 2)
        client.unstar.assert_not_called()
        restored = receipts.read_receipt(
            self.project / ".agent-thanks" / "receipts", receipt.id, read=cli._private_read
        )
        self.assertEqual(restored.confirmed_repositories, ["owner/one"])

    @patch("agent_thanks.cli.GitHubClient")
    def test_wrong_account_cannot_query_or_delete_receipt_stars(self, client_type) -> None:
        receipt = self.confirmed_receipt(account="alice")
        client = client_type.return_value
        client.whoami.return_value = "bob"
        with patch("agent_thanks.cli._require_interactive_terminal"), patch(
            "builtins.input"
        ) as ask, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = cli.main(["undo", "--repo", str(self.project), "--receipt", receipt.id])
        self.assertEqual(status, 2)
        client.is_starred.assert_not_called()
        client.unstar.assert_not_called()
        ask.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_empty_history_and_undo_preview_do_not_create_state_or_authenticate(self, client_type) -> None:
        commands = (
            ["history", "--repo", str(self.project)],
            ["history", "--repo", str(self.project), "--json"],
            ["undo", "--repo", str(self.project), "--dry-run"],
        )
        for command in commands:
            with self.subTest(command=command):
                output = StringIO()
                with patch("agent_thanks.cli._require_interactive_terminal") as terminal, patch(
                    "builtins.input"
                ) as ask, redirect_stdout(output), redirect_stderr(StringIO()):
                    self.assertEqual(cli.main(command), 0)
                if "--json" in command:
                    self.assertEqual(json.loads(output.getvalue()), [])
                terminal.assert_not_called()
                ask.assert_not_called()
                self.assertFalse((self.project / ".agent-thanks").exists())
        client_type.assert_not_called()

    @patch("agent_thanks.cli.GitHubClient")
    def test_undo_preview_never_offers_unconfirmed_requests_or_authenticates(self, client_type) -> None:
        receipt = self.confirmed_receipt(include_pending=True)
        output = StringIO()
        with patch("agent_thanks.cli._require_interactive_terminal") as terminal, patch(
            "builtins.input"
        ) as ask, redirect_stdout(output), redirect_stderr(StringIO()):
            status = cli.main(["undo", "--repo", str(self.project), "--receipt", receipt.id, "--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("Would unstar: https://github.com/owner/one", output.getvalue())
        self.assertNotIn("Would unstar: https://github.com/owner/unconfirmed", output.getvalue())
        terminal.assert_not_called()
        ask.assert_not_called()
        client_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
