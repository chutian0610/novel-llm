"""Unit-seam tests for layered configuration loading (ADR-0002).

Rules under test:
- defaults come from default.toml
- local.toml overrides defaults (deep merge) when present
- the test profile loads default.toml + test.toml and IGNORES local.toml,
  so tests never depend on one machine's overrides
- the active profile is selected by NOVEL_LLM_ENV
- unknown keys fail loudly instead of passing typos through
"""

from pathlib import Path

import pytest

from novel_llm.config import ConfigError, load_config

DEFAULT_TOML = """
[database]
url = "postgresql+asyncpg://novel:novel@postgres:5432/novel_llm"

[storage]
path = "/var/lib/novel-llm/storage"
"""

LOCAL_TOML = """
[database]
url = "postgresql+asyncpg://novel:novel@localhost:5432/novel_llm_local"
"""

TEST_TOML = """
[database]
url = "postgresql+asyncpg://novel:novel@127.0.0.1:5432/novel_llm_test"
"""


def make_config_dir(
    tmp_path: Path, *, default: bool = True, local: bool = False, test: bool = False
) -> Path:
    if default:
        (tmp_path / "default.toml").write_text(DEFAULT_TOML)
    if local:
        (tmp_path / "local.toml").write_text(LOCAL_TOML)
    if test:
        (tmp_path / "test.toml").write_text(TEST_TOML)
    return tmp_path


def test_defaults_apply_when_no_local_override(tmp_path):
    config = load_config(config_dir=make_config_dir(tmp_path))
    assert config.database.url == "postgresql+asyncpg://novel:novel@postgres:5432/novel_llm"


def test_local_overrides_defaults(tmp_path):
    config = load_config(config_dir=make_config_dir(tmp_path, local=True))
    assert config.database.url == "postgresql+asyncpg://novel:novel@localhost:5432/novel_llm_local"


def test_missing_local_file_is_not_an_error(tmp_path):
    config = load_config(config_dir=make_config_dir(tmp_path))
    assert config.storage.path == "/var/lib/novel-llm/storage"


def test_test_profile_ignores_local_overrides(tmp_path):
    config_dir = make_config_dir(tmp_path, local=True, test=True)
    config = load_config(env="test", config_dir=config_dir)
    assert config.database.url == "postgresql+asyncpg://novel:novel@127.0.0.1:5432/novel_llm_test"


def test_env_var_selects_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_LLM_ENV", "test")
    config = load_config(config_dir=make_config_dir(tmp_path, local=True, test=True))
    assert config.database.url == "postgresql+asyncpg://novel:novel@127.0.0.1:5432/novel_llm_test"


def test_unknown_profile_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(env="staging", config_dir=make_config_dir(tmp_path))


def test_unknown_keys_fail_loudly(tmp_path):
    config_dir = make_config_dir(tmp_path)
    (config_dir / "local.toml").write_text('[databse]\nurl = "x"\n')
    with pytest.raises(ConfigError, match="unknown"):
        load_config(config_dir=config_dir)
