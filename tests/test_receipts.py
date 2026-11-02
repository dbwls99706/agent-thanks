import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from agent_thanks.receipts import (
    Receipt, ReceiptError, begin_receipt, latest_receipt, list_receipts,
    read_receipt, update_receipt,
)


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root / "receipts"
        self.directory.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def read(path: Path) -> str | None:
        return path.read_text(encoding="utf-8") if path.exists() else None

    def begin(self, *repositories: str, account: str = "octocat"):
        return begin_receipt(self.directory, account, self.root, repositories, write=self.write)

    def test_plan_is_persisted_before_any_outcome_and_repositories_are_deduplicated(self) -> None:
        receipt = self.begin("owner/one", "OWNER/ONE", "owner/two")
        restored = read_receipt(self.directory, receipt.id, read=self.read)
        self.assertEqual(restored, receipt)
        self.assertEqual(restored.account, "octocat")
        self.assertEqual(restored.root, str(self.root))
        self.assertEqual(restored.confirmed_repositories, [])
        self.assertEqual(restored.pending_repositories, ["owner/one", "owner/two"])
        self.assertEqual(json.loads(json.dumps(receipt.to_dict())), receipt.to_dict())

    def test_confirmed_and_interrupted_requests_remain_distinct_on_disk(self) -> None:
        planned = self.begin("owner/one", "owner/two")
        confirmed = update_receipt(self.directory, planned, "OWNER/ONE", "starred", write=self.write)
        interrupted = update_receipt(
            self.directory, confirmed, "owner/two", "pending", error="interrupted", write=self.write
        )
        restored = read_receipt(self.directory, planned.id, read=self.read)
        self.assertEqual(restored, interrupted)
        self.assertEqual(planned.confirmed_repositories, [])
        self.assertEqual(restored.confirmed_repositories, ["owner/one"])
        self.assertEqual(restored.pending_repositories, ["owner/two"])
        self.assertEqual(restored.entries[1].error, "interrupted")
        self.assertGreaterEqual(restored.updated_at, restored.created_at)

    def test_failed_writes_do_not_claim_an_unpersisted_confirmation(self) -> None:
        planned = self.begin("owner/one")
        failed_writer = Mock(side_effect=OSError("disk full"))
        with self.assertRaisesRegex(OSError, "disk full"):
            update_receipt(self.directory, planned, "owner/one", "starred", write=failed_writer)
        self.assertEqual(planned.confirmed_repositories, [])
        self.assertEqual(read_receipt(self.directory, planned.id, read=self.read), planned)
        with self.assertRaisesRegex(OSError, "disk full"):
            begin_receipt(self.directory, "octocat", self.root, ["owner/new"], write=failed_writer)
        self.assertEqual(len(list_receipts(self.directory, read=self.read)), 1)

    def test_latest_undo_candidate_excludes_unconfirmed_failed_and_undone_entries(self) -> None:
        older = self.begin("owner/one", "owner/two")
        older = update_receipt(self.directory, older, "owner/one", "starred", write=self.write)
        older = update_receipt(self.directory, older, "owner/two", "failed", error="github", write=self.write)
        pending = self.begin("owner/new")
        self.assertEqual([item.id for item in list_receipts(self.directory, read=self.read)], [pending.id, older.id])
        self.assertEqual(latest_receipt(self.directory, read=self.read), older)
        undone = update_receipt(self.directory, older, "owner/one", "undone", write=self.write)
        self.assertEqual(undone.confirmed_repositories, [])
        self.assertIsNone(latest_receipt(self.directory, read=self.read))

    def test_invalid_state_transitions_cannot_create_confirmation_or_undo(self) -> None:
        pending = self.begin("owner/one")
        for state in ([], {}, None, "unknown"):
            with self.subTest(state=state), self.assertRaises(ReceiptError):
                update_receipt(self.directory, pending, "owner/one", state, write=self.write)
        with self.assertRaises(ReceiptError):
            update_receipt(self.directory, pending, "owner/one", "undone", write=self.write)
        failed = update_receipt(self.directory, pending, "owner/one", "failed", write=self.write)
        with self.assertRaises(ReceiptError):
            update_receipt(self.directory, failed, "owner/one", "starred", write=self.write)
        with self.assertRaises(ReceiptError):
            update_receipt(self.directory, failed, "owner/missing", "failed", write=self.write)

    def test_reading_empty_history_never_creates_a_directory(self) -> None:
        missing = self.root / "missing" / "receipts"
        reader = Mock(side_effect=AssertionError("no files should be read"))
        self.assertEqual(list_receipts(missing, read=reader), [])
        self.assertIsNone(latest_receipt(missing, read=reader))
        self.assertFalse(missing.exists())
        reader.assert_not_called()

    def test_receipt_ids_cannot_escape_the_private_directory(self) -> None:
        reader = Mock()
        for receipt_id in ("../outside", "/tmp/other", "a/b", "a\\b", "F" * 32, "a" * 31, "a" * 32 + "\n"):
            with self.subTest(receipt_id=receipt_id), self.assertRaises(ReceiptError):
                read_receipt(self.directory, receipt_id, read=reader)
        reader.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_symbolic_link_history_directory_is_refused_before_reading(self) -> None:
        alias = self.root / "linked"
        alias.symlink_to(self.directory, target_is_directory=True)
        reader = Mock()
        with self.assertRaisesRegex(ReceiptError, "symbolic-link"):
            list_receipts(alias, read=reader)
        reader.assert_not_called()

    def test_corrupt_history_and_filename_mismatches_are_explicit(self) -> None:
        receipt = self.begin("owner/one")
        path = self.directory / f"{receipt.id}.json"
        path.write_text('{"invalid":', encoding="utf-8")
        with self.assertRaisesRegex(ReceiptError, "Cannot read receipt"):
            list_receipts(self.directory, read=self.read)
        path.unlink()
        other_id = "a" * 32
        self.write(self.directory / f"{other_id}.json", json.dumps(receipt.to_dict()))
        with self.assertRaisesRegex(ReceiptError, "does not match its filename"):
            read_receipt(self.directory, other_id, read=self.read)

    def test_duplicate_keys_are_rejected_at_root_and_entry_levels(self) -> None:
        receipt = self.begin("owner/one")
        text = json.dumps(receipt.to_dict())
        for duplicate in (
            text.replace('"account": "octocat"', '"account": "wrong", "account": "octocat"'),
            text.replace('"state": "pending"', '"state": "starred", "state": "pending"'),
        ):
            with self.subTest(text=duplicate):
                self.write(self.directory / f"{receipt.id}.json", duplicate)
                with self.assertRaisesRegex(ReceiptError, "Duplicate key"):
                    read_receipt(self.directory, receipt.id, read=self.read)

    def test_malformed_fields_and_sensitive_error_text_are_rejected(self) -> None:
        receipt = self.begin("owner/one")
        source = receipt.to_dict()
        changes = (
            {"schema": "unknown"}, {"id": []}, {"account": "bad/account"},
            {"root": None}, {"created_at": "2026-01-01T00:00:00"},
            {"updated_at": "2000-01-01T00:00:00+00:00"}, {"entries": []},
            {"entries": [{"repository": "https://github.com/owner/one", "state": "starred", "error": None}]},
            {"entries": [{"repository": "owner/one", "state": [], "error": None}]},
            {"entries": [source["entries"][0], source["entries"][0]]},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ReceiptError):
                Receipt.from_dict({**source, **change})
        with self.assertRaisesRegex(ReceiptError, "error classification"):
            update_receipt(self.directory, receipt, "owner/one", "failed", error="token=secret", write=self.write)
        self.assertEqual(read_receipt(self.directory, receipt.id, read=self.read), receipt)

    def test_project_paths_can_preserve_valid_filename_characters(self) -> None:
        receipt = begin_receipt(
            self.directory, "octocat", str(self.root / "한글\tproject\nname"), ["owner/one"], write=self.write
        )
        self.assertEqual(read_receipt(self.directory, receipt.id, read=self.read), receipt)


if __name__ == "__main__":
    unittest.main()
