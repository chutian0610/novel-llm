"""Shared black-box fixtures: an ephemeral PostgreSQL database per test.

The database itself runs via `docker compose -f compose.yaml -f compose.test.yaml
up -d postgres` (started by `make check`); each test creates, migrates, and
drops its own database, so tests never share mutable state.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from novel_llm.api.app import create_app
from novel_llm.config import Config, DatabaseSettings, load_config
from novel_llm.infrastructure.migrations import upgrade_to_head


def _with_database_name(url: str, name: str) -> str:
    # str(URL) masks the password as ***; render_as_string keeps it real.
    return make_url(url).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_config() -> Config:
    return load_config(env="test")


@asynccontextmanager
async def _ephemeral_database(test_config: Config) -> AsyncIterator[str]:
    admin_url = _with_database_name(test_config.database.url, "postgres")
    db_name = f"novel_llm_test_{uuid4().hex[:8]}"

    server = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with server.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    await server.dispose()

    try:
        yield _with_database_name(test_config.database.url, db_name)
    finally:
        teardown = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        async with teardown.connect() as connection:
            await connection.execute(sa.text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        await teardown.dispose()


@pytest.fixture
async def db_url(test_config: Config) -> AsyncIterator[str]:
    """A per-test ephemeral database: no shared mutable state between tests."""
    async with _ephemeral_database(test_config) as url:
        yield url


@pytest.fixture
async def client(test_config: Config, db_url: str):
    """The system black-box seam: the per-test database migrated to head by the
    real Alembic scripts, and the ASGI app with its real lifespan (the database
    engine comes up with the app, as it does under uvicorn)."""
    await upgrade_to_head(db_url)
    app_config = test_config.model_copy(update={"database": DatabaseSettings(url=db_url)})
    app = create_app(config=app_config)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client


@pytest.fixture
async def fresh_client(test_config: Config):
    """The app against a per-test database before any migration runs."""
    async with _ephemeral_database(test_config) as url:
        app_config = test_config.model_copy(update={"database": DatabaseSettings(url=url)})
        app = create_app(config=app_config)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client
