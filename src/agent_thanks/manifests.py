from __future__ import annotations

import configparser
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import shlex
from typing import Any
from urllib.parse import urlsplit

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
    from_registry: bool = True

    @property
    def identity(self) -> tuple[str, str, str]:
        """Ecosystem, normalized name, and the pinned repository source, if any."""
        name = self.name.casefold()
        name = re.sub(r"[-_.]+", "-", name) if self.ecosystem == "pypi" else name.replace("_", "-")
        return (
            self.ecosystem,
            name,
            (self.repository or "").casefold(),
        )


_REQUIREMENTS_FILE = re.compile(r"requirements(?:[-_.].+)?\.txt$", re.IGNORECASE)
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")
_EDITABLE_OPTION = re.compile(r"(?:--editable(?:=|\s+)|-e(?:=|\s*))(.*)", re.DOTALL)
_VCS_SCHEME_PREFIXES = ("git+", "hg+", "svn+", "bzr+")
_VCS_PREFIXES = _VCS_SCHEME_PREFIXES + ("git@",)
_ALTERNATE_SOURCE_OPTION = re.compile(
    r"^(?:--(?:extra-index-url|index-url|find-links|no-index)(?:\s|=|$)|-[if](?:\s|=|\S))"
)
_NON_REGISTRY_SPEC_PREFIXES = (
    "git+",
    "git://",
    "git@",
    "github:",
    "gitlab:",
    "bitbucket:",
    "gist:",
    "file:",
    "link:",
    "workspace:",
    "portal:",
    "npm:",
    "http://",
    "https://",
    "ssh://",
)
_NON_REGISTRY_SPEC_KEYS = ("git", "url", "path", "workspace", "registry", "source")


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


def _spec_from_registry(spec: object) -> bool:
    """Return False when a manifest pins a source other than the ecosystem registry."""
    if isinstance(spec, dict):
        return not any(key in spec for key in _NON_REGISTRY_SPEC_KEYS)
    if isinstance(spec, str):
        value = spec.strip()
        return not (
            value.casefold().startswith(_NON_REGISTRY_SPEC_PREFIXES)
            or "://" in value
            or "/" in value
        )
    return True


def _dependency_from_spec(ecosystem: str, name: str, spec: object) -> Dependency:
    if ecosystem == "crates" and isinstance(spec, dict) and "package" in spec:
        name = spec["package"]
        if not isinstance(name, str) or not name:
            raise ValueError("invalid Cargo package name")
    repository = repository_from_metadata_url(spec)
    if isinstance(spec, dict):
        repository = repository or repository_from_metadata_url(spec.get("git"))
    return Dependency(
        ecosystem=ecosystem,
        name=name,
        repository=repository,
        from_registry=_spec_from_registry(spec),
    )


def _is_pinned_source(value: str) -> bool:
    return "://" in value or value.casefold().startswith(_VCS_PREFIXES)


def _looks_like_local_path(value: str) -> bool:
    return value.startswith((".", "/", "~", "\\")) or "/" in value or "\\" in value


def _url_label(value: str) -> str:
    target = value.split("+", 1)[1] if value.casefold().startswith(_VCS_SCHEME_PREFIXES) else value
    split = urlsplit(target)
    path = split.path.split("@", 1)[0].rstrip("/")
    label = f"{split.hostname or ''}{path}".removesuffix(".git")
    return label or value


def _parse_requirement(value: str) -> Dependency | None:
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    editable = _EDITABLE_OPTION.fullmatch(value)
    if editable is not None:
        value = editable.group(1).strip()
    elif value.startswith("-"):
        return None
    value = value.split(" ;", 1)[0].strip()
    if not _is_pinned_source(value):
        if editable is not None or _looks_like_local_path(value):
            return None
        match = _PACKAGE_NAME.match(value)
        if not match:
            return None
        return Dependency("pypi", match.group(0))

    repository = repository_from_metadata_url(value)

    egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.-]+)", value)
    if egg_match:
        return Dependency("pypi", egg_match.group(1), repository, from_registry=False)

    direct_match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?\s*@\s*(.+)", value)
    if direct_match:
        return Dependency(
            "pypi",
            direct_match.group(1),
            repository or repository_from_metadata_url(direct_match.group(2)),
            from_registry=False,
        )

    return Dependency("pypi", repository or _url_label(value), repository, from_registry=False)


