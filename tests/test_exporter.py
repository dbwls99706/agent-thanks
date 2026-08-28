from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from agent_thanks.cli import main
from agent_thanks.exporter import render_markdown
from agent_thanks.models import Candidate, Evidence, Report, UnresolvedDependency


class MarkdownExporterTests(unittest.TestCase):
    def make_report(self, root: Path) -> Report:
        return Report(
            root=str(root),
            base="HEAD",
            candidates=[
                Candidate(
                    "owner/verified",
                    [
                        Evidence(
                            "direct_dependency",
                            str(root / "packages" / "pyproject.toml"),
                            "Added package_with_[special] characters",
                            "high",
                            True,
                        )
                    ],
                ),
                Candidate(
                    "owner/reference",
                    [
                        Evidence(
                            "session_reference",
                            "/Users/private-name/logs/session.log:42",
                            "Repository was viewed",
                            "low",
                            False,
                        )
                    ],
                ),
            ],
            unresolved_dependencies=[
                UnresolvedDependency("pypi", "unmapped", str(root / "requirements.txt"))
            ],
            generated_at="2026-08-28T00:00:00+00:00",
        )

    def test_default_export_contains_verified_use_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rendered = render_markdown(self.make_report(Path(directory)))

        self.assertIn("## Verified use", rendered)
        self.assertIn("owner/verified", rendered)
        self.assertNotIn("## References to review", rendered)
        self.assertNotIn("owner/reference", rendered)
        self.assertIn("## Unresolved dependencies", rendered)

    def test_optional_reference_section_is_clearly_separated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rendered = render_markdown(
                self.make_report(Path(directory)), include_low_confidence=True
            )

        self.assertIn("## References to review", rendered)
        self.assertIn("owner/reference", rendered)
        self.assertIn("Session reference", rendered)

    def test_export_removes_absolute_local_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = render_markdown(
                self.make_report(root), include_low_confidence=True
            )

        self.assertNotIn(str(root), rendered)
        self.assertNotIn("/Users/private-name", rendered)
        self.assertIn("packages/pyproject.toml", rendered)
        self.assertIn("session.log:42", rendered)

    def test_relative_hidden_directory_is_preserved_without_parent_traversal(self) -> None:
        report = Report(
            root="/workspace/project",
            base="HEAD",
            candidates=[
                Candidate(
                    "owner/verified",
                    [
                        Evidence(
                            "direct_dependency",
                            ".github/workflows/tests.yml",
                            "Added dependency",
                            "high",
                            True,
                        ),
                        Evidence(
                            "session_usage",
                            "../../private/session.log:9",
                            "Used repository",
                            "high",
                            True,
                        ),
                    ],
                )
            ],
        )

        rendered = render_markdown(report)

        self.assertIn(".github/workflows/tests.yml", rendered)
        self.assertIn("session.log:9", rendered)
        self.assertNotIn("../", rendered)

    def test_windows_absolute_sources_are_sanitized_on_every_platform(self) -> None:
        report = Report(
            root=r"C:\work\project",
            base="HEAD",
            candidates=[
                Candidate(
                    "owner/verified",
                    [
                        Evidence(
                            "session_usage",
                            r"C:\work\project\logs\session.log:7",
                            "Used repository",
                            "high",
                            True,
                        ),
                        Evidence(
                            "session_usage",
                            r"D:\private-name\external.log:8",
                            "Used external repository",
                            "high",
                            True,
                        ),
                    ],
                )
            ],
        )

        rendered = render_markdown(report)

        self.assertIn("logs/session.log:7", rendered)
        self.assertIn("external.log:8", rendered)
        self.assertNotIn("C:\\", rendered)
        self.assertNotIn("D:\\", rendered)
        self.assertNotIn("private-name", rendered)

    def test_markdown_control_characters_are_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rendered = render_markdown(self.make_report(Path(directory)))

        self.assertIn(r"package\_with\_\[special\]", rendered)

    def test_cli_can_write_markdown_without_github_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            output_path = root / "OPEN_SOURCE_USE.md"
            self.make_report(root).write(report_path)

            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "export",
                        str(report_path),
                        "--output",
                        str(output_path),
                        "--include-low-confidence",
                    ]
                )

            self.assertEqual(status, 0)
            self.assertTrue(output_path.is_file())
            self.assertIn("Markdown:", output.getvalue())
            self.assertIn("owner/reference", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
