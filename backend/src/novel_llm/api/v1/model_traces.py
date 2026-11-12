"""Model trace resources under /api/v1 (ADR-0008): full traces — rendered
prompt, completion, metadata, latency, token use, cost, repair linkage —
keyset-paginated per Project, newest first."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.problems import Problem
from novel_llm.domain import projects
from novel_llm.domain.cursor import decode_cursor, encode_cursor
from novel_llm.models import gateway

router = APIRouter(tags=["model-traces"])


class ModelTraceResource(BaseModel):
    id: str
    project_id: str
    model_profile_id: str
    template_key: str
    template_version: int
    output_kind: str
    prompt: str
    completion: str
    metadata: dict[str, Any]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost: float
    repair_of_id: str | None
    created_at: datetime


class ModelTraceListResource(BaseModel):
    items: list[ModelTraceResource]
    next_cursor: str | None


def _resource(row) -> ModelTraceResource:
    return ModelTraceResource(
        id=str(row.id),
        project_id=str(row.project_id),
        model_profile_id=str(row.model_profile_id),
        template_key=row.template_key,
        template_version=row.template_version,
        output_kind=row.output_kind,
        prompt=row.prompt,
        completion=row.completion,
        metadata=row.call_metadata,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        cost=float(row.cost),
        repair_of_id=str(row.repair_of_id) if row.repair_of_id else None,
        created_at=row.created_at,
    )


@router.get(
    "/projects/{project_id}/model-traces",
    response_model=ModelTraceListResource,
    responses={
        400: {"model": Problem, "description": "Malformed cursor"},
        404: {"model": Problem, "description": "Project not found"},
    },
)
async def list_model_traces(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ModelTraceListResource:
    # An unknown Project is a 404, not an empty list (the container check).
    await projects.get_project(session, project_id)
    trace_cursor = decode_cursor(cursor) if cursor is not None else None
    rows = await gateway.list_traces(
        session, project_id=project_id, limit=limit + 1, cursor=trace_cursor
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor(created_at=rows[-1].created_at, id=rows[-1].id) if has_more and rows else None
    )
    return ModelTraceListResource(
        items=[_resource(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/model-traces/{trace_id}",
    response_model=ModelTraceResource,
    responses={404: {"model": Problem, "description": "Model trace not found"}},
)
async def get_model_trace(
    trace_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelTraceResource:
    return _resource(await gateway.get_trace(session, trace_id))
