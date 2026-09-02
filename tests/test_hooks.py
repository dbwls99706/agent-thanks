import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_thanks.cli import main


class IsolatedEnvironmentTestCase(unittest.TestCase):
    """Keep agent home overrides from the developer's shell out of the tests."""

    def setUp(self) -> None:
        self._environment = mock.patch.dict(os.environ)
        self._environment.start()
        for variable in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            os.environ.pop(variable, None)
        self.addCleanup(self._environment.stop)


def run(argv: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = main(argv)
    return status, stdout.getvalue()


def write_transcript(path: Path, command: str, *, is_error: bool = False) -> None:
    records = [
        {"type": "user", "message": {"role": "user", "content": "please"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": is_error, "content": "..."}],
            },
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


class HookRecordTests(IsolatedEnvironmentTestCase):
    def test_records_shell_commands_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {"cwd": directory, "session_id": "s1", "tool_use_id": "toolu_1", "tool_name": "Bash",
                       "tool_input": {"command": "git clone https://github.com/x/y"}}
            status, output = run(["hook", "record", "--from", "claude-code", json.dumps(payload)])
            self.assertEqual((status, output), (0, ""))
            other = {"cwd": directory, "session_id": "s1", "tool_name": "Read", "tool_input": {"command": "not a shell"}}
            self.assertEqual(run(["hook", "record", json.dumps(other)])[0], 0)

            state = root / ".agent-thanks"
            entries = [json.loads(line) for line in (state / "sessions" / "s1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["command"], "git clone https://github.com/x/y")
            self.assertEqual((entries[0]["status"], entries[0]["basis"], entries[0]["agent"], entries[0]["tool_call_id"]),
                             ("ok", "successful_post_tool_event", "claude-code", "toolu_1"))
            self.assertEqual((state / ".gitignore").read_text(encoding="utf-8"), "*\n")

    def test_success_event_basis_requires_an_explicit_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse",
                       "transcript_path": str(Path(directory) / "t.jsonl"), "tool_name": "Bash",
                       "tool_input": {"command": "git clone https://github.com/codex/shaped"},
                       "tool_response": {"stdout": "fatal: repository not found"}}
            self.assertEqual(run(["hook", "record", json.dumps(payload)]), (0, ""))
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / "s.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual((entries[0]["status"], entries[0]["basis"], entries[0]["agent"]), ("unknown", "no_result", None))

    def test_hook_log_is_primary_and_transcript_is_merged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git clone https://github.com/hooked/repo"}},
                    {"type": "text", "text": "Adapted from https://github.com/prose/claim"}]}},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "no is_error field here"}]}},
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            record = {"cwd": directory, "session_id": "s", "tool_name": "Bash",
                      "tool_input": {"command": "git clone https://github.com/hooked/repo"}}
            run(["hook", "record", "--from", "claude-code", json.dumps(record)])
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            status, output = run(["hook", "stop", "--offline", payload])
            self.assertEqual(status, 0)
            message = json.loads(output)["systemMessage"]
            self.assertIn("hooked/repo", message)
            self.assertIn("prose/claim", message)
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            hooked = next(c for c in report["candidates"] if c["repository"] == "hooked/repo")
            self.assertTrue(hooked["recommended"])
            self.assertEqual({e["confidence"] for e in hooked["evidence"]}, {"high", "low"})

    def test_hook_failure_overrides_a_transcript_success_for_the_same_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                 "arguments": json.dumps({"command": "git clone https://github.com/conflict/repo"})}},
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                 "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            record = {"cwd": directory, "session_id": "s", "tool_call_id": "c1", "tool_name": "shell",
                      "tool_input": {"command": "git clone https://github.com/conflict/repo"},
                      "tool_response": "Exit code: 128\nOutput:\nfatal"}
            run(["hook", "record", "--from", "codex", json.dumps(record)])
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertFalse(candidate["recommended"])
            self.assertTrue(all(e["confidence"] == "low" for e in candidate["evidence"]))

    def test_outcome_follows_the_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def record(agent: list[str], response: object, command: str) -> None:
                payload = {"cwd": directory, "session_id": "s", "tool_name": "shell",
                           "tool_input": {"command": command}, "tool_response": response}
                self.assertEqual(run(["hook", "record", *agent, json.dumps(payload)]), (0, ""))

            record(["--from", "codex"], {"stdout": "Cloning..."}, "git clone https://github.com/codex/unknown")
            record(["--from", "codex"], "Exit code: 0\nOutput:\nCloning...", "git clone https://github.com/codex/zero")
            record(["--from", "codex"], "Exit code: 128\nOutput:\nfatal", "git clone https://github.com/codex/failed")
            record([], {"stdout": ""}, "git clone https://github.com/generic/unknown")
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / "s.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(e["status"], e["basis"]) for e in entries],
                             [("unknown", "no_result"), ("ok", "exit_status"), ("error", "tool_response"), ("unknown", "no_result")])

            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s"})])
            self.assertEqual(status, 0)
            message = json.loads(output)["systemMessage"]
            self.assertIn("codex/zero", message)
            for repository in ("codex/unknown", "codex/failed", "generic/unknown"):
                self.assertNotIn(repository, message)
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            by_repo = {c["repository"]: c for c in report["candidates"]}
            self.assertTrue(by_repo["codex/zero"]["recommended"])
            self.assertIn("failed", by_repo["codex/failed"]["evidence"][0]["detail"])
            self.assertIn("cannot be judged", by_repo["codex/unknown"]["evidence"][0]["detail"])

    def test_malformed_payload_never_fails(self) -> None:
        status, output = run(["hook", "record", "{not json"])
        self.assertEqual((status, output), (0, ""))


