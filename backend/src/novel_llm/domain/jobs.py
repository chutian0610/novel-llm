"""Job lifecycle services (ADR-0003): transactional claims, leases,
heartbeats, fenced step/checkpoint writes, retries, and terminal outcomes.

The database rows are authoritative. Every persisted transition is a
conditional UPDATE on the expected current state — and on the fencing token
wherever a lease is involved; ``python-statemachine`` validates the transition
in memory before the UPDATE is issued, and the matched-row count tells the
caller whether the database still agreed. Job-state events commit in the same
transaction as the transition they describe.

Per-Project serialization: the claim takes the Project row's lock inside the
claim transaction, so two Jobs of one Project can never be leased at once,
under any worker concurrency.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import events
from novel_llm.domain.errors import JobNotFound
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.domain.lifecycles import FAILURE_CLASSES, JobLifecycle, transition_target
from novel_llm.domain.workflow_runs import transition_run_for_job_event
from novel_llm.infrastructure.orm import (
    JobAttemptRow,
    JobCheckpointRow,
    JobRow,
    JobStepRow,
    JobTypeRow,
    ProjectRow,
)

log = logging.getLogger(__name__)

# States in which a Worker may still be executing the Job under a lease.
LEASE_HOLDING_STATES = (
    "leased",
    "running",
    "pause_requested",
    "cancel_requested",
    "cancelling",
)

_CLAIM_CANDIDATES = 5


class LeaseLost(Exception):
    """The write could not be made because this attempt's fencing token no
    longer matches the Job row: a newer attempt owns the Job."""


@dataclass(frozen=True, slots=True)
class JobTypeSettings:
    """The database-owned runtime settings for one Job Type (ADR-0003)."""

    timeout_seconds: int
    heartbeat_seconds: int
    lease_seconds: int
    max_attempts: int
    backoff_seconds: int
    backoff_multiplier: float
    backoff_max_seconds: int

    def backoff_delay(self, failed_attempt: int) -> timedelta:
        """The delay before the retry that follows ``failed_attempt``:
        exponential backoff, capped by the configured maximum."""
        raw = self.backoff_seconds * (self.backoff_multiplier ** (failed_attempt - 1))
        return timedelta(seconds=min(raw, float(self.backoff_max_seconds)))


@dataclass(frozen=True, slots=True)
class ActiveJob:
    """The snapshot a Worker holds between claim and completion."""

    id: UUID
    project_id: UUID
    workflow_run_id: UUID
    job_type: str
    payload: dict[str, Any]
    attempt: int
    fencing_token: int
    lease_expires_at: datetime
    settings: JobTypeSettings


def _now() -> datetime:
    return datetime.now(UTC)


async def job_type_settings(session: AsyncSession, job_type: str) -> JobTypeSettings | None:
    row = (
        await session.execute(select(JobTypeRow).where(JobTypeRow.job_type == job_type))
    ).scalar_one_or_none()
    if row is None:
        return None
    return JobTypeSettings(
        timeout_seconds=row.timeout_seconds,
        heartbeat_seconds=row.heartbeat_seconds,
        lease_seconds=row.lease_seconds,
        max_attempts=row.max_attempts,
        backoff_seconds=row.backoff_seconds,
        backoff_multiplier=float(row.backoff_multiplier),
        backoff_max_seconds=row.backoff_max_seconds,
    )


def _job_event_payload(row: JobRow, from_status: str, to_status: str) -> dict[str, Any]:
    return {
        "job_id": str(row.id),
        "workflow_run_id": str(row.workflow_run_id),
        "from": from_status,
        "to": to_status,
        "attempt": row.attempt,
    }


def _stage_job_event(
    session: AsyncSession, row: JobRow, *, from_status: str, to_status: str, attempt: int
) -> None:
    record_event(
        session,
        project_id=row.project_id,
        event_type=events.JOB_STATE_CHANGED,
        payload={
            "job_id": str(row.id),
            "workflow_run_id": str(row.workflow_run_id),
            "from": from_status,
            "to": to_status,
            "attempt": attempt,
        },
    )


async def _stage_run_transition(session: AsyncSession, run_id: UUID, job_event: str) -> None:
    """Advance the Workflow Run for one Job transition, per the bound
    definition's mapping (see workflow_runs)."""
    await transition_run_for_job_event(session, run_id=run_id, job_event=job_event)


