"""Project resources under /api/v1 (ADR-0008): creations take idempotency
keys, mutable objects carry ETag/If-Match, errors are problem-details.

Every error response is declared with the ``Problem`` model so the generated
typed client models the real error surface, including the 200 replay path on
creation."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.problems import Problem, ProblemError
from novel_llm.domain import projects
from novel_llm.domain.projects import Project, ProjectChanges, Unset

router = APIRouter(tags=["projects"])

_IDEMPOTENCY_KEY_MAX_LENGTH = 200

_CONCURRENCY_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Malformed request or If-Match header"},
    404: {"model": Problem, "description": "Project not found"},
    409: {"model": Problem, "description": "Domain rule refused the change"},
    412: {"model": Problem, "description": "If-Match ETag does not match the current version"},
    428: {
        "model": Problem,
        "description": "If-Match header is required to change a mutable object",
    },
}


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    language: str = Field(default=projects.DEFAULT_LANGUAGE, max_length=35)

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProjectResource(BaseModel):
    id: str
    title: str
    description: str | None
    language: str
    status: str
    created_at: datetime
    updated_at: datetime


def _resource(project: Project) -> ProjectResource:
    payload = projects.project_payload(project)
    return ProjectResource.model_validate(payload)


def _set_etag(response: Response, project: Project) -> None:
    response.headers["ETag"] = projects.project_etag(project)


def _check_idempotency_key(key: str) -> str:
    key = key.strip()
    if len(key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ProblemError(
            status=400,
            code="invalid_idempotency_key",
            title="Idempotency-Key header is too long",
        )
    return key


@router.post(
    "/projects",
    status_code=201,
    response_model=ProjectResource,
    responses={
        200: {
            "model": ProjectResource,
            "description": "Idempotent replay: the original Project, not a duplicate",
        },
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
        409: {"model": Problem, "description": "Idempotency key reused with a different payload"},
    },
)
async def create_project(
    payload: CreateProjectRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ProjectResource:
    key = _check_idempotency_key(idempotency_key)
    project, created = await projects.create_project(
        session,
        title=payload.title,
        description=payload.description,
        language=payload.language,
        idempotency_key=key,
    )
    if not created:
        # A replayed create returns the original Project (200), not a duplicate.
        response.status_code = 200
    _set_etag(response, project)
    response.headers["Location"] = f"/api/v1/projects/{project.id}"
    return _resource(project)


class ProjectListResource(BaseModel):
    items: list[ProjectResource]
    next_cursor: str | None


@router.get(
    "/projects",
    response_model=ProjectListResource,
    responses={400: {"model": Problem, "description": "Malformed cursor or query"}},
)
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
    status: Annotated[Literal["active", "archived", "all"], Query()] = "all",
) -> ProjectListResource:
    page = await projects.list_projects(session, limit=limit, cursor=cursor, status=status)
    return ProjectListResource(
        items=[_resource(project) for project in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResource,
    responses={
        404: {"model": Problem, "description": "Project not found"},
        400: {"model": Problem, "description": "Malformed project id"},
    },
)
async def get_project(
    project_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectResource:
    project = await projects.get_project(session, project_id)
    _set_etag(response, project)
    return _resource(project)


class UpdateProjectRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, max_length=35)

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


def _expected_revision(if_match: str | None) -> int | None:
    """Parse If-Match into the expected revision. `*` maps to None, meaning
    any current version is accepted; a missing header is a 428, a malformed
    one a 400."""
    value = (if_match or "").strip()
    if not value:
        raise ProblemError(
            status=428,
            code="missing_if_match",
            title="If-Match header is required to change a mutable object",
        )
    if value == "*":
        return None
    if value.startswith('"') and value.endswith('"') and value[1:-1].isdigit():
        return int(value[1:-1])
    raise ProblemError(status=400, code="malformed_if_match", title="If-Match header is malformed")


def _changes(payload: UpdateProjectRequest) -> ProjectChanges:
    """Fields the client explicitly sent become changes; explicitly-null
    fields clear, absent fields stay untouched."""
    return ProjectChanges(
        title=payload.title if "title" in payload.model_fields_set else Unset,
        description=payload.description if "description" in payload.model_fields_set else Unset,
        language=payload.language if "language" in payload.model_fields_set else Unset,
    )


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResource,
    responses=_CONCURRENCY_PROBLEMS,
)
async def update_project(
    project_id: UUID,
    payload: UpdateProjectRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProjectResource:
    expected_revision = _expected_revision(if_match)
    project = await projects.update_project(
        session,
        project_id=project_id,
        expected_revision=expected_revision,
        changes=_changes(payload),
    )
    _set_etag(response, project)
    return _resource(project)


@router.post(
    "/projects/{project_id}/archive",
    response_model=ProjectResource,
    responses=_CONCURRENCY_PROBLEMS,
)
async def archive_project(
    project_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProjectResource:
    expected_revision = _expected_revision(if_match)
    project = await projects.archive_project(
        session, project_id=project_id, expected_revision=expected_revision
    )
    _set_etag(response, project)
    return _resource(project)
