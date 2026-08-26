from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any


CONSENT_MODES = ("ask", "auto")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    consent_mode: str = "ask"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.consent_mode not in CONSENT_MODES:
            raise ConfigError(
                f"Unknown consent mode '{self.consent_mode}'. "
                f"Expected one of: {', '.join(CONSENT_MODES)}"
            )
        if self.schema_version != 1:
            raise ConfigError(f"Unsupported config schema version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consent_mode": self.consent_mode,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Settings":
        return cls(
            consent_mode=str(value.get("consent_mode", "ask")),
            schema_version=int(value.get("schema_version", 1)),
        )


def default_config_path() -> Path:
    override = os.environ.get("AGENT_THANKS_CONFIG")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / "agent-thanks" / "config.json"

    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "agent-thanks" / "config.json"
    return Path.home() / ".config" / "agent-thanks" / "config.json"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_config_path()).expanduser()

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Settings:
        if not self.exists:
            return Settings()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"Could not read config file {self.path}: {error}") from error
        if not isinstance(value, dict):
            raise ConfigError(f"Config file must contain a JSON object: {self.path}")
        try:
            return Settings.from_dict(value)
        except (ConfigError, TypeError, ValueError) as error:
            raise ConfigError(f"Invalid config file {self.path}: {error}") from error

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".config-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
        except OSError as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ConfigError(f"Could not save config file {self.path}: {error}") from error
