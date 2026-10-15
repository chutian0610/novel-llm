"""Process health endpoints (ADR-0008): /healthz liveness, /readyz readiness.

Readiness checks three components:
- database:   the process can connect and query PostgreSQL
- migrations: the schema history is applied up to the code-owned head
- queue:      the transactional claim primitive (FOR UPDATE SKIP LOCKED) works

The queue check probes the claim primitive on the schema-history table until
the jobs tables exist; the Job runtime ticket replaces it with a probe over
the real queue.
"""

from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from novel_llm.infrastructure.db import create_db_engine
from novel_llm.infrastructure.migrations import head_revision

router = APIRouter()


class HealthResponse(BaseModel):
    status: str


class CheckStatus(BaseModel):
    status: Literal["ok", "error"]
    detail: str = ""


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, CheckStatus]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: the process is up. Carries no dependency information."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(request: Request, response: Response) -> ReadinessResponse:
    database_url = request.app.state.config.database.url
    checks: dict[str, CheckStatus] = {}

    engine = create_db_engine(database_url)

    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
        checks["database"] = CheckStatus(status="ok")
    except Exception as error:
        checks["database"] = CheckStatus(status="error", detail=str(error))

    if checks["database"].status == "ok":
        checks["migrations"] = await _check_migrations(engine)
        if checks["migrations"].status == "ok":
            checks["queue"] = await _check_queue_claim_primitive(engine)
        else:
            checks["queue"] = CheckStatus(
                status="error", detail="queue probe needs a migrated schema"
            )
    else:
        checks["migrations"] = CheckStatus(status="error", detail="database unreachable")
        checks["queue"] = CheckStatus(status="error", detail="database unreachable")

    await engine.dispose()

    ready = all(check.status == "ok" for check in checks.values())
    response.status_code = 200 if ready else 503
    return ReadinessResponse(status="ready" if ready else "unavailable", checks=checks)


async def _check_migrations(engine) -> CheckStatus:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
            applied = {row[0] for row in result.fetchall()}
    except Exception:
        return CheckStatus(status="error", detail="schema history is missing; run migrations")
    head = head_revision()
    if applied == {head}:
        return CheckStatus(status="ok")
    return CheckStatus(status="error", detail=f"applied {sorted(applied)} != head {head}")


async def _check_queue_claim_primitive(engine) -> CheckStatus:
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text("SELECT version_num FROM alembic_version FOR UPDATE SKIP LOCKED")
            )
        return CheckStatus(status="ok")
    except Exception as error:
        return CheckStatus(status="error", detail=f"claim primitive failed: {error}")
