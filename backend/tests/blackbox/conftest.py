"""Shared black-box fixtures: an ephemeral PostgreSQL database per session.

The database itself runs via `docker compose -f compose.yaml -f compose.test.yaml
up -d postgres` (started by `make check`); each test session then creates and
drops its own database inside it, so runs never share mutable state.
"""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from novel_llm.config import Config, DatabaseSettings, load_config


def _with_database_name(url: str, name: str) -> str:
    # str(URL) masks the password as ***; render_as_string keeps it real.
    return make_url(url).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_config() -> Config:
    return load_config(env="test")


@pytest.fixture(scope="session")
async def ephemeral_db_url(test_config: Config):
    admin_url = _with_database_name(test_config.database.url, "postgres")
    db_name = f"novel_llm_test_{uuid.uuid4().hex[:8]}"

    server = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    await server.dispose()

    yield _with_database_name(test_config.database.url, db_name)

    teardown = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with teardown.connect() as connection:
        await connection.execute(sa.text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
    await teardown.dispose()


@pytest.fixture
def app_config(test_config: Config, ephemeral_db_url: str) -> Config:
    """The test profile with only the database pointed at the ephemeral DB."""
    return test_config.model_copy(update={"database": DatabaseSettings(url=ephemeral_db_url)})
