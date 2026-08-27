from __future__ import annotations

import json
import os
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import __version__
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

    def whoami(self) -> str:
        """Return the login that owns the active GitHub credentials."""
        if self.token:
            payload = self._request_json("/user")
            login = payload.get("login")
            if not isinstance(login, str) or not login:
                raise GitHubError("GitHub API did not return an authenticated login")
            return login

        result = self._run_gh_api("user", jq=".login")
        login = result.stdout.strip()
        if not login:
            raise GitHubError("GitHub CLI did not return an authenticated login")
        return login

    def is_starred(self, repository: str) -> bool:
        """Return whether the active user already starred a repository."""
        repository = validate_repository(repository)
        endpoint = f"/user/starred/{repository}"
        if self.token:
            status, _ = self._request(
                endpoint,
                "GET",
                expected_statuses={204},
                allow_not_found=True,
            )
            return status == 204

        result = self._run_gh_api(endpoint, check=False, silent=True)
        if result.returncode == 0:
            return True
        if "HTTP 404" in result.stderr:
            return False
        self._raise_gh_error(result)
        raise AssertionError("unreachable")

    def _mutate(self, repository: str, *, method: str) -> None:
        repository = validate_repository(repository)
        endpoint = f"/user/starred/{repository}"
        if self.token:
            self._request(endpoint, method, expected_statuses={204})
            return
        self._run_gh_api(
            endpoint,
            method=method,
            headers=["Content-Length: 0"],
            silent=True,
        )

    def _request(
        self,
        endpoint: str,
        method: str,
        *,
        expected_statuses: set[int],
        allow_not_found: bool = False,
    ) -> tuple[int, bytes]:
        mutating = method in {"PUT", "DELETE", "POST", "PATCH"}
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": f"agent-thanks/{__version__}",
        }
        if mutating:
            headers["Content-Length"] = "0"
        request = Request(
            f"https://api.github.com{endpoint}",
            data=b"" if mutating else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status not in expected_statuses:
                    raise GitHubError(f"Unexpected GitHub response: HTTP {response.status}")
                return response.status, response.read()
        except HTTPError as error:
            if allow_not_found and error.code == 404:
                return 404, b""
            raise self._github_error(error) from error

    def _request_json(self, endpoint: str) -> dict[str, object]:
        _, body = self._request(endpoint, "GET", expected_statuses={200})
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise GitHubError("GitHub API returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise GitHubError("GitHub API returned an unexpected response")
        return payload

    def _run_gh_api(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        headers: list[str] | None = None,
        jq: str | None = None,
        silent: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if not shutil.which("gh"):
            raise GitHubError(
                "Authentication required. Run 'gh auth login' or set GH_TOKEN "
                "with Starring: write permission."
            )

        command = ["gh", "api", "--method", method, endpoint]
        for header in headers or []:
            command.extend(["--header", header])
        if jq is not None:
            command.extend(["--jq", jq])
        if silent:
            command.append("--silent")
        result = subprocess.run(command, capture_output=True, text=True)
        if check and result.returncode != 0:
            self._raise_gh_error(result)
        return result

    @staticmethod
    def _raise_gh_error(result: subprocess.CompletedProcess[str]) -> None:
        message = result.stderr.strip() or "GitHub CLI request failed"
        raise GitHubError(message)

    @staticmethod
    def _github_error(error: HTTPError) -> GitHubError:
        message = f"GitHub API returned HTTP {error.code}"
        try:
            payload = json.loads(error.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("message"):
                message += f": {payload['message']}"
        except (ValueError, UnicodeDecodeError):
            pass
        return GitHubError(message)
