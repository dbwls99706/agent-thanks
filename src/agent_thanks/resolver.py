from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .repositories import repository_from_metadata_url


JsonFetcher = Callable[[str], dict[str, Any]]


class PackageRepositoryResolver:
    def __init__(
        self,
        *,
        offline: bool = False,
        timeout: float = 5.0,
        fetcher: JsonFetcher | None = None,
    ) -> None:
        self.offline = offline
        self.timeout = timeout
        self._fetcher = fetcher or self._fetch_json
        self._cache: dict[tuple[str, str], str | None] = {}

    def resolve(self, ecosystem: str, package: str) -> str | None:
        key = ecosystem, package.casefold()
        if key in self._cache:
            return self._cache[key]
        if self.offline:
            self._cache[key] = None
            return None

        try:
            if ecosystem == "pypi":
                result = self._resolve_pypi(package)
            elif ecosystem == "npm":
                result = self._resolve_npm(package)
            elif ecosystem == "crates":
                result = self._resolve_crate(package)
            else:
                result = None
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            json.JSONDecodeError,
        ):
            result = None
        self._cache[key] = result
        return result

    def _fetch_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "agent-thanks/0.1"})
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def _resolve_pypi(self, package: str) -> str | None:
        data = self._fetcher(f"https://pypi.org/pypi/{quote(package, safe='')}/json")
        info = data.get("info", {})
        project_urls = info.get("project_urls") or {}
        preferred_labels = ("source", "repository", "code", "github", "homepage")
        for preferred in preferred_labels:
            for label, value in project_urls.items():
                if str(label).casefold() == preferred:
                    repository = repository_from_metadata_url(value)
                    if repository:
                        return repository
        return repository_from_metadata_url(info.get("home_page"))

    def _resolve_npm(self, package: str) -> str | None:
        data = self._fetcher(f"https://registry.npmjs.org/{quote(package, safe='')}")
        latest = data.get("dist-tags", {}).get("latest")
        metadata = data.get("versions", {}).get(latest, {}) if latest else data
        return repository_from_metadata_url(metadata.get("repository"))

    def _resolve_crate(self, package: str) -> str | None:
        data = self._fetcher(f"https://crates.io/api/v1/crates/{quote(package, safe='')}")
        return repository_from_metadata_url(data.get("crate", {}).get("repository"))
