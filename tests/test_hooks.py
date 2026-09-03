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
            payload = {"cwd": directory, "session_id": "s1", "hook_event_name": "PostToolUse", "tool_use_id": "toolu_1",
                       "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/x/y"}}
            status, output = run(["hook", "record", "--from", "claude-code", json.dumps(payload)])
            self.assertEqual((status, output), (0, ""))
            other = {"cwd": directory, "session_id": "s1", "tool_name": "Read", "tool_input": {"command": "not a shell"}}
            self.assertEqual(run(["hook", "record", json.dumps(other)])[0], 0)
            before = dict(payload, hook_event_name="PreToolUse", tool_use_id="toolu_2",
                          tool_input={"command": "git clone https://github.com/x/never-ran"})
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(before)]), (0, ""))

            state = root / ".agent-thanks"
            entries = [json.loads(line) for line in (state / "sessions" / "s1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["command"], "git clone https://github.com/x/y")
            self.assertEqual((entries[0]["status"], entries[0]["basis"], entries[0]["agent"], entries[0]["tool_call_id"]),
                             ("ok", "successful_post_tool_event", "claude-code", "toolu_1"))
            self.assertEqual((entries[0]["schema"], entries[0]["event"]), ("agent-thanks/hook-log/1", "PostToolUse"))
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
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/hooked/repo"}}
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
            # The hook entry and the transcript call it confirms are both high; nothing is left unjudged.
            self.assertEqual({e["confidence"] for e in hooked["evidence"]}, {"high"})

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

    def test_conflicting_hook_entries_for_one_call_never_announce(self) -> None:
        header = "Chunk ID: c0de\nWall time: 0.1 seconds\nProcess exited with code {code}\nOutput:\n"
        with tempfile.TemporaryDirectory() as directory:
            for code in (128, 0):
                record = {"cwd": directory, "session_id": "s", "tool_use_id": "same", "tool_name": "Bash",
                          "tool_input": {"command": "git clone https://github.com/conflict/hook"},
                          "tool_response": header.format(code=code) + ("fatal" if code else "Cloning...")}
                self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)]), (0, ""))
            payload = json.dumps({"cwd": directory, "session_id": "s"})
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertEqual(candidate["repository"], "conflict/hook")
            self.assertFalse(candidate["recommended"])
            self.assertEqual({e["confidence"] for e in candidate["evidence"]}, {"low"})

    def test_hook_success_applies_only_to_the_same_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "same",
                 "arguments": json.dumps({"command": "git clone https://github.com/mismatch/command"})}},
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "same",
                 "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            record = {"cwd": directory, "session_id": "s", "tool_use_id": "same", "tool_name": "Bash",
                      "tool_input": {"command": "echo ok"},
                      "tool_response": json.dumps({"output": "ok", "metadata": {"exit_code": 0}})}
            self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)]), (0, ""))
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertEqual(candidate["repository"], "mismatch/command")
            self.assertFalse(candidate["recommended"])
            self.assertTrue(all(e["confidence"] == "low" for e in candidate["evidence"]))
            self.assertTrue(any("disagree about the command" in e["detail"] for e in candidate["evidence"]))

    def test_outcome_follows_the_agent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            counter = iter(range(1, 100))

            def record(agent: list[str], response: object, command: str) -> None:
                payload = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse",
                           "tool_use_id": f"call_{next(counter)}", "tool_name": "Bash",
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
            record = {"cwd": directory, "hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Bash",
                      "tool_input": {"command": "gh repo clone acme/tool"}}
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


class GeminiHookOutputTests(IsolatedEnvironmentTestCase):
    def test_gemini_hooks_answer_with_an_empty_object_when_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = json.dumps({"cwd": directory, "session_id": "g"})
            status, out = run(["hook", "stop", "--from", "gemini", "--offline", payload])
            self.assertEqual((status, json.loads(out)), (0, {}))
            record = json.dumps({"cwd": directory, "session_id": "g", "tool_name": "read_file", "tool_input": {"path": "x"}})
            status, out = run(["hook", "record", "--from", "gemini", record])
            self.assertEqual((status, json.loads(out)), (0, {}))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status, out = run(["hook", "stop", "--from", "gemini", "{not json"])
            self.assertEqual((status, json.loads(out)), (0, {}))
            self.assertIn("agent-thanks hook", stderr.getvalue())
            # Other agents accept an empty standard output and keep it.
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", payload]), (0, ""))


