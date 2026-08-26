from __future__ import annotations

import json
import os
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .repositories import normalize_repository


class GitHubError(RuntimeError):
    pass


def validate_repository(value: str) -> str:
    parts = value.strip().split("/")
    if len(parts) != 2:
        raise ValueError(f"Expected owner/repo, got: {value}")
    repository = normalize_repository(parts[0], parts[1])
    if repository is None:
        raise ValueError(f"Invalid GitHub repository: {value}")
    return repository


class GitHubClient:
    """Small GitHub starring client that never stores credentials."""

    def __init__(self, token: str | None = None, *, timeout: float = 10.0) -> None:
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout

    def star(self, repository: str) -> None:
        self._mutate(repository, method="PUT")

    def unstar(self, repository: str) -> None:
        self._mutate(repository, method="DELETE")

    def _mutate(self, repository: str, *, method: str) -> None:
        repository = validate_repository(repository)
        endpoint = f"/user/starred/{repository}"
        if self.token:
            self._request(endpoint, method)
            return
        if shutil.which("gh"):
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    method,
                    endpoint,
                    "--header",
                    "Content-Length: 0",
                    "--silent",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or "GitHub CLI request failed"
                raise GitHubError(message)
            return
        raise GitHubError(
            "Authentication required. Run 'gh auth login' or set GH_TOKEN "
            "with Starring: write permission."
        )

    def _request(self, endpoint: str, method: str) -> None:
        request = Request(
            f"https://api.github.com{endpoint}",
            data=b"",
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "agent-thanks/0.1",
                "Content-Length": "0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 204:
                    raise GitHubError(f"Unexpected GitHub response: HTTP {response.status}")
        except HTTPError as error:
            message = f"GitHub API returned HTTP {error.code}"
            try:
                payload = json.loads(error.read().decode("utf-8"))
                if payload.get("message"):
                    message += f": {payload['message']}"
            except (ValueError, UnicodeDecodeError):
                pass
            raise GitHubError(message) from error
