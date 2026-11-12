"""Story Bible domain rules (ADR-0004/0008/0009): an editable Bible Draft of
typed Bible Entries, published as immutable Published Bible Versions.

Identity is split from content: `bible_identities` carry the stable names
and aliases that persist across versions; draft entries hold the typed
content and freeze into a version at publication. Generation-facing reads
resolve only to published versions — the draft never answers them.

Entry details are code-owned typed contracts (ADR-0008): the nine
first-version entry types each have a payload model, and an unknown key or
wrong shape is a malformed payload, never a silently unfired entry. Event
entries distinguish Narrative Time from Disclosure Order in the schema.
References (identities, relationship endpoints) are validated inside the
mutation's transaction — no physical foreign keys (ADR-0009); every mutation
serializes on the Project row lock and archived Projects reject edits.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import events, idempotency
from novel_llm.domain import projects as projects_domain
from novel_llm.domain.errors import (
    BibleEntryNotFound,
    BibleEntryPayloadInvalid,
    BibleIdentityNotFound,
    BibleNotFound,
    ConcurrencyConflict,
    IdentityNameTaken,
)
from novel_llm.domain.events import record_event
from novel_llm.domain.ids import new_id
from novel_llm.domain.projects import get_project
from novel_llm.infrastructure.orm import (
    BibleEntryIdempotencyKeyRow,
    BibleEntryRow,
    BibleIdentityIdempotencyKeyRow,
    BibleIdentityRow,
    BiblePublishIdempotencyKeyRow,
    PublishedBibleVersionRow,
)

PAYLOAD_SCHEMA_VERSION = 1


class CharacterDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)
    role: str | None = Field(default=None, max_length=500)


class RelationshipDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_identity: UUID
    to_identity: UUID
    description: str = Field(min_length=1, max_length=4000)


class LocationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)


class OrganizationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)


class ItemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)


class RuleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)


class EventDetail(BaseModel):
    """Story events keep Narrative Time (when it happened in the story
    world) apart from Disclosure Order (when the reader learns of it)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)
    narrative_time: str | None = Field(default=None, max_length=500)
    disclosure_order: str | None = Field(default=None, max_length=500)


class StyleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)


class OpenQuestionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    resolved: bool = False


# The nine first-version entry types (architecture doc): the closed set the
# database also enforces.
ENTRY_DETAIL_MODELS: dict[str, type[BaseModel]] = {
    "character": CharacterDetail,
    "relationship": RelationshipDetail,
    "location": LocationDetail,
    "organization": OrganizationDetail,
    "item": ItemDetail,
    "rule": RuleDetail,
    "event": EventDetail,
    "style": StyleDetail,
    "open_question": OpenQuestionDetail,
}

BIBLE_ENTRY_TYPES: frozenset[str] = frozenset(ENTRY_DETAIL_MODELS)


def _validate_detail(entry_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Validate one detail against its type's contract (ADR-0008)."""
    model = ENTRY_DETAIL_MODELS.get(entry_type)
    if model is None:
        raise BibleEntryPayloadInvalid(f"{entry_type!r} is not a Bible entry type")
    try:
        return model.model_validate(detail).model_dump(mode="json")
    except ValidationError as error:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in error.errors()[:5]
        )
        raise BibleEntryPayloadInvalid(
            f"detail for {entry_type!r} does not match its contract: {summary}"
        ) from None


@dataclass(frozen=True, slots=True)
class BibleIdentity:
    id: UUID
    project_id: UUID
    name: str
    aliases: list[str]
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BibleEntry:
    id: UUID
    project_id: UUID
    identity_id: UUID
    entry_type: str
    detail: dict[str, Any]
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PublishedBibleVersion:
    id: UUID
    project_id: UUID
    version_number: int
    payload_schema_version: int
    entries: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BibleDraft:
    """The editable working state: identities and active entries."""

    project_id: UUID
    identities: list[BibleIdentity]
    entries: list[BibleEntry]


