import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_thanks.cli import main, session_file_stem


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


def write_transcript(path: Path, command: str, *, is_error: bool = False, cwd: str | None = None,
                     session_id: str | None = None) -> None:
    records: list[dict] = [
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
    for record in records:
        if cwd is not None:
            record["cwd"] = cwd  # Claude Code records the project directory on every line
        if session_id is not None:
            record["sessionId"] = session_id
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
            entries = [json.loads(line) for line in (state / "sessions" / f"{session_file_stem('id:s1')}.jsonl").read_text(encoding="utf-8").splitlines()]
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
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual((entries[0]["status"], entries[0]["basis"], entries[0]["agent"]), ("unknown", "no_result", None))

    def test_hook_log_is_primary_and_transcript_is_merged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "assistant", "cwd": directory, "sessionId": "s", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git clone https://github.com/hooked/repo"}},
                    {"type": "text", "text": "Adapted from https://github.com/prose/claim"}]}},
                {"type": "user", "cwd": directory, "sessionId": "s", "message": {"role": "user", "content": [
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
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
            hooked = next(c for c in report["candidates"] if c["repository"] == "hooked/repo")
            self.assertTrue(hooked["recommended"])
            # The hook entry and the transcript call it confirms are both high; nothing is left unjudged.
            self.assertEqual({e["confidence"] for e in hooked["evidence"]}, {"high"})

    def test_hook_failure_overrides_a_transcript_success_for_the_same_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "s", "cwd": directory}},
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
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
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
                self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)]), (0, "{}\n"))
            payload = json.dumps({"cwd": directory, "session_id": "s"})
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", payload]), (0, "{}\n"))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertEqual(candidate["repository"], "conflict/hook")
            self.assertFalse(candidate["recommended"])
            self.assertEqual({e["confidence"] for e in candidate["evidence"]}, {"low"})

    def test_hook_success_applies_only_to_the_same_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "t.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "s", "cwd": directory}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "same",
                 "arguments": json.dumps({"command": "git clone https://github.com/mismatch/command"})}},
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "same",
                 "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            record = {"cwd": directory, "session_id": "s", "tool_use_id": "same", "tool_name": "Bash",
                      "tool_input": {"command": "echo ok"},
                      "tool_response": json.dumps({"output": "ok", "metadata": {"exit_code": 0}})}
            self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)]), (0, "{}\n"))
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", payload]), (0, "{}\n"))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
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
                expected = "{}\n" if agent == ["--from", "codex"] else ""
                self.assertEqual(run(["hook", "record", *agent, json.dumps(payload)]), (0, expected))

            record(["--from", "codex"], {"stdout": "Cloning..."}, "git clone https://github.com/codex/unknown")
            record(["--from", "codex"], "Exit code: 0\nOutput:\nCloning...", "git clone https://github.com/codex/zero")
            record(["--from", "codex"], "Exit code: 128\nOutput:\nfatal", "git clone https://github.com/codex/failed")
            record([], {"stdout": ""}, "git clone https://github.com/generic/unknown")
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(e["status"], e["basis"]) for e in entries],
                             [("unknown", "no_result"), ("ok", "exit_status"), ("error", "tool_response"), ("unknown", "no_result")])

            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s"})])
            self.assertEqual(status, 0)
            message = json.loads(output)["systemMessage"]
            self.assertIn("codex/zero", message)
            for repository in ("codex/unknown", "codex/failed", "generic/unknown"):
                self.assertNotIn(repository, message)
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
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
            # Without a session id the payload is scoped by its transcript path; the latest copy still lands in report.json.
            self.assertIn("agent-thanks star .agent-thanks/reports/", message)
            self.assertNotIn("Would star", output)

            report = json.loads((root / ".agent-thanks" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual([item["repository"] for item in report["candidates"]], ["BehaviorTree/BehaviorTree.CPP"])

            status, output = run(["hook", "stop", "--offline", payload])
            self.assertEqual((status, output), (0, ""))

    def test_falls_back_to_recorded_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = {"cwd": directory, "session_id": "fallback", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": "gh repo clone acme/tool"}}
            run(["hook", "record", "--from", "claude-code", json.dumps(record)])
            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "fallback"})])
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
            (sessions / f"{session_file_stem('id:shape')}.jsonl").write_text(json.dumps(shaped) + "\n", encoding="utf-8")
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
            (sessions / f"{session_file_stem('id:partial')}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [base, *incomplete]), encoding="utf-8")
            status, output = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "partial"})])
            self.assertEqual(status, 0)
            self.assertIn("partial/complete", json.loads(output)["systemMessage"])
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:partial')}.json").read_text(encoding="utf-8"))
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
                    transcript_result: dict | None = None, drop_call: bool = False) -> dict:
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
        log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:g')}.jsonl"
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
        calls = [] if drop_call else [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": transcript_command or command}}]
        calls.extend(extra_calls or [])
        lines = [
            {"type": "assistant", "cwd": directory, "sessionId": "g", "message": {"role": "assistant", "content": calls}},
            {"type": "user", "cwd": directory, "sessionId": "g", "message": {"role": "user", "content": [
                transcript_result or {"type": "tool_result", "tool_use_id": "t1", "content": "no result signal recorded"}]}},
        ]
        transcript.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        stop = json.dumps({"cwd": directory, "session_id": "g", "transcript_path": str(transcript)})
        status, output = run(["hook", "stop", "--from", "claude-code", "--offline", stop])
        self.assertEqual(status, 0)
        report_path = Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:g')}.json"
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
            "transcript failure without call record": dict(transcript_command="git clone https://github.com/gate/unrelated",
                                                           extra_calls=[], drop_call=True,
                                                           transcript_result={"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "fatal"}),
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
                log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:m')}.jsonl"
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
                (sessions / f"{session_file_stem('id:m')}.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
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
                (sessions / f"{session_file_stem('id:c')}.jsonl").write_text(json.dumps(good) + "\n" + tail + "\n", encoding="utf-8")
                self.assertEqual(run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "c"})]), (0, ""))
                report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:c')}.json").read_text(encoding="utf-8"))
                self.assertFalse(report["candidates"][0]["recommended"])

    def test_hook_success_never_overrides_a_transcript_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = "git clone https://github.com/reverse/conflict"
            transcript = Path(directory) / "t.jsonl"
            write_transcript(transcript, command, is_error=True, cwd=directory, session_id="s")
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": command}}
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertFalse(candidate["recommended"])
            self.assertEqual({e["confidence"] for e in candidate["evidence"]}, {"low"})

    def test_session_file_names_never_collide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def record(session: str | None, repository: str, **extra: object) -> None:
                payload = {"cwd": directory, "hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Bash",
                           "tool_input": {"command": f"git clone https://github.com/{repository}"}, **extra}
                if session is not None:
                    payload["session_id"] = session
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(payload)]), (0, ""))

            def stop(session: str | None, **extra: object) -> str:
                payload = {"cwd": directory, **extra}
                if session is not None:
                    payload["session_id"] = session
                status, output = run(["hook", "stop", "--from", "claude-code", "--offline", json.dumps(payload)])
                self.assertEqual(status, 0)
                return output

            first = "collision/a"
            record(first, "collide/slash")
            lookalikes = ["collision?a", session_file_stem(f"id:{first}"), "default", "id:collision/a", None]
            for other in lookalikes:
                self.assertEqual(stop(other), "", other)  # no log of its own, nothing to announce
            self.assertIn("collide/slash", json.loads(stop(first))["systemMessage"])

            # A payload without any session identifier is scoped by its transcript path, or not recorded at all.
            record(None, "collide/unscoped")
            self.assertFalse(any(p.name for p in (Path(directory) / ".agent-thanks" / "sessions").iterdir() if "unscoped" in p.name))
            transcript = Path(directory) / "t.jsonl"
            write_transcript(transcript, "git clone https://github.com/collide/by-transcript")
            record(None, "collide/by-transcript", transcript_path=str(transcript))
            self.assertIn("collide/by-transcript", json.loads(stop(None, transcript_path=str(transcript)))["systemMessage"])
            self.assertEqual(stop(None, transcript_path=str(transcript)), "")
            record("default", "collide/default")
            self.assertIn("collide/default", json.loads(stop("default"))["systemMessage"])
            announced = json.loads((Path(directory) / ".agent-thanks" / "announced.json").read_text(encoding="utf-8"))
            self.assertEqual(set(announced), {"id:collision/a", f"transcript:{transcript}", "id:default"})

    def test_hook_payloads_without_a_scope_record_and_announce_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = {"cwd": directory, "hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Bash",
                      "tool_input": {"command": "git clone https://github.com/scope/less"}}
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            sessions = Path(directory) / ".agent-thanks" / "sessions"
            self.assertFalse(sessions.exists() and any(sessions.iterdir()))
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", json.dumps({"cwd": directory})]), (0, ""))

    def test_a_contradictory_field_inside_a_hook_response_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "c1",
                      "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/nested/failure"},
                      "tool_response": {"exit_code": 0, "returncode": 128}}
            self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)]), (0, "{}\n"))
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", json.dumps({"cwd": directory, "session_id": "s"})]), (0, "{}\n"))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
            self.assertFalse(report["candidates"][0]["recommended"])
            self.assertIn("failed", report["candidates"][0]["evidence"][0]["detail"])

    def test_codex_hooks_answer_with_an_empty_object_when_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = json.dumps({"cwd": directory, "session_id": "c"})
            self.assertEqual(run(["hook", "stop", "--from", "codex", "--offline", payload]), (0, "{}\n"))
            record = json.dumps({"cwd": directory, "session_id": "c", "hook_event_name": "PostToolUse",
                                 "tool_name": "apply_patch", "tool_input": {"patch": "x"}})
            self.assertEqual(run(["hook", "record", "--from", "codex", record]), (0, "{}\n"))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(run(["hook", "stop", "--from", "codex", "{not json"]), (0, "{}\n"))
            self.assertIn("agent-thanks hook", stderr.getvalue())

    def test_a_result_only_failure_in_a_partial_transcript_demotes_the_hook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = "git clone https://github.com/partial/failure"
            transcript = Path(directory) / "t.jsonl"
            transcript.write_text(json.dumps({"type": "user", "cwd": directory, "sessionId": "s", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "fatal"}]}}) + "\n", encoding="utf-8")
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": command}}
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            payload = json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})
            self.assertEqual(run(["hook", "stop", "--from", "claude-code", "--offline", payload]), (0, ""))
            report = json.loads((Path(directory) / ".agent-thanks" / "reports" / f"{session_file_stem('id:s')}.json").read_text(encoding="utf-8"))
            candidate = report["candidates"][0]
            self.assertFalse(candidate["recommended"])
            self.assertTrue(all(e["confidence"] == "low" for e in candidate["evidence"]))


    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_state_files_are_private_even_under_a_permissive_umask(self) -> None:
        import stat

        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / ".agent-thanks"
                state.mkdir()
                os.chmod(state, 0o755)  # an old, permissive state directory gets tightened
                record = {"cwd": directory, "session_id": "p", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                          "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/private/log"}}
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
                run(["hook", "stop", "--from", "claude-code", "--offline", json.dumps({"cwd": directory, "session_id": "p"})])
                mode = lambda path: stat.S_IMODE(path.stat().st_mode)  # noqa: E731
                for folder in (state, state / "sessions", state / "reports"):
                    self.assertEqual(mode(folder), 0o700, folder)
                for file in [*(state / "sessions").iterdir(), *(state / "reports").iterdir(),
                             state / "report.json", state / "announced.json", state / ".gitignore"]:
                    self.assertEqual(mode(file), 0o600, file)
        finally:
            os.umask(previous)

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_state_paths_never_follow_symbolic_links(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            important = outside / "important.jsonl"
            important.write_text("keep me\n", encoding="utf-8")
            old = time.time() - 40 * 24 * 3600
            os.utime(important, (old, old))
            state = root / ".agent-thanks"
            state.mkdir()
            (state / "sessions").symlink_to(outside, target_is_directory=True)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status, output = run(["hook", "stop", "--from", "claude-code", "--offline", json.dumps({"cwd": str(root), "session_id": "s"})])
                record = {"cwd": str(root), "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                          "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/symlink/repo"}}
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)])[0], 0)
            self.assertEqual(status, 0)
            self.assertTrue(important.exists(), "pruning followed the symbolic link")
            self.assertEqual(important.read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual(list(outside.iterdir()), [important])
            self.assertIn("symbolic link", stderr.getvalue())

            # A symlinked file in a real directory is never truncated either.
            (state / "sessions").unlink()
            (state / "sessions").mkdir()
            target = outside / "target.json"
            target.write_text("precious", encoding="utf-8")
            (state / "report.json").symlink_to(target)
            transcript = root / "t.jsonl"
            write_transcript(transcript, "git clone https://github.com/symlink/report")
            with contextlib.redirect_stderr(io.StringIO()):
                status, _ = run(["hook", "stop", "--from", "claude-code", "--offline",
                                 json.dumps({"cwd": str(root), "session_id": "s2", "transcript_path": str(transcript)})])
            self.assertEqual(status, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "precious")

    def test_transcripts_of_one_session_scanned_together_merge_failure_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = "git clone https://github.com/session/parts"
            meta = {"type": "session_meta", "payload": {"id": "s", "cwd": directory}}
            call = {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                                                         "arguments": json.dumps({"command": command})}}
            part_a = Path(directory) / "part-a.jsonl"
            part_b = Path(directory) / "part-b.jsonl"
            part_a.write_text("".join(json.dumps(r) + "\n" for r in [
                meta, call, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                                                  "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}}]), encoding="utf-8")
            part_b.write_text("".join(json.dumps(r) + "\n" for r in [
                meta, call, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                                                  "output": json.dumps({"output": "fatal", "metadata": {"exit_code": 128}})}}]), encoding="utf-8")
            status, output = run(["scan", "--repo", directory, "--offline", "--session", str(part_a), "--session", str(part_b), "--output", "-"])
            self.assertEqual(status, 0)
            candidate = next(c for c in json.loads(output)["candidates"] if c["repository"] == "session/parts")
            self.assertFalse(candidate["recommended"])
            self.assertTrue(all(e["confidence"] == "low" for e in candidate["evidence"]))
            # A hook log that agrees with part A does not rescue it either.
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "c1",
                      "tool_name": "Bash", "tool_input": {"command": command},
                      "tool_response": json.dumps({"output": "", "metadata": {"exit_code": 0}})}
            self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)])[0], 0)
            log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl"
            status, output = run(["scan", "--repo", directory, "--offline", "--session", str(log), "--session", str(part_a), "--session", str(part_b), "--output", "-"])
            candidate = next(c for c in json.loads(output)["candidates"] if c["repository"] == "session/parts")
            self.assertFalse(candidate["recommended"])


