"""Story structure domain rules (ADR-0004/0009): Volumes and Chapters with
stable identities and mutable positions.

Domain services own the transaction: each public function commits atomically,
validating references inside that transaction — there are no physical foreign
keys to rely on (ADR-0009), so an unknown or cross-Project reference is a
domain rule violation, never a database integrity error. Reordering renumbers
the siblings' positions in one transaction and leaves every identity alone:
references between story objects survive any restructure.

Archived Projects reject structure edits, as they reject every other edit
(ADR-0002: archive is the only removal path).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import events, idempotency
from novel_llm.domain import projects as projects_domain
from novel_llm.domain.errors import (
    ChapterNotFound,
    ConcurrencyConflict,
    StructureOrderInvalid,
    VolumeNotFound,
)
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.infrastructure.orm import (
    ChapterIdempotencyKeyRow,
    ChapterRow,
    VolumeIdempotencyKeyRow,
    VolumeRow,
)


@dataclass(frozen=True, slots=True)
class Volume:
    id: UUID
    project_id: UUID
    title: str
    position: int
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Chapter:
    id: UUID
    project_id: UUID
    volume_id: UUID
    title: str
    position: int
    revision: int
    created_at: datetime
    updated_at: datetime


def _to_volume(row: VolumeRow) -> Volume:
    return Volume(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        position=row.position,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_chapter(row: ChapterRow) -> Chapter:
    return Chapter(
        id=row.id,
        project_id=row.project_id,
        volume_id=row.volume_id,
        title=row.title,
        position=row.position,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def volume_payload(volume: Volume) -> dict[str, Any]:
    return {
        "id": str(volume.id),
        "project_id": str(volume.project_id),
        "title": volume.title,
        "position": volume.position,
        "created_at": volume.created_at,
        "updated_at": volume.updated_at,
    }


def chapter_payload(chapter: Chapter) -> dict[str, Any]:
    return {
        "id": str(chapter.id),
        "project_id": str(chapter.project_id),
        "volume_id": str(chapter.volume_id),
        "title": chapter.title,
        "position": chapter.position,
        "created_at": chapter.created_at,
        "updated_at": chapter.updated_at,
    }


def volume_etag(volume: Volume) -> str:
    """The strong validator for a mutable Volume: its revision."""
    return f'"{volume.revision}"'


def chapter_etag(chapter: Chapter) -> str:
    return f'"{chapter.revision}"'


def _now() -> datetime:
    return datetime.now(UTC)


async def create_volume(
    session: AsyncSession,
    *,
    project_id: UUID,
    title: str,
    idempotency_key: str,
) -> tuple[Volume, bool]:
    """Create a Volume, honoring the idempotency key like every creation
    (ADR-0008). It appends after the Project's current last Volume."""
    fingerprint = idempotency.request_fingerprint({"title": title, "project_id": str(project_id)})
    now = _now()
    volume_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        VolumeIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=volume_id,
        resource_ref_column="volume_id",
    )
    if existing_id is not None:
        existing = await session.get_one(VolumeRow, existing_id)
        return _to_volume(existing), False

    await projects_domain.lock_active_project(session, project_id=project_id)
    next_position = (
        await session.execute(
            select(func.max(VolumeRow.position)).where(VolumeRow.project_id == project_id)
        )
    ).scalar_one() or 0
    row = VolumeRow(
        id=volume_id,
        project_id=project_id,
        title=title,
        position=next_position + 1,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.VOLUME_CREATED,
        payload={"volume_id": str(volume_id), "title": title, "position": row.position},
    )
    await session.commit()
    return _to_volume(row), True


async def create_chapter(
    session: AsyncSession,
    *,
    project_id: UUID,
    volume_id: UUID,
    title: str,
    idempotency_key: str,
) -> tuple[Chapter, bool]:
    """Create a Chapter inside a Volume, honoring the idempotency key. The
    Volume reference is validated transactionally, scoped to the Project: a
    volume of another Project is simply not found here (ADR-0009)."""
    fingerprint = idempotency.request_fingerprint(
        {"title": title, "volume_id": str(volume_id), "project_id": str(project_id)}
    )
    now = _now()
    chapter_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        ChapterIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=chapter_id,
        resource_ref_column="chapter_id",
    )
    if existing_id is not None:
        existing = await session.get_one(ChapterRow, existing_id)
        return _to_chapter(existing), False

    await projects_domain.lock_active_project(session, project_id=project_id)
    volume = await session.get(VolumeRow, volume_id)
    if volume is None or volume.project_id != project_id:
        raise VolumeNotFound(f"no Volume with id {volume_id} in Project {project_id}")
    next_position = (
        await session.execute(
            select(func.max(ChapterRow.position)).where(ChapterRow.volume_id == volume_id)
        )
    ).scalar_one() or 0
    row = ChapterRow(
        id=chapter_id,
        project_id=project_id,
        volume_id=volume_id,
        title=title,
        position=next_position + 1,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.CHAPTER_CREATED,
        payload={
            "chapter_id": str(chapter_id),
            "volume_id": str(volume_id),
            "title": title,
            "position": row.position,
        },
    )
    await session.commit()
    return _to_chapter(row), True


