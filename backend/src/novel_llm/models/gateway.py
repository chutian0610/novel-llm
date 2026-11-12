"""The model gateway (ADR-0008): one place that calls a model through a
Model Profile — rendering a semantically versioned prompt template fully,
validating the completion per its contract, repairing structurally invalid
output at most once, and persisting a full trace for every call (original
and repair alike): rendered prompt, completion, metadata, latency, token
use, and cost computed from the profile's cost settings."""

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from novel_llm.domain import model_config
from novel_llm.domain.errors import ModelTraceNotFound
from novel_llm.domain.ids import new_id
from novel_llm.infrastructure.orm import ModelTraceRow
from novel_llm.models.contracts import ModelOutputInvalid, validate_output
from novel_llm.models.fake_provider import FakeProvider, ProviderRequest
from novel_llm.models.prompts import PromptTemplate, get_template

# The fake provider is a process-local singleton: deterministic, offline.
PROVIDER = FakeProvider()


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """What the caller (a Job handler) consumes: the validated output and
    the ids of the traces this call left behind."""

    output: dict | str
    parsed: Any
    trace_ids: list[UUID]
    repaired: bool


def compute_cost(
    input_tokens: int, output_tokens: int, input_token_cost: float, output_token_cost: float
) -> float:
    """Cost = tokens × per-token cost, summed over both directions."""
    return float(
        Decimal(str(input_tokens)) * Decimal(str(input_token_cost))
        + Decimal(str(output_tokens)) * Decimal(str(output_token_cost))
    )


async def call_model(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: UUID,
    model_profile_id: UUID,
    template: PromptTemplate,
    variables: dict[str, str | int],
    output_kind: str,
    corrupt_once: bool = False,
) -> ModelCallResult:
    """One model call through a Model Profile. `corrupt_once` is the fake
    provider's fault lever (framework tests only): the FIRST call emits
    structurally invalid output, the at-most-one repair call does not — so a
    successful call after corruption proves the repair path ran exactly one
    repair. Raises :class:`ModelOutputInvalid` when even the repair fails —
    terminal for the call, classified upstream_invalid by the runtime."""
    async with session_factory() as session:
        profile = await model_config.get_model_profile(session, model_profile_id)

    prompt = template.render(**variables)
    repaired = False
    trace_ids: list[UUID] = []
    while True:
        started = time.monotonic()
        response = PROVIDER.complete(
            ProviderRequest(
                prompt=prompt,
                model=profile.model,
                output_kind=output_kind,
                max_tokens=profile.max_tokens,
            ),
            corrupt_output=corrupt_once and not repaired,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        # Every call leaves its trace — the invalid original as much as the
        # successful repair (ADR-0008: both retained in full).
        trace_id = new_id()
        await _persist_trace(
            session_factory,
            project_id=project_id,
            profile_id=profile.id,
            template=template,
            output_kind=output_kind,
            prompt=prompt,
            completion=response.completion,
            call_metadata=response.metadata,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=latency_ms,
            cost=compute_cost(
                response.input_tokens,
                response.output_tokens,
                profile.input_token_cost,
                profile.output_token_cost,
            ),
            trace_id=trace_id,
            repair_of_id=trace_ids[0] if trace_ids else None,
        )
        trace_ids.append(trace_id)
        try:
            output = validate_output(output_kind, response.completion)
        except ModelOutputInvalid as invalid:
            if repaired:
                # The at-most-one repair already ran and the output still
                # breaks its contract: terminal for the call.
                raise
            repair_template = get_template("model.repair", 1)
            if repair_template is None:
                # The registry is pinned by unit tests; a missing repair
                # template is a programming error, not a silent fallback
                # rendering the original template with repair variables.
                raise
            prompt = repair_template.render(
                output_kind=output_kind,
                error=str(invalid),
                original_prompt=prompt,
            )
            repaired = True
            continue
        return ModelCallResult(
            output=output,
            parsed=output,
            trace_ids=trace_ids,
            repaired=repaired,
        )


async def _persist_trace(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: UUID,
    profile_id: UUID,
    template: PromptTemplate,
    output_kind: str,
    prompt: str,
    completion: str,
    call_metadata: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    cost: float,
    trace_id: UUID,
    repair_of_id: UUID | None,
) -> None:
    async with session_factory() as session:
        session.add(
            ModelTraceRow(
                id=trace_id,
                project_id=project_id,
                model_profile_id=profile_id,
                template_key=template.key,
                template_version=template.version,
                output_kind=output_kind,
                prompt=prompt,
                completion=completion,
                call_metadata=call_metadata,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost=cost,
                repair_of_id=repair_of_id,
            )
        )
        await session.commit()


async def list_traces(
    session: AsyncSession,
    *,
    project_id: UUID,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
) -> list[ModelTraceRow]:
    """A Project's model traces, newest first, keyset-paginated on
    (created_at, id) — the same opaque-cursor contract as every other list
    (ADR-0008)."""
    query = (
        select(ModelTraceRow)
        .where(ModelTraceRow.project_id == project_id)
        .order_by(ModelTraceRow.created_at.desc(), ModelTraceRow.id.desc())
        .limit(limit)
    )
    if cursor is not None:
        created_at, trace_id = cursor
        query = query.where(
            (ModelTraceRow.created_at < created_at)
            | ((ModelTraceRow.created_at == created_at) & (ModelTraceRow.id < trace_id))
        )
    return list((await session.execute(query)).scalars().all())


async def get_trace(session: AsyncSession, trace_id: UUID) -> ModelTraceRow:
    row = await session.get(ModelTraceRow, trace_id)
    if row is None:
        raise ModelTraceNotFound(f"no model trace with id {trace_id}")
    return row
