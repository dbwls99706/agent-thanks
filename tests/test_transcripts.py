import json
from pathlib import Path
import tempfile
import unittest

from agent_thanks.models import Evidence
from agent_thanks.transcripts import (
    RESULT_ERROR,
    RESULT_OK,
    RESULT_UNKNOWN,
    encode_project_path,
    is_transcript,
    iter_transcript_records,
    locate_transcript,
    result_status,
    same_path,
    scan_transcript_evidence,
    transcript_metadata,
)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def evidence_by_repository(items: list[tuple[str, Evidence]]) -> dict[str, Evidence]:
    return {repository: evidence for repository, evidence in items}


CLAUDE_CODE_RECORDS = [
    {"type": "user", "message": {"role": "user", "content": "Please run git clone https://github.com/prompt/only"}},
    {
        "type": "assistant",
        "cwd": "/work/project",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "git clone https://github.com/hidden/thought"},
                {
                    "type": "text",
                    "text": "I will clone it first:\n\n```bash\ngit clone https://github.com/fenced/example\n```\n",
                },
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "git clone https://github.com/real/used.git /tmp/used", "description": "Clone"},
                },
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "is_error": False,
                    "content": "Cloning into '/tmp/used'...\ngit clone https://github.com/output/noise",
                }
            ],
        },
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Adapted from https://github.com/prose/provenance"},
                {"type": "text", "text": "See also https://github.com/prose/reference for background."},
                {"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"file_path": "https://github.com/not/a-command"}},
            ],
        },
    },
]


