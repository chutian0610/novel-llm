"""In-process unit tests (Seam 2, spec): the versioned event envelope and the
outbox ordering rules. No I/O."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from novel_llm.domain.events import ENVELOPE_VERSION, DomainEvent, envelope


def _event(**overrides: Any) -> DomainEvent:
    fields: dict[str, Any] = {
        "id": UUID("0199e100-0000-7000-8000-000000000001"),
        "project_id": UUID("0199e100-0000-7000-8000-00000000ffff"),
        "event_type": "project.created",
        "payload": {"title": "长夜灯"},
        "occurred_at": datetime(2026, 11, 5, 9, 0, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return DomainEvent(**fields)


def test_envelope_carries_the_versioned_shape():
    wrapped = envelope(_event())

    assert wrapped["envelope_version"] == ENVELOPE_VERSION == 1
    assert wrapped["event_type"] == "project.created"
    assert wrapped["data"] == {"title": "长夜灯"}
    assert wrapped["occurred_at"] == datetime(2026, 11, 5, 9, 0, 0, tzinfo=UTC)


def test_envelope_cursor_is_the_event_id():
    event = _event()
    wrapped = envelope(event)
    assert wrapped["event_id"] == str(event.id)
    assert wrapped["cursor"] == wrapped["event_id"]
