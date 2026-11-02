"""Durable, account-bound Star receipts; this module never contacts GitHub.

The caller supplies private file readers and atomic writers and creates the
private receipts directory. Pending entries are unconfirmed and never qualify
for undo, including when a process stops between a request and its response.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Callable, Iterable
from uuid import uuid4

from .repositories import normalize_repository


SCHEMA = "agent-thanks/star-receipt/1"
STATES = frozenset({"pending", "starred", "failed", "undone"})
ERRORS = frozenset({"github", "network", "interrupted", "timeout", "unknown"})
PrivateWriter = Callable[[Path, str], None]
PrivateReader = Callable[[Path], str | None]
_ID = re.compile(r"[0-9a-f]{32}\Z")
_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")


class ReceiptError(ValueError):
    pass


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReceiptError("Receipt timestamp must be a string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReceiptError("Receipt timestamp is invalid") from error
    if result.tzinfo is None:
        raise ReceiptError("Receipt timestamp must include a timezone")
    return result


def _repository(value: object) -> str:
    if not isinstance(value, str) or len(value.split("/")) != 2:
        raise ReceiptError("Receipt repository must be owner/repo")
    normalized = normalize_repository(*value.split("/"))
    if normalized != value:
        raise ReceiptError("Receipt repository must be a canonical owner/repo")
    return value


def _receipt_path(directory: Path, receipt_id: str) -> Path:
    if not isinstance(receipt_id, str) or _ID.fullmatch(receipt_id) is None:
        raise ReceiptError("Receipt id must contain 32 lowercase hexadecimal characters")
    if directory.is_symlink():
        raise ReceiptError("Refusing a symbolic-link receipts directory")
    if directory.exists() and not directory.is_dir():
        raise ReceiptError("Receipts path is not a directory")
    return directory / f"{receipt_id}.json"


@dataclass(frozen=True)
class ReceiptEntry:
    repository: str
    state: str = "pending"
    error: str | None = None


@dataclass(frozen=True)
class Receipt:
    id: str
    account: str
    root: str
    created_at: str
    updated_at: str
    entries: tuple[ReceiptEntry, ...]

    @property
    def confirmed_repositories(self) -> list[str]:
        return [entry.repository for entry in self.entries if entry.state == "starred"]

    @property
    def pending_repositories(self) -> list[str]:
        return [entry.repository for entry in self.entries if entry.state == "pending"]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "id": self.id,
            "account": self.account,
            "root": self.root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [asdict(entry) for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, value: object) -> "Receipt":
        fields = {"schema", "id", "account", "root", "created_at", "updated_at", "entries"}
        if not isinstance(value, dict) or set(value) != fields or value.get("schema") != SCHEMA:
            raise ReceiptError("Invalid or unsupported Star receipt")
        receipt_id, account, root = value["id"], value["account"], value["root"]
        if not isinstance(receipt_id, str) or _ID.fullmatch(receipt_id) is None:
            raise ReceiptError("Invalid receipt id")
        if not isinstance(account, str) or _ACCOUNT.fullmatch(account) is None:
            raise ReceiptError("Invalid receipt account")
        if not isinstance(root, str) or not root.strip() or "\0" in root:
            raise ReceiptError("Invalid receipt project root")
        created, updated = _timestamp(value["created_at"]), _timestamp(value["updated_at"])
        if updated < created:
            raise ReceiptError("Receipt update precedes its creation")
        records = value["entries"]
        if not isinstance(records, list) or not records:
            raise ReceiptError("Receipt must contain repository entries")
        entries: list[ReceiptEntry] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"repository", "state", "error"}:
                raise ReceiptError("Invalid receipt entry")
            repository = _repository(record["repository"])
            state, error = record["state"], record["error"]
            if not isinstance(state, str) or state not in STATES:
                raise ReceiptError("Invalid receipt entry state")
            if error is not None and (not isinstance(error, str) or error not in ERRORS):
                raise ReceiptError("Invalid receipt error classification")
            if error is not None and state not in {"pending", "failed"}:
                raise ReceiptError("Confirmed entries cannot carry failure classifications")
            if repository.casefold() in seen:
                raise ReceiptError("Duplicate repository in receipt")
            seen.add(repository.casefold())
            entries.append(ReceiptEntry(repository, state, error))
        return cls(receipt_id, account, root, value["created_at"], value["updated_at"], tuple(entries))


def _write(directory: Path, receipt: Receipt, write: PrivateWriter) -> Receipt:
    validated = Receipt.from_dict(receipt.to_dict())
    write(_receipt_path(directory, validated.id), json.dumps(validated.to_dict(), indent=2) + "\n")
    return validated


def begin_receipt(
    directory: Path, account: str, root: Path | str, repositories: Iterable[str], *, write: PrivateWriter
) -> Receipt:
    """Persist the approved plan before the caller makes any Star request."""
    seen: set[str] = set()
    entries = []
    for item in repositories:
        repository = _repository(item)
        if repository.casefold() not in seen:
            seen.add(repository.casefold())
            entries.append(ReceiptEntry(repository))
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return _write(directory, Receipt(uuid4().hex, account, str(root), now, now, tuple(entries)), write)


def update_receipt(
    directory: Path, receipt: Receipt, repository: str, state: str, *, write: PrivateWriter,
    error: str | None = None,
) -> Receipt:
    """Persist a confirmed outcome; failed writes leave the input receipt intact."""
    if not isinstance(state, str) or state not in STATES:
        raise ReceiptError("Invalid receipt entry state")
    receipt = Receipt.from_dict(receipt.to_dict())
    repository = _repository(repository)
    current = next((entry for entry in receipt.entries if entry.repository.casefold() == repository.casefold()), None)
    if current is None:
        raise ReceiptError("Repository is not present in this receipt")
    allowed = {"pending": {"pending", "starred", "failed"}, "starred": {"starred", "undone"},
               "failed": {"failed"}, "undone": {"undone"}}
    if state not in allowed.get(current.state, set()):
        raise ReceiptError(f"Invalid receipt transition: {current.state} -> {state}")
    entries = tuple(replace(entry, state=state, error=error) if entry is current else entry for entry in receipt.entries)
    now = max(datetime.now(timezone.utc), _timestamp(receipt.updated_at)).isoformat(timespec="microseconds")
    return _write(directory, replace(receipt, entries=entries, updated_at=now), write)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("Duplicate key in Star receipt")
        result[key] = value
    return result


def read_receipt(directory: Path, receipt_id: str, *, read: PrivateReader) -> Receipt:
    path = _receipt_path(directory, receipt_id)
    text = read(path)
    if text is None:
        raise ReceiptError(f"Receipt does not exist: {receipt_id}")
    try:
        receipt = Receipt.from_dict(json.loads(text, object_pairs_hook=_unique_pairs))
    except (ValueError, TypeError, RecursionError) as error:
        raise ReceiptError(f"Cannot read receipt {receipt_id}: {error}") from error
    if receipt.id != receipt_id:
        raise ReceiptError("Receipt id does not match its filename")
    return receipt


def list_receipts(directory: Path, *, read: PrivateReader) -> list[Receipt]:
    """Read history without creating directories; corrupted history is explicit."""
    _receipt_path(directory, "0" * 32)
    receipts = [read_receipt(directory, path.stem, read=read) for path in directory.glob("*.json")]
    return sorted(receipts, key=lambda item: (_timestamp(item.created_at), item.id), reverse=True)


def latest_receipt(directory: Path, *, read: PrivateReader) -> Receipt | None:
    return next((receipt for receipt in list_receipts(directory, read=read) if receipt.confirmed_repositories), None)
