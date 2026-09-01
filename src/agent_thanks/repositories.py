from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9_.-]+"
_WEB_PATTERN = re.compile(
    rf"(?:https?://|git://)(?:www\.)?github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
    re.IGNORECASE,
)
_BARE_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9./?&=#:%+-])(?:www\.)?github\.com/"
    rf"(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
    re.IGNORECASE,
)
_SSH_PATTERN = re.compile(
    rf"(?:git@github\.com:|ssh://git@github\.com/)(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
    re.IGNORECASE,
)
_RAW_PATTERN = re.compile(
    rf"https?://raw\.githubusercontent\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
    re.IGNORECASE,
)


def normalize_repository(owner: str, repo: str) -> str | None:
    repo = unquote(repo).rstrip(".,;:!)]}\"'").removesuffix(".git")
    if not re.fullmatch(_OWNER, owner) or not re.fullmatch(_REPO, repo):
        return None
    if repo in {".", ".."}:
        return None
    return f"{owner}/{repo}"


def extract_github_repositories(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in (_WEB_PATTERN, _SSH_PATTERN, _RAW_PATTERN, _BARE_PATTERN):
        for match in pattern.finditer(text):
            repository = normalize_repository(match.group("owner"), match.group("repo"))
            if repository is not None and repository.casefold() not in seen:
                seen.add(repository.casefold())
                found.append(repository)
    return found


def extract_github_repository_occurrences(text: str) -> list[tuple[str, int, int]]:
    candidates: list[tuple[str, int, int]] = []
    for pattern in (_WEB_PATTERN, _SSH_PATTERN, _RAW_PATTERN, _BARE_PATTERN):
        for match in pattern.finditer(text):
            repository = normalize_repository(match.group("owner"), match.group("repo"))
            if repository is not None:
                candidates.append((repository, match.start(), match.end()))

    candidates.sort(key=lambda item: (item[1], -(item[2] - item[1])))
    occurrences: list[tuple[str, int, int]] = []
    last_end = -1
    for candidate in candidates:
        _, start, end = candidate
        if start < last_end:
            continue
        occurrences.append(candidate)
        last_end = end
    return occurrences


def repository_from_metadata_url(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    direct_reference = re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^\]]+\])?\s*@\s*(.+)",
        cleaned,
    )
    if direct_reference is not None:
        target = direct_reference.group(1).strip()
        if target.casefold().startswith(
            ("git+", "git://", "github:", "http://", "https://", "ssh://")
        ):
            cleaned = target

    if cleaned.casefold().startswith("github:"):
        cleaned = cleaned[len("github:") :]
    if re.fullmatch(rf"{_OWNER}/{_REPO}(?:#.+)?", cleaned):
        owner, repo = cleaned.split("/", 1)
        return normalize_repository(owner, repo.split("#", 1)[0])

    scp_match = re.fullmatch(
        rf"git@github\.com:(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
        cleaned,
        re.IGNORECASE,
    )
    if scp_match is not None:
        return normalize_repository(scp_match.group("owner"), scp_match.group("repo"))

    parse_target = cleaned
    if cleaned.casefold().startswith(("github.com/", "www.github.com/")):
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
    if hostname is None or hostname.casefold() not in {
        "github.com",
        "raw.githubusercontent.com",
        "www.github.com",
    }:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        repo = parts[1].split("@", 1)[0]
        return normalize_repository(parts[0], repo)
    return None
