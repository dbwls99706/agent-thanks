import unittest

from agent_thanks.repositories import (
    extract_github_repository_occurrences,
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
                "go get github.com/bare-owner/bare-repo/cmd/tool",
            ]
        )
        self.assertEqual(
            extract_github_repositories(text),
            [
                "owner/project",
                "Other/repo",
                "raw-owner/raw-repo",
                "bare-owner/bare-repo",
            ],
        )

    def test_deduplicates_case_insensitively(self) -> None:
        text = "https://github.com/Owner/Repo https://github.com/owner/repo"
        self.assertEqual(extract_github_repositories(text), ["Owner/Repo"])

    def test_occurrences_follow_text_order_without_overlapping_duplicates(self) -> None:
        text = (
            "git@github.com:first/ssh.git then "
            "https://github.com/second/web and "
            "https://raw.githubusercontent.com/third/raw/main/file.py"
        )
        occurrences = extract_github_repository_occurrences(text)
        self.assertEqual(
            [repository for repository, _, _ in occurrences],
            ["first/ssh", "second/web", "third/raw"],
        )
        self.assertEqual(
            [text[start:end] for _, start, end in occurrences],
            [
                "git@github.com:first/ssh.git",
                "https://github.com/second/web",
                "https://raw.githubusercontent.com/third/raw",
            ],
        )
        self.assertEqual(
            extract_github_repositories(text),
            ["second/web", "first/ssh", "third/raw"],
        )

    def test_occurrences_ignore_bare_github_text_nested_in_a_url_path(self) -> None:
        text = "https://github.com/a/b/blob/main/github.com/c/d"

        self.assertEqual(
            [
                repository
                for repository, _, _ in extract_github_repository_occurrences(text)
            ],
            ["a/b"],
        )

    def test_normalization_strips_sentence_punctuation_before_git_suffix(self) -> None:
        text = "Cloned https://github.com/punctuation/target.git."

        self.assertEqual(extract_github_repositories(text), ["punctuation/target"])
        self.assertEqual(
            repository_from_metadata_url("https://github.com/punctuation/target.git."),
            "punctuation/target",
        )

    def test_general_extractor_preserves_pattern_priority(self) -> None:
        text = "github.com/Owner/Repo then https://github.com/owner/repo"

        self.assertEqual(extract_github_repositories(text), ["owner/repo"])

    def test_understands_registry_metadata_forms(self) -> None:
        self.assertEqual(
            repository_from_metadata_url({"type": "git", "url": "git+https://github.com/a/b.git"}),
            "a/b",
        )
        self.assertEqual(repository_from_metadata_url("github:a/b#main"), "a/b")
        self.assertEqual(
            repository_from_metadata_url("demo @ git+https://github.com/a/b.git@main"),
            "a/b",
        )

    def test_malformed_metadata_urls_fail_closed(self) -> None:
        self.assertIsNone(repository_from_metadata_url("https://[example.invalid/a/b"))
        self.assertIsNone(repository_from_metadata_url("https://github.com:bad/a/b"))
        self.assertIsNone(
            repository_from_metadata_url(
                "https://example.com/?next=https://github.com/victim/repo"
            )
        )


if __name__ == "__main__":
    unittest.main()
