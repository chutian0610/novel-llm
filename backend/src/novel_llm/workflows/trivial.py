"""The trivial system Workflow Definition (issue: Phase 1 / 04 runtime core).

One Workflow Run schedules one Job of type ``trivial_work`` that executes a
list of steps through stable idempotency keys, records a checkpoint after
each, and completes. Every step's effect is its recorded result, so replay
after a crash is observable: a step that already committed returns its
recorded result instead of running again.

The payload carries deterministic pacing and fault injection (delay, fail,
stall) — the same philosophy as the deterministic fake model provider: the
framework acceptance tests choreograph crash recovery, lease expiry, and
stale-fencing rejection through the public API without reaching into
internals.
"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from novel_llm.domain.job_execution import (
    JobCancelled,
    JobContext,
    QuotaExhaustedFailure,
    TransientJobFailure,
    UpstreamInvalidFailure,
)
from novel_llm.workflows.definitions import WorkflowDefinition, register
from novel_llm.workflows.handlers import register_handler

DEFINITION_KEY = "system.trivial"
JOB_TYPE = "trivial_work"


class TrivialRunPayload(BaseModel):
    # Fault-injection choreography keys must not silently vanish: an unknown
    # key (a typo in a test payload) is a 400 at run creation, not an idle
    # fault that turns the scenario meaningless.
    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(
        default=["draft", "polish"],
        min_length=1,
        max_length=5,
        description="The step keys the Job executes, in order.",
    )
    step_delay_ms: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="Deterministic pacing before each step; heartbeats continue.",
    )
    fail_step: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="The step whose effect raises, injecting a failure.",
    )
    fail_on_attempt: int = Field(
        default=0,
        ge=0,
        description="Inject the failure on this attempt only; 0 injects on every attempt.",
    )
    failure_class: Literal["transient", "permanent", "quota_exhausted", "upstream_invalid"] = (
        "transient"
    )
    stall_after_step: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description=(
            "The step after which the handler stops making progress, "
            "simulating a deadlocked Worker."
        ),
    )
    stall_before_checkpoint: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description=(
            "The step whose progress checkpoint the handler stalls before: "
            "the step's effect commits, the checkpoint stays pending, and the "
            "Worker eventually wakes holding a late checkpoint write."
        ),
    )
    stall_on_attempt: int = Field(
        default=0,
        ge=0,
        description="Inject the stall on this attempt only; 0 injects on every attempt.",
    )
    stall_ms: int = Field(default=0, ge=0, le=120_000)

    @field_validator("steps")
    @classmethod
    def strip_steps(cls, steps: list[str]) -> list[str]:
        stripped = [step.strip() for step in steps]
        if any(not step for step in stripped):
            raise ValueError("step keys must not be blank")
        if len(set(stripped)) != len(stripped):
            raise ValueError("step keys must be unique")
        return stripped


# The injected failure per failure_class, with the closed set's vocabulary
# carrying into the error message the Job records.
_INJECTED_FAILURES: dict[str, type[Exception]] = {
    "transient": TransientJobFailure,
    "permanent": RuntimeError,
    "quota_exhausted": QuotaExhaustedFailure,
    "upstream_invalid": UpstreamInvalidFailure,
}


async def run_trivial_job(ctx: JobContext) -> None:
    payload = TrivialRunPayload.model_validate(ctx.payload)
    # Resume from the checkpoint (ADR-0003): the newest recorded progress
    # names the steps already covered, so a recovery attempt starts after
    # them instead of replaying history a checkpoint already covers. A step
    # whose effect committed but whose checkpoint did not is still replayed
    # through run_step, which returns the recorded result.
    steps_done: list[str] = []
    for _checkpoint_key, state in reversed(await ctx.checkpoint_history()):
        if "steps_done" in state:
            steps_done = list(state["steps_done"])
            break

    for step_key in payload.steps:
        if ctx.cancel_requested:
            raise JobCancelled(
                f"the cancellation request was honored at the safe checkpoint "
                f"before step {step_key!r}"
            )
        if step_key in steps_done:
            continue
        if payload.step_delay_ms:
            await asyncio.sleep(payload.step_delay_ms / 1000)

        async def effect(step_key: str = step_key) -> dict[str, str | int]:
            injects = step_key == payload.fail_step and payload.fail_on_attempt in (
                0,
                ctx.attempt,
            )
            if injects:
                failure = _INJECTED_FAILURES[payload.failure_class]
                raise failure(
                    f"injected {payload.failure_class.replace('_', ' ')} failure "
                    f"at step {step_key!r}"
                )
            return {"step": step_key, "attempt": ctx.attempt}

        await ctx.run_step(step_key, effect)
        steps_done.append(step_key)

        # Stall between the step's committed effect and its progress
        # checkpoint: the Worker holds a committed step whose checkpoint is
        # still pending, so a stale attempt eventually wakes holding a late
        # checkpoint write for a newer attempt's Job.
        if (
            step_key == payload.stall_before_checkpoint
            and payload.stall_ms
            and payload.stall_on_attempt in (0, ctx.attempt)
        ):
            await ctx.checkpoint(
                f"stalled-before:{step_key}",
                {"reason": "injected stall", "stall_ms": payload.stall_ms},
            )
            await asyncio.sleep(payload.stall_ms / 1000)

        await ctx.checkpoint(f"after:{step_key}", {"steps_done": list(steps_done)})

        if (
            step_key == payload.stall_after_step
            and payload.stall_ms
            and payload.stall_on_attempt in (0, ctx.attempt)
        ):
            await ctx.checkpoint(
                f"stalled:{step_key}", {"reason": "injected stall", "stall_ms": payload.stall_ms}
            )
            # No further writes: this simulates a Worker that has stopped
            # making progress (its heartbeats stop with the fault injection,
            # so the lease expires and a newer attempt takes over).
            await asyncio.sleep(payload.stall_ms / 1000)


register(
    WorkflowDefinition(
        key=DEFINITION_KEY,
        version=1,
        payload_schema_version=1,
        payload_model=TrivialRunPayload,
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
    )
)
register_handler(JOB_TYPE, run_trivial_job)
