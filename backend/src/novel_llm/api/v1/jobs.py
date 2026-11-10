"""Job resources under /api/v1 (ADR-0008): one Job with its attempts, steps,
and checkpoints — the observation surface for the runtime's claims, leases,
fencing tokens, and step idempotency (ADR-0003) — and the domain actions
(pause, resume) the Author issues against a running Job, never generic state
patches."""

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
    # The attempt's monotonic fencing token (ADR-0003): every checkpoint this
    # attempt wrote carries it, and a later attempt's is strictly greater.
    fencing_token: int
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
    # The fencing token of the attempt that recorded this checkpoint: a stale
    # Worker holding an older token can never appear in this history.
    fencing_token: int
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


async def _job_resource(session: AsyncSession, job_id: UUID) -> JobResource:
    """One Job with its attempts, steps, and checkpoints — the response of
    both the read and the domain-action endpoints."""
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
                fencing_token=attempt.fencing_token,
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
                fencing_token=checkpoint.fencing_token,
                state=checkpoint.state,
                created_at=checkpoint.created_at,
            )
            for checkpoint in checkpoints
        ],
    )


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
    return await _job_resource(session, job_id)


@router.post(
    "/jobs/{job_id}/pause",
    response_model=JobResource,
    responses={
        404: {"model": Problem, "description": "Job not found"},
        409: {"model": Problem, "description": "Job state does not allow pausing"},
        400: {"model": Problem, "description": "Malformed job id"},
    },
)
async def pause_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResource:
    """Domain action (ADR-0008): request a cooperative pause. The executing
    Worker honors it at the Job's next safe checkpoint."""
    await jobs_domain.request_pause(session, job_id=job_id)
    return await _job_resource(session, job_id)


@router.post(
    "/jobs/{job_id}/resume",
    response_model=JobResource,
    responses={
        404: {"model": Problem, "description": "Job not found"},
        409: {"model": Problem, "description": "Job state does not allow resuming"},
        400: {"model": Problem, "description": "Malformed job id"},
    },
)
async def resume_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResource:
    """Domain action (ADR-0008): resume a paused Job. History is never
    reopened — the Job requeues and its next attempt continues from the
    checkpoint."""
    await jobs_domain.resume_job(session, job_id=job_id)
    return await _job_resource(session, job_id)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResource,
    responses={
        404: {"model": Problem, "description": "Job not found"},
        409: {"model": Problem, "description": "Job state does not allow cancelling"},
        400: {"model": Problem, "description": "Malformed job id"},
    },
)
async def cancel_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobResource:
    """Domain action (ADR-0008): request cancellation. The Job stops without
    continuing its normal outcome — an in-flight step may finish, but its
    late result never advances state (ADR-0003)."""
    await jobs_domain.request_cancellation(session, job_id=job_id)
    return await _job_resource(session, job_id)
