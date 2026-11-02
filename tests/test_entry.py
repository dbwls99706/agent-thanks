from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from agent_thanks.entry import PROJECT_URL, _detect_agent, main
from agent_thanks.models import Report


class ThanksWorkflowTests(unittest.TestCase):
    def test_token_transport_only_sends_put_after_both_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "terminal.log"
            session.write_text("git clone https://github.com/used/manual.git\n", encoding="utf-8")
            for final_answer in ("n", "y"):
                with self.subTest(final_answer=final_answer):
                    requests = []
                    def respond(request, **kwargs):
                        requests.append((request.method, request.full_url))
                        response = MagicMock()
                        if request.full_url.endswith("/user"):
                            response.status = 200
                            response.read.return_value = b'{"login":"octocat"}'
                        elif request.method == "GET":
                            raise HTTPError(request.full_url, 404, "Not Found", None, BytesIO(b"{}"))
                        else:
                            response.status = 204
                            response.read.return_value = b""
                            self.assertEqual(request.data, b"")
                        response.__enter__.return_value = response
                        return response
                    with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), patch(
                        "agent_thanks.github.urlopen", side_effect=respond
                    ), patch("agent_thanks.cli._require_interactive_terminal"), patch(
                        "builtins.input", side_effect=["y", final_answer]
                    ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        self.assertEqual(main([
                            "thanks", "--repo", str(root), "--session", str(session),
                            "--trust-session", "--offline", "--output", str(root / "report.json"),
                        ]), 0)
                    expected = [
                        ("GET", "https://api.github.com/user"),
                        ("GET", "https://api.github.com/user/starred/used/manual"),
                    ]
                    if final_answer == "y":
                        expected.append(("PUT", "https://api.github.com/user/starred/used/manual"))
                    self.assertEqual(requests, expected)

    @patch("agent_thanks.cli.GitHubClient")
    def test_manual_cli_log_requires_attestation_before_star_approval(self, client_type) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "terminal.log"
            session.write_text(
                "git clone https://github.com/used/manual.git\n"
                "See https://github.com/reference/only for background.\n",
                encoding="utf-8",
            )
            report_path = root / "report.json"
            arguments = [
                "thanks", "--repo", str(root), "--session", str(session),
                "--offline", "--output", str(report_path),
            ]
            preview = StringIO()
            with redirect_stdout(preview), redirect_stderr(StringIO()), patch("builtins.input") as ask:
                self.assertEqual(main([*arguments, "--dry-run"]), 0)
            self.assertNotIn("Would star:", preview.getvalue())
            self.assertFalse(any(candidate.recommended for candidate in Report.read(report_path).candidates))
            ask.assert_not_called()
            client_type.assert_not_called()

            client = client_type.return_value
            client.whoami.return_value = "octocat"
            client.is_starred.return_value = False
            output = StringIO()
            with patch("agent_thanks.cli._require_interactive_terminal"), patch(
                "builtins.input", side_effect=["y", "y"]
            ) as ask, redirect_stdout(output), redirect_stderr(StringIO()):
                self.assertEqual(main([*arguments, "--trust-session"]), 0)

            client.star.assert_called_once_with("used/manual")
            self.assertEqual(ask.call_count, 2)
            self.assertIn("GitHub account: @octocat", output.getvalue())
            self.assertIn("Starred: https://github.com/used/manual", output.getvalue())
            self.assertNotIn("Starred: https://github.com/reference/only", output.getvalue())

    @patch("agent_thanks.cli.GitHubClient")
    def test_auto_detected_codex_session_only_offers_successful_repository_use(self, client_type) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / "project"
            root.mkdir()
            sessions = home / ".codex" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "session.jsonl"
            records = [{"type": "session_meta", "payload": {"id": "task", "cwd": str(root)}}]
            for name, exit_code in (("success", 0), ("failure", 128)):
                records.extend([
                    {"type": "response_item", "payload": {
                        "type": "function_call", "name": "exec_command", "call_id": name,
                        "arguments": json.dumps({"cmd": f"git clone https://github.com/used/{name}.git"}),
                    }},
                    {"type": "response_item", "payload": {
                        "type": "function_call_output", "call_id": name,
                        "output": json.dumps({"output": "done", "metadata": {"exit_code": exit_code}}),
                    }},
                ])
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            client = client_type.return_value
            client.whoami.return_value = "octocat"
            client.is_starred.return_value = False
            output, error = StringIO(), StringIO()
            report_path = root / "report.json"
            with patch.dict(os.environ, {"CODEX_HOME": str(home / ".codex"),
                                        "CLAUDE_CONFIG_DIR": str(home / ".claude")}), patch(
                "pathlib.Path.home", return_value=home
            ), patch("agent_thanks.cli._require_interactive_terminal"), patch(
                "builtins.input", side_effect=["y", "y"]
            ) as ask, redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main([
                    "thanks", "--repo", str(root), "--offline", "--output", str(report_path),
                ]), 0)

            self.assertIn("Detected coding agent: codex", error.getvalue())
            candidates = {candidate.repository: candidate for candidate in Report.read(report_path).candidates}
            self.assertTrue(candidates["used/success"].recommended)
            self.assertFalse(candidates["used/failure"].recommended)
            self.assertEqual(ask.call_count, 2)
            client.star.assert_called_once_with("used/success")
            self.assertIn("Undo this batch: agent-thanks unstar used/success", output.getvalue())


