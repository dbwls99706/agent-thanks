from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from agent_thanks import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_versions_stay_in_sync(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin = json.loads(
            (
                ROOT
                / "plugins"
                / "agent-thanks"
                / ".claude-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        bug_template = (
            ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        ).read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(pyproject["project"]["version"], __version__)
        self.assertEqual(marketplace["plugins"][0]["version"], __version__)
        self.assertEqual(plugin["version"], __version__)

        placeholder = re.search(
            r'id: version.*?placeholder: "([^"]+)"',
            bug_template,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(placeholder)
        self.assertEqual(placeholder.group(1), __version__)
        self.assertIn(f"## {__version__} -", changelog)


if __name__ == "__main__":
    unittest.main()