class SessionIdentityTests(IsolatedEnvironmentTestCase):
    def codex_file(self, directory: str, name: str, session: str | None, call_id: str, repository: str,
                   exit_code: int | None, *, cwd: str | None = None, tail: str | None = None) -> Path:
        records = []
        if session is not None:
            records.append({"type": "session_meta", "payload": {"id": session, "cwd": cwd or directory}})
        records.append({"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": call_id,
                                                              "arguments": json.dumps({"command": f"git clone https://github.com/{repository}"})}})
        if exit_code is not None:
            records.append({"type": "response_item", "payload": {"type": "function_call_output", "call_id": call_id,
                                                                  "output": json.dumps({"output": "", "metadata": {"exit_code": exit_code}})}})
        path = Path(directory) / name
        text = "".join(json.dumps(r) + "\n" for r in records)
        if tail is not None:
            text += tail + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def scan(self, directory: str, *paths: Path) -> dict[str, tuple[bool, set[str]]]:
        argv = ["scan", "--repo", directory, "--offline", "--output", "-"]
        for path in paths:
            argv += ["--session", str(path)]
        status, output = run(argv)
        self.assertEqual(status, 0)
        return {c["repository"]: (c["recommended"], {e["confidence"] for e in c["evidence"]})
                for c in json.loads(output)["candidates"]}

    def test_calls_merge_only_inside_a_confirmed_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a = self.codex_file(directory, "a.jsonl", "session-A", "c1", "identity/repo-a", 0)
            b = self.codex_file(directory, "b.jsonl", "session-B", "c1", "identity/repo-b", 0)
            result = self.scan(directory, a, b)
            self.assertEqual(result["identity/repo-a"], (True, {"high"}))
            self.assertEqual(result["identity/repo-b"], (True, {"high"}))
            # The same recorded session split across files merges failure first.
            ok = self.codex_file(directory, "ok.jsonl", "session-C", "c1", "identity/split", 0)
            failed = self.codex_file(directory, "failed.jsonl", "session-C", "c1", "identity/split", 128)
            self.assertEqual(self.scan(directory, ok, failed)["identity/split"], (False, {"low"}))
            # Different recorded projects are different identities even with the same session id.
            here = self.codex_file(directory, "here.jsonl", "session-D", "c1", "identity/here", 0)
            there = self.codex_file(directory, "there.jsonl", "session-D", "c1", "identity/there", 128, cwd="/elsewhere")
            result = self.scan(directory, here, there)
            self.assertEqual(result["identity/here"], (True, {"high"}))
            self.assertEqual(result["identity/there"], (False, {"low"}))

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_hook_and_transcript_share_an_identity_across_a_path_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "real-project"
            root.mkdir()
            alias = parent / "project-alias"
            alias.symlink_to(root, target_is_directory=True)
            command = "git clone https://github.com/identity/aliased"
            record = {
                "cwd": str(alias),
                "session_id": "s",
                "hook_event_name": "PostToolUse",
                "tool_use_id": "t1",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
            self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)])[0], 0)
            transcript = root / "failed.jsonl"
            write_transcript(transcript, command, is_error=True, cwd=str(alias), session_id="s")
            log = root / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl"

            result = self.scan(str(root), log, transcript)
            self.assertEqual(result["identity/aliased"], (False, {"low"}))

    def test_files_without_an_identity_never_touch_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ok = self.codex_file(directory, "ok.jsonl", None, "c1", "anonymous/ok", 0)
            failed = self.codex_file(directory, "failed.jsonl", None, "c1", "anonymous/failed", 128)
            result = self.scan(directory, ok, failed)
            self.assertEqual(result["anonymous/ok"], (True, {"high"}))
            self.assertEqual(result["anonymous/failed"], (False, {"low"}))
            # A hook log links only to a transcript that records the same session and project.
            record = {"cwd": directory, "session_id": "s", "hook_event_name": "PostToolUse", "tool_use_id": "c1",
                      "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/anonymous/ok"},
                      "tool_response": json.dumps({"output": "", "metadata": {"exit_code": 128}})}
            self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)])[0], 0)
            log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl"
            unlinked = self.scan(directory, log, ok)
            self.assertEqual(unlinked["anonymous/ok"], (True, {"high", "low"}))  # the transcript stands alone; the hook entry is low
            linked_file = self.codex_file(directory, "linked.jsonl", "s", "c1", "anonymous/ok", 0)
            linked = self.scan(directory, log, linked_file)
            self.assertEqual(linked["anonymous/ok"], (False, {"low"}))

    def test_a_corrupted_file_never_promotes_another_file_of_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            call_only = self.codex_file(directory, "call-only.jsonl", "s", "c1", "corrupt/cross", None)
            corrupted_ok = self.codex_file(directory, "corrupted.jsonl", "s", "c1", "corrupt/cross", 0,
                                           tail='{"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "outp')
            result = self.scan(directory, call_only, corrupted_ok)
            self.assertEqual(result["corrupt/cross"], (False, {"low"}))
            status, output = run(["hook", "stop", "--from", "codex", "--offline",
                                  json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(corrupted_ok)})])
            self.assertEqual((status, output), (0, "{}\n"))
            # A corrupted file's parseable failure still demotes the clean file.
            clean_ok = self.codex_file(directory, "clean-ok.jsonl", "s2", "c1", "corrupt/failure", 0)
            corrupted_failed = self.codex_file(directory, "corrupted-failed.jsonl", "s2", "c1", "corrupt/failure", 128, tail='{"trunc')
            result = self.scan(directory, clean_ok, corrupted_failed)
            self.assertEqual(result["corrupt/failure"], (False, {"low"}))
            self.assertTrue(all("failed" in e["detail"] or "could not be parsed" in e["detail"]
                                for c in [json.loads(run(["scan", "--repo", directory, "--offline", "--output", "-",
                                                          "--session", str(clean_ok), "--session", str(corrupted_failed)])[1])]
                                for cand in c["candidates"] if cand["repository"] == "corrupt/failure" for e in cand["evidence"]))


