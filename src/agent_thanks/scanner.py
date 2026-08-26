from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

from .manifests import Dependency, is_supported_manifest, parse_manifest
from .models import Evidence, Report, UnresolvedDependency, merge_candidates
from .repositories import extract_github_repositories
from .resolver import PackageRepositoryResolver


_IGNORED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "target",
    "dist",
    "build",
}
_MEANINGFUL_SESSION_MARKERS = (
    "git clone",
    "gh repo clone",
    "git submodule add",
    "pip install git+",
    "uv add git+",
    "cargo add --git",
    "npm install github:",
    "copied from",
    "adapted from",
    "used code from",
)


class ScanError(RuntimeError):
    pass


class ProjectScanner:
    def __init__(
        self,
        root: Path,
        *,
        base: str = "HEAD",
        resolver: PackageRepositoryResolver | None = None,
    ) -> None:
        self.root = root.resolve()
        self.base = base
        self.resolver = resolver or PackageRepositoryResolver()

    def scan(self, session_files: Iterable[Path] = ()) -> Report:
        evidence_items: list[tuple[str, Evidence]] = []
        unresolved: list[UnresolvedDependency] = []

        for path, before, after in self._manifest_snapshots():
            try:
                old_dependencies = parse_manifest(path, before) if before is not None else []
                new_dependencies = parse_manifest(path, after)
            except (ValueError, TypeError) as error:
                raise ScanError(f"Could not parse {path}: {error}") from error

            old_identities = {item.identity for item in old_dependencies}
            for dependency in new_dependencies:
                if dependency.identity in old_identities:
                    continue
                repository = dependency.repository or self.resolver.resolve(
                    dependency.ecosystem, dependency.name
                )
                if repository is None:
                    unresolved.append(
                        UnresolvedDependency(
                            ecosystem=dependency.ecosystem,
                            package=dependency.name,
                            source=path,
                        )
                    )
                    continue
                evidence_items.append(
                    (
                        repository,
                        Evidence(
                            kind="direct_dependency",
                            source=path,
                            detail=(
                                f"Added direct {dependency.ecosystem} dependency: "
                                f"{dependency.name}"
                            ),
                            confidence="high",
                            meaningful=True,
                        ),
                    )
                )

        for session_file in session_files:
            text = self._read_session(session_file)
            evidence_items.extend(self._scan_session(text, str(session_file)))

        candidates = merge_candidates(evidence_items)
        unresolved = sorted(
            set(unresolved), key=lambda item: (item.ecosystem, item.package.casefold(), item.source)
        )
        return Report(
            root=str(self.root),
            base=self.base if self._is_git_repository() and self._base_exists() else None,
            candidates=candidates,
            unresolved_dependencies=unresolved,
        )

    def _is_git_repository(self) -> bool:
        result = self._git("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _base_exists(self) -> bool:
        result = self._git("rev-parse", "--verify", f"{self.base}^{{commit}}", check=False)
        return result.returncode == 0

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def _manifest_snapshots(self) -> list[tuple[str, str | None, str]]:
        is_git = self._is_git_repository()
        has_base = self._base_exists() if is_git else False
        if is_git and not has_base and self.base != "HEAD":
            raise ScanError(f"Git base revision does not exist: {self.base}")
        if not is_git or not has_base:
            snapshots: list[tuple[str, str | None, str]] = []
            for path in self.root.rglob("*"):
                if (
                    path.is_file()
                    and not any(part in _IGNORED_PARTS for part in path.parts)
                    and is_supported_manifest(path.name)
                ):
                    relative = path.relative_to(self.root).as_posix()
                    snapshots.append((relative, None, path.read_text(encoding="utf-8")))
            return sorted(snapshots)

        changed = self._git(
            "diff", "--name-only", "--diff-filter=ACMR", self.base, "--"
        ).stdout.splitlines()
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard"
        ).stdout.splitlines()
        paths = sorted(
            {
                PurePosixPath(path).as_posix()
                for path in [*changed, *untracked]
                if is_supported_manifest(path)
            }
        )

        snapshots = []
        for relative in paths:
            current_path = self.root / relative
            if not current_path.is_file():
                continue
            before_result = self._git("show", f"{self.base}:{relative}", check=False)
            before = before_result.stdout if before_result.returncode == 0 else None
            snapshots.append((relative, before, current_path.read_text(encoding="utf-8")))
        return snapshots

    @staticmethod
    def _read_session(path: Path) -> str:
        if str(path) == "-":
            import sys

            return sys.stdin.read()
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _scan_session(text: str, source: str) -> list[tuple[str, Evidence]]:
        items: list[tuple[str, Evidence]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            repositories = extract_github_repositories(line)
            if not repositories:
                continue
            lowered = line.casefold()
            meaningful = any(marker in lowered for marker in _MEANINGFUL_SESSION_MARKERS)
            for repository in repositories:
                items.append(
                    (
                        repository,
                        Evidence(
                            kind="session_usage" if meaningful else "session_reference",
                            source=f"{source}:{line_number}",
                            detail=(
                                "Session shows a substantive repository-use command"
                                if meaningful
                                else "Repository was referenced in the session; verify actual reuse"
                            ),
                            confidence="high" if meaningful else "low",
                            meaningful=meaningful,
                        ),
                    )
                )
        return items
