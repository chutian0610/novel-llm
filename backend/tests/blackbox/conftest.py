"""Shared black-box fixtures: an ephemeral PostgreSQL database per session.

The database itself runs via `docker compose -f compose.yaml -f compose.test.yaml
up -d postgres` (started by `make check`); each test session then creates and
drops its own database inside it, so runs never share mutable state.
"""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from novel_llm.config import Config, DatabaseSettings, LogSettings, StorageSettings, load_config


def _with_database_name(url: str, name: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + name


@pytest.fixture(scope="session")
def test_config() -> Config:
    return load_config(env="test")


@pytest.fixture(scope="session")
async def ephemeral_db_url(test_config: Config):
    server = create_async_engine(
        _with_database_name(test_config.database.url, "postgres"),
        isolation_level="AUTOCOMMIT",
    )
    db_name = f"novel_llm_test_{uuid.uuid4().hex[:8]}"
    async with server.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    await server.dispose()

    yield _with_database_name(test_config.database.url, db_name)

    teardown = create_async_engine(
        _with_database_name(test_config.database.url, "postgres"),
        isolation_level="AUTOCOMMIT",
    )
    async with teardown.connect() as connection:
        await connection.execute(sa.text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
    await teardown.dispose()


@pytest.fixture
def app_config(ephemeral_db_url: str) -> Config:
    return Config(
        database=DatabaseSettings(url=ephemeral_db_url),
        storage=StorageSettings(path="/tmp/novel-llm-test-storage"),
        log=LogSettings(level="WARNING"),
    )
