"""Workflow Run resources under /api/v1 (ADR-0008): creation takes an
idempotency key, listing is cursor-paginated, errors are problem-details
with stable codes. The run row binds the Workflow Definition key, version,
and payload schema version that created it (ADR-0003)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.problems import Problem, ProblemError
from novel_llm.domain import jobs as jobs_domain
from novel_llm.domain import projects, workflow_runs
from novel_llm.domain.cursor import encode_cursor
from novel_llm.infrastructure.orm import WorkflowRunRow

router = APIRouter(tags=["workflow-runs"])

_IDEMPOTENCY_KEY_MAX_LENGTH = 200
_LIST_LIMIT = 100


class CreateWorkflowRunRequest(BaseModel):
    definition_key: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResource(BaseModel):
    id: str
    project_id: str
    definition_key: str
    definition_version: int
    payload_schema_version: int
    payload: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class JobSummaryResource(BaseModel):
    id: str
    job_type: str
    status: str
    attempt: int
    failure_class: str | None


class WorkflowRunDetailResource(WorkflowRunResource):
    jobs: list[JobSummaryResource]


class WorkflowRunListResource(BaseModel):
    items: list[WorkflowRunResource]
    next_cursor: str | None


_CREATE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    200: {
        "model": WorkflowRunResource,
        "description": "Idempotent replay: the original Workflow Run, not a duplicate",
    },
    400: {
        "model": Problem,
        "description": (
            "Missing/invalid Idempotency-Key, unknown Workflow Definition, "
            "or payload schema violation"
        ),
    },
    404: {"model": Problem, "description": "Project not found"},
    409: {
        "model": Problem,
        "description": "Project archived or Idempotency-Key reused with a different payload",
    },
}


def _resource(row: WorkflowRunRow) -> WorkflowRunResource:
    return WorkflowRunResource(
        id=str(row.id),
        project_id=str(row.project_id),
        definition_key=row.definition_key,
        definition_version=row.definition_version,
        payload_schema_version=row.payload_schema_version,
        payload=row.payload,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _check_idempotency_key(key: str) -> str:
    key = key.strip()
    if not key or len(key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ProblemError(
            status=400,
            code="invalid_idempotency_key",
            title="Idempotency-Key header is empty or too long",
        )
    return key


@router.post(
    "/projects/{project_id}/workflow-runs",
    status_code=201,
    response_model=WorkflowRunResource,
    responses=_CREATE_PROBLEMS,
)
async def create_workflow_run(
    project_id: UUID,
    payload: CreateWorkflowRunRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> WorkflowRunResource:
    key = _check_idempotency_key(idempotency_key)
    run, created = await workflow_runs.create_workflow_run(
        session,
        project_id=project_id,
        definition_key=payload.definition_key,
        payload=payload.payload,
        idempotency_key=key,
    )
    if not created:
        # A replayed create returns the original run (200), not a duplicate.
        response.status_code = 200
    response.headers["Location"] = f"/api/v1/workflow-runs/{run.id}"
    return _resource(run)


@router.get(
    "/projects/{project_id}/workflow-runs",
    response_model=WorkflowRunListResource,
    responses={
        400: {"model": Problem, "description": "Malformed cursor or query"},
        404: {"model": Problem, "description": "Project not found"},
    },
)
async def list_workflow_runs(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=_LIST_LIMIT)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> WorkflowRunListResource:
    await projects.get_project(session, project_id)  # unknown Project is 404
    rows = await workflow_runs.list_workflow_runs(
        session, project_id=project_id, limit=limit, cursor=cursor
    )
    next_cursor = (
        encode_cursor(created_at=rows[-1].created_at, id=rows[-1].id)
        if len(rows) == limit and rows
        else None
    )
    return WorkflowRunListResource(items=[_resource(row) for row in rows], next_cursor=next_cursor)


@router.get(
    "/workflow-runs/{run_id}",
    response_model=WorkflowRunDetailResource,
    responses={
        404: {"model": Problem, "description": "Workflow Run not found"},
        400: {"model": Problem, "description": "Malformed run id"},
    },
)
async def get_workflow_run(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkflowRunDetailResource:
    row = await workflow_runs.get_workflow_run(session, run_id)
    job_rows = await jobs_domain.list_jobs_for_run(session, run_id)
    detail = WorkflowRunDetailResource(
        **_resource(row).model_dump(),
        jobs=[
            JobSummaryResource(
                id=str(job.id),
                job_type=job.job_type,
                status=job.status,
                attempt=job.attempt,
                failure_class=job.failure_class,
            )
            for job in job_rows
        ],
    )
    return detail
