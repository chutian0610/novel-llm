"""Job resources under /api/v1 (ADR-0008): one Job with its attempts, steps,
and checkpoints — the observation surface for the runtime's claims, leases,
fencing tokens, and step idempotency (ADR-0003)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.problems import Problem
from novel_llm.domain import jobs as jobs_domain

router = APIRouter(tags=["jobs"])


class AttemptResource(BaseModel):
    attempt: int
    worker_id: str
    outcome: str | None
    failure_class: str | None
    started_at: datetime
    finished_at: datetime | None


class StepResource(BaseModel):
    step_key: str
    status: str
    attempt: int
    result: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None


class CheckpointResource(BaseModel):
    checkpoint_key: str
    attempt: int
    state: dict[str, Any]
    created_at: datetime


class JobResource(BaseModel):
    id: str
    project_id: str
    workflow_run_id: str
    job_type: str
    status: str
    attempt: int
    failure_class: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    attempts: list[AttemptResource]
    steps: list[StepResource]
    checkpoints: list[CheckpointResource]


@router.get(
    "/jobs/{job_id}",
    response_model=JobResource,
    responses={
        404: {"model": Problem, "description": "Job not found"},
        400: {"model": Problem, "description": "Malformed job id"},
    },
)
async def get_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResource:
    row = await jobs_domain.get_job(session, job_id)
    attempts = await jobs_domain.list_job_attempts(session, job_id)
    steps = await jobs_domain.list_job_steps(session, job_id)
    checkpoints = await jobs_domain.list_job_checkpoints(session, job_id)
    return JobResource(
        id=str(row.id),
        project_id=str(row.project_id),
        workflow_run_id=str(row.workflow_run_id),
        job_type=row.job_type,
        status=row.status,
        attempt=row.attempt,
        failure_class=row.failure_class,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        attempts=[
            AttemptResource(
                attempt=attempt.attempt,
                worker_id=attempt.worker_id,
                outcome=attempt.outcome,
                failure_class=attempt.failure_class,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
            )
            for attempt in attempts
        ],
        steps=[
            StepResource(
                step_key=step.step_key,
                status=step.status,
                attempt=step.attempt,
                result=step.result,
                started_at=step.started_at,
                finished_at=step.finished_at,
            )
            for step in steps
        ],
        checkpoints=[
            CheckpointResource(
                checkpoint_key=checkpoint.checkpoint_key,
                attempt=checkpoint.attempt,
                state=checkpoint.state,
                created_at=checkpoint.created_at,
            )
            for checkpoint in checkpoints
        ],
    )