def _parse_requirements(text: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    alternate_sources = False
    continued = ""
    for raw_line in text.splitlines():
        line = continued + raw_line.strip()
        if line.endswith("\\"):
            continued = line[:-1]
            continue
        continued = ""
        if _ALTERNATE_SOURCE_OPTION.match(line):
            alternate_sources = True
        dependency = _parse_requirement(line)
        if dependency is not None:
            dependencies.append(dependency)
    if alternate_sources:
        dependencies = [
            Dependency(item.ecosystem, item.name, from_registry=False)
            if item.from_registry else item for item in dependencies
        ]
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

    uv_sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    overridden = {Dependency("pypi", name).identity[1] for name in uv_sources}
    dependencies = [
        Dependency(item.ecosystem, item.name, from_registry=False)
        if item.identity[1] in overridden else item for item in dependencies
    ]
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
    patched = set()
    for patches in data.get("patch", {}).values():
        for name, spec in patches.items():
            patched.add(_dependency_from_spec("crates", name, spec).identity[1])
    for package_id in data.get("replace", {}):
        name = package_id.rsplit("#", 1)[-1].split(":", 1)[0]
        patched.add(Dependency("crates", name).identity[1])
    # A declared patch is not proof that Cargo selected it; selection also
    # depends on versions and the lockfile. Do not guess either upstream.
    dependencies = [
        Dependency(item.ecosystem, item.name, from_registry=False)
        if item.identity[1] in patched else item for item in dependencies
    ]
    return _unique(dependencies)


def _parse_go_mod(text: str) -> list[Dependency]:
    required: list[tuple[str, str]] = []
    replacements: dict[tuple[str, str | None], str | None] = {}
    block: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        tokens = shlex.split(line)
        if tokens == [")"]:
            block = None
            continue
        if tokens[0] in {"require", "replace"}:
            directive, values = tokens[0], tokens[1:]
        elif block is not None:
            directive, values = block, tokens
        else:
            continue
        if values == ["("]:
            block = directive
            continue
        if directive == "require":
            if re.search(r"//\s*indirect\b", raw_line):
                continue
            if len(values) != 2:
                raise ValueError("invalid Go require directive")
            required.append((values[0], values[1]))
            continue
        if values.count("=>") != 1:
            raise ValueError("invalid Go replace directive")
        arrow = values.index("=>")
        old, new = values[:arrow], values[arrow + 1:]
        if len(old) not in {1, 2} or len(new) not in {1, 2}:
            raise ValueError("invalid Go replace directive")
        key = old[0], old[1] if len(old) == 2 else None
        if key in replacements:
            raise ValueError("conflicting Go replace directives")
        # Local replacements have no module version and cannot identify which
        # upstream repository's code is actually in use.
        replacements[key] = new[0] if len(new) == 2 else None

    if block is not None:
        raise ValueError("unterminated Go directive block")
    dependencies: list[Dependency] = []
    for module, version in required:
        specific, general = (module, version), (module, None)
        replaced = specific in replacements or general in replacements
        target = replacements.get(specific, replacements.get(general)) if replaced else module
        repository = None
        if target is not None and target.casefold().startswith("github.com/"):
            repository = repository_from_metadata_url(f"https://{target}")
        dependencies.append(Dependency("go", module, repository, from_registry=not replaced))
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
    result: dict[tuple[str, str, str], Dependency] = {}
    for dependency in dependencies:
        result.setdefault(dependency.identity, dependency)
    return list(result.values())
