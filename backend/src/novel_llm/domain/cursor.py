"""Opaque keyset-pagination cursors (ADR-0008).

The cursor encodes the last row's sort key — ``(created_at, id)`` — as
base64url JSON. It is opaque to clients; decoding failures are a domain
error with a stable code.
"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from json import dumps, loads
from uuid import UUID

from novel_llm.domain.errors import InvalidCursor


def encode_cursor(*, created_at: datetime, id: UUID) -> str:
    payload = {"created_at": created_at.isoformat(), "id": str(id)}
    raw = dumps(payload, separators=(",", ":")).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = loads(urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.fromisoformat(payload["created_at"])
        id = UUID(payload["id"])
    except Exception:
        raise InvalidCursor("the pagination cursor is malformed") from None
    return created_at, id
