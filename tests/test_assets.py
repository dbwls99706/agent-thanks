from pathlib import Path
import struct
import unittest
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"


class DocumentationAssetTests(unittest.TestCase):
    def test_readme_banner_is_accessible_and_self_contained(self) -> None:
        path = ASSETS / "agent-thanks-banner.svg"
        root = ElementTree.parse(path).getroot()

        self.assertEqual(root.get("viewBox"), "0 0 1600 534")
        self.assertTrue(root.findtext(f"{SVG_NAMESPACE}title"))
        self.assertTrue(root.findtext(f"{SVG_NAMESPACE}desc"))
        self.assertFalse(root.findall(f".//{SVG_NAMESPACE}script"))

        serialized = path.read_text(encoding="utf-8").casefold()
        self.assertNotIn("javascript:", serialized)
        self.assertNotIn('href="http', serialized)

    def test_social_preview_has_github_recommended_dimensions(self) -> None:
        path = ASSETS / "agent-thanks-social-preview.png"
        with path.open("rb") as stream:
            self.assertEqual(stream.read(8), b"\x89PNG\r\n\x1a\n")
            length = struct.unpack(">I", stream.read(4))[0]
            self.assertEqual(stream.read(4), b"IHDR")
            width, height = struct.unpack(">II", stream.read(8))

        self.assertEqual(length, 13)
        self.assertEqual((width, height), (1280, 640))

    def test_terminal_walkthrough_is_accessible_and_self_contained(self) -> None:
        path = ASSETS / "terminal-walkthrough.svg"
        root = ElementTree.parse(path).getroot()

        self.assertEqual(root.get("viewBox"), "0 0 1400 780")
        self.assertTrue(root.findtext(f"{SVG_NAMESPACE}title"))
        self.assertTrue(root.findtext(f"{SVG_NAMESPACE}desc"))
        self.assertFalse(root.findall(f".//{SVG_NAMESPACE}script"))

        serialized = path.read_text(encoding="utf-8").casefold()
        self.assertNotIn("javascript:", serialized)
        self.assertNotIn('href="http', serialized)

    def test_readme_references_the_banner(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/assets/agent-thanks-banner.svg", readme)
        self.assertIn("docs/assets/terminal-walkthrough.svg", readme)
        self.assertIn('alt="AI: done in 12 seconds.', readme)


if __name__ == "__main__":
    unittest.main()
