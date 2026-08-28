from __future__ import annotations

from pathlib import Path, PureWindowsPath
import re
from urllib.parse import quote

from .models import Candidate, Evidence, Report, UnresolvedDependency


_LINE_SUFFIX = re.compile(r"^(.*):(\d+)$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def render_markdown(report: Report, *, include_low_confidence: bool = False) -> str:
    """Render a shareable evidence summary without exposing absolute local paths."""

    verified = [candidate for candidate in report.candidates if candidate.recommended]
    references = [candidate for candidate in report.candidates if not candidate.recommended]

    lines = [
        "# Open-source use evidence",
        "",
        "Evidence observed during one coding task. Review this list before sharing it.",
        "",
        "## Verified use",
        "",
    ]
    _append_candidates(lines, verified, report.root)

    if include_low_confidence:
        lines.extend(["", "## References to review", ""])
        _append_candidates(lines, references, report.root)

    if report.unresolved_dependencies:
        lines.extend(["", "## Unresolved dependencies", ""])
        for item in report.unresolved_dependencies:
            lines.append(_render_unresolved(item, report.root))

    return "\n".join(lines).rstrip() + "\n"


def _append_candidates(lines: list[str], candidates: list[Candidate], root: str) -> None:
    if not candidates:
        lines.append("None.")
        return

    for index, candidate in enumerate(candidates):
        if index:
            lines.append("")
        label = _escape_markdown(candidate.repository)
        url_repository = quote(candidate.repository, safe="/.-_")
        lines.append(f"### [{label}](https://github.com/{url_repository})")
        lines.append("")
        for evidence in candidate.evidence:
            lines.append(_render_evidence(evidence, root))


def _render_evidence(evidence: Evidence, root: str) -> str:
    kind = {
        "direct_dependency": "Direct dependency",
        "session_usage": "Session use",
        "session_reference": "Session reference",
    }.get(evidence.kind, evidence.kind.replace("_", " ").strip().title() or "Evidence")
    detail = _escape_markdown(evidence.detail)
    source = _escape_code(_safe_source(evidence.source, root))
    return f"- **{_escape_markdown(kind)}** — {detail} (`{source}`)"


def _render_unresolved(item: UnresolvedDependency, root: str) -> str:
    ecosystem = _escape_code(item.ecosystem)
    package = _escape_code(item.package)
    source = _escape_code(_safe_source(item.source, root))
    return f"- `{ecosystem}:{package}` (`{source}`)"


def _safe_source(source: str, root: str) -> str:
    """Keep useful filenames and line numbers while removing absolute directories."""

    match = _LINE_SUFFIX.match(source)
    raw_path, line = (match.group(1), match.group(2)) if match else (source, None)
    normalized = raw_path.replace("\\", "/")

    try:
        root_path = Path(root).expanduser().resolve()
        source_path = Path(raw_path).expanduser()
        if source_path.is_absolute():
            try:
                label = source_path.resolve().relative_to(root_path).as_posix()
            except ValueError:
                label = source_path.name
        elif _WINDOWS_ABSOLUTE.match(raw_path):
            label = PureWindowsPath(raw_path).name
        else:
            if normalized == ".." or normalized.startswith("../"):
                label = Path(normalized).name
            elif normalized.startswith("./"):
                label = normalized[2:]
            else:
                label = normalized
            label = label or Path(normalized).name
    except (OSError, RuntimeError, ValueError):
        label = Path(normalized).name or "source"

    return f"{label}:{line}" if line else label


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\n", " ").replace("\r", " ").replace("\\", "\\\\")
    for character in "`*_{}[]<>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _escape_code(value: str) -> str:
    return value.replace("`", "'").replace("\n", " ").replace("\r", " ")
