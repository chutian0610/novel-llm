"""Story Bible resources under /api/v1 (ADR-0008/0004): the editable draft
(identities + typed entries) and the immutable published versions. Creations
take idempotency keys; mutable objects carry ETag/If-Match; the
generation-facing read resolves only published versions."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.headers import check_idempotency_key, parse_if_match
from novel_llm.api.problems import Problem
from novel_llm.domain import bible as bible_domain
from novel_llm.domain.bible import (
    BibleDraft,
    BibleEntry,
    BibleIdentity,
    PublishedBibleVersion,
)

router = APIRouter(tags=["bible"])

# The nine first-version entry types (architecture doc) — the closed set the
# API speaks, enforced again in the domain and the database.
EntryType = Literal[
    "character",
    "relationship",
    "location",
    "organization",
    "item",
    "rule",
    "event",
    "style",
    "open_question",
]

_BIBLE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Malformed request"},
    404: {"model": Problem, "description": "Project, identity, or entry not found"},
    409: {"model": Problem, "description": "Domain rule refused the change"},
    412: {"model": Problem, "description": "If-Match ETag does not match the current version"},
    428: {
        "model": Problem,
        "description": "If-Match header is required to change a mutable object",
    },
}


class BibleIdentityResource(BaseModel):
    id: str
    project_id: str
    name: str
    aliases: list[str]
    revision: int
    created_at: datetime
    updated_at: datetime


class BibleEntryResource(BaseModel):
    id: str
    project_id: str
    identity_id: str
    entry_type: EntryType
    detail: dict[str, Any]
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class BibleDraftResource(BaseModel):
    project_id: str
    identities: list[BibleIdentityResource]
    entries: list[BibleEntryResource]


class PublishedBibleResource(BaseModel):
    id: str
    project_id: str
    version_number: int
    payload_schema_version: int
    entries: dict[str, Any]
    created_at: datetime


def _identity_resource(identity: BibleIdentity) -> BibleIdentityResource:
    return BibleIdentityResource.model_validate(bible_domain.identity_payload(identity))


def _entry_resource(entry: BibleEntry) -> BibleEntryResource:
    return BibleEntryResource.model_validate(bible_domain.entry_payload(entry))


def _draft_resource(draft: BibleDraft) -> BibleDraftResource:
    return BibleDraftResource.model_validate(bible_domain.draft_payload(draft))


def _version_resource(version: PublishedBibleVersion) -> PublishedBibleResource:
    return PublishedBibleResource.model_validate(bible_domain.version_payload(version))


class CreateIdentityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class UpdateIdentityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CreateEntryRequest(BaseModel):
    identity_id: UUID
    entry_type: EntryType
    detail: dict[str, Any]


class UpdateEntryRequest(BaseModel):
    detail: dict[str, Any]


@router.post(
    "/projects/{project_id}/bible/identities",
    status_code=201,
    response_model=BibleIdentityResource,
    responses={
        200: {
            "model": BibleIdentityResource,
            "description": "Idempotent replay: the original identity, not a duplicate",
        },
        404: {"model": Problem, "description": "Project not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
    },
)
async def create_bible_identity(
    project_id: UUID,
    payload: CreateIdentityRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> BibleIdentityResource:
    identity, created = await bible_domain.create_identity(
        session,
        project_id=project_id,
        name=payload.name,
        aliases=payload.aliases,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = bible_domain.identity_etag(identity)
    response.headers["Location"] = f"/api/v1/projects/{project_id}/bible/identities/{identity.id}"
    return _identity_resource(identity)


@router.patch(
    "/projects/{project_id}/bible/identities/{identity_id}",
    response_model=BibleIdentityResource,
    responses=_BIBLE_PROBLEMS,
)
async def update_bible_identity(
    project_id: UUID,
    identity_id: UUID,
    payload: UpdateIdentityRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> BibleIdentityResource:
    identity = await bible_domain.update_identity(
        session,
        project_id=project_id,
        identity_id=identity_id,
        expected_revision=parse_if_match(if_match),
        name=payload.name,
        aliases=payload.aliases,
    )
    response.headers["ETag"] = bible_domain.identity_etag(identity)
    return _identity_resource(identity)


@router.post(
    "/projects/{project_id}/bible/entries",
    status_code=201,
    response_model=BibleEntryResource,
    responses={
        200: {
            "model": BibleEntryResource,
            "description": "Idempotent replay: the original entry, not a duplicate",
        },
        400: {
            "model": Problem,
            "description": "Detail does not match the type contract, or invalid Idempotency-Key",
        },
        404: {"model": Problem, "description": "Project or referenced identity not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
    },
)
async def create_bible_entry(
    project_id: UUID,
    payload: CreateEntryRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> BibleEntryResource:
    entry, created = await bible_domain.create_entry(
        session,
        project_id=project_id,
        identity_id=payload.identity_id,
        entry_type=payload.entry_type,
        detail=payload.detail,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = bible_domain.entry_etag(entry)
    response.headers["Location"] = f"/api/v1/bible/entries/{entry.id}"
    return _entry_resource(entry)


@router.patch(
    "/bible/entries/{entry_id}",
    response_model=BibleEntryResource,
    responses=_BIBLE_PROBLEMS,
)
async def update_bible_entry(
    entry_id: UUID,
    payload: UpdateEntryRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> BibleEntryResource:
    entry = await bible_domain.update_entry(
        session,
        entry_id=entry_id,
        expected_revision=parse_if_match(if_match),
        detail=payload.detail,
    )
    response.headers["ETag"] = bible_domain.entry_etag(entry)
    return _entry_resource(entry)


@router.post(
    "/bible/entries/{entry_id}/archive",
    response_model=BibleEntryResource,
    responses=_BIBLE_PROBLEMS,
)
async def archive_bible_entry(
    entry_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> BibleEntryResource:
    """Domain action: archive an entry — the draft's only removal path
    (ADR-0002). Published versions keep what they froze."""
    entry = await bible_domain.archive_entry(
        session, entry_id=entry_id, expected_revision=parse_if_match(if_match)
    )
    response.headers["ETag"] = bible_domain.entry_etag(entry)
    return _entry_resource(entry)


@router.get(
    "/projects/{project_id}/bible/draft",
    response_model=BibleDraftResource,
    responses={404: {"model": Problem, "description": "Project not found"}},
)
async def get_bible_draft(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BibleDraftResource:
    return _draft_resource(await bible_domain.bible_draft(session, project_id))


@router.post(
    "/projects/{project_id}/bible/publish",
    status_code=201,
    response_model=PublishedBibleResource,
    responses={
        200: {
            "model": PublishedBibleResource,
            "description": "Idempotent replay: the original version, not a duplicate",
        },
        404: {"model": Problem, "description": "Project not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
    },
)
async def publish_bible(
    project_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> PublishedBibleResource:
    version, created = await bible_domain.publish_bible(
        session, project_id=project_id, idempotency_key=idempotency_key
    )
    if not created:
        response.status_code = 200
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/bible/versions/{version.version_number}"
    )
    return _version_resource(version)


@router.get(
    "/projects/{project_id}/bible",
    response_model=PublishedBibleResource,
    responses={
        404: {
            "model": Problem,
            "description": "Project not found, or no Bible version published yet",
        },
    },
)
async def get_current_bible(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublishedBibleResource:
    """The generation-facing reference: the highest published version. The
    draft never answers this call (ADR-0004)."""
    return _version_resource(await bible_domain.get_current_bible_version(session, project_id))


@router.get(
    "/projects/{project_id}/bible/versions/{version_number}",
    response_model=PublishedBibleResource,
    responses={
        404: {
            "model": Problem,
            "description": "Project not found, or no such Bible version",
        },
    },
)
async def get_bible_version(
    project_id: UUID,
    version_number: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublishedBibleResource:
    return _version_resource(
        await bible_domain.get_bible_version(session, project_id, version_number)
    )
