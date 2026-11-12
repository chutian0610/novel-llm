"""Workflow Run domain rules (ADR-0003): a run is one business-process
instance bound to a code-owned Workflow Definition — key, definition version,
and payload schema version are recorded on the row, so code upgrades affect
only new runs. Creating a run schedules its first Job in the same
transaction; job outcomes drive run transitions through the definition's
mapping, validated by the run state machine with the database authoritative.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from statemachine.exceptions import TransitionNotAllowed as MachineTransitionNotAllowed

from novel_llm.domain import events, idempotency, projects
from novel_llm.domain.cursor import decode_cursor
from novel_llm.domain.errors import (
    InvalidWorkflowPayload,
    ProjectArchived,
    UnknownWorkflowDefinition,
    WorkflowRunNotFound,
)
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.domain.lifecycles import RunLifecycle, transition_target
from novel_llm.infrastructure.orm import (
    JobRow,
    WorkflowRunIdempotencyKeyRow,
    WorkflowRunRow,
)
from novel_llm.workflows.definitions import resolve_definition


def _run_event_payload(
    row: WorkflowRunRow, from_status: str | None, to_status: str
) -> dict[str, Any]:
    return {
        "workflow_run_id": str(row.id),
        "from": from_status,
        "to": to_status,
    }


def request_fingerprint(definition_key: str, payload: dict[str, Any]) -> str:
    """A stable digest of the create payload; idempotent replay must match it."""
    return idempotency.request_fingerprint({"definition_key": definition_key, "payload": payload})


async def create_workflow_run(
    session: AsyncSession,
    *,
    project_id: UUID,
    definition_key: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> tuple[WorkflowRunRow, bool]:
    """Create a Workflow Run of ``definition_key`` and schedule its first Job,
    atomically. Honors the idempotency key like every creation (ADR-0008): a
    replay with the same key and payload returns the original run.

    The payload is validated against the definition's schema; the run row
    binds the definition version and payload schema version that created it."""
    project = await projects.get_project(session, project_id)
    if project.status == "archived":
        raise ProjectArchived("an archived Project does not accept new Workflow Runs")

    definition = resolve_definition(definition_key)
    if definition is None:
        raise UnknownWorkflowDefinition(f"no Workflow Definition with key {definition_key!r}")
    try:
        validated = definition.payload_model.model_validate(payload)
    except ValidationError as error:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in error.errors()[:5]
        )
        raise InvalidWorkflowPayload(summary) from None

    if definition.validate_payload is not None:
        await definition.validate_payload(session, validated)

    fingerprint = request_fingerprint(definition_key, validated.model_dump(mode="json"))
    now = datetime.now(UTC)
    run_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        WorkflowRunIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=run_id,
        resource_ref_column="run_id",
    )
    if existing_id is not None:
        existing = await session.get_one(WorkflowRunRow, existing_id)
        return existing, False

    row = WorkflowRunRow(
        id=run_id,
        project_id=project_id,
        definition_key=definition.key,
        definition_version=definition.version,
        payload_schema_version=definition.payload_schema_version,
        payload=validated.model_dump(mode="json"),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    job = JobRow(
        id=new_id(),
        project_id=project_id,
        workflow_run_id=run_id,
        job_type=definition.job_type,
        payload=definition.job_payload(validated),
        status="ready",
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    record_event(
        session,
        project_id=project_id,
        event_type=events.WORKFLOW_RUN_STATE_CHANGED,
        payload=_run_event_payload(row, None, "pending"),
    )
    record_event(
        session,
        project_id=project_id,
        event_type=events.JOB_STATE_CHANGED,
        payload={
            "job_id": str(job.id),
            "workflow_run_id": str(run_id),
            "from": None,
            "to": "ready",
            "attempt": 0,
        },
    )
    await session.commit()
    return row, True


async def get_workflow_run(session: AsyncSession, run_id: UUID) -> WorkflowRunRow:
    row = await session.get(WorkflowRunRow, run_id)
    if row is None:
        raise WorkflowRunNotFound(f"no Workflow Run with id {run_id}")
    return row


async def list_workflow_runs(
    session: AsyncSession,
    *,
    project_id: UUID,
    limit: int,
    cursor: str | None,
) -> list[WorkflowRunRow]:
    """A Project's Workflow Runs, newest first, keyset-paginated."""
    query = (
        select(WorkflowRunRow)
        .where(WorkflowRunRow.project_id == project_id)
        .order_by(WorkflowRunRow.created_at.desc(), WorkflowRunRow.id.desc())
    )
    if cursor is not None:
        created_at, id = decode_cursor(cursor)
        query = query.where(
            or_(
                WorkflowRunRow.created_at < created_at,
                and_(WorkflowRunRow.created_at == created_at, WorkflowRunRow.id < id),
            )
        )
    rows = (await session.execute(query.limit(limit))).scalars().all()
    return list(rows)


async def transition_run_for_job_event(
    session: AsyncSession, *, run_id: UUID, job_event: str
) -> None:
    """Advance the Workflow Run for a Job transition, using the bound
    definition's mapping. Unmapped job events (retries, interrupts) leave the
    run untouched; transitions the run state machine refuses (recovering a
    run that is already running) are skipped. The UPDATE carries the expected
    status, so the database stays authoritative.

    The mapping resolves the definition's current code (the registry holds
    only the latest version). The run row still records the definition
    version that created it, so history can be explained; while a single
    version exists, mapping changes apply to new transitions of existing
    runs — a per-version registry arrives with the first upgrade."""
    row = (
        await session.execute(
            select(WorkflowRunRow).where(WorkflowRunRow.id == run_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return
    definition = resolve_definition(row.definition_key)
    if definition is None:
        return
    run_event = definition.run_transition_for_job_event.get(job_event)
    if run_event is None:
        return
    try:
        target = transition_target(RunLifecycle, row.status, run_event)
    except MachineTransitionNotAllowed:
        return
    from_status = row.status
    row.status = target
    row.updated_at = datetime.now(UTC)
    record_event(
        session,
        project_id=row.project_id,
        event_type=events.WORKFLOW_RUN_STATE_CHANGED,
        payload=_run_event_payload(row, from_status, target),
    )
