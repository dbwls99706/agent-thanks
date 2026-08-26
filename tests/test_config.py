import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_thanks.config import ConfigError, ConfigStore, Settings, default_config_path


class ConfigTests(unittest.TestCase):
    def test_environment_override_controls_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "custom.json"
            with patch.dict(os.environ, {"AGENT_THANKS_CONFIG": str(expected)}):
                self.assertEqual(default_config_path(), expected)

    def test_atomic_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            store = ConfigStore(path)
            store.save(Settings(consent_mode="auto"))
            self.assertEqual(store.load(), Settings(consent_mode="auto"))
            self.assertEqual(json.loads(path.read_text())["schema_version"], 1)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_config_uses_safe_ask_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "missing.json")
            self.assertFalse(store.exists)
            self.assertEqual(store.load().consent_mode, "ask")

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Settings(consent_mode="everything")

    def test_malformed_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                ConfigStore(path).load()

    def test_invalid_schema_type_is_reported_as_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version": null}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                ConfigStore(path).load()


if __name__ == "__main__":
    unittest.main()