class HookLogSchemaTests(IsolatedEnvironmentTestCase):
    def test_a_hook_log_needs_the_schema_marker_and_complete_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = Path(directory) / ".agent-thanks" / "sessions"
            sessions.mkdir(parents=True)
            shaped = {"command": "git clone https://github.com/shape/confused", "status": "ok", "basis": "model-output",
                      "tool_call_id": "x1", "agent": "claude-code"}
            (sessions / "shape.jsonl").write_text(json.dumps(shaped) + "\n", encoding="utf-8")
            self.assertEqual(run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "shape"})]), (0, ""))

            base = {"schema": "agent-thanks/hook-log/1", "agent": "claude-code", "event": "PostToolUse", "tool": "Bash",
                    "session_id": "partial", "tool_call_id": "c1", "command": "git clone https://github.com/partial/complete",
                    "status": "ok", "basis": "successful_post_tool_event"}
            incomplete = [
                dict(base, agent=None, tool_call_id="c2", command="git clone https://github.com/partial/no-agent"),
                dict(base, basis="model-output", tool_call_id="c3", command="git clone https://github.com/partial/basis"),
                dict(base, tool_call_id=None, command="git clone https://github.com/partial/no-id"),
                dict(base, tool_call_id="", command="git clone https://github.com/partial/empty-id"),
                dict(base, agent="unknown-agent", tool_call_id="c4", command="git clone https://github.com/partial/agent"),
            ]
            (sessions / "partial.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [base, *incomplete]), encoding="utf-8")
            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "partial"})])
            self.assertEqual(status, 0)
            self.assertIn("partial/complete", json.loads(output)["systemMessage"])
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "partial.json").read_text(encoding="utf-8"))
            by_repo = {c["repository"]: c for c in report["candidates"]}
            self.assertTrue(by_repo["partial/complete"]["recommended"])
            for repository in ("partial/no-agent", "partial/basis", "partial/no-id", "partial/empty-id", "partial/agent"):
                self.assertFalse(by_repo[repository]["recommended"], repository)


