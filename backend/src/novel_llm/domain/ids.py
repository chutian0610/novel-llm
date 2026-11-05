"""Domain identifiers: UUIDv7 primary keys generated app-side (ADR-0002;
PostgreSQL 16 has no native uuidv7). Time-ordered IDs keep keyset pagination
on (created_at, id) coherent."""

from uuid import UUID

from uuid_utils.compat import uuid7


def new_id() -> UUID:
    return uuid7()