class HookStopTests(IsolatedEnvironmentTestCase):
    def test_announces_new_verified_repositories_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.jsonl"
            write_transcript(transcript, "git clone https://github.com/BehaviorTree/BehaviorTree.CPP.git")
            payload = json.dumps({"cwd": directory, "transcript_path": str(transcript), "hook_event_name": "Stop"})

            status, output = run(["hook", "stop", "--offline", payload])
            self.assertEqual(status, 0)
            message = json.loads(output)["systemMessage"]
            self.assertIn("BehaviorTree/BehaviorTree.CPP", message)
            self.assertIn("agent-thanks star .agent-thanks/report.json", message)
            self.assertNotIn("Would star", output)

            report = json.loads((root / ".agent-thanks" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual([item["repository"] for item in report["candidates"]], ["BehaviorTree/BehaviorTree.CPP"])

            status, output = run(["hook", "stop", "--offline", payload])
            self.assertEqual((status, output), (0, ""))

    def test_falls_back_to_recorded_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = {"cwd": directory, "tool_name": "Bash", "tool_input": {"command": "gh repo clone acme/tool"}}
            run(["hook", "record", "--from", "claude-code", json.dumps(record)])
            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory})])
            self.assertEqual(status, 0)
            self.assertIn("acme/tool", json.loads(output)["systemMessage"])

    def test_silent_without_sources_or_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(run(["hook", "stop", "--offline", json.dumps({"cwd": directory})]), (0, ""))
            transcript = Path(directory) / "t.jsonl"
            write_transcript(transcript, "ls -la")
            payload = json.dumps({"cwd": directory, "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--offline", payload]), (0, ""))


class CodexNotifyTests(IsolatedEnvironmentTestCase):
    def codex_rollout(self, directory: Path, cwd: str, command: str, thread: str = "thread-1") -> Path:
        sessions = directory / "sessions" / "2026" / "09" / "02"
        sessions.mkdir(parents=True, exist_ok=True)
        records = [
            {"timestamp": "t", "type": "session_meta", "payload": {"id": thread, "cwd": cwd}},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
             "arguments": json.dumps({"command": ["bash", "-lc", command], "workdir": cwd})}},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
             "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
        ]
        path = sessions / f"rollout-2026-09-02T10-00-00-{thread}.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return path

    def test_clean_project_gets_a_report_from_the_located_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            codex_home = Path(directory) / "codex-home"
            self.codex_rollout(codex_home, str(project.resolve()), "git clone https://github.com/codex/located")
            payload = json.dumps({"type": "agent-turn-complete", "thread-id": "thread-1", "turn-id": "turn-1",
                                  "cwd": str(project), "input-messages": ["clone it"], "last-assistant-message": "done"})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                status, output = run(["hook", "stop", "--offline", "--from", "codex", payload])
                self.assertEqual(status, 0)
                message = json.loads(output)["systemMessage"]
                self.assertIn("codex/located", message)
                self.assertIn(".agent-thanks/reports/thread-1.json", message)
                self.assertTrue((project / ".agent-thanks" / "reports" / "thread-1.json").is_file())
                self.assertTrue((project / ".agent-thanks" / "report.json").is_file())

                other = Path(directory) / "other"
                other.mkdir()
                status, output = run(["hook", "stop", "--offline", "--from", "codex", json.dumps({"type": "agent-turn-complete", "cwd": str(other)})])
                self.assertEqual((status, output), (0, ""))
                self.assertFalse((other / ".agent-thanks" / "report.json").exists())

    def test_notify_picks_the_rollout_of_its_own_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            codex_home = Path(directory) / "codex-home"
            older = self.codex_rollout(codex_home, str(project.resolve()), "git clone https://github.com/thread/a", thread="thread-A")
            newer = self.codex_rollout(codex_home, str(project.resolve()), "git clone https://github.com/thread/b", thread="thread-B")
            os.utime(older, (5, 5))
            os.utime(newer, (9, 9))
            payload = json.dumps({"type": "agent-turn-complete", "thread-id": "thread-A", "cwd": str(project)})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                status, output = run(["hook", "stop", "--offline", "--from", "codex", payload])
            self.assertEqual(status, 0)
            message = json.loads(output)["systemMessage"]
            self.assertIn("thread/a", message)
            self.assertNotIn("thread/b", message)
            report = json.loads((project / ".agent-thanks" / "reports" / "thread-A.json").read_text(encoding="utf-8"))
            self.assertEqual([c["repository"] for c in report["candidates"]], ["thread/a"])

    def test_agent_is_inferred_from_the_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            codex_home = Path(directory) / "codex-home"
            self.codex_rollout(codex_home, str(project.resolve()), "gh repo clone codex/inferred")
            payload = json.dumps({"type": "agent-turn-complete", "thread-id": "thread-1", "cwd": str(project)})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
                status, output = run(["hook", "stop", "--offline", payload])
            self.assertEqual(status, 0)
            self.assertIn("codex/inferred", json.loads(output)["systemMessage"])


