"""Idempotent creation keys (ADR-0008): the one shape every create path
shares. PostgreSQL enforces the key's uniqueness; the request fingerprint —
a stable digest of the create payload — detects reuse of a key with a
different payload."""

from hashlib import sha256
from json import dumps
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain.errors import IdempotencyKeyConflict
from novel_llm.domain.ids import new_id


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


async def begin_create(
    session: AsyncSession,
    key_row_type: type,
    *,
    key: str,
    fingerprint: str,
    resource_id: UUID,
    resource_ref_column: str,
) -> UUID | None:
    """Stage the create-attempt key row inside the caller's transaction.

    Returns None when the key is new — the caller proceeds with the create
    and everything commits together. Returns the existing resource's id when
    the key replays the same payload (the caller loads it and returns it
    instead of creating a duplicate). Raises IdempotencyKeyConflict when the
    key was used with a different payload.

    After a conflicting insert the session rolls back to READ COMMITTED,
    which sees a concurrent creator's committed key row, so the replay
    decision is made on committed state."""
    try:
        await session.execute(
            insert(key_row_type).values(
                id=new_id(),
                key=key,
                request_fingerprint=fingerprint,
                **{resource_ref_column: resource_id},
            )
        )
    except IntegrityError:
        await session.rollback()
        key_row: Any = (
            await session.execute(
                select(key_row_type).where(key_row_type.key == key)  # type: ignore[attr-defined]
            )
        ).scalar_one()
        if key_row.request_fingerprint != fingerprint:
            raise IdempotencyKeyConflict(
                "the Idempotency-Key was already used with a different request payload"
            ) from None
        return getattr(key_row, resource_ref_column)
    return None
