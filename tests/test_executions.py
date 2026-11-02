from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.executions import (
    EXECUTION_LOG_SCHEMA,
    execute,
    is_execution_log,
    scan_execution_log_evidence,
    scan_execution_logs_evidence,
)


class ExecutionCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.log = self.root / "executions.jsonl"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_captures_start_before_running_and_preserves_stdio_and_argv(self) -> None:
        argv = ["git", "clone", "https://github.com/used/project", "folder with spaces"]
        def run(arguments, **kwargs):
            self.assertEqual(arguments, argv)
            self.assertEqual(kwargs, {"cwd": str(self.root.resolve()), "shell": False, "check": False})
            self.assertEqual([record["event"] for record in self.records()], ["started"])
            return subprocess.CompletedProcess(arguments, 0)
        with patch("agent_thanks.executions.subprocess.run", side_effect=run):
            self.assertEqual(execute(argv, cwd=self.root, log_path=self.log, write_record=self.write), 0)
        records = self.records()
        self.assertEqual([record["event"] for record in records], ["started", "finished"])
        self.assertEqual(records[0]["execution_id"], records[1]["execution_id"])
        self.assertEqual(records[1]["exit_code"], 0)
        self.assertNotIn("stdout", records[1])
        self.assertNotIn("environment", records[1])
        evidence = scan_execution_log_evidence(self.log, root=self.root)
        self.assertEqual([repository for repository, _ in evidence], ["used/project"])
        self.assertTrue(evidence[0][1].meaningful)
        self.assertEqual(evidence[0][1].kind, "execution_usage")

    def test_real_child_receives_literal_arguments_and_returns_its_exit_code(self) -> None:
        arguments = ["a b", "&&", "https://github.com/reference/only"]
        command = [sys.executable, "-c", (
            "import json,pathlib,sys;"
            "pathlib.Path('received.json').write_text(json.dumps(sys.argv[1:]));"
            "sys.exit(7)"
        ), *arguments]
        self.assertEqual(execute(command, cwd=self.root, log_path=self.log, write_record=self.write), 7)
        self.assertEqual(json.loads((self.root / "received.json").read_text()), arguments)
        self.assertEqual(self.records()[-1]["exit_code"], 7)
        self.assertFalse(any(evidence.meaningful for _, evidence in scan_execution_log_evidence(self.log, root=self.root)))

    def test_signal_status_is_preserved_instead_of_changed_to_success(self) -> None:
        with patch("agent_thanks.executions.subprocess.run", return_value=subprocess.CompletedProcess(["git"], -15)):
            self.assertEqual(execute(["git", "clone", "https://github.com/used/project"], cwd=self.root,
                                     log_path=self.log, write_record=self.write), -15)
        self.assertEqual(self.records()[-1]["exit_code"], -15)
        self.assertFalse(scan_execution_log_evidence(self.log, root=self.root)[0][1].meaningful)

    def test_launch_failures_are_recorded_and_never_verify_use(self) -> None:
        for error, status in ((FileNotFoundError("missing executable"), 127), (PermissionError("not executable"), 126)):
            with self.subTest(status=status):
                self.log.unlink(missing_ok=True)
                with patch("agent_thanks.executions.subprocess.run", side_effect=error), redirect_stderr(StringIO()):
                    result = execute(["git", "clone", "https://github.com/used/project"], cwd=self.root,
                                     log_path=self.log, write_record=self.write)
                self.assertEqual(result, status)
                self.assertEqual(self.records()[-1]["event"], "launch_failed")
                self.assertFalse(scan_execution_log_evidence(self.log, root=self.root)[0][1].meaningful)

    def test_interrupt_keeps_a_nonpromoting_record_and_propagates(self) -> None:
        with patch("agent_thanks.executions.subprocess.run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute(["git", "clone", "https://github.com/used/project"], cwd=self.root,
                        log_path=self.log, write_record=self.write)
        self.assertEqual(self.records()[-1]["event"], "interrupted")
        self.assertIsNone(self.records()[-1]["exit_code"])
        self.assertFalse(scan_execution_log_evidence(self.log, root=self.root)[0][1].meaningful)

    def test_cannot_run_when_initial_private_write_fails(self) -> None:
        def unwritable(path, text):
            raise PermissionError("cannot persist execution")
        with patch("agent_thanks.executions.subprocess.run") as run, self.assertRaises(PermissionError):
            execute(["git", "clone", "https://github.com/used/project"], cwd=self.root,
                    log_path=self.log, write_record=unwritable)
        run.assert_not_called()

    def test_interrupt_is_not_replaced_by_a_failed_final_write(self) -> None:
        def write(path, text):
            if json.loads(text)["event"] != "started":
                raise PermissionError("storage disappeared")
            self.write(path, text)
        with patch("agent_thanks.executions.subprocess.run", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            execute(["git", "clone", "https://github.com/used/project"], cwd=self.root,
                    log_path=self.log, write_record=write)
        self.assertEqual(len(self.records()), 1)
        self.assertFalse(scan_execution_log_evidence(self.log, root=self.root)[0][1].meaningful)

    def test_invalid_argv_or_missing_cwd_never_launches_a_command(self) -> None:
        for argv in ([], "git clone URL", [""], ["git", None], ["git", "null\0byte"]):
            with self.subTest(argv=argv), patch("agent_thanks.executions.subprocess.run") as run, self.assertRaises(ValueError):
                execute(argv, cwd=self.root, log_path=self.log, write_record=self.write)
            run.assert_not_called()
        with patch("agent_thanks.executions.subprocess.run") as run, self.assertRaises(ValueError):
            execute(["git"], cwd=self.root / "missing", log_path=self.log, write_record=self.write)
        run.assert_not_called()
        self.assertFalse(self.log.exists())


class ExecutionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.log = self.root / "executions.jsonl"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def pair(self, argv: list[str] | None = None, *, status: int = 0, identity: str = "a" * 32) -> list[dict]:
        common = {
            "schema": EXECUTION_LOG_SCHEMA,
            "execution_id": identity,
            "cwd": str(self.root.resolve()),
            "argv": argv or ["git", "clone", "https://github.com/used/project"],
        }
        return [
            dict(common, event="started", recorded_at="2026-11-02T01:00:00+00:00"),
            dict(common, event="finished", recorded_at="2026-11-02T01:00:01+00:00", exit_code=status),
        ]

    def scan(self, records: list[dict], *, suffix: str = "") -> list:
        self.log.write_text("".join(json.dumps(record) + "\n" for record in records) + suffix, encoding="utf-8")
        return scan_execution_log_evidence(self.log, "captured.jsonl", root=self.root)

    def test_only_exact_project_receives_execution_evidence(self) -> None:
        records = self.pair()
        for record in records:
            record["cwd"] = str(self.root / "other")
        self.assertEqual(self.scan(records), [])

    def test_failure_missing_and_unanchored_results_never_verify_use(self) -> None:
        cases = (self.pair(status=1), self.pair()[:1], self.pair()[1:], list(reversed(self.pair())))
        for records in cases:
            with self.subTest(events=[record["event"] for record in records]):
                evidence = self.scan(records)
                self.assertTrue(evidence)
                self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_duplicate_or_conflicting_records_demote_every_affected_command(self) -> None:
        start, finish = self.pair()
        changed = dict(finish, argv=["git", "clone", "https://github.com/other/project"])
        cases = ([start, start, finish], [start, finish, finish],
                 [start, finish, dict(finish, exit_code=1)], [start, changed],
                 [start, dict(finish, recorded_at="2026-11-02T00:59:59+00:00")])
        for records in cases:
            with self.subTest(records=records):
                self.assertFalse(any(item.meaningful for _, item in self.scan(records)))

    def test_malformed_schema_fields_cannot_speak_for_valid_execution(self) -> None:
        for update in (
            {"exit_code": False}, {"exit_code": "0"}, {"event": []},
            {"recorded_at": "2026-11-02T01:00:01"}, {"success": False},
            {"schema": "agent-thanks/execution-log/2"}, {"execution_id": "invalid"},
            {"event": "interrupted", "exit_code": 0},
        ):
            with self.subTest(update=update):
                records = self.pair()
                records[1].update(update)
                evidence = self.scan(records)
                self.assertTrue(is_execution_log(self.log))
                self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_damaged_or_mixed_file_demotes_other_successful_commands(self) -> None:
        for suffix in ('{"schema":', '{"schema":"other-schema","argv":[]}\n',
                       '{"schema":"agent-thanks/execution-log/1","exit_code":1,"exit_code":0}\n'):
            with self.subTest(suffix=suffix):
                evidence = self.scan(self.pair(), suffix=suffix)
                self.assertTrue(is_execution_log(self.log))
                self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_no_provenance_or_shell_argument_can_fabricate_successful_use(self) -> None:
        commands = (
            ["echo", "Adapted from https://github.com/used/project"],
            ["echo", "&&", "git", "clone", "https://github.com/used/project"],
            ["echo", ";", "git", "clone", "https://github.com/used/project"],
            ["sh", "-c", "git clone https://github.com/used/project || true"],
            ["python", "-c", "print('https://github.com/used/project')"],
        )
        for command in commands:
            with self.subTest(command=command):
                evidence = self.scan(self.pair(command))
                self.assertTrue(evidence)
                self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_success_and_failure_with_different_ids_are_not_merged(self) -> None:
        records = self.pair(identity="a" * 32) + self.pair(status=1, identity="b" * 32)
        evidence = self.scan(records)
        self.assertEqual([item.meaningful for _, item in evidence], [True, False])
        self.assertEqual(evidence[0][1].source, "captured.jsonl:1")

    def test_literal_separator_as_a_clone_destination_is_an_ordinary_operand(self) -> None:
        evidence = self.scan(self.pair(["git", "clone", "https://github.com/used/project", "&&"]))
        self.assertEqual([repository for repository, item in evidence if item.meaningful], ["used/project"])

    def test_log_detection_and_invalid_utf8_never_promote_evidence(self) -> None:
        self.assertFalse(is_execution_log(self.log))
        self.log.write_text("ordinary log\n", encoding="utf-8")
        self.assertFalse(is_execution_log(self.log))
        self.log.write_bytes((json.dumps(self.pair()[0]) + "\n").encode() + b"\xff")
        self.assertTrue(is_execution_log(self.log))
        self.assertEqual(scan_execution_log_evidence(self.log, root=self.root), [])

    def test_schema_mentions_in_agent_prose_do_not_reclassify_a_transcript(self) -> None:
        for content in (
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": EXECUTION_LOG_SCHEMA}}),
            "Read the schema agent-thanks/execution-log/1 before running this tool.",
        ):
            with self.subTest(content=content):
                self.log.write_text(content + "\n", encoding="utf-8")
                self.assertFalse(is_execution_log(self.log))
        self.log.write_text('{"schema":"other","schema":"agent-thanks/execution-log/1"}\n', encoding="utf-8")
        self.assertTrue(is_execution_log(self.log))
        self.log.write_text('{"schema":"agent-thanks/execution-log/1",', encoding="utf-8")
        self.assertTrue(is_execution_log(self.log))

    def test_cross_file_conflicts_demote_every_copy_with_original_sources(self) -> None:
        other = self.root / "copy.jsonl"
        self.scan(self.pair())
        other.write_text("".join(json.dumps(record) + "\n" for record in self.pair(status=1)), encoding="utf-8")
        evidence = scan_execution_logs_evidence([(self.log, "one.jsonl"), (other, "copy.jsonl")], root=self.root)
        self.assertEqual({item.source for _, item in evidence}, {"one.jsonl:1", "copy.jsonl:1"})
        self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_different_execution_ids_and_projects_never_share_outcomes(self) -> None:
        other = self.root / "other.jsonl"
        self.scan(self.pair(identity="a" * 32))
        records = self.pair(status=1, identity="b" * 32)
        foreign = self.pair(status=1, identity="a" * 32)
        for record in foreign:
            record["cwd"] = str(self.root / "foreign")
        other.write_text("".join(json.dumps(record) + "\n" for record in records + foreign), encoding="utf-8")
        evidence = scan_execution_logs_evidence([(self.log, "one"), (other, "other")], root=self.root)
        self.assertEqual([(item.source, item.meaningful) for _, item in evidence], [("one:1", True), ("other:1", False)])

    def test_split_start_and_finish_cannot_assume_cross_file_order(self) -> None:
        other = self.root / "finish.jsonl"
        start, finish = self.pair()
        self.scan([start])
        other.write_text(json.dumps(finish) + "\n", encoding="utf-8")
        evidence = scan_execution_logs_evidence([(self.log, "start"), (other, "finish")], root=self.root)
        self.assertEqual({item.source for _, item in evidence}, {"start:1", "finish:1"})
        self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_malformed_known_id_taints_copies_without_affecting_other_ids(self) -> None:
        other = self.root / "broken.jsonl"
        self.scan(self.pair(identity="a" * 32))
        for identity, expected in (("a" * 32, False), ("b" * 32, True)):
            with self.subTest(identity=identity):
                broken = self.pair(identity=identity)[1]
                broken["exit_code"] = "0"
                other.write_text(json.dumps(broken) + "\n", encoding="utf-8")
                evidence = scan_execution_logs_evidence([(self.log, "one"), (other, "broken")], root=self.root)
                self.assertEqual(next(item.meaningful for _, item in evidence if item.source == "one:1"), expected)

    def test_duplicate_exit_keys_in_a_copy_cannot_leave_success_authoritative(self) -> None:
        self.scan(self.pair())
        other = self.root / "broken.jsonl"
        finish = json.dumps(self.pair()[1])
        other.write_text(finish[:-1] + ', "exit_code": 1}\n', encoding="utf-8")
        evidence = scan_execution_logs_evidence([(self.log, "one"), (other, "broken")], root=self.root)
        self.assertFalse(any(item.meaningful for _, item in evidence))

    def test_a_damaged_file_does_not_demote_an_unrelated_clean_file(self) -> None:
        self.scan(self.pair(identity="a" * 32), suffix="{broken\n")
        other = self.root / "clean.jsonl"
        other.write_text("".join(json.dumps(record) + "\n" for record in self.pair(identity="b" * 32)), encoding="utf-8")
        evidence = scan_execution_logs_evidence([(self.log, "broken"), (other, "clean")], root=self.root)
        self.assertEqual([(item.source, item.meaningful) for _, item in evidence], [("broken:1", False), ("clean:1", True)])


if __name__ == "__main__":
    unittest.main()
