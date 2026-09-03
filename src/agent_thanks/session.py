from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re
import shlex
from urllib.parse import urlparse

from .models import Evidence
from .repositories import (
    extract_github_repository_occurrences,
    normalize_repository,
)


_PROVENANCE_PATTERN = re.compile(
    r"\b(?:copied from|adapted from|used code from)\b",
    re.IGNORECASE,
)
_PROVENANCE_BOUNDARY_PATTERN = re.compile(r"&&|\|\||[;|#]|[.!?](?=\s|$)")
_PROVENANCE_PREFIX_PATTERN = re.compile(r"\s*(?:[-*•]\s*)?")
_PROVENANCE_TARGET_GAP_PATTERN = re.compile(r"\s*(?::\s*)?[<(\[]?\s*")
_PROVENANCE_TARGET_SUFFIX_PATTERN = re.compile(
    r"(?:[/?#][^\s<>()\[\]{}]*)?[>)}\],.!;:]*"
)
_PROVENANCE_CONTRADICTION_PATTERN = re.compile(
    r"\b(?:did not|didn't|never|no|not|nothing|neither|unused|was not|wasn't|without)\b"
    r"|\b(?:only viewed|reference only)\b",
    re.IGNORECASE,
)
_HEREDOC_PATTERN = re.compile(
    r'''<<-?\s*(?:\$?'[^'\n]+'|\$?"[^"\n]+"|(?:\\.|[^\s;&|<>])+)'''
)
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
_PROMPT_TOKENS = {"$", "%", "❯"}

_GIT_CLONE_FLAG_OPTIONS = {
    "--also-filter-submodules",
    "--bare",
    "--dissociate",
    "--local",
    "--mirror",
    "--no-checkout",
    "--no-hardlinks",
    "--no-recurse-submodules",
    "--no-remote-submodules",
    "--no-shallow-submodules",
    "--no-single-branch",
    "--progress",
    "--quiet",
    "--recurse-submodules",
    "--reject-shallow",
    "--remote-submodules",
    "--shared",
    "--shallow-submodules",
    "--single-branch",
    "--sparse",
    "--verbose",
    "-l",
    "-n",
    "-q",
    "-s",
    "-v",
}
_GIT_CLONE_VALUE_OPTIONS = {
    "--branch",
    "--bundle-uri",
    "--config",
    "--depth",
    "--filter",
    "--jobs",
    "--origin",
    "--recurse-submodules",
    "--reference",
    "--reference-if-able",
    "--separate-git-dir",
    "--server-option",
    "--shallow-exclude",
    "--shallow-since",
    "--template",
    "--upload-pack",
    "-b",
    "-c",
    "-j",
    "-o",
    "-u",
}
_GIT_CLONE_ATTACHED_VALUE_OPTIONS = {"-b", "-c", "-j", "-o", "-u"}

_GIT_SUBMODULE_FLAG_OPTIONS = {"--force", "-f"}
_GIT_SUBMODULE_VALUE_OPTIONS = {
    "--branch",
    "--depth",
    "--name",
    "--ref-format",
    "--reference",
    "-b",
}
_GIT_SUBMODULE_ATTACHED_VALUE_OPTIONS = {"-b"}

_GH_CLONE_VALUE_OPTIONS = {"--upstream-remote-name", "-u"}
_GH_CLONE_ATTACHED_VALUE_OPTIONS = {"-u"}

