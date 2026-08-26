from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_thanks.resolver import PackageRepositoryResolver
from agent_thanks.scanner import ProjectScanner


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class ScannerTests(unittest.TestCase):
    def test_scans_new_dependency_and_session_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.com")
            manifest = root / "package.json"
            manifest.write_text('{"dependencies":{}}\n', encoding="utf-8")
            git(root, "add", "package.json")
            git(root, "commit", "-m", "base")

            manifest.write_text(
                '{"dependencies":{"robot-lib":"github:robotics/robot-lib"}}\n',
                encoding="utf-8",
            )
            session = root / "session.log"
            session.write_text(
                "git clone https://github.com/BehaviorTree/BehaviorTree.CPP.git\n"
                "Viewed https://github.com/example/read-only\n",
                encoding="utf-8",
            )

            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan([session])
            by_name = {item.repository: item for item in report.candidates}

            self.assertTrue(by_name["robotics/robot-lib"].recommended)
            self.assertTrue(by_name["BehaviorTree/BehaviorTree.CPP"].recommended)
            self.assertFalse(by_name["example/read-only"].recommended)

    def test_non_git_project_scans_current_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text(
                "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()
            self.assertEqual(report.base, None)
            self.assertEqual(report.candidates[0].repository, "acme/demo")

    def test_unborn_git_repository_scans_current_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "requirements.txt").write_text(
                "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()
            self.assertIsNone(report.base)
            self.assertEqual(report.candidates[0].repository, "acme/demo")


if __name__ == "__main__":
    unittest.main()
