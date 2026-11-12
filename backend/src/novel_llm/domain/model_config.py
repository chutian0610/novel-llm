"""Model configuration domain rules (ADR-0006/0008/0009): provider
credentials and Model Profiles are separate database objects — plaintext
local credential rows whose complete key never leaves through any response —
and every profile validates its credential reference inside the mutation's
transaction (no physical foreign keys).

These objects are global configuration, not Project data: no project lock,
but creations are idempotency-keyed and mutable updates carry the revision
guard (ADR-0008), matching every other domain surface."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import idempotency
from novel_llm.domain.errors import (
    ConcurrencyConflict,
    CredentialNameTaken,
    CredentialNotFound,
    ModelProfileNameTaken,
    ModelProfileNotFound,
)
from novel_llm.domain.ids import new_id
from novel_llm.infrastructure.orm import (
    ModelCredentialIdempotencyKeyRow,
    ModelProfileIdempotencyKeyRow,
    ModelProfileRow,
    ProviderCredentialRow,
)


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    id: UUID
    name: str
    provider: str
    api_key_hint: str | None
    base_url: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: UUID
    name: str
    model: str
    credential_id: UUID
    temperature: float
    top_p: float
    max_tokens: int
    timeout_seconds: int
    max_retries: int
    input_token_cost: float
    output_token_cost: float
    revision: int
    created_at: datetime
    updated_at: datetime


def _key_hint(api_key: str) -> str | None:
    """The only key material that ever leaves the database: the last four
    characters, prefixed with an ellipsis — never a complete key. A key of
    four characters or fewer would echo itself through that suffix, so it
    masks to the bare ellipsis (ADR-0006)."""
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "…"
    return f"…{api_key[-4:]}"


def _to_credential(row: ProviderCredentialRow) -> ProviderCredential:
    return ProviderCredential(
        id=row.id,
        name=row.name,
        provider=row.provider,
        api_key_hint=_key_hint(row.api_key),
        base_url=row.base_url,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_profile(row: ModelProfileRow) -> ModelProfile:
    return ModelProfile(
        id=row.id,
        name=row.name,
        model=row.model,
        credential_id=row.credential_id,
        temperature=float(row.temperature),
        top_p=float(row.top_p),
        max_tokens=row.max_tokens,
        timeout_seconds=row.timeout_seconds,
        max_retries=row.max_retries,
        input_token_cost=float(row.input_token_cost),
        output_token_cost=float(row.output_token_cost),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def credential_payload(credential: ProviderCredential) -> dict[str, Any]:
    return {
        "id": str(credential.id),
        "name": credential.name,
        "provider": credential.provider,
        "api_key_hint": credential.api_key_hint,
        "base_url": credential.base_url,
        "created_at": credential.created_at,
        "updated_at": credential.updated_at,
    }


def profile_payload(profile: ModelProfile) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "model": profile.model,
        "credential_id": str(profile.credential_id),
        "sampling": {
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_tokens,
        },
        "timeout_seconds": profile.timeout_seconds,
        "max_retries": profile.max_retries,
        "cost": {
            "input_token_cost": profile.input_token_cost,
            "output_token_cost": profile.output_token_cost,
        },
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def credential_etag(credential: ProviderCredential) -> str:
    return f'"{credential.revision}"'


def profile_etag(profile: ModelProfile) -> str:
    return f'"{profile.revision}"'


def _now() -> datetime:
    return datetime.now(UTC)


async def create_credential(
    session: AsyncSession,
    *,
    name: str,
    provider: str,
    api_key: str,
    base_url: str | None,
    idempotency_key: str,
) -> tuple[ProviderCredential, bool]:
    """Store one plaintext local credential row (ADR-0006), honoring the
    idempotency key. The stored key never reappears in any payload."""
    fingerprint = idempotency.request_fingerprint(
        {"name": name, "provider": provider, "api_key": api_key, "base_url": base_url}
    )
    now = _now()
    credential_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        ModelCredentialIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=credential_id,
        resource_ref_column="credential_id",
    )
    if existing_id is not None:
        existing = await session.get_one(ProviderCredentialRow, existing_id)
        return _to_credential(existing), False

    row = ProviderCredentialRow(
        id=credential_id,
        name=name,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise CredentialNameTaken(f"a credential named {name!r} already exists") from None
    return _to_credential(row), True


async def get_credential(session: AsyncSession, credential_id: UUID) -> ProviderCredential:
    row = await session.get(ProviderCredentialRow, credential_id)
    if row is None:
        raise CredentialNotFound(f"no provider credential with id {credential_id}")
    return _to_credential(row)


async def list_credentials(session: AsyncSession) -> list[ProviderCredential]:
    rows = (
        (
            await session.execute(
                select(ProviderCredentialRow).order_by(
                    ProviderCredentialRow.created_at.asc(), ProviderCredentialRow.id.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [_to_credential(row) for row in rows]


async def update_credential(
    session: AsyncSession,
    *,
    credential_id: UUID,
    expected_revision: int | None,
    values: dict[str, Any],
) -> ProviderCredential:
    """Apply mutable changes (name, provider, api_key, base_url) under
    optimistic concurrency; the response payload keeps masking the key. An
    empty change set is a read of the current state (the projects
    precedent)."""
    if not values:
        return await get_credential(session, credential_id)
    if values:
        statement = (
            update(ProviderCredentialRow)
            .where(ProviderCredentialRow.id == credential_id)
            .values(
                **values,
                revision=ProviderCredentialRow.revision + 1,
                updated_at=_now(),
            )
            .returning(ProviderCredentialRow)
        )
        if expected_revision is not None:
            statement = statement.where(ProviderCredentialRow.revision == expected_revision)
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is not None:
            await session.commit()
            return _to_credential(row)

    existing = await session.get(ProviderCredentialRow, credential_id)
    if existing is None:
        raise CredentialNotFound(f"no provider credential with id {credential_id}")
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")


async def create_model_profile(
    session: AsyncSession,
    *,
    name: str,
    model: str,
    credential_id: UUID,
    temperature: Decimal,
    top_p: Decimal,
    max_tokens: int,
    timeout_seconds: int,
    max_retries: int,
    input_token_cost: Decimal,
    output_token_cost: Decimal,
    idempotency_key: str,
) -> tuple[ModelProfile, bool]:
    """Create a Model Profile referencing a credential validated inside this
    transaction (ADR-0009), honoring the idempotency key."""
    fingerprint = idempotency.request_fingerprint(
        {
            "name": name,
            "model": model,
            "credential_id": str(credential_id),
            "temperature": str(temperature),
            "top_p": str(top_p),
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "input_token_cost": str(input_token_cost),
            "output_token_cost": str(output_token_cost),
        }
    )
    now = _now()
    profile_id = new_id()

    existing_id = await idempotency.begin_create(
        session,
        ModelProfileIdempotencyKeyRow,
        key=idempotency_key,
        fingerprint=fingerprint,
        resource_id=profile_id,
        resource_ref_column="profile_id",
    )
    if existing_id is not None:
        existing = await session.get_one(ModelProfileRow, existing_id)
        return _to_profile(existing), False

    credential = await session.get(ProviderCredentialRow, credential_id)
    if credential is None:
        raise CredentialNotFound(f"no provider credential with id {credential_id}")

    row = ModelProfileRow(
        id=profile_id,
        name=name,
        model=model,
        credential_id=credential_id,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        input_token_cost=input_token_cost,
        output_token_cost=output_token_cost,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ModelProfileNameTaken(f"a model profile named {name!r} already exists") from None
    return _to_profile(row), True


async def get_model_profile(session: AsyncSession, profile_id: UUID) -> ModelProfile:
    row = await session.get(ModelProfileRow, profile_id)
    if row is None:
        raise ModelProfileNotFound(f"no model profile with id {profile_id}")
    return _to_profile(row)


async def list_model_profiles(session: AsyncSession) -> list[ModelProfile]:
    rows = (
        (
            await session.execute(
                select(ModelProfileRow).order_by(
                    ModelProfileRow.created_at.asc(), ModelProfileRow.id.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [_to_profile(row) for row in rows]


async def update_model_profile(
    session: AsyncSession,
    *,
    profile_id: UUID,
    expected_revision: int | None,
    values: dict[str, Any],
) -> ModelProfile:
    if not values:
        return await get_model_profile(session, profile_id)
    if values:
        statement = (
            update(ModelProfileRow)
            .where(ModelProfileRow.id == profile_id)
            .values(
                **values,
                revision=ModelProfileRow.revision + 1,
                updated_at=_now(),
            )
            .returning(ModelProfileRow)
        )
        if expected_revision is not None:
            statement = statement.where(ModelProfileRow.revision == expected_revision)
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is not None:
            await session.commit()
            return _to_profile(row)

    existing = await session.get(ModelProfileRow, profile_id)
    if existing is None:
        raise ModelProfileNotFound(f"no model profile with id {profile_id}")
    raise ConcurrencyConflict("the If-Match ETag does not match the current version")
