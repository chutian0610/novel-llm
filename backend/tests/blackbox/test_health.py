"""Black-box (thin usage) tests at the HTTP boundary: health endpoints (ADR-0008).

These drive the ASGI app directly — same public surface a real client sees —
without needing the Worker or any workflow to run.
"""

import httpx
import pytest

from novel_llm.api.app import create_app
from novel_llm.config import Config
from novel_llm.infrastructure.migrations import upgrade_to_head


@pytest.fixture
async def client(app_config: Config):
    app = create_app(config=app_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def test_healthz_reports_liveness(client: httpx.AsyncClient):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_is_unavailable_before_migrations(client: httpx.AsyncClient):
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"]["status"] == "error"


async def test_readyz_reports_ready_after_migrations(client: httpx.AsyncClient, app_config: Config):
    await upgrade_to_head(app_config.database.url)
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert all(check["status"] == "ok" for check in body["checks"].values())
