from unittest.mock import MagicMock, patch
import unittest

from agent_thanks.github import GitHubClient, validate_repository


class GitHubClientTests(unittest.TestCase):
    def test_validate_repository(self) -> None:
        self.assertEqual(validate_repository("owner/repo"), "owner/repo")
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

    @patch("agent_thanks.github.urlopen")
    def test_unstar_uses_delete(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 204
        urlopen.return_value.__enter__.return_value = response

        GitHubClient(token="secret").unstar("owner/repo")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "DELETE")


if __name__ == "__main__":
    unittest.main()