async def get_volume(session: AsyncSession, volume_id: UUID) -> Volume:
    row = await session.get(VolumeRow, volume_id)
    if row is None:
        raise VolumeNotFound(f"no Volume with id {volume_id}")
    return _to_volume(row)


async def get_chapter(session: AsyncSession, chapter_id: UUID) -> Chapter:
    row = await session.get(ChapterRow, chapter_id)
    if row is None:
        raise ChapterNotFound(f"no Chapter with id {chapter_id}")
    return _to_chapter(row)


async def update_volume(
    session: AsyncSession, *, volume_id: UUID, expected_revision: int | None, title: str
) -> Volume:
    """The Volume's mutable title under optimistic concurrency (same shape as
    the Chapter update). The UPDATE
    carries the expected revision (from If-Match), so a stale writer changes
    nothing. An archived Project rejects the edit (ADR-0002)."""
    existing = await session.get(VolumeRow, volume_id)
    if existing is None:
        raise VolumeNotFound(f"no Volume with id {volume_id}")
    await projects_domain.lock_active_project(session, project_id=existing.project_id)
    statement = (
        update(VolumeRow)
        .where(VolumeRow.id == volume_id)
        .values(title=title, revision=VolumeRow.revision + 1, updated_at=_now())
        .returning(VolumeRow)
    )
    if expected_revision is not None:
        statement = statement.where(VolumeRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=row.project_id,
            event_type=events.VOLUME_UPDATED,
            payload={"volume_id": str(row.id), "title": row.title, "revision": row.revision},
        )
        await session.commit()
        return _to_volume(row)

    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def update_chapter(
    session: AsyncSession, *, chapter_id: UUID, expected_revision: int | None, title: str
) -> Chapter:
    """The Chapter's mutable title under optimistic concurrency; an archived
    Project rejects the edit (ADR-0002)."""
    existing = await session.get(ChapterRow, chapter_id)
    if existing is None:
        raise ChapterNotFound(f"no Chapter with id {chapter_id}")
    await projects_domain.lock_active_project(session, project_id=existing.project_id)
    statement = (
        update(ChapterRow)
        .where(ChapterRow.id == chapter_id)
        .values(title=title, revision=ChapterRow.revision + 1, updated_at=_now())
        .returning(ChapterRow)
    )
    if expected_revision is not None:
        statement = statement.where(ChapterRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=row.project_id,
            event_type=events.CHAPTER_UPDATED,
            payload={"chapter_id": str(row.id), "title": row.title, "revision": row.revision},
        )
        await session.commit()
        return _to_chapter(row)

    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


def _validate_permutation(ordered_ids: list[UUID], actual_ids: list[UUID]) -> None:
    """A reorder names every sibling exactly once — anything else is a
    malformed order, refused before any row moves."""
    if len(ordered_ids) != len(actual_ids) or set(ordered_ids) != set(actual_ids):
        raise StructureOrderInvalid(
            "a reorder must name every sibling exactly once, in the new order"
        )


