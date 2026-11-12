"""Outline resources under /api/v1 (ADR-0008/0004): one immutable version
per publication, addressed forever by its version number. Publication takes
an idempotency key; a replay returns the original version."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.headers import check_idempotency_key
from novel_llm.api.problems import Problem
from novel_llm.domain import outline as outline_domain
from novel_llm.domain.outline import Outline

router = APIRouter(tags=["outline"])


class OutlineResource(BaseModel):
    id: str
    project_id: str
    version_number: int
    payload_schema_version: int
    plans: dict[str, Any]
    created_at: datetime


def _resource(outline: Outline) -> OutlineResource:
    return OutlineResource.model_validate(outline_domain.outline_payload(outline))


@router.post(
    "/projects/{project_id}/outline/publish",
    status_code=201,
    response_model=OutlineResource,
    responses={
        200: {
            "model": OutlineResource,
            "description": "Idempotent replay: the original version, not a duplicate",
        },
        400: {
            "model": Problem,
            "description": "Payload does not match the plan schema, or invalid Idempotency-Key",
        },
        404: {"model": Problem, "description": "Project or referenced story object not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
    },
)
async def publish_outline(
    project_id: UUID,
    payload: dict[str, Any],
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> OutlineResource:
    outline, created = await outline_domain.publish_outline(
        session, project_id=project_id, payload=payload, idempotency_key=idempotency_key
    )
    if not created:
        response.status_code = 200
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/outline/versions/{outline.version_number}"
    )
    return _resource(outline)


@router.get(
    "/projects/{project_id}/outline",
    response_model=OutlineResource,
    responses={
        404: {
            "model": Problem,
            "description": "Project not found, or no Outline published yet",
        },
    },
)
async def get_current_outline(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OutlineResource:
    return _resource(await outline_domain.get_current_outline(session, project_id))


@router.get(
    "/projects/{project_id}/outline/versions/{version_number}",
    response_model=OutlineResource,
    responses={
        404: {
            "model": Problem,
            "description": "Project not found, or no such Outline version",
        },
    },
)
async def get_outline_version(
    project_id: UUID,
    version_number: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OutlineResource:
    return _resource(await outline_domain.get_outline_version(session, project_id, version_number))
