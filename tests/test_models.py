from pathlib import Path
import tempfile
import unittest

from agent_thanks.models import Candidate, Evidence, Report


class ReportTests(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        report = Report(
            root="/tmp/project",
            base="HEAD",
            candidates=[
                Candidate(
                    repository="owner/repo",
                    evidence=[
                        Evidence(
                            kind="direct_dependency",
                            source="pyproject.toml",
                            detail="Added dependency",
                            confidence="high",
                            meaningful=True,
                        )
                    ],
                )
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            report.write(path)
            restored = Report.read(path)
        self.assertEqual(restored.candidates[0].repository, "owner/repo")
        self.assertTrue(restored.candidates[0].recommended)


if __name__ == "__main__":
    unittest.main()
