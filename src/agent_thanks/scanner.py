from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

from .manifests import Dependency, is_supported_manifest, parse_manifest
from .models import Evidence, Report, UnresolvedDependency, merge_candidates
from .resolver import PackageRepositoryResolver
from .session import scan_session_evidence


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
        baseline_identities = self._baseline_dependency_identities()

        for path, before, after in self._manifest_snapshots():
            try:
                old_dependencies = parse_manifest(path, before) if before is not None else []
                new_dependencies = parse_manifest(path, after)
            except (ValueError, TypeError) as error:
                raise ScanError(f"Could not parse {path}: {error}") from error

            old_identities = (
                baseline_identities
                if baseline_identities is not None
                else {item.identity for item in old_dependencies}
            )
            for dependency in new_dependencies:
                if dependency.identity in old_identities:
                    continue
                repository = dependency.repository
                if repository is None and dependency.from_registry:
                    repository = self.resolver.resolve(dependency.ecosystem, dependency.name)
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

    def _baseline_dependency_identities(self) -> set[tuple[str, str]] | None:
        if not self._is_git_repository() or not self._base_exists():
            return None

        identities: set[tuple[str, str]] = set()
        paths = self._git(
            "ls-tree", "-r", "--name-only", self.base, "--"
        ).stdout.splitlines()
        for path in paths:
            relative = PurePosixPath(path).as_posix()
            if not is_supported_manifest(relative):
                continue
            result = self._git("show", f"{self.base}:{relative}", check=False)
            if result.returncode != 0:
                continue
            try:
                dependencies = parse_manifest(relative, result.stdout)
            except (ValueError, TypeError) as error:
                raise ScanError(f"Could not parse {relative} at {self.base}: {error}") from error
            identities.update(dependency.identity for dependency in dependencies)
        return identities

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
            "diff",
            "--name-status",
            "--find-renames",
            "--diff-filter=ACMR",
            self.base,
            "--",
        ).stdout.splitlines()
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard"
        ).stdout.splitlines()

        paths: dict[str, str | None] = {}
        for change in changed:
            fields = change.split("\t")
            status = fields[0]
            if status.startswith(("R", "C")) and len(fields) >= 3:
                before_path = PurePosixPath(fields[1]).as_posix()
                relative = PurePosixPath(fields[2]).as_posix()
            elif len(fields) >= 2:
                relative = PurePosixPath(fields[1]).as_posix()
                before_path = None if status.startswith("A") else relative
            else:
                continue
            if is_supported_manifest(relative):
                paths[relative] = before_path

        for path in untracked:
            relative = PurePosixPath(path).as_posix()
            if is_supported_manifest(relative):
                paths[relative] = None

        snapshots = []
        for relative, before_path in sorted(paths.items()):
            current_path = self.root / relative
            if not current_path.is_file():
                continue
            before = None
            if before_path is not None:
                before_result = self._git(
                    "show", f"{self.base}:{before_path}", check=False
                )
                if before_result.returncode == 0:
                    before = before_result.stdout
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
        return scan_session_evidence(text, source)
