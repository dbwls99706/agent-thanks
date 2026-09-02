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
        if not isinstance(value, dict):
            raise ValueError("Malformed report: evidence must be an object")
        kind, source, detail = value.get("kind"), value.get("source"), value.get("detail")
        confidence, meaningful = value.get("confidence"), value.get("meaningful")
        if not all(isinstance(item, str) for item in (kind, source, detail)):
            raise ValueError(
                "Malformed report: evidence kind, source, and detail must be strings"
            )
        if not isinstance(confidence, str) or confidence not in CONFIDENCE_RANK:
            raise ValueError(f"Malformed report: unknown evidence confidence {confidence!r}")
        if not isinstance(meaningful, bool):
            raise ValueError("Malformed report: evidence meaningful must be true or false")
        return cls(
            kind=kind,
            source=source,
            detail=detail,
            confidence=confidence,
            meaningful=meaningful,
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
        if not isinstance(value, dict):
            raise ValueError("Malformed report: candidate must be an object")
        repository = value.get("repository")
        if not isinstance(repository, str) or not repository:
            raise ValueError(
                "Malformed report: candidate repository must be a non-empty string"
            )
        evidence = value.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("Malformed report: candidate evidence must be a list")
        return cls(
            repository=repository,
            evidence=[Evidence.from_dict(item) for item in evidence],
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
        if not isinstance(value, dict):
            raise ValueError("Malformed report: expected a JSON object")
        if value.get("schema_version") != 1:
            raise ValueError("Unsupported report schema version")
        try:
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
        except KeyError as error:
            raise ValueError(f"Malformed report: missing field {error}") from error
        except (TypeError, AttributeError) as error:
            raise ValueError(f"Malformed report: {error}") from error

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
