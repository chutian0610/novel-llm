"""In-process unit tests (Seam 2, spec): the pure pieces of the Project
surface — UUIDv7 ids, cursor codec, idempotency fingerprints. No I/O."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from novel_llm.domain.cursor import decode_cursor, encode_cursor
from novel_llm.domain.errors import InvalidCursor
from novel_llm.domain.ids import new_id
from novel_llm.domain.projects import request_fingerprint


def test_new_ids_are_uuidv7_and_time_ordered():
    first = new_id()
    second = new_id()
    assert first.version == 7
    assert second.version == 7
    assert second >= first


def test_cursor_roundtrips_the_sort_key():
    created_at = datetime(2026, 11, 5, 12, 30, 45, 123456, tzinfo=UTC)
    project_id = UUID("0199e0a2-7b3a-7cc2-9f31-2a4b6c8d0e1f")

    cursor = encode_cursor(created_at=created_at, id=project_id)
    decoded_created_at, decoded_id = decode_cursor(cursor)

    assert decoded_created_at == created_at
    assert decoded_id == project_id


@pytest.mark.parametrize("cursor", ["", "not-a-cursor", "aGVsbG8", "###", "eyJvbmx5IjoiaWQifQ"])
def test_malformed_cursors_are_invalid(cursor: str):
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor)


def test_request_fingerprint_is_sensitive_to_the_payload():
    base = request_fingerprint("山海经新考", None, "zh-Hans")

    assert base == request_fingerprint("山海经新考", None, "zh-Hans")
    assert base != request_fingerprint("山海经新考", "有描述", "zh-Hans")
    assert base != request_fingerprint("山海经新考", None, "en-US")
