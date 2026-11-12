"""Typed execution contracts (ADR-0008): writing models return Markdown plus
metadata; evaluation, diagnosis, extraction, and projection models return
typed JSON validated by Pydantic. The gateway refuses anything that does not
validate — and repairs it at most once."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class ModelOutputInvalid(Exception):
    """The completion did not satisfy its contract, even after the one
    allowed repair call (ADR-0008) — terminal for the call."""


class EvaluationOutput(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Literal["approve", "revise"]
    notes: str = Field(min_length=1, max_length=4000)


class DiagnosisIssue(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    severity: Literal["minor", "major", "critical"]


class DiagnosisOutput(BaseModel):
    issues: list[DiagnosisIssue] = Field(min_length=1)


class ExtractionEntity(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)


class ExtractionOutput(BaseModel):
    entities: list[ExtractionEntity] = Field(min_length=1)


class ProjectionOutput(BaseModel):
    state_summary: str = Field(min_length=1, max_length=8000)


# The output kinds and the typed contract each validates against. `write` is
# Markdown plus metadata: structurally, non-empty Markdown is the contract.
OUTPUT_KINDS: frozenset[str] = frozenset({"write", "evaluate", "diagnose", "extract", "project"})

_TYPED_MODELS: dict[str, type[BaseModel]] = {
    "evaluate": EvaluationOutput,
    "diagnose": DiagnosisOutput,
    "extract": ExtractionOutput,
    "project": ProjectionOutput,
}


def validate_output(output_kind: str, completion: str) -> dict | str:
    """Validate a completion per its contract. Writing returns the Markdown
    verbatim (non-empty is the structure); typed kinds parse the JSON and
    validate the model, returning the canonical dict. Anything invalid raises
    :class:`ModelOutputInvalid` — the gateway's repair signal."""
    if output_kind == "write":
        if not completion.strip():
            raise ModelOutputInvalid("the writing output was empty")
        return completion
    model = _TYPED_MODELS.get(output_kind)
    if model is None:
        raise ModelOutputInvalid(f"{output_kind!r} is not an output kind")
    try:
        parsed = json.loads(completion)
    except json.JSONDecodeError as error:
        raise ModelOutputInvalid(f"the output was not valid JSON: {error.msg}") from None
    try:
        return model.model_validate(parsed).model_dump(mode="json")
    except ValidationError as validation_error:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in validation_error.errors()[:5]
        )
        raise ModelOutputInvalid(
            f"the output did not match the {output_kind} contract: {summary}"
        ) from None