_PIP_FLAG_OPTIONS = {
    "--break-system-packages",
    "--compile",
    "--force-reinstall",
    "--ignore-installed",
    "--ignore-requires-python",
    "--no-build-isolation",
    "--no-clean",
    "--no-compile",
    "--no-deps",
    "--no-index",
    "--no-use-pep517",
    "--pre",
    "--prefer-binary",
    "--quiet",
    "--require-hashes",
    "--upgrade",
    "--use-pep517",
    "--verbose",
    "-I",
    "-U",
    "-q",
    "-v",
}
_PIP_VALUE_OPTIONS = {
    "--abi",
    "--cache-dir",
    "--cert",
    "--client-cert",
    "--config-settings",
    "--constraint",
    "--extra-index-url",
    "--find-links",
    "--group",
    "--implementation",
    "--index-url",
    "--no-binary",
    "--only-binary",
    "--platform",
    "--prefix",
    "--progress-bar",
    "--proxy",
    "--python-version",
    "--report",
    "--requirement",
    "--retries",
    "--root",
    "--root-user-action",
    "--src",
    "--target",
    "--timeout",
    "--trusted-host",
    "--upgrade-strategy",
    "-c",
    "-f",
    "-i",
    "-r",
    "-t",
}
_PIP_ATTACHED_VALUE_OPTIONS = {"-c", "-f", "-i", "-r", "-t"}
_PIP_SOURCE_VALUE_OPTIONS = {"--editable", "-e"}
_PIP_ATTACHED_SOURCE_OPTIONS = {"-e"}

_UV_FLAG_OPTIONS = {
    "--active",
    "--dev",
    "--editable",
    "--frozen",
    "--locked",
    "--no-build-isolation",
    "--no-cache",
    "--no-index",
    "--no-sync",
    "--raw-sources",
    "--upgrade",
}
_UV_VALUE_OPTIONS = {
    "--constraint",
    "--default-index",
    "--extra-index-url",
    "--index",
    "--index-url",
    "--keyring-provider",
    "--optional",
    "--package",
    "--prerelease",
    "--project",
    "--python",
    "--resolution",
    "--requirements",
    "--script",
    "--workspace",
    "-c",
    "-r",
}
_UV_ATTACHED_VALUE_OPTIONS = {"-c", "-r"}

_PACKAGE_FLAG_OPTIONS = {
    "--force",
    "--global",
    "--ignore-scripts",
    "--legacy-peer-deps",
    "--no-save",
    "--save-dev",
    "--save-optional",
    "--save-peer",
    "--save-prod",
    "-D",
    "-O",
    "-P",
    "-g",
}
_PACKAGE_VALUE_OPTIONS = {
    "--cache",
    "--cache-folder",
    "--cwd",
    "--include",
    "--install-strategy",
    "--modules-folder",
    "--mutex",
    "--network-timeout",
    "--omit",
    "--prefix",
    "--registry",
    "--tag",
    "--userconfig",
    "--workspace",
    "-w",
}
_PACKAGE_ATTACHED_VALUE_OPTIONS = {"-w"}

_GO_GET_FLAG_OPTIONS = {"-insecure", "-t", "-u", "-v", "-x"}
_GO_GET_VALUE_OPTIONS = {
    "-exec",
    "-gcflags",
    "-ldflags",
    "-mod",
    "-modfile",
    "-overlay",
    "-tags",
    "-toolexec",
}

_CARGO_FLAG_OPTIONS = {
    "--build",
    "--default-features",
    "--dev",
    "--frozen",
    "--ignore-rust-version",
    "--locked",
    "--no-default-features",
    "--offline",
    "--optional",
    "--quiet",
    "--verbose",
    "-B",
    "-D",
    "-O",
    "-q",
    "-v",
}
_CARGO_VALUE_OPTIONS = {
    "--branch",
    "--color",
    "--config",
    "--features",
    "--git",
    "--manifest-path",
    "--package",
    "--registry",
    "--rename",
    "--rev",
    "--tag",
    "--target",
    "-F",
    "-p",
}
_CARGO_ATTACHED_VALUE_OPTIONS = {"-F", "-p"}

_NO_EFFECT_OPTIONS = {
    "--dry-run",
    "--help",
    "--no-op",
    "--noop",
    "--package-lock-only",
    "--simulate",
    "--version",
    "-V",
    "-h",
    "-n",
}


