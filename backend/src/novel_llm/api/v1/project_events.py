"""Project event delivery (ADR-0002/0003): the durable REST replay endpoint
and the live SSE stream, both serving the same versioned envelope.

- ``GET /projects/{id}/events?after=<cursor>`` — durable replay, ascending,
  keyset-paginated on the event id; a consumer reconnecting with the cursor
  of its last received event gets exactly what was missed.
- ``GET /projects/{id}/events/stream?cursor=<cursor>`` — SSE: replay anything
  after the cursor, then follow the outbox live (short polling of the durable
  events; the stream is an optimization, the outbox is the contract).

Delivery is at-least-once: consumers deduplicate by event id.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.problems import Problem
from novel_llm.domain import events
from novel_llm.domain.projects import get_project

router = APIRouter(tags=["events"])

_REPLAY_LIMIT = 200
_POLL_SECONDS = 0.25
_HEARTBEAT_SECONDS = 10.0


class EventEnvelopeResource(BaseModel):
    envelope_version: int
    event_id: str
    cursor: str
    occurred_at: datetime
    event_type: str
    data: dict


class EventListResource(BaseModel):
    items: list[EventEnvelopeResource]
    next_cursor: str | None


def _envelope_resource(event: events.DomainEvent) -> EventEnvelopeResource:
    return EventEnvelopeResource.model_validate(events.envelope(event))


async def _replay_page(
    session: AsyncSession, *, project_id: UUID, after: UUID | None, limit: int
) -> list[events.DomainEvent]:
    """One ascending replay page; the page's last id is the follow-up cursor."""
    return await events.list_events(session, project_id=project_id, after=after, limit=limit)


@router.get(
    "/projects/{project_id}/events",
    response_model=EventListResource,
    responses={
        400: {"model": Problem, "description": "Malformed cursor or query"},
        404: {"model": Problem, "description": "Project not found"},
    },
)
async def list_project_events(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    after: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_REPLAY_LIMIT)] = 100,
) -> EventListResource:
    await get_project(session, project_id)  # unknown Project is 404, not an empty feed
    after_id = UUID(after) if after is not None else None
    found = await _replay_page(session, project_id=project_id, after=after_id, limit=limit)
    next_cursor = str(found[-1].id) if len(found) == limit else None
    return EventListResource(
        items=[_envelope_resource(event) for event in found],
        next_cursor=next_cursor,
    )


def _sse_frame(event: events.DomainEvent) -> str:
    """One SSE frame per envelope: `id:` carries the event id so even a
    generic EventSource reconnect keeps its place; data is the envelope JSON.
    At-least-once means the consumer may see an id twice and dedupes."""
    envelope = _envelope_resource(event)
    payload = envelope.model_dump_json()
    return f"id: {envelope.event_id}\nevent: domain\ndata: {payload}\n\n"


async def _event_stream(
    request: Request, project_id: UUID, after: UUID | None
) -> AsyncIterator[str]:
    engine = request.app.state.db_engine
    yield ": stream open\n\n"
    last_id: UUID | None = after
    idle_since = asyncio.get_event_loop().time()

    while not await request.is_disconnected():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            found = await _replay_page(
                session, project_id=project_id, after=last_id, limit=_REPLAY_LIMIT
            )
        for event in found:
            last_id = event.id
            yield _sse_frame(event)
        if found:
            idle_since = asyncio.get_event_loop().time()
            continue
        now = asyncio.get_event_loop().time()
        if now - idle_since >= _HEARTBEAT_SECONDS:
            yield ": heartbeat\n\n"
            idle_since = now
        await asyncio.sleep(_POLL_SECONDS)


@router.get(
    "/projects/{project_id}/events/stream",
    response_class=StreamingResponse,
    responses={
        404: {"model": Problem, "description": "Project not found"},
    },
)
async def stream_project_events(
    project_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cursor: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    await get_project(session, project_id)
    after = UUID(cursor) if cursor is not None else None
    return StreamingResponse(
        _event_stream(request, project_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