class WholeRecordAndDuplicateKeyTests(IsolatedEnvironmentTestCase):
    def test_sibling_fields_of_a_codex_output_block_success(self) -> None:
        envelope = json.dumps({"output": "Cloning...", "metadata": {"exit_code": 0}})
        with tempfile.TemporaryDirectory() as directory:
            records = [
                {"type": "function_call", "name": "shell", "call_id": "m", "arguments": json.dumps({"command": "git clone https://github.com/sibling/metadata"})},
                {"type": "function_call_output", "call_id": "m", "output": envelope, "metadata": {"exit_code": 128}},
                {"type": "function_call", "name": "shell", "call_id": "e", "arguments": json.dumps({"command": "git clone https://github.com/sibling/error"})},
                {"type": "function_call_output", "call_id": "e", "output": envelope, "error": "fatal"},
                {"type": "custom_tool_call", "name": "exec_command", "call_id": "a", "input": "git clone https://github.com/sibling/array"},
                {"type": "custom_tool_call_output", "call_id": "a", "output": "Exit code: 0\nOutput:\n[{\"error\":\"fatal\"}]"},
                {"type": "function_call", "name": "shell", "call_id": "g", "arguments": json.dumps({"command": "git clone https://github.com/sibling/genuine"})},
                {"type": "function_call_output", "call_id": "g", "output": envelope},
            ]
            path = Path(directory) / "t.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            status, output = run(["scan", "--repo", directory, "--offline", "--session", str(path), "--output", "-"])
            by_repo = {c["repository"]: c for c in json.loads(output)["candidates"]}
        for name in ("metadata", "error", "array"):
            self.assertFalse(by_repo[f"sibling/{name}"]["recommended"], name)
        self.assertTrue(by_repo["sibling/genuine"]["recommended"])
        # The same envelopes through a Codex hook payload.
        with tempfile.TemporaryDirectory() as directory:
            for index, response in enumerate((
                {"output": envelope, "metadata": {"exit_code": 128}},
                {"output": envelope, "error": "fatal"},
                "Exit code: 0\nOutput:\n[{\"error\":\"fatal\"}]",
            )):
                record = {"cwd": directory, "session_id": "h", "hook_event_name": "PostToolUse", "tool_use_id": f"c{index}",
                          "tool_name": "Bash", "tool_input": {"command": f"git clone https://github.com/hooksibling/r{index}"},
                          "tool_response": response}
                self.assertEqual(run(["hook", "record", "--from", "codex", json.dumps(record)])[0], 0)
            log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:h')}.jsonl"
            entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["status"] for e in entries], ["error", "error", "error"])

    def test_duplicate_json_keys_never_resolve_to_a_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude = Path(directory) / "claude.jsonl"
            claude.write_text(
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git clone https://github.com/dup/claude"}}]}}) + "\n"
                + '{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": true, "is_error": false, "content": "x"}]}}\n',
                encoding="utf-8")
            codex = Path(directory) / "codex.jsonl"
            codex.write_text(
                json.dumps({"type": "function_call", "name": "shell", "call_id": "c1", "arguments": json.dumps({"command": "git clone https://github.com/dup/codex"})}) + "\n"
                + json.dumps({"type": "function_call_output", "call_id": "c1", "output": '{"output": "", "metadata": {"exit_code": 128, "exit_code": 0}}'}) + "\n",
                encoding="utf-8")
            for path, repository in ((claude, "dup/claude"), (codex, "dup/codex")):
                status, output = run(["scan", "--repo", directory, "--offline", "--session", str(path), "--output", "-"])
                candidate = next(c for c in json.loads(output)["candidates"] if c["repository"] == repository)
                self.assertFalse(candidate["recommended"], repository)
            # A hook log entry and a hook payload with duplicated keys are refused as well.
            sessions = Path(directory) / ".agent-thanks" / "sessions"
            sessions.mkdir(parents=True)
            entry = ('{"schema": "agent-thanks/hook-log/1", "agent": "claude-code", "event": "PostToolUse", "tool": "Bash", '
                     '"session_id": "d", "tool_call_id": "c1", "command": "git clone https://github.com/dup/log", '
                     '"status": "error", "status": "ok", "basis": "successful_post_tool_event"}')
            (sessions / f"{session_file_stem('id:d')}.jsonl").write_text(entry + "\n", encoding="utf-8")
            self.assertEqual(run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "d"})]), (0, ""))
            payload = ('{"cwd": ' + json.dumps(directory)
                       + ', "session_id": "p", "hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Bash", '
                       '"tool_input": {"command": "git clone https://github.com/dup/payload"}, "hook_event_name": "PreToolUse"}')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(run(["hook", "record", "--from", "claude-code", payload]), (0, ""))
            self.assertIn("duplicate key", stderr.getvalue())
            self.assertFalse((sessions / f"{session_file_stem('id:p')}.jsonl").exists())


