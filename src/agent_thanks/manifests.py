from __future__ import annotations

import configparser
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .repositories import repository_from_metadata_url


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    name: str
    repository: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return self.ecosystem, self.name.casefold().replace("_", "-")


_REQUIREMENTS_FILE = re.compile(r"requirements(?:[-_.].+)?\.txt$", re.IGNORECASE)
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")


def is_supported_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in {
        ".gitmodules",
        "go.mod",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
    } or bool(
        _REQUIREMENTS_FILE.fullmatch(name)
    )


def parse_manifest(path: str, text: str) -> list[Dependency]:
    name = PurePosixPath(path).name
    if _REQUIREMENTS_FILE.fullmatch(name):
        return _parse_requirements(text)
    if name == "pyproject.toml":
        return _parse_pyproject(text)
    if name == "package.json":
        return _parse_package_json(text)
    if name == "Cargo.toml":
        return _parse_cargo(text)
    if name == "go.mod":
        return _parse_go_mod(text)
    if name == ".gitmodules":
        return _parse_gitmodules(text)
    return []


def _dependency_from_spec(ecosystem: str, name: str, spec: object) -> Dependency:
    repository = repository_from_metadata_url(spec)
    if isinstance(spec, dict):
        repository = repository or repository_from_metadata_url(spec.get("git"))
    return Dependency(ecosystem=ecosystem, name=name, repository=repository)


def _parse_requirement(value: str) -> Dependency | None:
    value = value.strip()
    if not value or value.startswith(("#", "-r", "--requirement", "-c", "--constraint")):
        return None
    value = value.split(" ;", 1)[0].strip()
    repository = repository_from_metadata_url(value)

    egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.-]+)", value)
    if egg_match:
        return Dependency("pypi", egg_match.group(1), repository)

    direct_match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*@\s*(.+)", value)
    if direct_match:
        return Dependency(
            "pypi",
            direct_match.group(1),
            repository or repository_from_metadata_url(direct_match.group(2)),
        )

    match = _PACKAGE_NAME.match(value)
    if not match:
        return None
    return Dependency("pypi", match.group(0), repository)


def _parse_requirements(text: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    continued = ""
    for raw_line in text.splitlines():
        line = continued + raw_line.strip()
        if line.endswith("\\"):
            continued = line[:-1]
            continue
        continued = ""
        dependency = _parse_requirement(line)
        if dependency is not None:
            dependencies.append(dependency)
    return _unique(dependencies)


def _parse_pyproject(text: str) -> list[Dependency]:
    data = tomllib.loads(text)
    dependencies: list[Dependency] = []

    project = data.get("project", {})
    for value in project.get("dependencies", []):
        dependency = _parse_requirement(str(value))
        if dependency is not None:
            dependencies.append(dependency)
    for group in project.get("optional-dependencies", {}).values():
        for value in group:
            dependency = _parse_requirement(str(value))
            if dependency is not None:
                dependencies.append(dependency)

    poetry = data.get("tool", {}).get("poetry", {})
    for section in ("dependencies", "dev-dependencies"):
        for name, spec in poetry.get(section, {}).items():
            if name.casefold() != "python":
                dependencies.append(_dependency_from_spec("pypi", name, spec))
    for group in poetry.get("group", {}).values():
        for name, spec in group.get("dependencies", {}).items():
            dependencies.append(_dependency_from_spec("pypi", name, spec))

    for group in data.get("dependency-groups", {}).values():
        if isinstance(group, list):
            for value in group:
                if isinstance(value, str):
                    dependency = _parse_requirement(value)
                    if dependency is not None:
                        dependencies.append(dependency)

    return _unique(dependencies)


def _parse_package_json(text: str) -> list[Dependency]:
    data = json.loads(text)
    dependencies: list[Dependency] = []
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        for name, spec in data.get(section, {}).items():
            dependencies.append(_dependency_from_spec("npm", name, spec))
    return _unique(dependencies)


def _parse_cargo(text: str) -> list[Dependency]:
    data = tomllib.loads(text)
    dependencies: list[Dependency] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, spec in data.get(section, {}).items():
            dependencies.append(_dependency_from_spec("crates", name, spec))

    target = data.get("target", {})
    for target_config in target.values():
        if not isinstance(target_config, dict):
            continue
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            for name, spec in target_config.get(section, {}).items():
                dependencies.append(_dependency_from_spec("crates", name, spec))
    return _unique(dependencies)


def _parse_go_mod(text: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    in_require_block = False
    for raw_line in text.splitlines():
        if "// indirect" in raw_line:
            continue
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if line == "require (":
            in_require_block = True
            continue
        if in_require_block and line == ")":
            in_require_block = False
            continue
        if line.startswith("require "):
            value = line.removeprefix("require ").strip()
        elif in_require_block:
            value = line
        else:
            continue

        values = value.split(maxsplit=1)
        if not values:
            continue
        module = values[0]
        repository = None
        if module.casefold().startswith("github.com/"):
            repository = repository_from_metadata_url(f"https://{module}")
        dependencies.append(Dependency("go", module, repository))
    return _unique(dependencies)


def _parse_gitmodules(text: str) -> list[Dependency]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ValueError(f"invalid .gitmodules file: {error}") from error
    dependencies: list[Dependency] = []
    for section in parser.sections():
        url = parser.get(section, "url", fallback="").strip()
        repository = repository_from_metadata_url(url)
        if repository is not None:
            dependencies.append(Dependency("git", repository, repository))
    return _unique(dependencies)


def _unique(dependencies: list[Dependency]) -> list[Dependency]:
    result: dict[tuple[str, str], Dependency] = {}
    for dependency in dependencies:
        current = result.get(dependency.identity)
        if current is None or (current.repository is None and dependency.repository is not None):
            result[dependency.identity] = dependency
    return list(result.values())