class TranscriptRecordTests(unittest.TestCase):
    def test_claude_code_transcript_separates_actions_from_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            write_jsonl(path, CLAUDE_CODE_RECORDS)
            self.assertTrue(is_transcript(path))

            records = list(iter_transcript_records(path))
            self.assertIn((2, "command", "git clone https://github.com/real/used.git /tmp/used", "ok"), records)
            kinds = {(kind, text) for _, kind, text, _ in records}
            self.assertNotIn(("command", "git clone https://github.com/prompt/only"), kinds)

            evidence = evidence_by_repository(scan_transcript_evidence(path, "session.jsonl"))
            self.assertEqual(evidence["real/used"].confidence, "high")
            self.assertEqual(evidence["real/used"].source, "session.jsonl:2")
            self.assertEqual(evidence["prose/provenance"].confidence, "high")
            self.assertIn("adapted", evidence["prose/provenance"].detail)
            self.assertEqual(evidence["fenced/example"].confidence, "low")
            self.assertEqual(evidence["prose/reference"].confidence, "low")
            for ignored in ("prompt/only", "hidden/thought", "output/noise"):
                self.assertNotIn(ignored, evidence)
            self.assertEqual(evidence["not/a-command"].confidence, "low")

    def test_codex_rollout_commands_are_unwrapped_from_shell_argv(self) -> None:
        records = [
            {"type": "session_meta", "payload": {"cwd": "/work/project"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": json.dumps(
                        {"command": ["bash", "-lc", "git clone https://github.com/codex/target"], "workdir": "/work"}
                    ),
                    "call_id": "call_1",
                },
            },
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_1", "output": json.dumps({"output": "git clone https://github.com/codex/output", "metadata": {"exit_code": 0}})}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done. Reference: https://github.com/codex/mention"}]},
            },
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "call_2", "arguments": json.dumps({"command": ["git", "clone", "https://github.com/codex/argv"]})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_2", "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": "git clone https://github.com/codex/unpaired"})}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "rollout.jsonl"))
        self.assertEqual(evidence["codex/target"].confidence, "high")
        self.assertEqual(evidence["codex/argv"].confidence, "high")
        self.assertEqual(evidence["codex/mention"].confidence, "low")
        self.assertEqual(evidence["codex/unpaired"].confidence, "low")
        self.assertNotIn("codex/output", evidence)

    def test_gemini_style_json_document(self) -> None:
        document = {
            "sessionId": "abc",
            "messages": [
                {"type": "user", "content": "git clone https://github.com/user/typed"},
                {
                    "type": "gemini",
                    "content": "Cloning the reference implementation from https://github.com/gemini/mention.",
                    "toolCalls": [{"name": "run_shell_command", "args": {"command": "git clone https://github.com/gemini/target"}}],
                },
                {"role": "model", "parts": [{"functionCall": {"name": "run_shell_command", "args": {"command": "pip install git+https://github.com/gemini/pip-target"}}}]},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            evidence = evidence_by_repository(scan_transcript_evidence(path, "session.json"))
        self.assertEqual(evidence["gemini/target"].confidence, "low")
        self.assertIn("recorded no result", evidence["gemini/target"].detail)
        self.assertEqual(evidence["gemini/target"].source, "session.json:2")
        self.assertEqual(evidence["gemini/pip-target"].confidence, "low")
        self.assertEqual(evidence["gemini/mention"].confidence, "low")
        self.assertNotIn("user/typed", evidence)

    def test_plain_text_logs_are_not_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            path.write_text("git clone https://github.com/plain/log\n", encoding="utf-8")
            self.assertFalse(is_transcript(path))


class ResultStatusTests(unittest.TestCase):
    def test_only_explicit_success_counts_as_ok(self) -> None:
        cases = {
            "fatal: repository not found": RESULT_UNKNOWN,
            json.dumps({"error": "fatal"}): RESULT_ERROR,
            json.dumps({"status": "failed"}): RESULT_ERROR,
            json.dumps({"status": "success"}): RESULT_UNKNOWN,
            json.dumps({"output": "done", "metadata": {"exit_code": 0}}): RESULT_OK,
            "Exit code: 0\nOutput:\nCloning...": RESULT_OK,
            "Exit code: 128\nOutput:\nfatal": RESULT_ERROR,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(result_status(value), expected)
        self.assertEqual(result_status({"stdout": "", "stderr": ""}), RESULT_UNKNOWN)
        self.assertEqual(result_status({"is_error": False}), RESULT_OK)
        self.assertEqual(result_status({"is_error": True}), RESULT_ERROR)
        self.assertEqual(result_status([{"type": "text", "text": "Cloning..."}]), RESULT_UNKNOWN)
        self.assertEqual(result_status(None), RESULT_UNKNOWN)

    def test_failure_signals_win_and_success_needs_an_exact_signal(self) -> None:
        self.assertEqual(result_status({"type": "tool_result", "tool_use_id": "t", "content": "fatal: repository not found"}), RESULT_UNKNOWN)
        self.assertEqual(result_status({"is_error": False, "exit_code": 128}), RESULT_ERROR)
        self.assertEqual(result_status({"is_error": True, "exit_code": 0}), RESULT_ERROR)
        self.assertNotEqual(result_status({"is_error": "false"}), RESULT_OK)
        self.assertEqual(result_status({"is_error": "true"}), RESULT_ERROR)
        self.assertEqual(result_status({"status": "success", "error": "boom"}), RESULT_ERROR)
        self.assertEqual(result_status("Exit code: 0\nlater: Exit code: 1"), RESULT_ERROR)
        self.assertEqual(result_status({"is_error": False, "content": "Exit code 128"}), RESULT_ERROR)

    def test_program_output_can_never_fake_success(self) -> None:
        self.assertEqual(result_status({"content": "Exit code: 0"}), RESULT_UNKNOWN)
        self.assertEqual(result_status({"stdout": json.dumps({"status": "success"})}), RESULT_UNKNOWN)
        self.assertEqual(result_status({"stdout": json.dumps({"is_error": False})}), RESULT_UNKNOWN)
        self.assertEqual(result_status(json.dumps({"output": "Exit code: 0", "metadata": {}})), RESULT_UNKNOWN)
        self.assertEqual(result_status("Cloning...\nExit code: 0"), RESULT_UNKNOWN)
        self.assertEqual(result_status("Exit code: 0\nCloning..."), RESULT_OK)
        self.assertEqual(result_status(json.dumps({"output": "done", "metadata": {"exit_code": 0}})), RESULT_OK)
        self.assertEqual(result_status({"status": "success"}), RESULT_OK)
        self.assertEqual(result_status(json.dumps({"error": "fatal"})), RESULT_ERROR)

    def test_exit_codes_count_only_inside_the_result_header(self) -> None:
        header = "Chunk ID: ab12\nWall time: 0.0512 seconds\n{status}\nOriginal token count: 12\nOutput:\n"
        self.assertEqual(result_status(header.format(status="Process exited with code 0") + "Cloning..."), RESULT_OK)
        self.assertEqual(result_status(header.format(status="Process exited with code 128") + "fatal"), RESULT_ERROR)
        self.assertEqual(result_status(header.format(status="Process exited with code -1073741502")), RESULT_ERROR)
        self.assertEqual(result_status(header.format(status="Process running with session ID 7") + "Cloning..."), RESULT_UNKNOWN)
        self.assertEqual(result_status(header.format(status="Process exited with code 0") + "Exit code: 1"), RESULT_ERROR)
        self.assertEqual(result_status("Exit code: 0\nWall time: 0.1 seconds\nOutput:\nExit code: 0"), RESULT_OK)
        self.assertEqual(result_status("Output:\nProcess exited with code 0"), RESULT_UNKNOWN)
        self.assertEqual(result_status("Cloning...\nProcess exited with code 0\nOutput:\n"), RESULT_UNKNOWN)
        self.assertEqual(result_status("\n".join(["x: y"] * 9 + ["Process exited with code 0", "Output:"])), RESULT_UNKNOWN)
        self.assertEqual(result_status("Cloning...\nOutput:\nProcess exited with code 0"), RESULT_UNKNOWN)

    def test_codex_exec_command_header_results_are_judged(self) -> None:
        header = "Chunk ID: c0de\nWall time: 1.2 seconds\nProcess exited with code {code}\nOutput:\n"
        records = [
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec_command", "call_id": "e1",
                                                  "input": "git clone https://github.com/codex/exec-ok"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "e1",
                                                  "output": header.format(code=0) + "Cloning into 'exec-ok'..."}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "e2",
                                                  "arguments": json.dumps({"cmd": "git clone https://github.com/codex/exec-failed"})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "e2",
                                                  "output": header.format(code=128) + "fatal: repository not found"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "e3",
                                                  "arguments": json.dumps({"cmd": "git clone https://github.com/codex/exec-faked"})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "e3",
                                                  "output": "Chunk ID: c0de\nWall time: 9.0 seconds\nProcess running with session ID 4\nOutput:\nProcess exited with code 0"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "rollout.jsonl"))
        self.assertEqual(evidence["codex/exec-ok"].confidence, "high")
        self.assertEqual(evidence["codex/exec-failed"].confidence, "low")
        self.assertIn("failed", evidence["codex/exec-failed"].detail)
        self.assertEqual(evidence["codex/exec-faked"].confidence, "low")
        self.assertIn("cannot be judged", evidence["codex/exec-faked"].detail)

    def test_duplicate_results_for_one_call_combine_failure_first(self) -> None:
        records = [
            {"type": "function_call", "name": "shell", "call_id": "d1", "arguments": json.dumps({"command": "git clone https://github.com/dup/repo"})},
            {"type": "function_call_output", "call_id": "d1", "output": json.dumps({"output": "", "metadata": {"exit_code": 128}})},
            {"type": "function_call_output", "call_id": "d1", "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "r.jsonl"))
        self.assertEqual(evidence["dup/repo"].confidence, "low")
        self.assertIn("failed", evidence["dup/repo"].detail)

    def test_user_role_tool_calls_and_nested_results_are_ignored(self) -> None:
        records = [
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_use", "id": "fake", "name": "Bash", "input": {"command": "git clone https://github.com/user/forged"}},
                {"type": "tool_result", "tool_use_id": "fake", "is_error": False, "content": "..."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "real", "name": "Bash", "input": {"command": "git clone https://github.com/nested/result"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "real", "content": json.dumps({"type": "tool_result", "tool_use_id": "real", "is_error": False})}]}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "t.jsonl"))
        self.assertNotIn("user/forged", evidence)
        self.assertEqual(evidence["nested/result"].confidence, "low")

    def test_hook_log_statuses_override_transcript_results(self) -> None:
        records = [
            {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": json.dumps({"command": "git clone https://github.com/hook/failed"})},
            {"type": "function_call_output", "call_id": "c1", "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})},
            {"type": "function_call", "name": "shell", "call_id": "c2", "arguments": json.dumps({"command": "git clone https://github.com/hook/agreed"})},
            {"type": "function_call_output", "call_id": "c2", "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})},
            {"type": "function_call", "name": "shell", "call_id": "c3", "arguments": json.dumps({"command": "git clone https://github.com/hook/unseen"})},
            {"type": "function_call_output", "call_id": "c3", "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(
                scan_transcript_evidence(path, "r.jsonl", authoritative={"c1": "error", "c2": "ok"})
            )
        self.assertEqual(evidence["hook/failed"].confidence, "low")
        self.assertEqual(evidence["hook/agreed"].confidence, "high")
        self.assertEqual(evidence["hook/unseen"].confidence, "low")
        self.assertIn("hook log did not confirm", evidence["hook/unseen"].detail)

    def test_codex_custom_tool_calls_are_recognized(self) -> None:
        records = [
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec_command", "call_id": "x1",
                                                  "input": "git clone https://github.com/codex/custom"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "x1",
                                                  "output": json.dumps({"output": "", "metadata": {"exit_code": 0}})}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "x2",
                                                  "input": json.dumps({"cmd": "git clone https://github.com/codex/custom-json"})}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "x2", "output": "Exit code: 1"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "rollout.jsonl"))
        self.assertEqual(evidence["codex/custom"].confidence, "high")
        self.assertEqual(evidence["codex/custom-json"].confidence, "low")

    def test_unjudgeable_results_keep_commands_as_references(self) -> None:
        records = [
            {"type": "function_call", "name": "shell", "call_id": "u1", "arguments": json.dumps({"command": "git clone https://github.com/unknown/result"})},
            {"type": "function_call_output", "call_id": "u1", "output": "fatal: repository not found"},
            {"type": "function_call", "name": "shell", "call_id": "u2", "arguments": json.dumps({"command": "git clone https://github.com/errored/result"})},
            {"type": "function_call_output", "call_id": "u2", "output": json.dumps({"error": "fatal"})},
            {"type": "function_call", "name": "shell", "call_id": "u3", "arguments": json.dumps({"command": "git clone https://github.com/status/failed"})},
            {"type": "function_call_output", "call_id": "u3", "output": json.dumps({"status": "failed"})},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "r.jsonl"))
        for repository in ("unknown/result", "errored/result", "status/failed"):
            with self.subTest(repository=repository):
                self.assertEqual(evidence[repository].confidence, "low")
                self.assertFalse(evidence[repository].meaningful)

    def test_transcripts_without_any_results_never_promote_commands(self) -> None:
        document = {"messages": [{"type": "gemini", "content": "", "toolCalls": [
            {"name": "run_shell_command", "args": {"command": "git clone https://github.com/untracked/format"}}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            evidence = evidence_by_repository(scan_transcript_evidence(path, "chat.json"))
        self.assertEqual(evidence["untracked/format"].confidence, "low")
        self.assertFalse(evidence["untracked/format"].meaningful)
        self.assertIn("recorded no result", evidence["untracked/format"].detail)


class SuccessAttributionTests(unittest.TestCase):
    """The reviewer's table: only a direct success recorded for the command itself counts."""

    def classify(self, command: str, is_error: bool | None) -> Evidence:
        records = [{"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}}]
        if is_error is not None:
            records.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error, "content": "..."}]}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "t.jsonl"))
        return evidence["acme/repo"]

    def test_success_attribution_table(self) -> None:
        cases = [
            ("git clone https://github.com/acme/repo", False, True, "completed successfully"),
            ("git clone https://github.com/acme/repo", True, False, "failed"),
            ("git clone https://github.com/acme/repo", None, False, "recorded no result"),
            ("git clone https://github.com/acme/repo || true", False, False, "compound"),
            ("git clone https://github.com/acme/repo; echo ok", False, False, "compound"),
            ("git clone https://github.com/acme/repo | tee clone.log", False, False, "compound"),
            ("git clone https://github.com/acme/repo &", False, False, "compound"),
            ("cd /tmp && git clone https://github.com/acme/repo", False, True, "completed successfully"),
            ("mkdir -p v && cd v && git clone https://github.com/acme/repo", False, True, "completed successfully"),
            ("git clone https://github.com/acme/repo && git submodule add https://github.com/acme/other x", False, True, "completed successfully"),
            ("git clone https://github.com/acme/repo && make", False, False, "compound"),
            ("builtin exit 0 && git clone https://github.com/acme/repo", False, False, "compound"),
            ("eval 'exit 0' && git clone https://github.com/acme/repo", False, False, "compound"),
            ("source ./env.sh && git clone https://github.com/acme/repo", False, False, "compound"),
            ("unknown && git clone https://github.com/acme/repo", False, False, "compound"),
            ("set -n && git clone https://github.com/acme/repo", False, False, "compound"),
            ("PATH=/tmp/fake && git clone https://github.com/acme/repo", False, False, "compound"),
            ("export GIT_DIR=/x && git clone https://github.com/acme/repo", False, False, "compound"),
            ("git clone https://github.com/acme/repo\ntrue", False, False, "multi-line"),
            ("git clone \\\n  https://github.com/acme/repo", False, True, "completed successfully"),
        ]
        for command, is_error, expected_high, expected_detail in cases:
            with self.subTest(command=command, is_error=is_error):
                evidence = self.classify(command, is_error)
                self.assertEqual(evidence.meaningful, expected_high)
                self.assertEqual(evidence.confidence, "high" if expected_high else "low")
                self.assertIn(expected_detail, evidence.detail)


class ToolResultTests(unittest.TestCase):
    def test_failed_and_unfinished_shell_calls_stay_references(self) -> None:
        def call(identifier: str, command: str) -> dict:
            return {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": identifier, "name": "Bash", "input": {"command": command}}]}}

        def result(identifier: str, is_error: bool) -> dict:
            return {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": identifier, "is_error": is_error, "content": "..."}]}}

        records = [
            call("t1", "git clone https://github.com/failed/clone"), result("t1", True),
            call("t2", "git clone https://github.com/worked/clone"), result("t2", False),
            call("t3", "git clone https://github.com/unfinished/clone"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "s.jsonl"))
        self.assertEqual(evidence["failed/clone"].confidence, "low")
        self.assertFalse(evidence["failed/clone"].meaningful)
        self.assertEqual(evidence["worked/clone"].confidence, "high")
        self.assertEqual(evidence["unfinished/clone"].confidence, "low")

    def test_codex_exit_codes_gate_commands(self) -> None:
        def call(identifier: str, command: str) -> dict:
            return {"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                    "call_id": identifier, "arguments": json.dumps({"command": ["bash", "-lc", command]})}}

        def output(identifier: str, body: object) -> dict:
            return {"type": "response_item", "payload": {"type": "function_call_output", "call_id": identifier, "output": body}}

        records = [
            call("c1", "git clone https://github.com/exit/nonzero"), output("c1", json.dumps({"output": "fatal", "metadata": {"exit_code": 128}})),
            call("c2", "git clone https://github.com/exit/zero"), output("c2", json.dumps({"output": "ok", "metadata": {"exit_code": 0}})),
            call("c3", "git clone https://github.com/exit/textual"), output("c3", "Exit code: 1\nOutput:\nfatal: repository not found"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "rollout.jsonl"))
        self.assertEqual(evidence["exit/nonzero"].confidence, "low")
        self.assertEqual(evidence["exit/zero"].confidence, "high")
        self.assertEqual(evidence["exit/textual"].confidence, "low")

    def test_unknown_tools_never_produce_commands(self) -> None:
        records = [
            {"type": "function_call", "name": "execute_query", "call_id": "q1", "arguments": json.dumps({"command": "git clone https://github.com/false/positive"})},
            {"type": "function_call_output", "call_id": "q1", "output": "ok"},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "w1", "name": "WebFetch", "input": {"url": "https://github.com/fetched/page"}}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "is_error": False, "content": "..."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "git clone https://github.com/allowlisted/tool"}}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b1", "is_error": False, "content": "..."}]}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.jsonl"
            write_jsonl(path, records)
            evidence = evidence_by_repository(scan_transcript_evidence(path, "mixed.jsonl"))
        self.assertEqual(evidence["false/positive"].confidence, "low")
        self.assertEqual(evidence["fetched/page"].confidence, "low")
        self.assertEqual(evidence["allowlisted/tool"].confidence, "high")

class TranscriptLocationTests(unittest.TestCase):
    def test_project_path_encoding_matches_agent_layout(self) -> None:
        self.assertEqual(encode_project_path("/home/user/agent-thanks"), "-home-user-agent-thanks")
        self.assertEqual(encode_project_path(r"C:\Users\me\proj"), "C--Users-me-proj")

    def test_locates_newest_transcript_for_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cwd = Path("/work/project")
            project_dir = home / ".claude" / "projects" / "-work-project"
            project_dir.mkdir(parents=True)
            older = project_dir / "older.jsonl"
            newer = project_dir / "newer.jsonl"
            older.write_text("{}\n", encoding="utf-8")
            newer.write_text("{}\n", encoding="utf-8")
            import os

            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            self.assertEqual(locate_transcript("claude-code", cwd, home, {}), newer)
            self.assertIsNone(locate_transcript("claude-code", Path("/elsewhere"), home, {}))
            self.assertEqual(locate_transcript("claude-code", cwd, home, {}, session_id="older"), older)
            self.assertIsNone(locate_transcript("claude-code", cwd, home, {}, session_id="missing"))

            codex_dir = home / ".codex" / "sessions" / "2026" / "09" / "02"
            codex_dir.mkdir(parents=True)
            other = codex_dir / "rollout-other.jsonl"
            mine = codex_dir / "rollout-mine.jsonl"
            other.write_text(json.dumps({"cwd": "/elsewhere"}) + "\n", encoding="utf-8")
            mine.write_text(json.dumps({"cwd": "/work/project"}) + "\n", encoding="utf-8")
            os.utime(other, (3, 3))
            os.utime(mine, (2, 2))
            self.assertEqual(locate_transcript("codex", cwd, home, {}), mine)
            self.assertIsNone(locate_transcript("codex", Path("/nowhere"), home, {}))

            gemini_dir = home / ".gemini" / "tmp" / "hash" / "chats"
            gemini_dir.mkdir(parents=True)
            (gemini_dir / "session-1.json").write_text(json.dumps({"cwd": "/elsewhere"}), encoding="utf-8")
            self.assertIsNone(locate_transcript("gemini", cwd, home, {}))
            (gemini_dir / "session-2.json").write_text(json.dumps({"projectRoot": "/work/project"}), encoding="utf-8")
            self.assertEqual(locate_transcript("gemini", cwd, home, {}), gemini_dir / "session-2.json")

            with self.assertRaises(ValueError):
                locate_transcript("unknown-agent", cwd, home, {})

    def test_codex_lookup_requires_exact_project_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / ".codex" / "sessions" / "2026" / "09" / "02"
            sessions.mkdir(parents=True)

            def rollout(name: str, cwd: str, thread: str, age: int) -> Path:
                path = sessions / f"rollout-2026-09-02T10-00-00-{name}.jsonl"
                write_jsonl(path, [{"type": "session_meta", "payload": {"id": thread, "cwd": cwd}}])
                import os

                os.utime(path, (age, age))
                return path

            other = rollout("other", "/work/project-other", "thread-other", 9)
            thread_a = rollout("thread-A", "/work/proj", "thread-A", 5)
            thread_b = rollout("thread-B", "/work/proj/", "thread-B", 7)

            self.assertIsNone(locate_transcript("codex", Path("/work/proj"), home, {}, session_id="thread-none"))
            self.assertEqual(locate_transcript("codex", Path("/work/proj"), home, {}, session_id="thread-A"), thread_a)
            self.assertEqual(locate_transcript("codex", Path("/work/proj"), home, {}), thread_b)
            self.assertIsNone(locate_transcript("codex", Path("/work/pro"), home, {}))
            self.assertEqual(locate_transcript("codex", Path("/work/project-other"), home, {}), other)

    def test_metadata_and_path_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            write_jsonl(path, [
                {"type": "summary", "summary": "x"},
                {"type": "assistant", "cwd": "/work/project", "sessionId": "abc-123", "message": {"role": "assistant", "content": []}},
            ])
            metadata = transcript_metadata(path)
        self.assertEqual((metadata.cwd, metadata.session_id), ("/work/project", "abc-123"))
        self.assertTrue(same_path("/work/project/", Path("/work/project")))
        self.assertFalse(same_path("/work/project-other", Path("/work/proj")))
        self.assertFalse(same_path("/work/proj", Path("/work/project-other")))

    def test_agent_home_overrides_are_honored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            codex_home = Path(directory) / "codex-home"
            claude_dir = Path(directory) / "claude-config"
            cwd = Path("/work/project")
            (codex_home / "sessions").mkdir(parents=True)
            (codex_home / "sessions" / "rollout-1.jsonl").write_text(json.dumps({"cwd": "/work/project"}) + "\n", encoding="utf-8")
            (claude_dir / "projects" / "-work-project").mkdir(parents=True)
            (claude_dir / "projects" / "-work-project" / "s.jsonl").write_text("{}\n", encoding="utf-8")
            environ = {"CODEX_HOME": str(codex_home), "CLAUDE_CONFIG_DIR": str(claude_dir)}
            self.assertEqual(locate_transcript("codex", cwd, home, environ), codex_home / "sessions" / "rollout-1.jsonl")
            self.assertEqual(locate_transcript("claude-code", cwd, home, environ), claude_dir / "projects" / "-work-project" / "s.jsonl")
            self.assertIsNone(locate_transcript("codex", cwd, home, {}))
            self.assertIsNone(locate_transcript("claude-code", cwd, home, {}))


if __name__ == "__main__":
    unittest.main()
