"""Shared API header validation (ADR-0008): every creation takes an
Idempotency-Key, and every creation validates it identically. The header is
declared required, so a missing header is FastAPI's 422; a present-but-blank
or over-long key is a 400 problem-details."""

from typing import Annotated

from fastapi import Header

from novel_llm.api.problems import ProblemError

IDEMPOTENCY_KEY_MAX_LENGTH = 200


def parse_if_match(if_match: str | None) -> int | None:
    """Parse If-Match into the expected revision (ADR-0008 optimistic
    concurrency). `*` maps to None, meaning any current version is accepted;
    a missing header is a 428, a malformed one a 400."""
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
