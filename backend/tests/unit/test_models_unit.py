"""In-process unit tests (Seam 2, spec): the fake provider's determinism,
prompt-template semantic versioning, the typed output contracts, and the
cost computation — no I/O, no network."""

import pytest
from pydantic import ValidationError

from novel_llm.models.contracts import (
    OUTPUT_KINDS,
    EvaluationOutput,
    ModelOutputInvalid,
    validate_output,
)
from novel_llm.models.fake_provider import FakeProvider, ProviderRequest
from novel_llm.models.gateway import compute_cost
from novel_llm.models.prompts import PromptTemplate, get_template, register

_REQUEST = ProviderRequest(
    prompt="你为《长夜灯》撰写第 1 章「夜行」的试写文本。",
    model="fake/writer-v1",
    output_kind="write",
)


def test_the_fake_provider_needs_no_network_and_is_deterministic():
    provider = FakeProvider()
    first = provider.complete(_REQUEST)
    second = provider.complete(_REQUEST)

    assert first.completion == second.completion
    assert first.input_tokens == second.input_tokens
    assert first.output_tokens == second.output_tokens
    assert first.metadata == second.metadata


def test_token_counts_derive_from_the_text_lengths():
    provider = FakeProvider()
    response = provider.complete(_REQUEST)
    assert response.input_tokens == len(_REQUEST.prompt)
    assert response.output_tokens == len(response.completion)


def test_the_write_contract_yields_markdown_and_metadata():
    response = FakeProvider().complete(_REQUEST)
    assert response.completion.startswith("#")
    assert response.metadata["provider"] == "mock"


def test_typed_kinds_yield_parseable_json_per_kind():
    provider = FakeProvider()
    for kind in ("evaluate", "diagnose", "extract", "project"):
        response = provider.complete(
            ProviderRequest(prompt="评估这段文本。", model="fake/writer-v1", output_kind=kind)
        )
        # The gateway's validator accepts every kind's own JSON.
        assert validate_output(kind, response.completion)


def test_the_corruption_lever_breaks_typed_json_only_once_detected():
    provider = FakeProvider()
    request = ProviderRequest(prompt="评估这段文本。", model="m", output_kind="evaluate")
    corrupted = provider.complete(request, corrupt_output=True)
    with pytest.raises(ModelOutputInvalid):
        validate_output("evaluate", corrupted.completion)
    # The honest call validates — the repair call's shape.
    honest = provider.complete(request)
    assert validate_output("evaluate", honest.completion)


def test_the_output_kinds_are_the_closed_set():
    assert OUTPUT_KINDS == {"write", "evaluate", "diagnose", "extract", "project"}


def test_typed_contracts_refuse_wrong_shapes():
    with pytest.raises(ValidationError):
        EvaluationOutput.model_validate({"score": "高", "verdict": "approve", "notes": "…"})
    with pytest.raises(ValidationError):
        EvaluationOutput.model_validate({"score": 101, "verdict": "approve", "notes": "…"})


def test_prompt_templates_are_semantically_versioned_in_code():
    write_v1 = get_template("probe.write", 1)
    assert write_v1 is not None
    assert write_v1.version == 1
    assert get_template("probe.write", 2) is None  # not shipped yet
    assert get_template("probe.write", 99) is None

    register(PromptTemplate(key="probe.write", version=2, template="新版 {work_title}"))
    assert get_template("probe.write", 1) is not None  # the old version persists
    write_v2 = get_template("probe.write", 2)
    assert write_v2 is not None
    assert write_v2.render(work_title="长夜灯") == "新版 长夜灯"


def test_template_rendering_is_deterministic_and_fully_substituted():
    template = get_template("probe.write", 1)
    assert template is not None
    rendered = template.render(work_title="长夜灯", chapter_number=1, chapter_title="夜行")
    again = template.render(work_title="长夜灯", chapter_number=1, chapter_title="夜行")
    assert rendered == again
    assert "长夜灯" in rendered and "{" not in rendered  # fully substituted


def test_cost_computes_from_the_profile_settings_over_both_directions():
    assert compute_cost(0, 0, 0.0, 0.0) == 0.0
    assert compute_cost(100, 50, 0.00000003, 0.00000006) == pytest.approx(
        100 * 0.00000003 + 50 * 0.00000006
    )