class FailClosedStateTests(IsolatedEnvironmentTestCase):
    def test_private_file_io_does_not_open_a_directory_without_dir_fd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            real_open = os.open
            opened_directories: list[Path] = []

            def tracking_open(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
                candidate = Path(path) if not isinstance(path, int) else None
                if candidate is not None and candidate.is_dir():
                    opened_directories.append(candidate)
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            record = {
                "cwd": directory,
                "session_id": "fallback",
                "hook_event_name": "PostToolUse",
                "tool_use_id": "t1",
                "tool_name": "Bash",
                "tool_input": {"command": "git clone https://github.com/fallback/repo"},
            }
            with mock.patch.object(os, "supports_dir_fd", set()), mock.patch.object(os, "open", side_effect=tracking_open):
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))

            log = Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:fallback')}.jsonl"
            self.assertTrue(log.is_file())
            self.assertEqual(opened_directories, [])

    @unittest.skipIf(os.name == "nt", "POSIX special files")
    def test_a_fifo_at_the_log_path_is_refused_promptly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = Path(directory) / ".agent-thanks" / "sessions"
            sessions.mkdir(parents=True)
            os.mkfifo(sessions / f"{session_file_stem('id:f')}.jsonl")
            record = {"cwd": directory, "session_id": "f", "hook_event_name": "PostToolUse", "tool_use_id": "t1",
                      "tool_name": "Bash", "tool_input": {"command": "git clone https://github.com/fifo/repo"}}
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(run(["hook", "record", "--from", "claude-code", json.dumps(record)]), (0, ""))
            self.assertIn("not a regular file", stderr.getvalue())

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_a_symlinked_announcement_file_is_never_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "outside.json"
            outside.write_text(json.dumps({"id:s": ["symlink/repo"]}), encoding="utf-8")
            state = Path(directory) / ".agent-thanks"
            state.mkdir()
            (state / "announced.json").symlink_to(outside)
            transcript = Path(directory) / "t.jsonl"
            write_transcript(transcript, "git clone https://github.com/symlink/repo", cwd=directory, session_id="s")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status, output = run(["hook", "stop", "--from", "claude-code", "--offline",
                                      json.dumps({"cwd": directory, "session_id": "s", "transcript_path": str(transcript)})])
            self.assertEqual((status, output), (0, ""))
            self.assertIn("symbolic link", stderr.getvalue())
            self.assertEqual(json.loads(outside.read_text(encoding="utf-8")), {"id:s": ["symlink/repo"]})

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_existing_state_files_are_tightened(self) -> None:
        import stat as stat_module

        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / ".agent-thanks"
                (state / "sessions").mkdir(parents=True)
                (state / "reports").mkdir()
                loose = [state / ".gitignore", state / "report.json", state / "announced.json",
                         state / "sessions" / "old-0123456789abcdef.jsonl", state / "reports" / "old-0123456789abcdef.json"]
                for path in loose:
                    path.write_text("{}\n" if path.suffix == ".json" else "*\n", encoding="utf-8")
                    os.chmod(path, 0o644)
                for folder in (state, state / "sessions", state / "reports"):
                    os.chmod(folder, 0o755)
                run(["hook", "stop", "--from", "claude-code", "--offline", json.dumps({"cwd": directory, "session_id": "t"})])
                mode = lambda path: stat_module.S_IMODE(path.stat().st_mode)  # noqa: E731
                for folder in (state, state / "sessions", state / "reports"):
                    self.assertEqual(mode(folder), 0o700, folder)
                for path in loose:
                    self.assertEqual(mode(path), 0o600, path)
        finally:
            os.umask(previous)


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
                self.assertIn(f".agent-thanks/reports/{session_file_stem('id:thread-1')}.json", message)
                self.assertTrue((project / ".agent-thanks" / "reports" / f"{session_file_stem('id:thread-1')}.json").is_file())
                self.assertTrue((project / ".agent-thanks" / "report.json").is_file())

                other = Path(directory) / "other"
                other.mkdir()
                status, output = run(["hook", "stop", "--offline", "--from", "codex", json.dumps({"type": "agent-turn-complete", "cwd": str(other)})])
                self.assertEqual((status, output), (0, "{}\n"))  # Codex parses stdout as JSON
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
            report = json.loads((project / ".agent-thanks" / "reports" / f"{session_file_stem('id:thread-A')}.json").read_text(encoding="utf-8"))
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
            self.assertTrue((root / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s-one')}.jsonl").is_file())
            self.assertTrue((root / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s-two')}.jsonl").is_file())

            first = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-one"})])
            self.assertIn("shared/repo", json.loads(first[1])["systemMessage"])
            self.assertIn(f".agent-thanks/reports/{session_file_stem('id:s-one')}.json", json.loads(first[1])["systemMessage"])
            self.assertTrue((root / ".agent-thanks" / "reports" / f"{session_file_stem('id:s-one')}.json").is_file())
            again = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-one"})])
            self.assertEqual(again, (0, ""))
            second_session = run(["hook", "stop", "--offline", json.dumps({"cwd": directory, "session_id": "s-two"})])
            self.assertIn("shared/repo", json.loads(second_session[1])["systemMessage"])

            announced = json.loads((root / ".agent-thanks" / "announced.json").read_text(encoding="utf-8"))
            self.assertEqual(set(announced), {"id:s-one", "id:s-two"})

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
            entries = [json.loads(line) for line in (Path(directory) / ".agent-thanks" / "sessions" / f"{session_file_stem('id:s')}.jsonl").read_text(encoding="utf-8").splitlines()]
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
            write_transcript(transcripts / "s.jsonl", "git clone https://github.com/located/repo", cwd=str(project.resolve()))

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