def _deduplicate_repositories(repositories: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for repository in repositories:
        key = repository.casefold()
        if key not in seen:
            seen.add(key)
            result.append(repository)
    return result


def _split_shell_segments(
    line: str,
) -> tuple[list[tuple[str | None, list[str]]], str | None] | None:
    """Split a shell line into (separator, tokens) segments plus any trailing separator."""
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[tuple[str | None, list[str]]] = []
    current: list[str] = []
    preceding_separator: str | None = None
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            if not current:
                return None
            if current:
                segments.append((preceding_separator, current))
                current = []
            preceding_separator = token
            continue
        current.append(token)
    trailing: str | None = None
    if current:
        segments.append((preceding_separator, current))
    elif preceding_separator in {"&&", "||", "|"}:
        return None
    else:
        trailing = preceding_separator
    return segments, trailing


def _strip_command_wrappers(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining and remaining[0] in _PROMPT_TOKENS:
        remaining.pop(0)

    if remaining and remaining[0].casefold() == "command":
        remaining.pop(0)
    if remaining and remaining[0].casefold() == "sudo":
        remaining.pop(0)
        if remaining and remaining[0].startswith("-"):
            return []
    if remaining and remaining[0].casefold() == "env":
        remaining.pop(0)
        if remaining and ("=" in remaining[0] or remaining[0].startswith("-")):
            # ``env PATH=... git`` or ``env -i git`` changes the environment the
            # command resolves in, so its exit status proves nothing about git.
            return []
    return remaining


def _executable_name(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _positional_arguments(
    arguments: list[str],
    *,
    flag_options: set[str],
    value_options: set[str],
    attached_value_options: set[str],
) -> list[str] | None:
    positionals: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            positionals.extend(arguments[index + 1 :])
            break
        if argument in flag_options:
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                return None
            index += 2
            continue
        if any(argument.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if any(
            not argument.startswith("--")
            and argument.startswith(option)
            and len(argument) > len(option)
            for option in attached_value_options
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        positionals.append(argument)
        index += 1
    return positionals


def _repository_from_target(
    token: str,
    *,
    allow_shorthand: bool = False,
    allow_github_prefix: bool = False,
    allow_bare_github: bool = False,
    allow_git_plus: bool = False,
    allow_at_revision: bool = False,
    allow_subpath: bool = False,
) -> str | None:
    cleaned = token.strip()
    if cleaned != cleaned.strip("()[]{}<>"):
        return None
    cleaned = cleaned.strip(",")
    lowered = cleaned.casefold()
    git_marker = lowered.find("git+")
    if git_marker > 0:
        if cleaned[git_marker - 1] != "@":
            return None
        cleaned = cleaned[git_marker:]
        lowered = cleaned.casefold()
    if lowered.startswith("git+") and not allow_git_plus:
        return None

    if allow_github_prefix and lowered.startswith("github:"):
        shorthand = cleaned[len("github:") :].split("#", 1)[0]
        parts = shorthand.split("/")
        return normalize_repository(*parts) if len(parts) == 2 else None

    if allow_shorthand and "://" not in cleaned and not lowered.startswith("git@"):
        shorthand = cleaned.split("#", 1)[0]
        parts = shorthand.split("/")
        return normalize_repository(*parts) if len(parts) == 2 else None

    scp_match = re.fullmatch(
        r"git@github\.com:([^/\s]+)/([^/\s]+)",
        cleaned,
        re.IGNORECASE,
    )
    if scp_match is not None:
        return normalize_repository(scp_match.group(1), scp_match.group(2))

    parse_target = cleaned
    if lowered.startswith(("github.com/", "www.github.com/")):
        if not allow_bare_github:
            return None
        parse_target = f"https://{cleaned}"
    try:
        parsed = urlparse(parse_target)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {
        "git",
        "git+http",
        "git+https",
        "git+ssh",
        "http",
        "https",
        "ssh",
    }:
        return None
    if hostname is None or hostname.casefold() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or (len(parts) != 2 and not allow_subpath):
        return None
    repo = parts[1]
    if "@" in repo:
        if not (allow_bare_github or allow_at_revision):
            return None
        repo = repo.split("@", 1)[0]
    return normalize_repository(parts[0], repo)


def _has_no_effect_option(arguments: Iterable[str]) -> bool:
    return any(
        argument.split("=", 1)[0] in _NO_EFFECT_OPTIONS
        for argument in arguments
    )


def _command_operands(
    arguments: list[str],
    *,
    flag_options: set[str],
    value_options: set[str],
    attached_value_options: set[str] | None = None,
    source_value_options: set[str] | None = None,
    attached_source_options: set[str] | None = None,
) -> list[str]:
    attached_value_options = attached_value_options or set()
    source_value_options = source_value_options or set()
    attached_source_options = attached_source_options or set()
    operands: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            operands.extend(arguments[index + 1 :])
            break
        if argument in flag_options:
            index += 1
            continue
        if argument in source_value_options:
            if index + 1 >= len(arguments):
                return []
            operands.append(arguments[index + 1])
            index += 2
            continue
        source_option = next(
            (
                option
                for option in source_value_options
                if argument.startswith(f"{option}=")
            ),
            None,
        )
        if source_option is not None:
            operands.append(argument.split("=", 1)[1])
            index += 1
            continue
        attached_source = next(
            (
                option
                for option in attached_source_options
                if not argument.startswith("--")
                and argument.startswith(option)
                and len(argument) > len(option)
            ),
            None,
        )
        if attached_source is not None:
            operands.append(argument[len(attached_source) :])
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                return []
            index += 2
            continue
        if any(argument.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if any(
            not argument.startswith("--")
            and argument.startswith(option)
            and len(argument) > len(option)
            for option in attached_value_options
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return []
        operands.append(argument)
        index += 1
    return operands


def _vcs_repositories(
    arguments: list[str],
    *,
    flag_options: set[str],
    value_options: set[str],
    attached_value_options: set[str] | None = None,
    source_value_options: set[str] | None = None,
    attached_source_options: set[str] | None = None,
) -> list[str]:
    repositories: list[str] = []
    operands = _command_operands(
        arguments,
        flag_options=flag_options,
        value_options=value_options,
        attached_value_options=attached_value_options,
        source_value_options=source_value_options,
        attached_source_options=attached_source_options,
    )
    for argument in operands:
        if "git+" not in argument.casefold():
            continue
        repository = _repository_from_target(
            argument,
            allow_git_plus=True,
            allow_at_revision=True,
        )
        if repository is not None:
            repositories.append(repository)
    return _deduplicate_repositories(repositories)


def _package_repositories(arguments: list[str]) -> list[str]:
    repositories: list[str] = []
    operands = _command_operands(
        arguments,
        flag_options=_PACKAGE_FLAG_OPTIONS,
        value_options=_PACKAGE_VALUE_OPTIONS,
        attached_value_options=_PACKAGE_ATTACHED_VALUE_OPTIONS,
    )
    for argument in operands:
        lowered = argument.casefold()
        if (
            "git+" not in lowered
            and not lowered.startswith("github:")
            and "github.com" not in lowered
        ):
            continue
        git_marker = lowered.find("git+")
        repository = _repository_from_target(
            argument,
            allow_github_prefix=True,
            allow_git_plus=True,
            allow_at_revision=(
                git_marker == 0
                or (git_marker > 0 and argument[git_marker - 1] == "@")
            ),
        )
        if repository is not None:
            repositories.append(repository)
    return _deduplicate_repositories(repositories)


def _cargo_repositories(arguments: list[str]) -> list[str]:
    dependency_operands: list[str] = []
    git_targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            dependency_operands.extend(arguments[index + 1 :])
            break
        if argument in _CARGO_FLAG_OPTIONS:
            index += 1
            continue
        if argument == "--git":
            if index + 1 >= len(arguments):
                return []
            git_targets.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith("--git="):
            git_targets.append(argument.split("=", 1)[1])
            index += 1
            continue
        if argument in _CARGO_VALUE_OPTIONS:
            if index + 1 >= len(arguments):
                return []
            index += 2
            continue
        if any(argument.startswith(f"{option}=") for option in _CARGO_VALUE_OPTIONS):
            index += 1
            continue
        if any(
            not argument.startswith("--")
            and argument.startswith(option)
            and len(argument) > len(option)
            for option in _CARGO_ATTACHED_VALUE_OPTIONS
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return []
        dependency_operands.append(argument)
        index += 1

    if not dependency_operands:
        return []
    repositories = [
        repository
        for target in git_targets
        if (repository := _repository_from_target(target)) is not None
    ]
    return _deduplicate_repositories(repositories)


def _repositories_from_command(tokens: list[str]) -> list[str]:
    tokens = _strip_command_wrappers(tokens)
    if not tokens:
        return []

    command = _executable_name(tokens[0])
    arguments = tokens[1:]

    if command == "git" and arguments[:1] == ["clone"]:
        positionals = _positional_arguments(
            arguments[1:],
            flag_options=_GIT_CLONE_FLAG_OPTIONS,
            value_options=_GIT_CLONE_VALUE_OPTIONS,
            attached_value_options=_GIT_CLONE_ATTACHED_VALUE_OPTIONS,
        )
        if positionals is None or not 1 <= len(positionals) <= 2:
            return []
        repository = _repository_from_target(positionals[0])
        return [repository] if repository is not None else []

    if command == "git" and arguments[:2] == ["submodule", "add"]:
        positionals = _positional_arguments(
            arguments[2:],
            flag_options=_GIT_SUBMODULE_FLAG_OPTIONS,
            value_options=_GIT_SUBMODULE_VALUE_OPTIONS,
            attached_value_options=_GIT_SUBMODULE_ATTACHED_VALUE_OPTIONS,
        )
        if positionals is None or not 1 <= len(positionals) <= 2:
            return []
        repository = _repository_from_target(positionals[0])
        return [repository] if repository is not None else []

    if command == "gh" and arguments[:2] == ["repo", "clone"]:
        positionals = _positional_arguments(
            arguments[2:],
            flag_options=set(),
            value_options=_GH_CLONE_VALUE_OPTIONS,
            attached_value_options=_GH_CLONE_ATTACHED_VALUE_OPTIONS,
        )
        if positionals is None or not 1 <= len(positionals) <= 2:
            return []
        repository = (
            _repository_from_target(positionals[0], allow_shorthand=True)
        )
        return [repository] if repository is not None else []

    pip_arguments: list[str] | None = None
    if re.fullmatch(r"pip(?:\d+(?:\.\d+)?)?", command) and arguments[:1] == ["install"]:
        pip_arguments = arguments[1:]
    elif (
        re.fullmatch(r"(?:python|python\d+(?:\.\d+)?|py)", command)
        and arguments[:3] == ["-m", "pip", "install"]
    ):
        pip_arguments = arguments[3:]
    if pip_arguments is not None:
        if _has_no_effect_option(pip_arguments):
            return []
        return _vcs_repositories(
            pip_arguments,
            flag_options=_PIP_FLAG_OPTIONS,
            value_options=_PIP_VALUE_OPTIONS,
            attached_value_options=_PIP_ATTACHED_VALUE_OPTIONS,
            source_value_options=_PIP_SOURCE_VALUE_OPTIONS,
            attached_source_options=_PIP_ATTACHED_SOURCE_OPTIONS,
        )

    if command == "uv" and arguments[:1] == ["add"]:
        uv_arguments = arguments[1:]
        if _has_no_effect_option(uv_arguments):
            return []
        return _vcs_repositories(
            uv_arguments,
            flag_options=_UV_FLAG_OPTIONS,
            value_options=_UV_VALUE_OPTIONS,
            attached_value_options=_UV_ATTACHED_VALUE_OPTIONS,
        )

    if command == "cargo" and arguments[:1] == ["add"]:
        cargo_arguments = arguments[1:]
        if _has_no_effect_option(cargo_arguments):
            return []
        return _cargo_repositories(cargo_arguments)

    if command == "go" and arguments[:1] == ["get"]:
        repositories = []
        go_arguments = arguments[1:]
        if _has_no_effect_option(go_arguments):
            return []
        operands = _command_operands(
            go_arguments,
            flag_options=_GO_GET_FLAG_OPTIONS,
            value_options=_GO_GET_VALUE_OPTIONS,
        )
        for argument in operands:
            if not argument.casefold().startswith("github.com/"):
                continue
            normalized_argument = argument.rstrip(".,;:!)]}\"'")
            if (
                normalized_argument.casefold().rsplit("@", 1)[-1] == "none"
                and "@" in normalized_argument
            ):
                continue
            repository = _repository_from_target(
                normalized_argument,
                allow_bare_github=True,
                allow_subpath=True,
            )
            if repository is not None:
                repositories.append(repository)
        return _deduplicate_repositories(repositories)

    if command in {"npm", "pnpm"} and arguments and arguments[0] in {
        "install",
        "add",
        "i",
    }:
        package_arguments = arguments[1:]
        if _has_no_effect_option(package_arguments):
            return []
        return _package_repositories(package_arguments)
    if command == "yarn" and arguments[:1] == ["add"]:
        package_arguments = arguments[1:]
        if _has_no_effect_option(package_arguments):
            return []
        return _package_repositories(package_arguments)

    return []


def _position_is_quoted(line: str, position: int) -> bool:
    quote: str | None = None
    escaped = False
    for character in line[:position]:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote is None and character in {"'", '"', "`"}:
            quote = character
        elif character == quote:
            quote = None
    return quote is not None


def _provenance_repositories(line: str) -> list[str]:
    occurrences = extract_github_repository_occurrences(line)
    occurrence_starts = [start for _, start, _ in occurrences]
    repositories: list[str] = []
    for marker in _PROVENANCE_PATTERN.finditer(line):
        if _position_is_quoted(line, marker.start()):
            continue
        prefix = line[: marker.start()]
        if _PROVENANCE_PREFIX_PATTERN.fullmatch(prefix) is None:
            continue
        boundary = _PROVENANCE_BOUNDARY_PATTERN.search(line, marker.end())
        clause_end = boundary.start() if boundary is not None else len(line)
        if boundary is not None and line[boundary.start()] == "?":
            continue
        if boundary is not None and _PROVENANCE_CONTRADICTION_PATTERN.search(
            line[boundary.end() :]
        ):
            continue
        occurrence_index = bisect_left(occurrence_starts, marker.end())
        if occurrence_index >= len(occurrences):
            continue
        repository, start, end = occurrences[occurrence_index]
        if start >= clause_end:
            continue
        gap = line[marker.end() : start]
        if _PROVENANCE_TARGET_GAP_PATTERN.fullmatch(gap) is None:
            continue
        target_tail = line[start:clause_end]
        whitespace = re.search(r"\s", target_tail)
        token_end = (
            start + whitespace.start() if whitespace is not None else clause_end
        )
        if _PROVENANCE_TARGET_SUFFIX_PATTERN.fullmatch(line[end:token_end]) is None:
            continue
        if whitespace is not None:
            remainder = target_tail[whitespace.start() :]
            if remainder.strip(" \t>)]},"):
                continue
        repositories.append(repository)
    return _deduplicate_repositories(repositories)


@dataclass(frozen=True)
class CommandAnalysis:
    """Repositories targeted by supported commands in one shell statement.

    ``pure`` is True when the statement is a single command, or a chain joined
    only by ``&&`` in which every segment is either a repository command or a
    trivially safe command such as ``cd`` or ``mkdir``; then a successful exit
    status of the whole statement proves that every repository command in it ran
    and succeeded. Statements using ``;``, ``||``, ``|``, or ``&``, and chains
    containing any other command (``exit``, ``eval``, ``source``, ``make``, an
    unknown executable), are never pure.
    """

    repositories: list[str]
    pure: bool


_CHAIN_PREFIX_ALLOWLIST = {"cd", "pushd", "popd", "mkdir", "true", "echo", "pwd"}


def analyze_command_line(line: str) -> CommandAnalysis:
    split = _split_shell_segments(line)
    if not split:
        return CommandAnalysis([], False)
    segments, trailing = split
    if not segments:
        return CommandAnalysis([], False)
    pure = trailing is None and all(separator in (None, "&&") for separator, _ in segments)
    repositories: list[str] = []
    for _, segment in segments:
        found = _repositories_from_command(segment)
        repositories.extend(found)
        if not found and not _is_safe_chain_segment(segment):
            pure = False
    return CommandAnalysis(_deduplicate_repositories(repositories), pure)


def _is_safe_chain_segment(segment: list[str]) -> bool:
    """Only trivially side-effect-free commands may share a chain with a repository command.

    Anything else makes the chain impure: control-flow builtins (``exit``,
    ``exec``, ``eval``, ``source``, ``builtin``), ``set`` (``set -n`` stops
    execution while exiting 0), ``export``, ``printf -v``, and variable
    assignments (they can redirect ``PATH`` to a fake ``git``), and arbitrary
    executables. A successful exit status can then no longer prove that the
    repository command ran and succeeded.
    """
    command = _strip_command_wrappers(segment)
    if not command:
        return False
    return _executable_name(command[0]) in _CHAIN_PREFIX_ALLOWLIST


def _fence_marker(line: str) -> tuple[str, int] | None:
    match = re.match(r"\s*(`{3,}|~{3,})", line)
    if match is None:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _heredoc_delimiters(line: str) -> list[str] | None:
    matches = list(_HEREDOC_PATTERN.finditer(line))
    if len(matches) != len(re.findall(r"<<-?", line)):
        return None
    delimiters: list[str] = []
    for match in matches:
        token = match.group(0).split("<<", 1)[1].lstrip("-").strip()
        if token.startswith("$") and token[1:2] in {"'", '"'}:
            token = token[1:]
        if token[:1] == token[-1:] and token[:1] in {"'", '"'}:
            token = token[1:-1]
        token = token.replace("\\", "")
        if token:
            delimiters.append(token)
    return delimiters


def _logical_session_lines(text: str) -> Iterable[tuple[int, str]]:
    pending = ""
    start_line = 1
    for line_number, physical_line in enumerate(text.splitlines(), start=1):
        if not pending:
            start_line = line_number
        pending += physical_line
        stripped = pending.rstrip()
        trailing_backslashes = len(stripped) - len(stripped.rstrip("\\"))
        if trailing_backslashes % 2 == 1:
            pending = stripped[:-1] + " "
            continue
        yield start_line, pending
        pending = ""
    if pending:
        yield start_line, pending


OUTCOME_OK = "ok"
OUTCOME_ATTESTED = "attested"
OUTCOME_ERROR = "error"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_MISSING = "missing"
OUTCOME_UNCONFIRMED = "unconfirmed"
OUTCOME_CONFLICT = "conflict"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_UNANCHORED = "unanchored"
_PROMOTING_OUTCOMES = {OUTCOME_OK, OUTCOME_ATTESTED}

PROVENANCE_DETAIL = "Session states that code was adapted from this repository"
REFERENCE_DETAIL = "Repository was referenced in the session; verify actual reuse"
_USAGE_DETAILS = {
    OUTCOME_OK: "Session ran a repository-use command that completed successfully",
    OUTCOME_ATTESTED: (
        "Session ran a repository-use command; success attested with --trust-session"
    ),
}
_DEMOTED_DETAILS = {
    OUTCOME_ERROR: "Session ran a repository command that failed; not counted as use",
    OUTCOME_UNKNOWN: (
        "Session ran a repository command whose result cannot be judged; verify actual reuse"
    ),
    OUTCOME_MISSING: (
        "Session ran a repository command but recorded no result; verify actual reuse"
    ),
    OUTCOME_UNCONFIRMED: (
        "Session ran a repository command whose success the hook log did not confirm; "
        "verify actual reuse"
    ),
    OUTCOME_CONFLICT: (
        "Session ran a repository command, but the hook log and the transcript disagree about "
        "the command behind this tool call; verify actual reuse"
    ),
    OUTCOME_AMBIGUOUS: (
        "Session reused one tool call identifier for different calls, so no result can be "
        "attributed to this command; verify actual reuse"
    ),
    OUTCOME_UNANCHORED: (
        "Session recorded this command outside a recognized tool call position, so no result "
        "can be attributed to it; verify actual reuse"
    ),
}
COMPOUND_DETAIL = (
    "Session ran a compound shell statement; the repository command's own result "
    "cannot be confirmed"
)
MULTILINE_DETAIL = (
    "Session ran a multi-line shell invocation; the repository command's own result "
    "cannot be confirmed"
)

Classification = list[tuple[str, bool, str]]


def scan_session_evidence(
    text: str,
    source: str,
    *,
    line_labels: bool = True,
    outcome: str = OUTCOME_MISSING,
    single_statement: bool = False,
    provenance: bool = True,
) -> list[tuple[str, Evidence]]:
    """Classify shell-style lines whose commands share one recorded outcome.

    A repository command counts as use only when ``outcome`` is a recorded
    success (or an explicit attestation) and the statement is pure, so that the
    success provably belongs to that command. With ``single_statement`` the
    whole text is one tool invocation that received one result; if it spans
    several logical lines, no command in it can claim that result. Provenance
    statements count as use regardless of outcome only while ``provenance`` is
    on, which is right for a mixed plain-text log but never for the text of a
    tool command; everything else stays a reference.
    """
    if outcome not in _USAGE_DETAILS and outcome not in _DEMOTED_DETAILS:
        raise ValueError(f"Unknown session outcome: {outcome}")
    multiline = single_statement and sum(1 for _ in _logical_session_lines(text)) > 1

    def classify(line: str) -> Classification:
        classified: Classification = []
        if provenance:
            classified.extend(
                (repository, True, PROVENANCE_DETAIL) for repository in _provenance_repositories(line)
            )
        analysis = analyze_command_line(line)
        if outcome not in _PROMOTING_OUTCOMES:
            detail, meaningful = _DEMOTED_DETAILS[outcome], False
        elif multiline:
            detail, meaningful = MULTILINE_DETAIL, False
        elif analysis.pure:
            detail, meaningful = _USAGE_DETAILS[outcome], True
        else:
            detail, meaningful = COMPOUND_DETAIL, False
        classified.extend(
            (repository, meaningful, detail) for repository in analysis.repositories
        )
        return classified

    return _scan_lines(text, source, classify, line_labels=line_labels)


def scan_prose_evidence(
    text: str, source: str, *, line_labels: bool = True
) -> list[tuple[str, Evidence]]:
    """Classify agent prose: only an explicit provenance statement counts as use."""

    def classify(line: str) -> Classification:
        return [(repository, True, PROVENANCE_DETAIL) for repository in _provenance_repositories(line)]

    return _scan_lines(text, source, classify, line_labels=line_labels)


def scan_reference_evidence(
    text: str, source: str, *, line_labels: bool = True
) -> list[tuple[str, Evidence]]:
    """Record repository mentions only; nothing in the text can count as use."""
    return _scan_lines(text, source, lambda line: [], line_labels=line_labels)


def _scan_lines(
    text: str,
    source: str,
    classify: Callable[[str], Classification],
    *,
    line_labels: bool,
) -> list[tuple[str, Evidence]]:
    items: list[tuple[str, Evidence]] = []
    fence: tuple[str, int] | None = None
    heredocs: list[str] = []
    for line_number, line in _logical_session_lines(text):
        references = [
            repository
            for repository, _, _ in extract_github_repository_occurrences(line)
        ]
        classified: dict[str, tuple[str, bool, str]] = {}
        stripped = line.strip()
        if fence is not None:
            marker_character, minimum_length = fence
            if re.fullmatch(
                rf"{re.escape(marker_character)}{{{minimum_length},}}\s*", stripped
            ):
                fence = None
        elif heredocs:
            if stripped == heredocs[0]:
                heredocs.pop(0)
        else:
            opening_fence = _fence_marker(line)
            if opening_fence is not None:
                fence = opening_fence
            elif line.startswith("\t") or line.startswith("    "):
                pass
            else:
                for repository, meaningful, detail in classify(line):
                    key = repository.casefold()
                    if key not in classified or (meaningful and not classified[key][1]):
                        classified[key] = (repository, meaningful, detail)
                detected_heredocs = _heredoc_delimiters(line)
                heredocs = detected_heredocs if detected_heredocs is not None else ["\0"]
        repositories = _deduplicate_repositories(
            [*references, *(entry[0] for entry in classified.values())]
        )
        label = f"{source}:{line_number}" if line_labels else source
        for repository in repositories:
            _, meaningful, detail = classified.get(
                repository.casefold(), (repository, False, REFERENCE_DETAIL)
            )
            items.append(
                (
                    repository,
                    Evidence(
                        kind="session_usage" if meaningful else "session_reference",
                        source=label,
                        detail=detail,
                        confidence="high" if meaningful else "low",
                        meaningful=meaningful,
                    ),
                )
            )
    return items
