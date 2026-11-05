"""Project domain rules: creation, listing, mutation, archive (ADR-0002/0009).

Domain services own the transaction: each public function commits atomically,
validating references and invariants inside that transaction (there are no
physical foreign keys to rely on).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import events, idempotency
from novel_llm.domain.cursor import decode_cursor, encode_cursor
from novel_llm.domain.errors import (
    ConcurrencyConflict,
    ProjectArchived,
    ProjectNotFound,
)
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.infrastructure.orm import ProjectIdempotencyKeyRow, ProjectRow

DEFAULT_LANGUAGE = "zh-Hans"


@dataclass(frozen=True, slots=True)
class Project:
    id: UUID
    title: str
    description: str | None
    language: str
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


def _to_project(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        title=row.title,
        description=row.description,
        language=row.language,
        status=row.status,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def request_fingerprint(title: str, description: str | None, language: str) -> str:
    """A stable digest of the create payload; idempotent replay must match it."""
    return idempotency.request_fingerprint(
        {"title": title, "description": description, "language": language}
    )


async def create_project(
    session: AsyncSession,
    *,
    title: str,
    description: str | None,
    language: str,
    idempotency_key: str,
) -> tuple[Project, bool]:
    """Create a Project, honoring the idempotency key: a retried create with
    the same key and payload returns the original Project, never a duplicate.

    Returns the Project and whether this call created it."""
    fingerprint = request_fingerprint(title, description, language)
    now = datetime.now(UTC)
    project_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        ProjectIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=project_id,
        resource_ref_column="project_id",
    )
    if existing_id is not None:
        existing = await session.get_one(ProjectRow, existing_id)
        return _to_project(existing), False

    result = await session.execute(
        insert(ProjectRow)
        .values(
            id=project_id,
            title=title,
            description=description,
            language=language,
            status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        .returning(ProjectRow)
    )
    row = result.scalar_one()
    record_event(
        session,
        project_id=project_id,
        event_type=events.PROJECT_CREATED,
        payload={"project_id": str(project_id), "title": title, "language": language},
    )
    await session.commit()
    return _to_project(row), True


async def get_project(session: AsyncSession, project_id: UUID) -> Project:
    row = await session.get(ProjectRow, project_id)
    if row is None:
        raise ProjectNotFound(f"no Project with id {project_id}")
    return _to_project(row)


@dataclass(frozen=True, slots=True)
class ProjectPage:
    items: list[Project]
    next_cursor: str | None


class _Unset:
    """Sentinel distinguishing 'field not sent' from 'field explicitly null'."""


Unset = _Unset()


@dataclass(frozen=True, slots=True)
class ProjectChanges:
    """A typed change set for Project mutation: Unset means untouched,
    None means explicitly cleared, a value means replaced."""

    title: str | None | _Unset = Unset
    description: str | None | _Unset = Unset
    language: str | None | _Unset = Unset

    def to_values(self) -> dict[str, str | None]:
        values: dict[str, str | None] = {}
        for field in ("title", "description", "language"):
            value = getattr(self, field)
            if not isinstance(value, _Unset):
                values[field] = value
        return values


async def list_projects(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
    status: str = "all",
) -> ProjectPage:
    """List Projects, newest first, keyset-paginated on (created_at, id).

    UUIDv7 ids are time-ordered, so the pair is a stable sort key. The cursor
    is opaque to clients (see domain.cursor)."""
    query = select(ProjectRow).order_by(ProjectRow.created_at.desc(), ProjectRow.id.desc())
    if status != "all":
        query = query.where(ProjectRow.status == status)
    if cursor is not None:
        created_at, id = decode_cursor(cursor)
        query = query.where(
            or_(
                ProjectRow.created_at < created_at,
                and_(ProjectRow.created_at == created_at, ProjectRow.id < id),
            )
        )
    result = await session.execute(query.limit(limit + 1))
    rows = result.scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor(created_at=rows[-1].created_at, id=rows[-1].id) if has_more and rows else None
    )
    return ProjectPage(items=[_to_project(row) for row in rows], next_cursor=next_cursor)


def _resolve_conditional_failure(
    project_id: UUID, existing: ProjectRow | None, *, archived_detail: str
) -> NoReturn:
    """A conditional UPDATE matched no row: say which domain rule refused it."""
    if existing is None:
        raise ProjectNotFound(f"no Project with id {project_id}")
    if existing.status == "archived":
        raise ProjectArchived(archived_detail)
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def update_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    expected_revision: int | None,
    changes: ProjectChanges,
) -> Project:
    """Apply mutable-field changes under optimistic concurrency: the UPDATE
    carries the expected revision (from If-Match), so a stale writer changes
    nothing. Archived Projects reject edits; an empty change set is a read
    returning the current state without a revision bump."""
    values = changes.to_values()
    if not values:
        return await get_project(session, project_id)

    now = datetime.now(UTC)
    statement = (
        update(ProjectRow)
        .where(ProjectRow.id == project_id, ProjectRow.status == "active")
        .values(**values, revision=ProjectRow.revision + 1, updated_at=now)
        .returning(ProjectRow)
    )
    if expected_revision is not None:
        statement = statement.where(ProjectRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=project_id,
            event_type=events.PROJECT_UPDATED,
            payload={
                "project_id": str(project_id),
                "fields": sorted(values),
                "revision": row.revision,
            },
        )
        await session.commit()
        return _to_project(row)

    existing = await session.get(ProjectRow, project_id)
    _resolve_conditional_failure(
        project_id, existing, archived_detail="an archived Project no longer accepts edits"
    )


async def archive_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    expected_revision: int | None,
) -> Project:
    """Archive the Project — the only removal path (ADR-0002). Terminal:
    archiving an archived Project is refused, and there is no unarchive."""
    now = datetime.now(UTC)
    statement = (
        update(ProjectRow)
        .where(ProjectRow.id == project_id, ProjectRow.status == "active")
        .values(status="archived", revision=ProjectRow.revision + 1, updated_at=now)
        .returning(ProjectRow)
    )
    if expected_revision is not None:
        statement = statement.where(ProjectRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=project_id,
            event_type=events.PROJECT_ARCHIVED,
            payload={"project_id": str(project_id), "revision": row.revision},
        )
        await session.commit()
        return _to_project(row)

    existing = await session.get(ProjectRow, project_id)
    _resolve_conditional_failure(
        project_id, existing, archived_detail="the Project is already archived"
    )


def project_etag(project: Project) -> str:
    """The strong validator for a mutable Project: its revision."""
    return f'"{project.revision}"'


def project_payload(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "title": project.title,
        "description": project.description,
        "language": project.language,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
