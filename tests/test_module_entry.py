import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_thanks import __version__


ROOT = Path(__file__).resolve().parents[1]


class ModuleEntryTests(unittest.TestCase):
    def invoke(self, kind: str, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        launcher = (
            ["-m", "agent_thanks"] if kind == "module" else
            ["-c", "from agent_thanks.entry import main; raise SystemExit(main())"]
        )
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.run(
            [sys.executable, *launcher, *arguments], cwd=root, env=environment,
            capture_output=True, text=True, timeout=20,
        )

    def test_module_and_console_entry_share_primary_help_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arguments in ((), ("--help",), ("thanks", "--help"), ("--version",)):
                with self.subTest(arguments=arguments):
                    module = self.invoke("module", root, *arguments)
                    entry = self.invoke("entry", root, *arguments)
                    self.assertEqual(module.returncode, 0, module.stderr)
                    self.assertEqual(entry.returncode, 0, entry.stderr)
                    self.assertEqual(module.stdout, entry.stdout)
                    self.assertEqual(module.stderr, entry.stderr)
                    if arguments == ("--version",):
                        self.assertEqual(module.stdout.strip(), f"agent-thanks {__version__}")
                    elif arguments == ("thanks", "--help"):
                        self.assertIn("--all-sessions", module.stdout)
                    else:
                        self.assertIn("agent-thanks thanks", module.stdout)
                        self.assertIn("agent-thanks exec", module.stdout)
            self.assertFalse((root / ".agent-thanks").exists())

    def test_module_and_console_entry_preserve_captured_child_status_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = (
                "exec", "--repo", str(root), "--", sys.executable, "-c",
                "import sys; sys.stdout.write('child-output'); sys.exit(7)",
            )
            module = self.invoke("module", root, *arguments)
            entry = self.invoke("entry", root, *arguments)
            self.assertEqual(module.returncode, 7, module.stderr)
            self.assertEqual(entry.returncode, 7, entry.stderr)
            self.assertEqual(module.stdout, "child-output")
            self.assertEqual(module.stdout, entry.stdout)
            self.assertEqual(module.stderr, entry.stderr)
            records = [json.loads(line) for line in (
                root / ".agent-thanks" / "sessions" / "executions.jsonl"
            ).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["event"] for item in records], ["started", "finished"] * 2)
            self.assertEqual([item["exit_code"] for item in records if item["event"] == "finished"], [7, 7])


if __name__ == "__main__":
    unittest.main()