class SessionScopeTests(IsolatedEnvironmentTestCase):
    def test_logs_and_announcements_are_scoped_to_the_agent_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for session in ("s-one", "s-two"):
                record = {"cwd": directory, "session_id": session, "tool_name": "Bash",
                          "tool_input": {"command": "git clone https://github.com/shared/repo"}}
                run(["hook", "record", "--from", "claude-code", json.dumps(record)])
            self.assertTrue((root / ".agent-thanks" / "sessions" / "s-one.jsonl").is_file())
            self.assertTrue((root / ".agent-thanks" / "sessions" / "s-two.jsonl").is_file())

            first = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-one"})])
            self.assertIn("shared/repo", json.loads(first[1])["systemMessage"])
            self.assertIn(".agent-thanks/reports/s-one.json", json.loads(first[1])["systemMessage"])
            self.assertTrue((root / ".agent-thanks" / "reports" / "s-one.json").is_file())
            again = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-one"})])
            self.assertEqual(again, (0, ""))
            second_session = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-two"})])
            self.assertIn("shared/repo", json.loads(second_session[1])["systemMessage"])

            announced = json.loads((root / ".agent-thanks" / "announced.json").read_text(encoding="utf-8"))
            self.assertEqual(set(announced), {"s-one", "s-two"})

    def test_failed_tool_runs_are_recorded_as_errors_and_never_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def record(response: object, command: str) -> None:
                payload = {"cwd": directory, "session_id": "s", "tool_name": "Bash",
                           "tool_input": {"command": command}, "tool_response": response}
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(payload)]), (0, ""))

            record({"is_error": True, "stderr": "fatal"}, "git clone https://github.com/x/flagged")
            record("Exit code: 128\nOutput:\nfatal: repository not found", "git clone https://github.com/x/textual")
            record({"exit_code": 1}, "git clone https://github.com/x/code")
            record({"exit_code": 0}, "git clone https://github.com/x/zero")
            record({"stdout": "Cloning into 'y'...", "stderr": ""}, "git clone https://github.com/x/event")
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / "s.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry["status"] for entry in entries], ["error", "error", "error", "ok", "ok"])
            self.assertEqual(entries[-1]["basis"], "successful_post_tool_event")

            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s"})])
            message = json.loads(output)["systemMessage"]
            self.assertIn("x/zero", message)
            self.assertIn("x/event", message)
            for repository in ("x/flagged", "x/textual", "x/code"):
                self.assertNotIn(repository, message)

    def test_old_session_logs_are_pruned(self) -> None:
        import os
        import time

        with tempfile.TemporaryDirectory() as directory:
            sessions = Path(directory) / ".agent-thanks" / "sessions"
            sessions.mkdir(parents=True)
            stale = sessions / "stale.jsonl"
            stale.write_text("git clone https://github.com/old/work\n", encoding="utf-8")
            old = time.time() - 40 * 24 * 3600
            os.utime(stale, (old, old))
            run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "fresh"})])
            self.assertFalse(stale.exists())


class FromAgentTests(IsolatedEnvironmentTestCase):
    def test_from_locates_the_project_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            project = Path(directory) / "project"
            project.mkdir()
            from agent_thanks.transcripts import encode_project_path

            transcripts = home / ".claude" / "projects" / encode_project_path(project.resolve())
            transcripts.mkdir(parents=True)
            write_transcript(transcripts / "s.jsonl", "git clone https://github.com/located/repo")

            with mock.patch.object(Path, "home", return_value=home):
                status, output = run(["scan", "--repo", str(project), "--from", "claude-code", "--offline", "--output", "-"])
                self.assertEqual(status, 0)
                self.assertEqual([c["repository"] for c in json.loads(output)["candidates"]], ["located/repo"])

                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    status, _ = run(["scan", "--repo", str(Path(directory)), "--from", "codex", "--offline", "--output", "-"])
                self.assertEqual(status, 2)
                self.assertIn("No codex transcript found", stderr.getvalue())
                self.assertIn(".codex", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
