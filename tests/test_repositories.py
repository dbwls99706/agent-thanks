import unittest

from agent_thanks.repositories import (
    extract_github_repositories,
    repository_from_metadata_url,
)


class RepositoryParsingTests(unittest.TestCase):
    def test_extracts_common_github_urls(self) -> None:
        text = " ".join(
            [
                "https://github.com/owner/project/tree/main",
                "git@github.com:Other/repo.git",
                "https://raw.githubusercontent.com/raw-owner/raw-repo/main/file.py",
            ]
        )
        self.assertEqual(
            extract_github_repositories(text),
            ["owner/project", "Other/repo", "raw-owner/raw-repo"],
        )

    def test_deduplicates_case_insensitively(self) -> None:
        text = "https://github.com/Owner/Repo https://github.com/owner/repo"
        self.assertEqual(extract_github_repositories(text), ["Owner/Repo"])

    def test_understands_registry_metadata_forms(self) -> None:
        self.assertEqual(
            repository_from_metadata_url({"type": "git", "url": "git+https://github.com/a/b.git"}),
            "a/b",
        )
        self.assertEqual(repository_from_metadata_url("github:a/b#main"), "a/b")


if __name__ == "__main__":
    unittest.main()