async def claim_next_job(session: AsyncSession, *, worker_id: str) -> ActiveJob | None:
    """Claim the next claimable Job, transactionally.

    Candidates are expired-lease recoveries first — a dead Worker's Job must
    be taken over before the Project's queue advances — then ready Jobs
    oldest first. Rows are locked ``FOR UPDATE SKIP LOCKED`` so competing
    Workers never fight over one Job; the Project row is then locked inside
    the same transaction so two Jobs of one Project can never be leased
    concurrently. Returns None when nothing is claimable."""
    now = _now()
    candidates = (
        (
            await session.execute(
                select(JobRow.id)
                .where(
                    (JobRow.status == "ready")
                    # interrupted Jobs are recoverable by definition: their
                    # Worker is gone (lease expired or a forced exit)
                    | (JobRow.status == "interrupted")
                    | (JobRow.status.in_(LEASE_HOLDING_STATES) & (JobRow.lease_expires_at < now))
                )
                .order_by(
                    case((JobRow.status != "ready", 0), else_=1).asc(),
                    JobRow.created_at.asc(),
                    JobRow.id.asc(),
                )
                .limit(_CLAIM_CANDIDATES)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    claimed: ActiveJob | None = None
    for job_id in candidates:
        async with session.begin_nested():
            row = (
                await session.execute(select(JobRow).where(JobRow.id == job_id).with_for_update())
            ).scalar_one()
            settings = await job_type_settings(session, row.job_type)
            if settings is None:
                # Misconfiguration: the Job stays visibly queued and the
                # claim loop moves on; runtime settings are database-owned
                # and this Job Type's row is missing.
                log.warning("job %s has no job_types row; skipping claim", row.id)
                continue

            if row.status not in ("ready", "interrupted"):
                expired = row.lease_expires_at is not None and row.lease_expires_at < now
                if not expired:
                    continue  # the previous Worker renewed before we locked it

            # Serialize per Project: wait for any concurrent claimer of this
            # Project, then re-check under the lock before leasing anything.
            await session.execute(
                select(ProjectRow.id).where(ProjectRow.id == row.project_id).with_for_update()
            )
            project_busy = (
                await session.execute(
                    select(
                        exists().where(
                            JobRow.project_id == row.project_id,
                            JobRow.id != row.id,
                            JobRow.status.in_(LEASE_HOLDING_STATES),
                        )
                    )
                )
            ).scalar_one()
            if project_busy:
                continue

            if row.status == "ready":
                claimed = await _claim_ready(session, row, settings, worker_id, now)
            else:
                claimed = await _take_over(session, row, settings, worker_id, now)
        if claimed is not None:
            break
    await session.commit()
    return claimed


async def _claim_ready(
    session: AsyncSession, row: JobRow, settings: JobTypeSettings, worker_id: str, now: datetime
) -> ActiveJob:
    """ready -> leased: a fresh attempt with the next fencing token."""
    transition_target(JobLifecycle, "ready", "claim")
    from_status = row.status
    row.status = "leased"
    row.attempt += 1
    row.fencing_token += 1
    row.leased_at = now
    row.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
    row.last_heartbeat_at = now
    row.updated_at = now
    session.add(
        JobAttemptRow(
            id=new_id(),
            job_id=row.id,
            attempt=row.attempt,
            fencing_token=row.fencing_token,
            worker_id=worker_id,
            started_at=now,
        )
    )
    _stage_job_event(session, row, from_status=from_status, to_status="leased", attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, "claim")
    return _active_job(row, settings)


async def _take_over(
    session: AsyncSession, row: JobRow, settings: JobTypeSettings, worker_id: str, now: datetime
) -> ActiveJob:
    """Recover a Job whose lease expired: interrupted, then leased again under
    a new attempt and fencing token. Two machine-validated transitions, one
    transaction, two events."""
    await session.execute(
        update(JobAttemptRow)
        .where(
            JobAttemptRow.job_id == row.id,
            JobAttemptRow.attempt == row.attempt,
            JobAttemptRow.finished_at.is_(None),
        )
        .values(outcome="abandoned", finished_at=now)
    )

    if row.status != "interrupted":
        # An expired lease marks the interruption; an already-interrupted Job
        # (forced exit) skips straight to the takeover.
        from_status = row.status
        transition_target(JobLifecycle, from_status, "interrupt")
        row.status = "interrupted"
        row.updated_at = now
        _stage_job_event(
            session, row, from_status=from_status, to_status="interrupted", attempt=row.attempt
        )
        await _stage_run_transition(session, row.workflow_run_id, "interrupt")

    transition_target(JobLifecycle, "interrupted", "take_over")
    row.status = "leased"
    row.attempt += 1
    row.fencing_token += 1
    row.leased_at = now
    row.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
    row.last_heartbeat_at = now
    row.updated_at = now
    session.add(
        JobAttemptRow(
            id=new_id(),
            job_id=row.id,
            attempt=row.attempt,
            fencing_token=row.fencing_token,
            worker_id=worker_id,
            started_at=now,
        )
    )
    _stage_job_event(
        session, row, from_status="interrupted", to_status="leased", attempt=row.attempt
    )
    await _stage_run_transition(session, row.workflow_run_id, "take_over")
    return _active_job(row, settings)


def _active_job(row: JobRow, settings: JobTypeSettings) -> ActiveJob:
    return ActiveJob(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        job_type=row.job_type,
        payload=row.payload,
        attempt=row.attempt,
        fencing_token=row.fencing_token,
        lease_expires_at=row.lease_expires_at or _now(),
        settings=settings,
    )


async def begin_execution(session: AsyncSession, *, job_id: UUID, fencing_token: int) -> bool:
    """leased -> running, fenced: False means this attempt no longer owns the
    Job."""
    target = transition_target(JobLifecycle, "leased", "begin")
    now = _now()
    row = (
        await session.execute(
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.fencing_token == fencing_token,
                JobRow.status == "leased",
            )
            .values(status=target, updated_at=now)
            .returning(JobRow)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return False
    _stage_job_event(session, row, from_status="leased", to_status=target, attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, "begin")
    await session.commit()
    return True


async def extend_lease(
    session: AsyncSession, *, job_id: UUID, fencing_token: int, lease_seconds: int
) -> bool:
    """One heartbeat: renew the lease window, fenced. False means the lease
    was lost — the Worker must stop this attempt immediately."""
    now = _now()
    result = await session.execute(
        update(JobRow)
        .where(
            JobRow.id == job_id,
            JobRow.fencing_token == fencing_token,
            JobRow.status.in_(LEASE_HOLDING_STATES),
        )
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            last_heartbeat_at=now,
            updated_at=now,
        )
    )
    await session.commit()
    rowcount: int = result.rowcount  # type: ignore[attr-defined]  # CursorResult
    return rowcount == 1


async def begin_step(
    session: AsyncSession, *, job_id: UUID, step_key: str, fencing_token: int
) -> dict[str, Any] | None:
    """Open one step under its stable idempotency key (job_id, step_key).

    Returns the recorded result when the step already succeeded — replaying a
    committed step never re-runs its effect (ADR-0003) — or None when the
    caller should execute the effect and call :func:`complete_step`. The
    fencing token is verified first: a stale attempt learns its lease is gone
    before doing any work."""
    async with session.begin_nested():
        current_token = (
            await session.execute(
                select(JobRow.fencing_token).where(JobRow.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if current_token != fencing_token:
            raise LeaseLost(f"job {job_id} is owned by a newer attempt")
        step = (
            await session.execute(
                select(JobStepRow)
                .where(JobStepRow.job_id == job_id, JobStepRow.step_key == step_key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if step is not None and step.status == "succeeded":
            return dict(step.result or {})
        now = _now()
        if step is None:
            session.add(
                JobStepRow(
                    id=new_id(),
                    job_id=job_id,
                    step_key=step_key,
                    status="started",
                    attempt=0,
                    fencing_token=fencing_token,
                    started_at=now,
                    updated_at=now,
                )
            )
    await session.commit()
    return None


async def complete_step(
    session: AsyncSession,
    *,
    job_id: UUID,
    step_key: str,
    attempt: int,
    fencing_token: int,
    result: dict[str, Any],
) -> None:
    """Record a step's committed effect, fenced by the attempt's token."""
    now = _now()
    step = (
        await session.execute(
            select(JobStepRow)
            .where(JobStepRow.job_id == job_id, JobStepRow.step_key == step_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if step is None:
        await session.rollback()
        raise LeaseLost(f"step {step_key!r} of job {job_id} was never begun")
    current_token = (
        await session.execute(
            select(JobRow.fencing_token).where(JobRow.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if current_token != fencing_token:
        raise LeaseLost(f"job {job_id} is owned by a newer attempt")
    step.status = "succeeded"
    step.attempt = attempt
    step.fencing_token = fencing_token
    step.result = result
    step.finished_at = now
    step.updated_at = now
    await session.commit()


async def record_checkpoint(
    session: AsyncSession,
    *,
    job_id: UUID,
    attempt: int,
    fencing_token: int,
    checkpoint_key: str,
    state: dict[str, Any],
) -> None:
    """Append a resumable position, fenced: the write carries the attempt's
    fencing token, so a stale Worker cannot record checkpoints into a newer
    attempt's history (ADR-0003)."""
    current_token = (
        await session.execute(
            select(JobRow.fencing_token).where(JobRow.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if current_token != fencing_token:
        raise LeaseLost(f"job {job_id} is owned by a newer attempt")
    session.add(
        JobCheckpointRow(
            id=new_id(),
            job_id=job_id,
            attempt=attempt,
            fencing_token=fencing_token,
            checkpoint_key=checkpoint_key,
            state=state,
            created_at=_now(),
        )
    )
    await session.commit()


async def complete_job(session: AsyncSession, *, job_id: UUID, fencing_token: int) -> bool:
    """running -> succeeded, fenced. False means the Job row moved on — the
    attempt must not advance state (its result is discarded)."""
    target = transition_target(JobLifecycle, "running", "complete")
    now = _now()
    row = (
        await session.execute(
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.fencing_token == fencing_token,
                JobRow.status == "running",
            )
            .values(status=target, completed_at=now, updated_at=now)
            .returning(JobRow)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return False
    await _finish_attempt(session, job_id=job_id, attempt=row.attempt, outcome="succeeded", now=now)
    _stage_job_event(session, row, from_status="running", to_status=target, attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, "complete")
    await session.commit()
    return True


async def fail_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    fencing_token: int,
    failure_class: str,
    error: str,
    settings: JobTypeSettings,
) -> str | None:
    """Classify a failure (ADR-0003: the closed set) and either schedule an
    automatic transient retry — under the Job Type's database-owned retry and
    backoff settings — or reach the terminal failed state. Returns the new
    status, or None when this attempt was fenced out."""
    if failure_class not in FAILURE_CLASSES:
        raise ValueError(f"{failure_class!r} is not a failure class from the closed set")
    now = _now()
    row = (
        await session.execute(
            select(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.fencing_token == fencing_token,
                JobRow.status == "running",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return None

    retryable = failure_class == "transient" and row.attempt < settings.max_attempts
    if retryable:
        event = "schedule_retry"
        target = transition_target(JobLifecycle, "running", event)
        run_at = now + settings.backoff_delay(row.attempt)
        row.status = target
        row.run_at = run_at
        row.failure_class = failure_class
        row.last_error = error
        row.updated_at = now
        await _finish_attempt(
            session,
            job_id=job_id,
            attempt=row.attempt,
            outcome="failed",
            now=now,
            failure_class=failure_class,
        )
        _stage_job_event(session, row, from_status="running", to_status=target, attempt=row.attempt)
        await _stage_run_transition(session, row.workflow_run_id, event)
        await session.commit()
        return target

    event = "fail"
    target = transition_target(JobLifecycle, "running", event)
    row.status = target
    row.failure_class = failure_class
    row.last_error = error
    row.completed_at = now
    row.updated_at = now
    await _finish_attempt(
        session,
        job_id=job_id,
        attempt=row.attempt,
        outcome="failed",
        now=now,
        failure_class=failure_class,
    )
    _stage_job_event(session, row, from_status="running", to_status=target, attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, event)
    await session.commit()
    return target


async def request_cancellation(session: AsyncSession, *, job_id: UUID, fencing_token: int) -> bool:
    """running -> cancel_requested: a cooperative control request the handler
    honors at its next safe checkpoint (ADR-0003). Fenced."""
    target = transition_target(JobLifecycle, "running", "request_cancel")
    now = _now()
    row = (
        await session.execute(
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.fencing_token == fencing_token,
                JobRow.status == "running",
            )
            .values(status=target, updated_at=now)
            .returning(JobRow)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return False
    _stage_job_event(session, row, from_status="running", to_status=target, attempt=row.attempt)
    await session.commit()
    return True


async def cancel_job(session: AsyncSession, *, job_id: UUID, fencing_token: int) -> bool:
    """Land a cancellation: cancel_requested -> cancelling -> cancelled (two
    machine-validated transitions, one transaction), closing the attempt with
    the cancelled outcome. False when this attempt no longer owns the Job."""
    now = _now()
    row = (
        await session.execute(
            select(JobRow)
            .where(JobRow.id == job_id, JobRow.fencing_token == fencing_token)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return False

    if row.status == "cancel_requested":
        transition_target(JobLifecycle, "cancel_requested", "cancel")
        row.status = "cancelling"
        row.updated_at = now
        _stage_job_event(
            session,
            row,
            from_status="cancel_requested",
            to_status="cancelling",
            attempt=row.attempt,
        )
        final_event = "finish_cancel"
        from_status = "cancelling"
        transition_target(JobLifecycle, "cancelling", "finish_cancel")
    elif row.status == "paused":
        transition_target(JobLifecycle, "paused", "cancel")
        final_event = "cancel"
        from_status = "paused"
    else:
        await session.rollback()
        return False

    target = "cancelled"
    row.status = target
    row.failure_class = "cancelled"
    row.completed_at = now
    row.updated_at = now
    await _finish_attempt(
        session,
        job_id=job_id,
        attempt=row.attempt,
        outcome="cancelled",
        now=now,
        failure_class="cancelled",
    )
    _stage_job_event(session, row, from_status=from_status, to_status=target, attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, final_event)
    await session.commit()
    return True


async def force_interrupt(
    session: AsyncSession, *, job_id: UUID, fencing_token: int, checkpoint_state: dict[str, Any]
) -> bool:
    """SIGTERM forced exit (ADR-0003): record an interrupted checkpoint and
    move the Job to interrupted so recovery starts a new attempt. Fenced;
    False when the Job row moved on (e.g. the lease was already lost)."""
    now = _now()
    row = (
        await session.execute(
            select(JobRow)
            .where(JobRow.id == job_id, JobRow.fencing_token == fencing_token)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status not in ("running", "cancel_requested", "cancelling"):
        await session.rollback()
        return False
    from_status = row.status
    target = transition_target(JobLifecycle, from_status, "interrupt")
    row.status = target
    row.updated_at = now
    session.add(
        JobCheckpointRow(
            id=new_id(),
            job_id=row.id,
            attempt=row.attempt,
            fencing_token=row.fencing_token,
            checkpoint_key="interrupted",
            state=checkpoint_state,
            created_at=now,
        )
    )
    await _finish_attempt(
        session, job_id=job_id, attempt=row.attempt, outcome="interrupted", now=now
    )
    _stage_job_event(session, row, from_status=from_status, to_status=target, attempt=row.attempt)
    await _stage_run_transition(session, row.workflow_run_id, "interrupt")
    await session.commit()
    return True


async def abandon_attempt(session: AsyncSession, *, job_id: UUID, attempt: int) -> None:
    """Best-effort bookkeeping when a Worker learns its lease is gone: the
    attempt ends abandoned. Recovery would record the same thing."""
    await session.execute(
        update(JobAttemptRow)
        .where(
            JobAttemptRow.job_id == job_id,
            JobAttemptRow.attempt == attempt,
            JobAttemptRow.finished_at.is_(None),
        )
        .values(outcome="abandoned", finished_at=_now())
    )
    await session.commit()


async def list_jobs_for_run(session: AsyncSession, run_id: UUID) -> list[JobRow]:
    query = select(JobRow).where(JobRow.workflow_run_id == run_id).order_by(JobRow.created_at.asc())
    return list((await session.execute(query)).scalars().all())


async def list_job_attempts(session: AsyncSession, job_id: UUID) -> list[JobAttemptRow]:
    query = (
        select(JobAttemptRow)
        .where(JobAttemptRow.job_id == job_id)
        .order_by(JobAttemptRow.attempt.asc())
    )
    return list((await session.execute(query)).scalars().all())


async def list_job_steps(session: AsyncSession, job_id: UUID) -> list[JobStepRow]:
    query = (
        select(JobStepRow).where(JobStepRow.job_id == job_id).order_by(JobStepRow.started_at.asc())
    )
    return list((await session.execute(query)).scalars().all())


async def list_job_checkpoints(session: AsyncSession, job_id: UUID) -> list[JobCheckpointRow]:
    query = (
        select(JobCheckpointRow)
        .where(JobCheckpointRow.job_id == job_id)
        .order_by(JobCheckpointRow.created_at.asc())
    )
    return list((await session.execute(query)).scalars().all())


async def get_job(session: AsyncSession, job_id: UUID) -> JobRow:
    row = await session.get(JobRow, job_id)
    if row is None:
        raise JobNotFound(f"no Job with id {job_id}")
    return row


async def _finish_attempt(
    session: AsyncSession,
    *,
    job_id: UUID,
    attempt: int,
    outcome: str,
    now: datetime,
    failure_class: str | None = None,
) -> None:
    await session.execute(
        update(JobAttemptRow)
        .where(
            JobAttemptRow.job_id == job_id,
            JobAttemptRow.attempt == attempt,
            JobAttemptRow.finished_at.is_(None),
        )
        .values(outcome=outcome, failure_class=failure_class, finished_at=now)
    )