class PromotionGateTests(IsolatedEnvironmentTestCase):
    """Every gate condition is necessary: change one field of a verified session and it stays a reference."""

    COMMAND = "git clone https://github.com/gate/repo"

    def run_session(self, directory: str, *, agent: list[str] | None = None, payload_changes: dict | None = None,
                    transcript_command: str | None = None, extra_calls: list[dict] | None = None,
                    extra_records: list[dict] | None = None, strip_schema: bool = False,
                    edit_log: dict | None = None, append_log: str | None = None,
                    transcript_result: dict | None = None) -> dict:
        command = self.COMMAND
        payload = {"cwd": directory, "session_id": "g", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(payload_changes or {})
        records = [json.dumps({"cwd": directory, "session_id": "g", "hook_event_name": "PostToolUse", "tool_use_id": "t0",
                               "tool_name": "Bash", "tool_input": {"command": "echo unrelated"}})]
        records.append(json.dumps(payload))
        for record in records:
            run(["hook", "record", *(["--from", "claude-code"] if agent is None else agent), record])
        for record in extra_records or []:
            run(["hook", "record", "--from", "claude-code", json.dumps(dict(record, cwd=directory, session_id="g"))])
        log = Path(directory) / ".agent-thanks" / "sessions" / "g.jsonl"
        if (strip_schema or edit_log) and log.is_file():
            entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            for entry in entries:
                if strip_schema:
                    entry.pop("schema", None)
                if edit_log and entry.get("tool_call_id") == "t1":
                    entry.update(edit_log)
            log.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        if append_log and log.is_file():
            with log.open("a", encoding="utf-8") as handle:
                handle.write(append_log + "\n")
        transcript = Path(directory) / "t.jsonl"
        calls = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": transcript_command or command}}]
        calls.extend(extra_calls or [])
        lines = [
            {"type": "assistant", "message": {"role": "assistant", "content": calls}},
            {"type": "user", "message": {"role": "user", "content": [
                transcript_result or {"type": "tool_result", "tool_use_id": "t1", "content": "no result signal recorded"}]}},
        ]
        transcript.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        stop = json.dumps({"cwd": directory, "session_id": "g", "transcript_path": str(transcript)})
        status, output = run(["hook", "stop", "--from", "claude-code", "--offline", stop])
        self.assertEqual(status, 0)
        report_path = Path(directory) / ".agent-thanks" / "reports" / "g.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {"candidates": []}
        candidates = {c["repository"]: c for c in report["candidates"]}
        return {"announced": bool(output.strip()), "candidate": candidates.get("gate/repo")}

    def test_the_verified_baseline_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_session(directory)
        self.assertTrue(result["announced"])
        self.assertTrue(result["candidate"]["recommended"])

    def test_every_condition_is_necessary(self) -> None:
        mutations = {
            "pre-tool event": dict(payload_changes={"hook_event_name": "PreToolUse"}),
            "missing event": dict(payload_changes={"hook_event_name": None}),
            "other event": dict(payload_changes={"hook_event_name": "Notification"}),
            "no agent": dict(agent=[]),
            "other agent contract": dict(agent=["--from", "codex"]),
            "missing call id": dict(payload_changes={"tool_use_id": None}),
            "empty call id": dict(payload_changes={"tool_use_id": ""}),
            "non-shell tool": dict(payload_changes={"tool_name": "Read"}),
            "command differs by newline": dict(transcript_command="git clone \\\n  https://github.com/gate/repo"),
            "command differs by spacing": dict(transcript_command="git  clone https://github.com/gate/repo"),
            "reused call id": dict(extra_calls=[{"type": "tool_use", "id": "t1", "name": "Bash",
                                                  "input": {"command": "git clone https://github.com/gate/other"}}]),
            "conflicting hook entry": dict(extra_records=[{"hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Bash",
                                                            "tool_input": {"command": "git clone https://github.com/gate/repo"},
                                                            "tool_response": {"is_error": True}}]),
            "env wrapper": dict(payload_changes={"tool_input": {"command": "env PATH=/tmp/fake git clone https://github.com/gate/repo"}},
                                transcript_command="env PATH=/tmp/fake git clone https://github.com/gate/repo"),
            "printf chain": dict(payload_changes={"tool_input": {"command": "printf -v PATH /tmp/fake && git clone https://github.com/gate/repo"}},
                                 transcript_command="printf -v PATH /tmp/fake && git clone https://github.com/gate/repo"),
            "compound statement": dict(payload_changes={"tool_input": {"command": "git clone https://github.com/gate/repo || true"}},
                                       transcript_command="git clone https://github.com/gate/repo || true"),
            "multi-line call": dict(payload_changes={"tool_input": {"command": "git clone https://github.com/gate/repo\ntrue"}},
                                    transcript_command="git clone https://github.com/gate/repo\ntrue"),
            "explicit failure": dict(payload_changes={"tool_response": {"is_error": True}}),
            "failure event": dict(payload_changes={"hook_event_name": "PostToolUseFailure"}),
            "schema stripped": dict(strip_schema=True),
            "transcript records failure": dict(transcript_result={"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "fatal"}),
            "stored event altered": dict(edit_log={"event": "AfterTool"}),
            "stored tool altered": dict(edit_log={"tool": "run_shell_command"}),
            "stored agent altered": dict(edit_log={"agent": "gemini"}),
            "stored basis altered": dict(edit_log={"basis": "exit_status"}),
            "stored status without contract": dict(edit_log={"agent": "codex", "event": None}),
            "corrupted log line": dict(append_log='{"schema": "agent-thanks/hook-log/1", "trunc'),
            "foreign schema line": dict(append_log=json.dumps({"schema": "other/1", "command": "x", "status": "ok"})),
            "provenance phrase as command": dict(payload_changes={"tool_input": {"command": "Adapted from https://github.com/gate/repo"}, "hook_event_name": "PostToolUseFailure"},
                                                 transcript_command="Adapted from https://github.com/gate/repo",
                                                 transcript_result={"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "command not found"}),
        }
        for name, mutation in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as directory:
                result = self.run_session(directory, **mutation)
                self.assertFalse(result["announced"], name)
                candidate = result["candidate"]
                if candidate is not None:
                    self.assertFalse(candidate["recommended"], name)
                    self.assertTrue(all(e["confidence"] == "low" for e in candidate["evidence"]), name)


class HookContractTests(IsolatedEnvironmentTestCase):
    HEADER = "Chunk ID: c0de\nWall time: 0.1 seconds\nProcess exited with code 0\nOutput:\nCloning..."

    def test_record_time_contract_matrix(self) -> None:
        cases = [
            (["--from", "codex"], "PostToolUse", "Bash", self.HEADER, ("ok", "exit_status")),
            (["--from", "codex"], None, "Bash", self.HEADER, ("unknown", "no_result")),
            (["--from", "codex"], "AfterTool", "Bash", self.HEADER, ("unknown", "no_result")),
            (["--from", "codex"], "PostToolUse", "shell", self.HEADER, ("unknown", "no_result")),
            (["--from", "gemini"], "AfterTool", "run_shell_command", {"exit_code": 0}, ("unknown", "no_result")),
            (["--from", "gemini"], "AfterTool", "run_shell_command", {"error": {"message": "boom"}}, ("error", "tool_response")),
            (["--from", "claude-code"], "PostToolUse", "Bash", None, ("ok", "successful_post_tool_event")),
            (["--from", "claude-code"], "PostToolUse", "Bash", {"exit_code": 0}, ("ok", "successful_post_tool_event")),
            (["--from", "claude-code"], "PostToolUse", "run_shell_command", None, ("unknown", "no_result")),
            (["--from", "claude-code"], "PostToolUseFailure", "Bash", None, ("error", "post_tool_failure_event")),
            (["--from", "claude-code"], "PostToolUse", "Bash", {"is_error": True}, ("error", "tool_response")),
            ([], "PostToolUse", "Bash", self.HEADER, ("unknown", "no_result")),
        ]
        for index, (agent, event, tool, response, expected) in enumerate(cases):
            with self.subTest(agent=agent, event=event, tool=tool), tempfile.TemporaryDirectory() as directory:
                payload = {"cwd": directory, "session_id": "m", "tool_use_id": f"c{index}", "tool_name": tool,
                           "tool_input": {"command": "git clone https://github.com/matrix/repo"}}
                if event is not None:
                    payload["hook_event_name"] = event
                if response is not None:
                    payload["tool_response"] = response
                self.assertEqual(run(["hook", "record", *agent, json.dumps(payload)])[0], 0)
                log = Path(directory) / ".agent-thanks" / "sessions" / "m.jsonl"
                entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual((entry["status"], entry["basis"]), expected)
                self.assertEqual((entry["agent"], entry["event"], entry["tool"]), (agent[-1] if agent else None, event, tool))

    def test_stored_log_contract_matrix(self) -> None:
        base = {"schema": "agent-thanks/hook-log/1", "session_id": "m", "tool_call_id": "c1",
                "command": "git clone https://github.com/stored/repo", "status": "ok"}
        rows = {
            ("claude-code", "PostToolUse", "Bash", "successful_post_tool_event"): True,
            ("codex", "PostToolUse", "Bash", "exit_status"): True,
            ("codex", None, "Bash", "exit_status"): False,
            ("codex", "AfterTool", "Bash", "exit_status"): False,
            ("codex", "PostToolUse", "shell", "exit_status"): False,
            ("gemini", "AfterTool", "run_shell_command", "exit_status"): False,
            ("claude-code", "PostToolUse", "run_shell_command", "successful_post_tool_event"): False,
            ("claude-code", "PreToolUse", "Bash", "successful_post_tool_event"): False,
            ("claude-code", "PostToolUse", "Bash", "exit_status"): False,
            (None, "PostToolUse", "Bash", "successful_post_tool_event"): False,
        }
        for (agent, event, tool, basis), promoted in rows.items():
            with self.subTest(agent=agent, event=event, tool=tool, basis=basis), tempfile.TemporaryDirectory() as directory:
                sessions = Path(directory) / ".agent-thanks" / "sessions"
                sessions.mkdir(parents=True)
                entry = dict(base, agent=agent, event=event, tool=tool, basis=basis)
                (sessions / "m.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
                status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "m"})])
                self.assertEqual(status, 0)
                self.assertEqual(bool(output.strip()), promoted)

    def test_a_corrupted_hook_log_promotes_nothing(self) -> None:
        good = {"schema": "agent-thanks/hook-log/1", "agent": "claude-code", "event": "PostToolUse", "tool": "Bash",
                "session_id": "c", "tool_call_id": "c1", "command": "git clone https://github.com/corrupt/repo",
                "status": "ok", "basis": "successful_post_tool_event"}
        for tail in ('{"schema": "agent-thanks/hook-log/1", "trunc', json.dumps({"schema": "other/1", "status": "ok"}), "[]"):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as directory:
                sessions = Path(directory) / ".agent-thanks" / "sessions"
                sessions.mkdir(parents=True)
                (sessions / "c.jsonl").write_text(json.dumps(good) + "\n" + tail + "\n", encoding="utf-8")
                self.assertEqual(run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "c"})]), (0, ""))
                report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "c.json").read_text(encoding="utf-8"))
                self.assertFalse(report["candidates"][0]["recommended"])

    def test_hook_success_never_overrides_a_transcript_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = "git clone https://github.com/reverse/conflict"
            transcript = Path(directory) / "t.jsonl"
            write_transcript(transcript, command, is_error=True)
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": command}}
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / "s.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertFalse(candidate["recommended"])
            self.assertEqual({e["confidence"] for e in candidate["evidence"]}, {"low"})

    def test_session_file_names_never_collide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = {"cwd": directory, "session_id": "collision/a", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/collide/repo"}}
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            other = json.dumps({"cwd": directory, "session_id": "collision?a"})
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", other]), (0, ""))
            first = json.dumps({"cwd": directory, "session_id": "collision/a"})
            status, output = run(["hook", "stop", "--from", "claude-code", "--offline", first])
            self.assertIn("collide/repo", json.loads(output)["systemMessage"])
            names = sorted(p.name for p in (Path(directory) / ".agent-thanks" / "sessions").iterdir())
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].startswith("collision_a-"))


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
                record = {"cwd": directory, "session_id": session, "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                          "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/shared/repo"}}
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
            counter = iter(range(1, 100))

            def record(response: object, command: str) -> None:
                payload = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse",
                           "tool_use_id": f"toolu_{next(counter)}", "tool_name": "Bash",
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
