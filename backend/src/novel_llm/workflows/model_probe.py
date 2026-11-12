"""The model-probe system Workflow Definition (issue: Phase 1 / 09 model
execution foundation): one Job calls the fake provider through a bound
Model Profile — rendering a semantically versioned template, validating the
output per its contract, repairing at most once — leaving full traces. A
small demonstration of the whole model-calling path end to end.

The payload carries deterministic fault injection (`force_invalid`) with the
same philosophy as the trivial workflow: the repair contract is exercisable
through the public API without touching internals."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novel_llm.domain import model_config
from novel_llm.domain.job_execution import JobContext
from novel_llm.models import gateway
from novel_llm.models.prompts import get_template
from novel_llm.workflows.definitions import WorkflowDefinition, register
from novel_llm.workflows.handlers import register_handler

DEFINITION_KEY = "system.model_probe"
JOB_TYPE = "model_probe_work"


class ModelProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_profile_id: UUID
    output_kind: Literal["write", "evaluate", "diagnose", "extract", "project"] = "write"
    force_invalid: bool = False
    work_title: str = Field(default="长夜灯", min_length=1, max_length=200)
    chapter_number: int = Field(default=1, ge=1)
    chapter_title: str = Field(default="夜行", min_length=1, max_length=200)


async def _validate_profile_exists(session: AsyncSession, payload: BaseModel) -> None:
    """Jobs bind their model profile at creation (ADR-0004): an unknown one
    is refused inside the run-creation transaction."""
    probe = ModelProbePayload.model_validate(payload.model_dump())
    await model_config.get_model_profile(session, probe.model_profile_id)


async def run_model_probe_job(ctx: JobContext) -> None:
    payload = ModelProbePayload.model_validate(ctx.payload)

    if payload.output_kind == "write":
        template = get_template("probe.write", 1)
    else:
        template = get_template("probe.evaluate", 1)
    assert template is not None  # code-owned registry, pinned by unit tests

    async def effect() -> dict:
        result = await gateway.call_model(
            ctx.session_factory,
            project_id=ctx.project_id,
            model_profile_id=payload.model_profile_id,
            template=template,
            variables={
                "work_title": payload.work_title,
                "chapter_number": payload.chapter_number,
                "chapter_title": payload.chapter_title,
                # The evaluation's subject: the chapter context this probe
                # stands in for, not the bare work title.
                "text": (
                    f"《{payload.work_title}》第{payload.chapter_number}章"
                    f"「{payload.chapter_title}」"
                ),
            },
            output_kind=payload.output_kind,
            corrupt_once=payload.force_invalid,
        )
        return {
            "output_kind": payload.output_kind,
            "parsed": result.parsed,
            "repaired": result.repaired,
            "trace_ids": [str(trace_id) for trace_id in result.trace_ids],
        }

    result = await ctx.run_step("model_call", effect)
    await ctx.checkpoint("after:model_call", {"traces": result["trace_ids"]})


register(
    WorkflowDefinition(
        key=DEFINITION_KEY,
        version=1,
        payload_schema_version=1,
        payload_model=ModelProbePayload,
        job_type=JOB_TYPE,
        job_payload=lambda payload: payload.model_dump(mode="json"),
        run_transition_for_job_event={
            "claim": "begin",
            "take_over": "begin",
            "complete": "complete",
            "fail": "fail",
            "cancel": "cancel",
            "finish_cancel": "cancel",
        },
        validate_payload=_validate_profile_exists,
    )
)
register_handler(JOB_TYPE, run_model_probe_job)
