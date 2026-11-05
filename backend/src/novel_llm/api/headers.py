"""Shared API header validation (ADR-0008): every creation takes an
Idempotency-Key, and every creation validates it identically. The header is
declared required, so a missing header is FastAPI's 422; a present-but-blank
or over-long key is a 400 problem-details."""

from typing import Annotated

from fastapi import Header

from novel_llm.api.problems import ProblemError

IDEMPOTENCY_KEY_MAX_LENGTH = 200


def check_idempotency_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> str:
    key = idempotency_key.strip()
    if not key or len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ProblemError(
            status=400,
            code="invalid_idempotency_key",
            title="Idempotency-Key header is empty or too long",
        )
    return key
