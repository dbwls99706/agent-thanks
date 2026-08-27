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
    rf"(?<![A-Za-z0-9.-])(?:www\.)?github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})",
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
    repo = unquote(repo).removesuffix(".git").rstrip(".,;:!)]}\"")
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


def repository_from_metadata_url(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None

    repositories = extract_github_repositories(value)
    if repositories:
        return repositories[0]

    cleaned = value.strip().removeprefix("github:")
    if re.fullmatch(rf"{_OWNER}/{_REPO}(?:#.+)?", cleaned):
        owner, repo = cleaned.split("/", 1)
        return normalize_repository(owner, repo.split("#", 1)[0])

    parsed = urlparse(value)
    if parsed.hostname and parsed.hostname.casefold() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return normalize_repository(parts[0], parts[1])
    return None
