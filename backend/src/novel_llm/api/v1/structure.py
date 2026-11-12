"""Story structure resources under /api/v1 (ADR-0008): Volumes and Chapters
with stable identities and mutable positions. Creations take idempotency
keys; mutable objects carry ETag/If-Match; reorders and moves are domain
actions that return the refreshed structure. Reference failures are stable
problem codes — never database integrity errors (ADR-0009)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.headers import check_idempotency_key, parse_if_match
from novel_llm.api.problems import Problem
from novel_llm.domain import projects as projects_domain
from novel_llm.domain import story
from novel_llm.domain.story import Chapter, Structure, Volume

router = APIRouter(tags=["story-structure"])


def _validated_title(value: str) -> str:
    """Strip surrounding blanks; a blank title is a 400 validation problem."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("title must not be blank")
    return stripped


TitleField = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_validated_title)]

_STRUCTURE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Malformed request"},
    404: {"model": Problem, "description": "Project, Volume, or Chapter not found"},
    409: {"model": Problem, "description": "Domain rule refused the change"},
    412: {"model": Problem, "description": "If-Match ETag does not match the current version"},
    428: {
        "model": Problem,
        "description": "If-Match header is required to change a mutable object",
    },
}


class VolumeResource(BaseModel):
    id: str
    project_id: str
    title: str
    position: int
    created_at: datetime
    updated_at: datetime


class ChapterResource(BaseModel):
    id: str
    project_id: str
    volume_id: str
    title: str
    position: int
    created_at: datetime
    updated_at: datetime


class ChapterNodeResource(BaseModel):
    id: str
    title: str
    volume_id: str
    position: int


class VolumeNodeResource(BaseModel):
    id: str
    title: str
    position: int
    chapters: list[ChapterNodeResource]


class StructureResource(BaseModel):
    project_id: str
    volumes: list[VolumeNodeResource]


def _volume_resource(volume: Volume) -> VolumeResource:
    return VolumeResource.model_validate(story.volume_payload(volume))


def _chapter_resource(chapter: Chapter) -> ChapterResource:
    return ChapterResource.model_validate(story.chapter_payload(chapter))


def _structure_resource(structure: Structure) -> StructureResource:
    return StructureResource(
        project_id=str(structure.project_id),
        volumes=[
            VolumeNodeResource(
                id=str(node.volume.id),
                title=node.volume.title,
                position=node.volume.position,
                chapters=[
                    ChapterNodeResource(
                        id=str(chapter.id),
                        title=chapter.title,
                        volume_id=str(chapter.volume_id),
                        position=chapter.position,
                    )
                    for chapter in node.chapters
                ],
            )
            for node in structure.volumes
        ],
    )


class CreateVolumeRequest(BaseModel):
    title: TitleField


class CreateChapterRequest(BaseModel):
    volume_id: UUID
    title: TitleField


class ReorderVolumesRequest(BaseModel):
    ordered_volume_ids: list[UUID]


class ReorderChaptersRequest(BaseModel):
    ordered_chapter_ids: list[UUID]


class MoveChapterRequest(BaseModel):
    to_volume_id: UUID


class UpdateVolumeRequest(BaseModel):
    title: TitleField


class UpdateChapterRequest(BaseModel):
    title: TitleField


async def _structure_of(session: AsyncSession, project_id: UUID) -> StructureResource:
    return _structure_resource(await story.project_structure(session, project_id))


