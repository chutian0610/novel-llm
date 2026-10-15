"""Layered TOML configuration (ADR-0002).

Profiles:
- ``local`` (default): ``default.toml`` overlaid with gitignored ``local.toml``.
- ``test``: ``default.toml`` overlaid with ``test.toml``; machine-local
  overrides are deliberately ignored so tests never depend on one machine.

The active profile comes from the ``env`` argument or ``NOVEL_LLM_ENV``.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(Exception):
    """Raised when configuration files are missing, malformed, or unknown."""


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class LogSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: DatabaseSettings
    storage: StorageSettings
    log: LogSettings = Field(default_factory=LogSettings)


_PROFILE_LOCAL = "local"
_PROFILE_TEST = "test"
_VALID_PROFILES = (_PROFILE_LOCAL, _PROFILE_TEST)
_ENV_VAR = "NOVEL_LLM_ENV"
_CONFIG_DIR_VAR = "NOVEL_LLM_CONFIG_DIR"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"required configuration file is missing: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error


def _default_config_dir() -> Path:
    override = os.environ.get(_CONFIG_DIR_VAR)
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[3]
    for candidate in (repo_root / "config", Path.cwd() / "config"):
        if (candidate / "default.toml").exists():
            return candidate
    raise ConfigError(
        "could not locate the configuration directory; "
        "set NOVEL_LLM_CONFIG_DIR or run from the repository"
    )


def load_config(env: str | None = None, config_dir: Path | None = None) -> Config:
    profile = env if env is not None else os.environ.get(_ENV_VAR, _PROFILE_LOCAL)
    if profile not in _VALID_PROFILES:
        raise ConfigError(f"unknown profile {profile!r}; expected one of {_VALID_PROFILES}")

    directory = config_dir if config_dir is not None else _default_config_dir()
    merged: dict[str, Any] = _read_toml(directory / "default.toml")

    if profile == _PROFILE_LOCAL and (directory / "local.toml").exists():
        merged = _deep_merge(merged, _read_toml(directory / "local.toml"))
    if profile == _PROFILE_TEST and (directory / "test.toml").exists():
        merged = _deep_merge(merged, _read_toml(directory / "test.toml"))

    try:
        return Config.model_validate(merged)
    except ValidationError as error:
        raise ConfigError(f"unknown or invalid configuration keys: {error}") from error
