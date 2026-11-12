"""In-process unit tests (Seam 2, spec): the typed Outline plan contracts
(ADR-0008) — an unknown key in a plan is a malformed payload, not a silently
unfired plan; the published plans freeze under a payload schema version. No
I/O."""

import pytest
from pydantic import ValidationError

from novel_llm.domain.outline import PAYLOAD_SCHEMA_VERSION, OutlinePlans

_CHAPTER_ID = "0199e0a2-7b3a-7cc2-9f31-2a4b6c8d0e1f"
_VOLUME_ID = "0199e0a2-7b3a-7cc2-9f31-2a4b6c8d0e1e"


def test_the_payload_schema_version_is_part_of_the_contract():
    assert PAYLOAD_SCHEMA_VERSION == 1


def test_minimal_plans_carry_only_the_work_plan():
    plans = OutlinePlans.model_validate({"work": {"synopsis": "少年执灯入山。"}})

    assert plans.volumes == []
    assert plans.chapters == []
    assert plans.scenes == []
    assert plans.foreshadowings == []
    assert plans.constraints == []


def test_every_plan_kind_roundtrips():
    plans = OutlinePlans.model_validate(
        {
            "work": {"synopsis": "少年执灯入山。"},
            "volumes": [{"volume_id": _VOLUME_ID, "summary": "入山"}],
            "chapters": [{"chapter_id": _CHAPTER_ID, "summary": "夜行遇灯"}],
            "scenes": [{"chapter_id": _CHAPTER_ID, "position": 1, "goal": "立灯的意象"}],
            "foreshadowings": [{"description": "灯芯的裂痕"}],
            "constraints": [{"description": "志怪语感"}],
        }
    )

    dumped = plans.model_dump(mode="json")
    assert dumped["chapters"][0]["goal"] is None  # goal is optional
    assert dumped["foreshadowings"][0]["chapter_id"] is None  # planting place optional
    assert dumped["scenes"][0]["position"] == 1


def test_an_unknown_plan_key_is_a_malformed_payload():
    with pytest.raises(ValidationError):
        OutlinePlans.model_validate({"work": {"synopsis": "…", "typo_key": True}})
    with pytest.raises(ValidationError):
        OutlinePlans.model_validate(
            {
                "work": {"synopsis": "…"},
                "chapters": [{"chapter_id": _CHAPTER_ID, "summary": "…", "unheard": 1}],
            }
        )


def test_a_scene_position_starts_at_one():
    with pytest.raises(ValidationError):
        OutlinePlans.model_validate(
            {
                "work": {"synopsis": "…"},
                "scenes": [{"chapter_id": _CHAPTER_ID, "position": 0, "goal": "…"}],
            }
        )
