"""The event outbox domain rules (ADR-0002/0003).

Domain changes and their events commit in the same transaction — the writers
in domain.projects call :func:`record_event` before their own commit. Events
are append-only, ordered by their time-ordered UUIDv7 ids, and delivered
at-least-once; consumers deduplicate by event id.

Delivery uses a versioned envelope so clients can evolve independently:

    {"envelope_version": 1, "event_id": ..., "cursor": ...,
     "occurred_at": ..., "event_type": ..., "data": {...}}
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain.ids import new_id
from novel_llm.infrastructure.orm import DomainEventRow

ENVELOPE_VERSION = 1

# Event types on the Project lifecycle. Constants keep producers and the
# API surface spelling them identically.
PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
PROJECT_ARCHIVED = "project.archived"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    id: UUID
    project_id: UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime


def envelope(event: DomainEvent) -> dict[str, Any]:
    """The versioned wire shape: cursor and event id coincide, because the
    id order is the delivery order."""
    return {
        "envelope_version": ENVELOPE_VERSION,
        "event_id": str(event.id),
        "cursor": str(event.id),
        "occurred_at": event.occurred_at,
        "event_type": event.event_type,
        "data": event.payload,
    }


def record_event(
    session: AsyncSession, *, project_id: UUID, event_type: str, payload: dict[str, Any]
) -> None:
    """Stage one event inside the caller's transaction — never commits."""
    session.add(
        DomainEventRow(
            id=new_id(),
            project_id=project_id,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
    )


async def list_events(
    session: AsyncSession,
    *,
    project_id: UUID,
    after: UUID | None,
    limit: int,
) -> list[DomainEvent]:
    """Events of one Project ascending (delivery order), keyset-paginated on
    the id: `after` is the last event id the consumer already has, so replay
    with a cursor yields exactly what was missed."""
    query = (
        select(DomainEventRow)
        .where(DomainEventRow.project_id == project_id)
        .order_by(DomainEventRow.id.asc())
        .limit(limit)
    )
    if after is not None:
        query = query.where(DomainEventRow.id > after)
    rows = (await session.execute(query)).scalars().all()
    return [
        DomainEvent(
            id=row.id,
            project_id=row.project_id,
            event_type=row.event_type,
            payload=row.payload,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]