async def reorder_volumes(
    session: AsyncSession, *, project_id: UUID, ordered_volume_ids: list[UUID]
) -> list[Volume]:
    """One Project's Volumes, renamed order in one transaction: positions
    renumber densely (1..n); identities and their chapters do not move. The
    Project lock serializes the reorder against concurrent creates."""
    await projects_domain.lock_active_project(session, project_id=project_id)
    rows = (
        (
            await session.execute(
                select(VolumeRow)
                .where(VolumeRow.project_id == project_id)
                .order_by(VolumeRow.position.asc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    _validate_permutation(ordered_volume_ids, [row.id for row in rows])

    now = _now()
    by_id = {row.id: row for row in rows}
    ordered: list[Volume] = []
    for position, volume_id in enumerate(ordered_volume_ids, start=1):
        row = by_id[volume_id]
        row.position = position
        row.revision += 1
        row.updated_at = now
        ordered.append(_to_volume(row))
    record_event(
        session,
        project_id=project_id,
        event_type=events.STRUCTURE_REORDERED,
        payload={
            "kind": "volumes",
            "project_id": str(project_id),
            "ordered_ids": [str(volume_id) for volume_id in ordered_volume_ids],
        },
    )
    await session.commit()
    return ordered


async def reorder_chapters(
    session: AsyncSession, *, volume_id: UUID, ordered_chapter_ids: list[UUID]
) -> list[Chapter]:
    """One Volume's Chapters, new order in one transaction, same rule as the
    Volume reorder. The Project lock serializes the reorder against
    concurrent creates."""
    volume = await session.get(VolumeRow, volume_id)
    if volume is None:
        raise VolumeNotFound(f"no Volume with id {volume_id}")
    await projects_domain.lock_active_project(session, project_id=volume.project_id)
    rows = (
        (
            await session.execute(
                select(ChapterRow)
                .where(ChapterRow.volume_id == volume_id)
                .order_by(ChapterRow.position.asc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    _validate_permutation(ordered_chapter_ids, [row.id for row in rows])

    now = _now()
    by_id = {row.id: row for row in rows}
    ordered: list[Chapter] = []
    for position, chapter_id in enumerate(ordered_chapter_ids, start=1):
        row = by_id[chapter_id]
        row.position = position
        row.revision += 1
        row.updated_at = now
        ordered.append(_to_chapter(row))
    record_event(
        session,
        project_id=volume.project_id,
        event_type=events.STRUCTURE_REORDERED,
        payload={
            "kind": "chapters",
            "volume_id": str(volume_id),
            "ordered_ids": [str(chapter_id) for chapter_id in ordered_chapter_ids],
        },
    )
    await session.commit()
    return ordered


async def move_chapter(session: AsyncSession, *, chapter_id: UUID, to_volume_id: UUID) -> Chapter:
    """Restructure: a Chapter moves to another Volume of the same Project,
    appending after that Volume's current last Chapter. Its identity is
    untouched — references to it survive the move (ADR-0004)."""
    chapter_row = await session.get(ChapterRow, chapter_id)
    if chapter_row is None:
        raise ChapterNotFound(f"no Chapter with id {chapter_id}")
    await projects_domain.lock_active_project(session, project_id=chapter_row.project_id)
    target = await session.get(VolumeRow, to_volume_id)
    if target is None or target.project_id != chapter_row.project_id:
        raise VolumeNotFound(
            f"no Volume with id {to_volume_id} in the Chapter's Project {chapter_row.project_id}"
        )
    next_position = (
        await session.execute(
            select(func.max(ChapterRow.position)).where(ChapterRow.volume_id == to_volume_id)
        )
    ).scalar_one() or 0

    from_volume_id = chapter_row.volume_id
    from_position = chapter_row.position
    chapter_row.volume_id = to_volume_id
    chapter_row.position = next_position + 1
    chapter_row.revision += 1
    chapter_row.updated_at = _now()
    # The source Volume stays dense: siblings after the moved Chapter close
    # the gap (the uniqueness is deferred, so the shift commits cleanly).
    await session.execute(
        update(ChapterRow)
        .where(
            ChapterRow.volume_id == from_volume_id,
            ChapterRow.position > from_position,
        )
        .values(position=ChapterRow.position - 1)
    )
    record_event(
        session,
        project_id=chapter_row.project_id,
        event_type=events.CHAPTER_MOVED,
        payload={
            "chapter_id": str(chapter_id),
            "from_volume_id": str(from_volume_id),
            "to_volume_id": str(to_volume_id),
            "position": chapter_row.position,
        },
    )
    await session.commit()
    return _to_chapter(chapter_row)


@dataclass(frozen=True, slots=True)
class VolumeNode:
    """One Volume of the structure view with its Chapters in order."""

    volume: Volume
    chapters: list[Chapter]


@dataclass(frozen=True, slots=True)
class Structure:
    """The Project's whole structure in display order: the read model the
    workbench renders and the reorder actions return."""

    project_id: UUID
    volumes: list[VolumeNode]


async def project_structure(session: AsyncSession, project_id: UUID) -> Structure:
    """The Project's structure in one read: Volumes in position order, each
    with its Chapters in position order."""
    volume_rows = (
        (
            await session.execute(
                select(VolumeRow)
                .where(VolumeRow.project_id == project_id)
                .order_by(VolumeRow.position.asc())
            )
        )
        .scalars()
        .all()
    )
    chapter_rows = (
        (
            await session.execute(
                select(ChapterRow)
                .where(ChapterRow.project_id == project_id)
                .order_by(ChapterRow.volume_id.asc(), ChapterRow.position.asc())
            )
        )
        .scalars()
        .all()
    )
    chapters_by_volume: dict[UUID, list[Chapter]] = {}
    for row in chapter_rows:
        chapters_by_volume.setdefault(row.volume_id, []).append(_to_chapter(row))
    return Structure(
        project_id=project_id,
        volumes=[
            VolumeNode(
                volume=_to_volume(row),
                chapters=chapters_by_volume.get(row.id, []),
            )
            for row in volume_rows
        ],
    )
