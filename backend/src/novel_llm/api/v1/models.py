"""Model configuration resources under /api/v1 (ADR-0006/0008): provider
credentials and Model Profiles as separate objects. The API never returns a
complete key — every response carries at most the masked hint."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.api.dependencies import get_db_session
from novel_llm.api.headers import check_idempotency_key, parse_if_match
from novel_llm.api.problems import Problem
from novel_llm.domain import model_config
from novel_llm.domain.model_config import ModelProfile, ProviderCredential

router = APIRouter(tags=["model-config"])


class ProviderCredentialResource(BaseModel):
    id: str
    name: str
    provider: str
    api_key_hint: str | None
    base_url: str | None
    created_at: datetime
    updated_at: datetime


class ModelProfileResource(BaseModel):
    id: str
    name: str
    model: str
    credential_id: str
    sampling: dict[str, Any]
    timeout_seconds: int
    max_retries: int
    cost: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CredentialListResource(BaseModel):
    items: list[ProviderCredentialResource]


class ProfileListResource(BaseModel):
    items: list[ModelProfileResource]


def _credential_resource(credential: ProviderCredential) -> ProviderCredentialResource:
    return ProviderCredentialResource.model_validate(model_config.credential_payload(credential))


def _profile_resource(profile: ModelProfile) -> ModelProfileResource:
    return ModelProfileResource.model_validate(model_config.profile_payload(profile))


class CreateCredentialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    # Plaintext by decision (ADR-0006); masked again on every way out.
    api_key: str = Field(default="", max_length=4000)
    base_url: str | None = Field(default=None, max_length=2000)


class UpdateCredentialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=4000)
    base_url: str | None = Field(default=None, max_length=2000)


class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    credential_id: UUID
    temperature: Decimal = Field(default=Decimal("0.7"), ge=0, le=2)
    top_p: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    max_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: int = Field(default=30, gt=0)
    max_retries: int = Field(default=0, ge=0)
    input_token_cost: Decimal = Field(default=Decimal("0"), ge=0)
    output_token_cost: Decimal = Field(default=Decimal("0"), ge=0)


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    top_p: Decimal | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    input_token_cost: Decimal | None = Field(default=None, ge=0)
    output_token_cost: Decimal | None = Field(default=None, ge=0)


def _credential_changes(
    payload: UpdateCredentialRequest,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in ("name", "provider", "api_key", "base_url"):
        if field in payload.model_fields_set and getattr(payload, field) is not None:
            changes[field] = getattr(payload, field)
    return changes


def _profile_changes(payload: UpdateProfileRequest) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in (
        "name",
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "timeout_seconds",
        "max_retries",
        "input_token_cost",
        "output_token_cost",
    ):
        if field in payload.model_fields_set and getattr(payload, field) is not None:
            changes[field] = getattr(payload, field)
    return changes


@router.post(
    "/model-credentials",
    status_code=201,
    response_model=ProviderCredentialResource,
    responses={
        200: {
            "model": ProviderCredentialResource,
            "description": "Idempotent replay: the original credential, not a duplicate",
        },
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
        409: {"model": Problem, "description": "Credential name already taken"},
    },
)
async def create_credential(
    payload: CreateCredentialRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> ProviderCredentialResource:
    credential, created = await model_config.create_credential(
        session,
        name=payload.name,
        provider=payload.provider,
        api_key=payload.api_key,
        base_url=payload.base_url,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = model_config.credential_etag(credential)
    response.headers["Location"] = f"/api/v1/model-credentials/{credential.id}"
    return _credential_resource(credential)


@router.get(
    "/model-credentials",
    response_model=CredentialListResource,
)
async def list_credentials(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CredentialListResource:
    return CredentialListResource(
        items=[
            _credential_resource(credential)
            for credential in await model_config.list_credentials(session)
        ]
    )


@router.get(
    "/model-credentials/{credential_id}",
    response_model=ProviderCredentialResource,
    responses={404: {"model": Problem, "description": "Credential not found"}},
)
async def get_credential(
    credential_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderCredentialResource:
    credential = await model_config.get_credential(session, credential_id)
    response.headers["ETag"] = model_config.credential_etag(credential)
    return _credential_resource(credential)


@router.patch(
    "/model-credentials/{credential_id}",
    response_model=ProviderCredentialResource,
    responses={
        400: {"model": Problem, "description": "Malformed If-Match header"},
        404: {"model": Problem, "description": "Credential not found"},
        409: {"model": Problem, "description": "Credential name already taken"},
        412: {"model": Problem, "description": "If-Match ETag does not match"},
        428: {"model": Problem, "description": "If-Match header is required"},
    },
)
async def update_credential(
    credential_id: UUID,
    payload: UpdateCredentialRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProviderCredentialResource:
    credential = await model_config.update_credential(
        session,
        credential_id=credential_id,
        expected_revision=parse_if_match(if_match),
        values=_credential_changes(payload),
    )
    response.headers["ETag"] = model_config.credential_etag(credential)
    return _credential_resource(credential)


@router.post(
    "/model-profiles",
    status_code=201,
    response_model=ModelProfileResource,
    responses={
        200: {
            "model": ModelProfileResource,
            "description": "Idempotent replay: the original profile, not a duplicate",
        },
        400: {"model": Problem, "description": "Missing or invalid Idempotency-Key"},
        404: {"model": Problem, "description": "Referenced credential not found"},
        409: {"model": Problem, "description": "Model profile name already taken"},
    },
)
async def create_profile(
    payload: CreateProfileRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Depends(check_idempotency_key)],
) -> ModelProfileResource:
    profile, created = await model_config.create_model_profile(
        session,
        name=payload.name,
        model=payload.model,
        credential_id=payload.credential_id,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
        timeout_seconds=payload.timeout_seconds,
        max_retries=payload.max_retries,
        input_token_cost=payload.input_token_cost,
        output_token_cost=payload.output_token_cost,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    response.headers["ETag"] = model_config.profile_etag(profile)
    response.headers["Location"] = f"/api/v1/model-profiles/{profile.id}"
    return _profile_resource(profile)


@router.get(
    "/model-profiles",
    response_model=ProfileListResource,
)
async def list_profiles(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProfileListResource:
    return ProfileListResource(
        items=[
            _profile_resource(profile)
            for profile in await model_config.list_model_profiles(session)
        ]
    )


@router.get(
    "/model-profiles/{profile_id}",
    response_model=ModelProfileResource,
    responses={404: {"model": Problem, "description": "Model profile not found"}},
)
async def get_profile(
    profile_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelProfileResource:
    profile = await model_config.get_model_profile(session, profile_id)
    response.headers["ETag"] = model_config.profile_etag(profile)
    return _profile_resource(profile)


@router.patch(
    "/model-profiles/{profile_id}",
    response_model=ModelProfileResource,
    responses={
        400: {"model": Problem, "description": "Malformed If-Match header"},
        404: {"model": Problem, "description": "Model profile not found"},
        412: {"model": Problem, "description": "If-Match ETag does not match"},
        428: {"model": Problem, "description": "If-Match header is required"},
    },
)
async def update_profile(
    profile_id: UUID,
    payload: UpdateProfileRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ModelProfileResource:
    profile = await model_config.update_model_profile(
        session,
        profile_id=profile_id,
        expected_revision=parse_if_match(if_match),
        values=_profile_changes(payload),
    )
    response.headers["ETag"] = model_config.profile_etag(profile)
    return _profile_resource(profile)