@router.post(
    "/projects/{project_id}/volumes",
    status_code=201,
    response_model=VolumeResource,
    responses={
        200: {
            "model": VolumeResource,
            "description": "Idempotent replay: the original Volume, not a duplicate",
        },
        404: {"model": Problem, "description": "Project not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
    },
)
async def create_volume(
    project_id: UUID,
    payload: CreateVolumeRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> VolumeResource:
    volume, created = await story.create_volume(
        session, project_id=project_id, title=payload.title, idempotency_key=idempotency_key
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = story.volume_etag(volume)
    response.headers["Location"] = f"/api/v1/volumes/{volume.id}"
    return _volume_resource(volume)


@router.post(
    "/projects/{project_id}/chapters",
    status_code=201,
    response_model=ChapterResource,
    responses={
        200: {
            "model": ChapterResource,
            "description": "Idempotent replay: the original Chapter, not a duplicate",
        },
        404: {"model": Problem, "description": "Project or Volume not found"},
        409: {"model": Problem, "description": "Project archived or idempotency key conflict"},
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
    },
)
async def create_chapter(
    project_id: UUID,
    payload: CreateChapterRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> ChapterResource:
    chapter, created = await story.create_chapter(
        session,
        project_id=project_id,
        volume_id=payload.volume_id,
        title=payload.title,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = story.chapter_etag(chapter)
    response.headers["Location"] = f"/api/v1/chapters/{chapter.id}"
    return _chapter_resource(chapter)


@router.get(
    "/projects/{project_id}/structure",
    response_model=StructureResource,
    responses={404: {"model": Problem, "description": "Project not found"}},
)
async def get_project_structure(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StructureResource:
    # The Project is the container — reference it explicitly so an unknown
    # id is a 404, not an empty structure.
    await projects_domain.get_project(session, project_id)
    return await _structure_of(session, project_id)


@router.get(
    "/volumes/{volume_id}",
    response_model=VolumeResource,
    responses={404: {"model": Problem, "description": "Volume not found"}},
)
async def get_volume(
    volume_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VolumeResource:
    volume = await story.get_volume(session, volume_id)
    response.headers["ETag"] = story.volume_etag(volume)
    return _volume_resource(volume)


@router.get(
    "/chapters/{chapter_id}",
    response_model=ChapterResource,
    responses={404: {"model": Problem, "description": "Chapter not found"}},
)
async def get_chapter(
    chapter_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChapterResource:
    chapter = await story.get_chapter(session, chapter_id)
    response.headers["ETag"] = story.chapter_etag(chapter)
    return _chapter_resource(chapter)


@router.post(
    "/projects/{project_id}/volumes/reorder",
    response_model=StructureResource,
    responses={
        400: {"model": Problem, "description": "The order does not match the structure"},
        404: {"model": Problem, "description": "Project not found"},
        409: {"model": Problem, "description": "Project archived"},
    },
)
async def reorder_project_volumes(
    project_id: UUID,
    payload: ReorderVolumesRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StructureResource:
    await story.reorder_volumes(
        session, project_id=project_id, ordered_volume_ids=payload.ordered_volume_ids
    )
    return await _structure_of(session, project_id)


@router.post(
    "/volumes/{volume_id}/chapters/reorder",
    response_model=StructureResource,
    responses={
        400: {"model": Problem, "description": "The order does not match the structure"},
        404: {"model": Problem, "description": "Volume not found"},
    },
)
async def reorder_volume_chapters(
    volume_id: UUID,
    payload: ReorderChaptersRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StructureResource:
    volume = await story.get_volume(session, volume_id)
    await story.reorder_chapters(
        session, volume_id=volume_id, ordered_chapter_ids=payload.ordered_chapter_ids
    )
    return await _structure_of(session, volume.project_id)


@router.post(
    "/chapters/{chapter_id}/move",
    response_model=ChapterResource,
    responses={
        404: {"model": Problem, "description": "Chapter or target Volume not found"},
    },
)
async def move_chapter(
    chapter_id: UUID,
    payload: MoveChapterRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChapterResource:
    return _chapter_resource(
        await story.move_chapter(session, chapter_id=chapter_id, to_volume_id=payload.to_volume_id)
    )


@router.patch(
    "/volumes/{volume_id}",
    response_model=VolumeResource,
    responses=_STRUCTURE_PROBLEMS,
)
async def update_volume(
    volume_id: UUID,
    payload: UpdateVolumeRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> VolumeResource:
    volume = await story.update_volume(
        session,
        volume_id=volume_id,
        expected_revision=parse_if_match(if_match),
        title=payload.title,
    )
    response.headers["ETag"] = story.volume_etag(volume)
    return _volume_resource(volume)


@router.patch(
    "/chapters/{chapter_id}",
    response_model=ChapterResource,
    responses=_STRUCTURE_PROBLEMS,
)
async def update_chapter(
    chapter_id: UUID,
    payload: UpdateChapterRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ChapterResource:
    chapter = await story.update_chapter(
        session,
        chapter_id=chapter_id,
        expected_revision=parse_if_match(if_match),
        title=payload.title,
    )
    response.headers["ETag"] = story.chapter_etag(chapter)
    return _chapter_resource(chapter)
