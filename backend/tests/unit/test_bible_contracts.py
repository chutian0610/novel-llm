"""In-process unit tests (Seam 2, spec): the typed Bible entry contracts
(ADR-0008) — nine first-version types, unknown keys refused, event entries
keeping Narrative Time apart from Disclosure Order. No I/O."""

import pytest
from pydantic import ValidationError

from novel_llm.domain.bible import BIBLE_ENTRY_TYPES, ENTRY_DETAIL_MODELS, EventDetail

_LIN = "0199e0a2-7b3a-7cc2-9f31-2a4b6c8d0e1f"
_CHEN = "0199e0a2-7b3a-7cc2-9f31-2a4b6c8d0e1e"


def test_the_entry_types_are_exactly_the_nine_first_version_types():
    assert BIBLE_ENTRY_TYPES == {
        "character",
        "relationship",
        "location",
        "organization",
        "item",
        "rule",
        "event",
        "style",
        "open_question",
    }


def test_every_type_validates_a_minimal_detail():
    minimal = {
        "character": {"description": "…"},
        "relationship": {"from_identity": _LIN, "to_identity": _CHEN, "description": "…"},
        "location": {"description": "…"},
        "organization": {"description": "…"},
        "item": {"description": "…"},
        "rule": {"description": "…"},
        "event": {"description": "…"},
        "style": {"description": "…"},
        "open_question": {"question": "…"},
    }
    for entry_type, detail in minimal.items():
        validated = ENTRY_DETAIL_MODELS[entry_type].model_validate(detail)
        assert validated.model_dump(mode="json")


def test_optional_fields_carry_their_defaults_in_the_canonical_form():
    character = ENTRY_DETAIL_MODELS["character"].model_validate({"description": "…"})
    assert character.model_dump(mode="json") == {"description": "…", "role": None}

    question = ENTRY_DETAIL_MODELS["open_question"].model_validate({"question": "…"})
    assert question.model_dump(mode="json") == {"question": "…", "resolved": False}


def test_an_unknown_key_in_a_detail_is_a_malformed_payload():
    with pytest.raises(ValidationError):
        ENTRY_DETAIL_MODELS["character"].model_validate({"description": "…", "age": 17})
    with pytest.raises(ValidationError):
        ENTRY_DETAIL_MODELS["style"].model_validate({"prose": "不是描述字段"})


def test_event_details_distinguish_narrative_time_from_disclosure_order():
    event = EventDetail.model_validate(
        {"description": "灯楼大火", "narrative_time": "百年前", "disclosure_order": "第三章"}
    )
    assert event.narrative_time == "百年前"
    assert event.disclosure_order == "第三章"
    assert event.narrative_time != event.disclosure_order


def test_an_empty_description_is_rejected_everywhere():
    for model in ENTRY_DETAIL_MODELS.values():
        field = "question" if model is ENTRY_DETAIL_MODELS["open_question"] else "description"
        with pytest.raises(ValidationError):
            model.model_validate({field: ""})
