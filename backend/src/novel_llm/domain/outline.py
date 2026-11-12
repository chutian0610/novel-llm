"""Outline publication domain rules (ADR-0004/0008/0009): one immutable
version per publication, containing work, volume, chapter, scene,
foreshadowing, and constraint plans as typed contracts. Generation always
references a fixed plan; changes require a new version, and published
versions are never rewritten.

The plan shapes are code-owned typed contracts (ADR-0008): unknown keys are
a malformed payload, not a silently unfired plan. Referenced story objects
are validated inside the publication transaction — no physical foreign keys
(ADR-0009), so an unknown or cross-Project reference is a domain rule
violation with a stable problem code.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import events, idempotency
from novel_llm.domain import projects as projects_domain
from novel_llm.domain.errors import (
    ChapterNotFound,
    InvalidOutlinePayload,
    OutlineNotFound,
    VolumeNotFound,
)
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.domain.projects import get_project
from novel_llm.infrastructure.orm import (
    ChapterRow,
    OutlineIdempotencyKeyRow,
    OutlineRow,
    VolumeRow,
)

PAYLOAD_SCHEMA_VERSION = 1


class WorkPlan(BaseModel):
    """The work-level plan: what the whole Project is about."""

    model_config = ConfigDict(extra="forbid")

    synopsis: str = Field(min_length=1, max_length=4000)


class VolumePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_id: UUID
    summary: str = Field(min_length=1, max_length=2000)


class ChapterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: UUID
    summary: str = Field(min_length=1, max_length=2000)
    goal: str | None = Field(default=None, max_length=2000)


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: UUID
    position: int = Field(ge=1)
    goal: str = Field(min_length=1, max_length=2000)


class ForeshadowingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2000)
    # The Chapter where the foreshadowing is planted, when known.
    chapter_id: UUID | None = None


class ConstraintPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2000)


class OutlinePlans(BaseModel):
    """The whole published plan: the six plan kinds of ADR-0004."""

    model_config = ConfigDict(extra="forbid")

    work: WorkPlan
    volumes: list[VolumePlan] = Field(default_factory=list)
    chapters: list[ChapterPlan] = Field(default_factory=list)
    scenes: list[ScenePlan] = Field(default_factory=list)
    foreshadowings: list[ForeshadowingPlan] = Field(default_factory=list)
    constraints: list[ConstraintPlan] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Outline:
    id: UUID
    project_id: UUID
    version_number: int
    payload_schema_version: int
    plans: dict[str, Any]
    created_at: datetime


def _to_outline(row: OutlineRow) -> Outline:
    return Outline(
        id=row.id,
        project_id=row.project_id,
        version_number=row.version_number,
        payload_schema_version=row.payload_schema_version,
        plans=row.plans,
        created_at=row.created_at,
    )


def outline_payload(outline: Outline) -> dict[str, Any]:
    return {
        "id": str(outline.id),
        "project_id": str(outline.project_id),
        "version_number": outline.version_number,
        "payload_schema_version": outline.payload_schema_version,
        "plans": outline.plans,
        "created_at": outline.created_at,
    }


async def publish_outline(
    session: AsyncSession,
    *,
    project_id: UUID,
    payload: dict[str, Any],
    idempotency_key: str,
) -> tuple[Outline, bool]:
    """Publish the Project's Outline as one immutable version, honoring the
    idempotency key like every creation (ADR-0008). The payload validates
    against the typed plan contracts, and every referenced story object must
    exist inside this Project — checked in the same transaction, standing in
    for physical foreign keys (ADR-0009)."""
    try:
        plans = OutlinePlans.model_validate(payload)
    except ValidationError as error:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in error.errors()[:5]
        )
        raise InvalidOutlinePayload(summary) from None

    fingerprint = idempotency.request_fingerprint(
        {"payload_schema_version": PAYLOAD_SCHEMA_VERSION, "plans": plans.model_dump(mode="json")}
    )
    outline_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        OutlineIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=outline_id,
        resource_ref_column="outline_id",
    )
    if existing_id is not None:
        existing = await session.get_one(OutlineRow, existing_id)
        return _to_outline(existing), False

    # The Project row lock serializes version allocation against concurrent
    # publications and rejects archived Projects (ADR-0002).
    await projects_domain.lock_active_project(session, project_id=project_id)

    await _validate_references(session, project_id=project_id, plans=plans)

    next_version = (
        await session.execute(
            select(func.max(OutlineRow.version_number)).where(OutlineRow.project_id == project_id)
        )
    ).scalar_one() or 0
    row = OutlineRow(
        id=outline_id,
        project_id=project_id,
        version_number=next_version + 1,
        payload_schema_version=PAYLOAD_SCHEMA_VERSION,
        plans=plans.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.OUTLINE_PUBLISHED,
        payload={"outline_id": str(outline_id), "version_number": row.version_number},
    )
    await session.commit()
    return _to_outline(row), True


async def _validate_references(
    session: AsyncSession, *, project_id: UUID, plans: OutlinePlans
) -> None:
    """Every story object a plan references must exist inside this Project.
    Batch checks inside the publication transaction (ADR-0009)."""
    volume_ids = {plan.volume_id for plan in plans.volumes}
    chapter_ids = {plan.chapter_id for plan in plans.chapters + plans.scenes} | {
        plan.chapter_id for plan in plans.foreshadowings if plan.chapter_id is not None
    }
    chapter_ids = {chapter_id for chapter_id in chapter_ids if chapter_id is not None}

    if volume_ids:
        known = set(
            (
                await session.execute(
                    select(VolumeRow.id).where(
                        VolumeRow.project_id == project_id, VolumeRow.id.in_(volume_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = volume_ids - known
        if missing:
            raise VolumeNotFound(
                f"the plan references Volume(s) not in Project {project_id}: "
                + ", ".join(str(volume_id) for volume_id in sorted(missing))
            )
    if chapter_ids:
        known = set(
            (
                await session.execute(
                    select(ChapterRow.id).where(
                        ChapterRow.project_id == project_id, ChapterRow.id.in_(chapter_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = chapter_ids - known
        if missing:
            raise ChapterNotFound(
                f"the plan references Chapter(s) not in Project {project_id}: "
                + ", ".join(str(chapter_id) for chapter_id in sorted(missing))
            )


async def get_current_outline(session: AsyncSession, project_id: UUID) -> Outline:
    """The Project's current (highest-numbered) Outline version."""
    await get_project(session, project_id)
    row = (
        await session.execute(
            select(OutlineRow)
            .where(OutlineRow.project_id == project_id)
            .order_by(OutlineRow.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise OutlineNotFound(f"Project {project_id} has no published Outline")
    return _to_outline(row)


async def get_outline_version(
    session: AsyncSession, project_id: UUID, version_number: int
) -> Outline:
    """One published version, addressable forever at its number (ADR-0004:
    versions are immutable history)."""
    await get_project(session, project_id)
    row = (
        await session.execute(
            select(OutlineRow).where(
                OutlineRow.project_id == project_id,
                OutlineRow.version_number == version_number,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise OutlineNotFound(f"Project {project_id} has no Outline version {version_number}")
    return _to_outline(row)
