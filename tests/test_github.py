from io import BytesIO
import os
import subprocess
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch
import unittest

from agent_thanks.github import GitHubClient, GitHubError, validate_repository


class GitHubClientTests(unittest.TestCase):
    def test_validate_repository(self) -> None:
        self.assertEqual(validate_repository("owner/repo"), "owner/repo")
        self.assertEqual(
            validate_repository("BehaviorTree/BehaviorTree.CPP"),
            "BehaviorTree/BehaviorTree.CPP",
        )
        with self.assertRaises(ValueError):
            validate_repository("https://github.com/owner/repo")

    @patch("agent_thanks.github.urlopen")
    def test_star_uses_zero_length_put(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 204
        urlopen.return_value.__enter__.return_value = response

        GitHubClient(token="secret").star("owner/repo")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.full_url, "https://api.github.com/user/starred/owner/repo")
        self.assertEqual(request.data, b"")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("X-github-api-version"), "2026-03-10")

    @patch("agent_thanks.github.urlopen")
    def test_unstar_uses_delete(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 204
        urlopen.return_value.__enter__.return_value = response

        GitHubClient(token="secret").unstar("owner/repo")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "DELETE")

    @patch("agent_thanks.github.urlopen")
    def test_whoami_returns_authenticated_token_user(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"login":"octocat"}'
        urlopen.return_value.__enter__.return_value = response

        self.assertEqual(GitHubClient(token="secret").whoami(), "octocat")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.full_url, "https://api.github.com/user")
        self.assertIsNone(request.data)

    @patch("agent_thanks.github.urlopen")
    def test_is_starred_handles_present_and_absent_state(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 204
        response.read.return_value = b""
        urlopen.return_value.__enter__.return_value = response
        client = GitHubClient(token="secret")

        self.assertTrue(client.is_starred("owner/repo"))

        urlopen.side_effect = HTTPError(
            url="https://api.github.com/user/starred/owner/repo",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=BytesIO(b'{"message":"Not Found"}'),
        )
        self.assertFalse(client.is_starred("owner/repo"))

    @patch("agent_thanks.github.subprocess.run")
    @patch("agent_thanks.github.shutil.which", return_value="/usr/bin/gh")
    def test_whoami_uses_github_cli(
        self, _which: MagicMock, run: MagicMock
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout="octocat\n", stderr=""
        )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(GitHubClient().whoami(), "octocat")

        command = run.call_args.args[0]
        self.assertIn("user", command)
        self.assertEqual(command[-2:], ["--jq", ".login"])

    @patch("agent_thanks.github.urlopen")
    def test_api_authentication_and_permission_errors_are_reported(
        self, urlopen: MagicMock
    ) -> None:
        errors = (
            (401, "Bad credentials"),
            (403, "Resource not accessible"),
            (404, "Not Found"),
        )
        for status, message in errors:
            with self.subTest(status=status):
                urlopen.side_effect = HTTPError(
                    url="https://api.github.com/user/starred/owner/repo",
                    code=status,
                    msg=message,
                    hdrs=None,
                    fp=BytesIO(f'{{"message":"{message}"}}'.encode()),
                )
                with self.assertRaisesRegex(
                    GitHubError, f"HTTP {status}: {message}"
                ):
                    GitHubClient(token="invalid").star("owner/repo")

    @patch("agent_thanks.github.shutil.which", return_value=None)
    def test_missing_authentication_is_reported(self, _which: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            GitHubError, "Authentication required"
        ):
            GitHubClient().star("owner/repo")

    @patch("agent_thanks.github.subprocess.run")
    @patch("agent_thanks.github.shutil.which", return_value="/usr/bin/gh")
    def test_github_cli_permission_failure_is_reported(
        self, _which: MagicMock, run: MagicMock
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["gh"],
            returncode=1,
            stdout="",
            stderr="HTTP 403: Resource not accessible by personal access token",
        )
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            GitHubError, "HTTP 403"
        ):
            GitHubClient().star("owner/repo")

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gh", "api", "--method", "PUT"])
        self.assertIn("/user/starred/owner/repo", command)


if __name__ == "__main__":
    unittest.main()