class EntryPointTests(unittest.TestCase):
    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent", return_value="codex")
    def test_thanks_auto_detects_the_agent_for_the_project(self, detect, cli_main) -> None:
        error = StringIO()
        with redirect_stderr(error), redirect_stdout(StringIO()):
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
        with redirect_stdout(StringIO()):
            status = main(["thanks", "--from", "claude-code", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_not_called()
        cli_main.assert_called_once_with(
            ["run", "--from", "claude-code", "--dry-run"]
        )

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent")
    def test_explicit_session_is_never_replaced(self, detect, cli_main) -> None:
        with redirect_stdout(StringIO()):
            status = main(["thanks", "--session", "session.jsonl", "--dry-run"])

        self.assertEqual(status, 0)
        detect.assert_not_called()
        cli_main.assert_called_once_with(
            ["run", "--session", "session.jsonl", "--dry-run"]
        )

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent")
    def test_equals_style_session_is_never_replaced(self, detect, cli_main) -> None:
        with redirect_stdout(StringIO()):
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
        with redirect_stderr(error), redirect_stdout(StringIO()):
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

    def test_detect_agent_skips_an_unreadable_agent_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex = root / "codex.jsonl"
            codex.write_text("{}\n", encoding="utf-8")

            def locate(agent, project, home):
                if agent == "claude-code":
                    raise PermissionError("unreadable")
                if agent == "codex":
                    return codex
                return None

            with patch("agent_thanks.entry.locate_transcript", side_effect=locate):
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

    @patch("agent_thanks.entry.cli_main", return_value=0)
    def test_demo_ends_with_an_explicit_project_star_invitation(self, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["demo"])

        rendered = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Like the idea? agent-thanks is open source too.", rendered)
        self.assertIn(f"Star agent-thanks: {PROJECT_URL}", rendered)
        self.assertNotIn(f"Would star: {PROJECT_URL}", rendered)
        cli_main.assert_called_once_with(["demo"])

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent", return_value=None)
    def test_thanks_dry_run_ends_with_the_same_project_star_invitation(
        self, detect, cli_main
    ) -> None:
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            status = main(["thanks", "--dry-run"])

        rendered = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn(f"Star agent-thanks: {PROJECT_URL}", rendered)
        self.assertNotIn(f"Would star: {PROJECT_URL}", rendered)
        detect.assert_called_once()
        cli_main.assert_called_once_with(["run", "--dry-run"])

    @patch("agent_thanks.entry.cli_main", return_value=0)
    @patch("agent_thanks.entry._detect_agent", return_value=None)
    def test_live_thanks_does_not_self_promote(self, detect, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            status = main(["thanks"])

        self.assertEqual(status, 0)
        self.assertNotIn(PROJECT_URL, output.getvalue())
        detect.assert_called_once()
        cli_main.assert_called_once_with(["run"])

    @patch("agent_thanks.entry.cli_main", return_value=2)
    def test_failed_demo_does_not_print_the_project_star_invitation(self, cli_main) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(["demo"])

        self.assertEqual(status, 2)
        self.assertNotIn(PROJECT_URL, output.getvalue())
        cli_main.assert_called_once_with(["demo"])


if __name__ == "__main__":
    unittest.main()
