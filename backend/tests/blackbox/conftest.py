"""Shared black-box fixtures: an ephemeral PostgreSQL database per test.

The database itself runs via `docker compose -f compose.yaml -f compose.test.yaml
up -d postgres` (started by `make check`); each test creates, migrates, and
drops its own database, so tests never share mutable state.

Two client fixtures cover the HTTP boundary: `client` drives the ASGI app
in-process (fast, for request/response endpoints); `live_server` runs the app
under a real uvicorn listener on loopback (SSE only — the in-process ASGI
transport buffers until the response completes, so it cannot observe a live
stream).

`start_worker` runs the real Worker loop in-process against the same
database (the runtime is a library object the entrypoint merely wires), with
the deterministic fault-injection levers the framework acceptance tests
need. `start_worker_process` goes one level blacker: it runs the actual
`python -m novel_llm.worker.main` entrypoint as an OS process, so crash
recovery is exercised against a Worker that can really be killed."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from asgi_lifespan import LifespanManager
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import novel_llm.workflows.trivial  # noqa: F401 — registers the workflow definitions
from novel_llm.api.app import create_app
from novel_llm.config import Config, DatabaseSettings, WorkerSettings, load_config
from novel_llm.infrastructure.migrations import upgrade_to_head
from novel_llm.worker.runtime import Worker, WorkerFaults


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


def _app_config_for(test_config: Config, url: str) -> Config:
    return test_config.model_copy(update={"database": DatabaseSettings(url=url)})


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
    app = create_app(config=_app_config_for(test_config, db_url))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client


@pytest.fixture
async def live_server(test_config: Config, db_url: str) -> AsyncIterator[str]:
    """The same app under a real uvicorn listener on a loopback port — for
    tests that must observe a live SSE stream."""
    await upgrade_to_head(db_url)
    app = create_app(config=_app_config_for(test_config, db_url))
    server_config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(server_config)
    serve_task = asyncio.create_task(server.serve())
    for _ in range(500):  # fail fast instead of hanging if the server never starts
        if server.started:
            break
        await asyncio.sleep(0.02)
    if not server.started:
        serve_task.cancel()
        raise RuntimeError("the live SSE server never started")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await serve_task


@pytest.fixture
async def fresh_client(test_config: Config):
    """The app against a per-test database before any migration runs."""
    async with _ephemeral_database(test_config) as url:
        app = create_app(config=_app_config_for(test_config, url))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client


@dataclass
class WorkerHandle:
    """A running real Worker loop and the engine underneath it."""

    worker: Worker
    engine: AsyncEngine
    task: asyncio.Task[None]


@pytest.fixture
async def start_worker(db_url: str) -> AsyncIterator[Callable[..., WorkerHandle]]:
    """Start real Worker loops (the same object `python -m novel_llm.worker.main`
    wires) against the per-test database; all are stopped gracefully on
    teardown."""
    handles: list[WorkerHandle] = []

    def _start(
        *,
        concurrency: int = 1,
        poll_seconds: float = 0.05,
        graceful_timeout_seconds: float = 1.0,
        skip_heartbeats: bool = False,
    ) -> WorkerHandle:
        engine = create_async_engine(db_url)
        settings = WorkerSettings(
            concurrency=concurrency,
            poll_seconds=poll_seconds,
            graceful_timeout_seconds=graceful_timeout_seconds,
        )
        worker = Worker(
            engine,
            settings,
            faults=WorkerFaults(skip_heartbeats=skip_heartbeats),
        )
        task = asyncio.create_task(worker.run())
        handle = WorkerHandle(worker=worker, engine=engine, task=task)
        handles.append(handle)
        return handle

    yield _start

    for handle in handles:
        handle.worker.stop()
    if handles:
        await asyncio.gather(*(handle.task for handle in handles), return_exceptions=True)
    for handle in handles:
        await handle.engine.dispose()


@dataclass
class WorkerProcessHandle:
    """A real Worker process (the `python -m novel_llm.worker.main`
    entrypoint) wired to the per-test database through its own config
    directory."""

    process: asyncio.subprocess.Process
    config_dir: Path


@pytest.fixture
async def start_worker_process(
    db_url: str, tmp_path: Path
) -> AsyncIterator[Callable[..., Awaitable[WorkerProcessHandle]]]:
    """Start real Worker processes — the OS-level entrypoint a deploy runs —
    against the per-test database. Each process gets its own layered config
    directory (fast polling, short graceful timeout) via NOVEL_LLM_CONFIG_DIR,
    exactly as the production entrypoint loads configuration. Teardown
    terminates anything still running."""
    handles: list[WorkerProcessHandle] = []
    backend_root = Path(__file__).resolve().parents[2]

    async def _start() -> WorkerProcessHandle:
        config_dir = tmp_path / f"worker-{len(handles)}-config"
        config_dir.mkdir()
        (config_dir / "default.toml").write_text(
            "\n".join(
                [
                    "[database]",
                    f'url = "{db_url}"',
                    "",
                    "[storage]",
                    f'path = "{tmp_path / f"worker-{len(handles)}-storage"}"',
                    "",
                    "[log]",
                    'level = "WARNING"',
                    "",
                    "[worker]",
                    "concurrency = 1",
                    "poll_seconds = 0.05",
                    "graceful_timeout_seconds = 1.0",
                    "",
                ]
            )
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "novel_llm.worker.main",
            cwd=backend_root,
            env={**os.environ, "NOVEL_LLM_CONFIG_DIR": str(config_dir.resolve())},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        handle = WorkerProcessHandle(process=process, config_dir=config_dir)
        handles.append(handle)
        return handle

    yield _start

    for handle in handles:
        if handle.process.returncode is None:
            handle.process.terminate()
    for handle in handles:
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=10.0)
        except TimeoutError:
            handle.process.kill()
            await handle.process.wait()
        # Drain the pipes so the transports close cleanly with the loop.
        out, err = await handle.process.communicate()
        if handle.process.returncode not in (0, -9):
            print(f"\nworker process (rc={handle.process.returncode}) stderr:\n{err.decode()}")


@pytest.fixture
def create_project(client: httpx.AsyncClient) -> Callable[..., object]:
    """Create a Project through the API and return its resource."""

    async def _create(**overrides: object) -> dict:
        body = {"title": f"项目-{uuid4().hex[:6]}", **overrides}
        response = await client.post(
            "/api/v1/projects",
            json=body,
            headers={"Idempotency-Key": f"create-{uuid4()}"},
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create
