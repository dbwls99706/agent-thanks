from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Evidence:
    kind: str
    source: str
    detail: str
    confidence: str
    meaningful: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        return cls(
            kind=str(value["kind"]),
            source=str(value["source"]),
            detail=str(value["detail"]),
            confidence=str(value["confidence"]),
            meaningful=bool(value["meaningful"]),
        )


@dataclass
class Candidate:
    repository: str
    evidence: list[Evidence] = field(default_factory=list)

    def add_evidence(self, item: Evidence) -> None:
        if item not in self.evidence:
            self.evidence.append(item)

    @property
    def confidence(self) -> str:
        if not self.evidence:
            return "low"
        return max(self.evidence, key=lambda item: CONFIDENCE_RANK[item.confidence]).confidence

    @property
    def recommended(self) -> bool:
        return any(item.meaningful and item.confidence == "high" for item in self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "confidence": self.confidence,
            "recommended": self.recommended,
            "evidence": [asdict(item) for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Candidate":
        return cls(
            repository=str(value["repository"]),
            evidence=[Evidence.from_dict(item) for item in value.get("evidence", [])],
        )


@dataclass(frozen=True)
class UnresolvedDependency:
    ecosystem: str
    package: str
    source: str


@dataclass
class Report:
    root: str
    base: str | None
    candidates: list[Candidate]
    unresolved_dependencies: list[UnresolvedDependency] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "root": self.root,
            "base": self.base,
            "candidates": [item.to_dict() for item in self.candidates],
            "unresolved_dependencies": [
                asdict(item) for item in self.unresolved_dependencies
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def write(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Report":
        if value.get("schema_version") != 1:
            raise ValueError("Unsupported report schema version")
        return cls(
            root=str(value["root"]),
            base=value.get("base"),
            candidates=[Candidate.from_dict(item) for item in value.get("candidates", [])],
            unresolved_dependencies=[
                UnresolvedDependency(**item)
                for item in value.get("unresolved_dependencies", [])
            ],
            generated_at=str(value["generated_at"]),
            schema_version=1,
        )

    @classmethod
    def read(cls, path: Path) -> "Report":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def merge_candidates(items: Iterable[tuple[str, Evidence]]) -> list[Candidate]:
    candidates: dict[str, Candidate] = {}
    for repository, evidence in items:
        key = repository.casefold()
        candidate = candidates.setdefault(key, Candidate(repository=repository))
        candidate.add_evidence(evidence)
    return sorted(candidates.values(), key=lambda item: item.repository.casefold())