def _to_identity(row: BibleIdentityRow) -> BibleIdentity:
    return BibleIdentity(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        aliases=list(row.aliases),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_entry(row: BibleEntryRow) -> BibleEntry:
    return BibleEntry(
        id=row.id,
        project_id=row.project_id,
        identity_id=row.identity_id,
        entry_type=row.entry_type,
        detail=row.detail,
        status=row.status,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_version(row: PublishedBibleVersionRow) -> PublishedBibleVersion:
    return PublishedBibleVersion(
        id=row.id,
        project_id=row.project_id,
        version_number=row.version_number,
        payload_schema_version=row.payload_schema_version,
        entries=row.entries,
        created_at=row.created_at,
    )


def identity_payload(identity: BibleIdentity) -> dict[str, Any]:
    return {
        "id": str(identity.id),
        "project_id": str(identity.project_id),
        "name": identity.name,
        "aliases": identity.aliases,
        "revision": identity.revision,
        "created_at": identity.created_at,
        "updated_at": identity.updated_at,
    }


def entry_payload(entry: BibleEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "project_id": str(entry.project_id),
        "identity_id": str(entry.identity_id),
        "entry_type": entry.entry_type,
        "detail": entry.detail,
        "status": entry.status,
        "revision": entry.revision,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def draft_payload(draft: BibleDraft) -> dict[str, Any]:
    return {
        "project_id": str(draft.project_id),
        "identities": [identity_payload(identity) for identity in draft.identities],
        "entries": [entry_payload(entry) for entry in draft.entries],
    }


def version_payload(version: PublishedBibleVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "project_id": str(version.project_id),
        "version_number": version.version_number,
        "payload_schema_version": version.payload_schema_version,
        "entries": version.entries,
        "created_at": version.created_at,
    }


def identity_etag(identity: BibleIdentity) -> str:
    return f'"{identity.revision}"'


def entry_etag(entry: BibleEntry) -> str:
    return f'"{entry.revision}"'


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_identity_in_project(
    session: AsyncSession, project_id: UUID, identity_id: UUID
) -> BibleIdentityRow:
    """The referenced identity must exist inside this Project — the
    transactional check that stands in for a physical foreign key
    (ADR-0009)."""
    row = await session.get(BibleIdentityRow, identity_id)
    if row is None or row.project_id != project_id:
        raise BibleIdentityNotFound(f"no Identity with id {identity_id} in Project {project_id}")
    return row


async def create_identity(
    session: AsyncSession,
    *,
    project_id: UUID,
    name: str,
    aliases: list[str],
    idempotency_key: str,
) -> tuple[BibleIdentity, bool]:
    """Create a stable Bible identity, honoring the idempotency key
    (ADR-0008). Identities persist across published versions; edits to them
    flow into the next publication, never into a frozen one."""
    fingerprint = idempotency.request_fingerprint(
        {"name": name, "aliases": aliases, "project_id": str(project_id)}
    )
    now = _now()
    identity_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        BibleIdentityIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=identity_id,
        resource_ref_column="identity_id",
    )
    if existing_id is not None:
        existing = await session.get_one(BibleIdentityRow, existing_id)
        return _to_identity(existing), False

    await projects_domain.lock_active_project(session, project_id=project_id)
    row = BibleIdentityRow(
        id=identity_id,
        project_id=project_id,
        name=name,
        aliases=aliases,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.BIBLE_IDENTITY_CREATED,
        payload={"identity_id": str(identity_id), "name": name, "aliases": aliases},
    )
    try:
        await session.commit()
    except IntegrityError:
        # (project_id, name) is unique (ADR-0002: PostgreSQL enforces the
        # integrity boundary); the Project lock serializes writers, so the
        # collision is a real duplicate, not a race artifact.
        await session.rollback()
        raise IdentityNameTaken(
            f"Project {project_id} already has a Bible identity named {name!r}"
        ) from None
    return _to_identity(row), True


async def update_identity(
    session: AsyncSession,
    *,
    project_id: UUID,
    identity_id: UUID,
    expected_revision: int | None,
    name: str,
    aliases: list[str],
) -> BibleIdentity:
    """Edit the identity's stable surface (name, aliases) under optimistic
    concurrency, scoped to the Project in the path. Frozen versions keep
    what they froze; the edit flows into the next publication."""
    existing = await session.get(BibleIdentityRow, identity_id)
    if existing is None or existing.project_id != project_id:
        raise BibleIdentityNotFound(f"no Identity with id {identity_id} in Project {project_id}")
    await projects_domain.lock_active_project(session, project_id=project_id)

    statement = (
        update(BibleIdentityRow)
        .where(BibleIdentityRow.id == identity_id)
        .values(
            name=name, aliases=aliases, revision=BibleIdentityRow.revision + 1, updated_at=_now()
        )
        .returning(BibleIdentityRow)
    )
    if expected_revision is not None:
        statement = statement.where(BibleIdentityRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=row.project_id,
            event_type=events.BIBLE_IDENTITY_UPDATED,
            payload={
                "identity_id": str(row.id),
                "name": row.name,
                "aliases": list(row.aliases),
                "revision": row.revision,
            },
        )
        await session.commit()
        return _to_identity(row)
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def create_entry(
    session: AsyncSession,
    *,
    project_id: UUID,
    identity_id: UUID,
    entry_type: str,
    detail: dict[str, Any],
    idempotency_key: str,
) -> tuple[BibleEntry, bool]:
    """Create one typed entry on the draft. The identity reference validates
    transactionally and scoped to the Project; relationship endpoints too;
    the detail validates against its type's contract."""
    fingerprint = idempotency.request_fingerprint(
        {
            "identity_id": str(identity_id),
            "entry_type": entry_type,
            "detail": detail,
            "project_id": str(project_id),
        }
    )
    now = _now()
    entry_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        BibleEntryIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=entry_id,
        resource_ref_column="entry_id",
    )
    if existing_id is not None:
        existing = await session.get_one(BibleEntryRow, existing_id)
        return _to_entry(existing), False

    await projects_domain.lock_active_project(session, project_id=project_id)
    await _get_identity_in_project(session, project_id, identity_id)
    validated = _validate_detail(entry_type, detail)
    await _validate_relationship_endpoints(
        session, project_id=project_id, entry_type=entry_type, detail=validated
    )

    row = BibleEntryRow(
        id=entry_id,
        project_id=project_id,
        identity_id=identity_id,
        entry_type=entry_type,
        detail=validated,
        status="active",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.BIBLE_ENTRY_CREATED,
        payload={
            "entry_id": str(entry_id),
            "identity_id": str(identity_id),
            "entry_type": entry_type,
        },
    )
    await session.commit()
    return _to_entry(row), True


async def _validate_relationship_endpoints(
    session: AsyncSession, *, project_id: UUID, entry_type: str, detail: dict[str, Any]
) -> None:
    """A relationship entry names two identities of the same Project."""
    if entry_type != "relationship":
        return
    for field in ("from_identity", "to_identity"):
        endpoint = UUID(detail[field])
        await _get_identity_in_project(session, project_id, endpoint)


async def update_entry(
    session: AsyncSession,
    *,
    entry_id: UUID,
    expected_revision: int | None,
    detail: dict[str, Any],
) -> BibleEntry:
    """Edit an entry's typed detail under optimistic concurrency; the entry
    type itself is fixed — changing what an entry *is* means a new entry."""
    existing = await session.get(BibleEntryRow, entry_id)
    if existing is None:
        raise BibleEntryNotFound(f"no Bible entry with id {entry_id}")
    await projects_domain.lock_active_project(session, project_id=existing.project_id)
    validated = _validate_detail(existing.entry_type, detail)
    await _validate_relationship_endpoints(
        session,
        project_id=existing.project_id,
        entry_type=existing.entry_type,
        detail=validated,
    )

    statement = (
        update(BibleEntryRow)
        .where(BibleEntryRow.id == entry_id)
        .values(detail=validated, revision=BibleEntryRow.revision + 1, updated_at=_now())
        .returning(BibleEntryRow)
    )
    if expected_revision is not None:
        statement = statement.where(BibleEntryRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=row.project_id,
            event_type=events.BIBLE_ENTRY_UPDATED,
            payload={"entry_id": str(row.id), "revision": row.revision},
        )
        await session.commit()
        return _to_entry(row)
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def archive_entry(
    session: AsyncSession,
    *,
    entry_id: UUID,
    expected_revision: int | None,
) -> BibleEntry:
    """Archive an entry — the draft's only removal path (ADR-0002: archive,
    never delete). Already-published versions keep what they froze."""
    existing = await session.get(BibleEntryRow, entry_id)
    if existing is None:
        raise BibleEntryNotFound(f"no Bible entry with id {entry_id}")
    await projects_domain.lock_active_project(session, project_id=existing.project_id)

    statement = (
        update(BibleEntryRow)
        .where(BibleEntryRow.id == entry_id)
        .values(status="archived", revision=BibleEntryRow.revision + 1, updated_at=_now())
        .returning(BibleEntryRow)
    )
    if expected_revision is not None:
        statement = statement.where(BibleEntryRow.revision == expected_revision)

    row = (await session.execute(statement)).scalar_one_or_none()
    if row is not None:
        record_event(
            session,
            project_id=row.project_id,
            event_type=events.BIBLE_ENTRY_ARCHIVED,
            payload={"entry_id": str(row.id), "entry_type": row.entry_type},
        )
        await session.commit()
        return _to_entry(row)
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def bible_draft(session: AsyncSession, project_id: UUID) -> BibleDraft:
    """The editable working state in one read: identities, then active
    entries — archived ones are working history, not draft content."""
    await get_project(session, project_id)
    identity_rows = (
        (
            await session.execute(
                select(BibleIdentityRow)
                .where(BibleIdentityRow.project_id == project_id)
                .order_by(BibleIdentityRow.created_at.asc(), BibleIdentityRow.id.asc())
            )
        )
        .scalars()
        .all()
    )
    entry_rows = (
        (
            await session.execute(
                select(BibleEntryRow)
                .where(
                    BibleEntryRow.project_id == project_id,
                    BibleEntryRow.status == "active",
                )
                .order_by(BibleEntryRow.created_at.asc(), BibleEntryRow.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return BibleDraft(
        project_id=project_id,
        identities=[_to_identity(row) for row in identity_rows],
        entries=[_to_entry(row) for row in entry_rows],
    )


async def publish_bible(
    session: AsyncSession,
    *,
    project_id: UUID,
    idempotency_key: str,
) -> tuple[PublishedBibleVersion, bool]:
    """Publish the draft as one immutable Published Bible Version: identities
    (names and aliases as of now) and active entries freeze; changes edit the
    draft and require a new publication. The Project row lock serializes
    version allocation against concurrent publications."""
    fingerprint = idempotency.request_fingerprint({"project_id": str(project_id)})
    version_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        BiblePublishIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=version_id,
        resource_ref_column="version_id",
    )
    if existing_id is not None:
        existing = await session.get_one(PublishedBibleVersionRow, existing_id)
        return _to_version(existing), False

    await projects_domain.lock_active_project(session, project_id=project_id)
    draft = await bible_draft(session, project_id)

    # The snapshot is self-contained (ADR-0004: the version freezes its
    # entries AND relationships): every identity of the Project freezes with
    # its name and aliases as of now — including ones no entry references —
    # and each entry keeps its own id, so relationship endpoints (identity
    # ids) resolve inside the version without touching the live tables.
    frozen = {
        "identities": [
            {
                "identity_id": str(identity.id),
                "name": identity.name,
                "aliases": identity.aliases,
            }
            for identity in draft.identities
        ],
        "entries": [
            {
                "entry_id": str(entry.id),
                "identity_id": str(entry.identity_id),
                "entry_type": entry.entry_type,
                "detail": entry.detail,
            }
            for entry in draft.entries
        ],
    }

    next_version = (
        await session.execute(
            select(func.max(PublishedBibleVersionRow.version_number)).where(
                PublishedBibleVersionRow.project_id == project_id
            )
        )
    ).scalar_one() or 0
    row = PublishedBibleVersionRow(
        id=version_id,
        project_id=project_id,
        version_number=next_version + 1,
        payload_schema_version=PAYLOAD_SCHEMA_VERSION,
        entries=frozen,
        created_at=_now(),
    )
    session.add(row)
    record_event(
        session,
        project_id=project_id,
        event_type=events.BIBLE_PUBLISHED,
        payload={
            "version_id": str(version_id),
            "version_number": row.version_number,
            "entry_count": len(frozen),
        },
    )
    await session.commit()
    return _to_version(row), True


async def get_current_bible_version(
    session: AsyncSession, project_id: UUID
) -> PublishedBibleVersion:
    """The generation-facing reference: the highest published version. The
    draft never answers this call (ADR-0004)."""
    await get_project(session, project_id)
    row = (
        await session.execute(
            select(PublishedBibleVersionRow)
            .where(PublishedBibleVersionRow.project_id == project_id)
            .order_by(PublishedBibleVersionRow.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise BibleNotFound(f"Project {project_id} has no published Bible version")
    return _to_version(row)


async def get_bible_version(
    session: AsyncSession, project_id: UUID, version_number: int
) -> PublishedBibleVersion:
    """One published version, addressable forever at its number."""
    await get_project(session, project_id)
    row = (
        await session.execute(
            select(PublishedBibleVersionRow).where(
                PublishedBibleVersionRow.project_id == project_id,
                PublishedBibleVersionRow.version_number == version_number,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise BibleNotFound(f"Project {project_id} has no published Bible version {version_number}")
    return _to_version(row)
