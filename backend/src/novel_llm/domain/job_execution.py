"""The execution seam between the Worker runtime and Job handlers.

Handlers receive a :class:`JobContext` and express outcomes as exceptions:
:class:`TransientJobFailure` retries automatically under database-owned
settings, :class:`JobCancelled` reports a cooperative cancellation, and any
other exception is a permanent failure (ADR-0003: only transient failures
retry automatically).

The context is the only way handlers touch durable state — steps and
checkpoints — and every write it makes carries the attempt's fencing token,
so a stale Worker cannot overwrite a newer attempt's results. Each write
commits in its own short transaction, which is what makes a crash between
steps recoverable: replaying a step whose effect already committed returns
the recorded result instead of duplicating it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from novel_llm.domain import jobs
from novel_llm.domain.jobs import ActiveJob


class TransientJobFailure(Exception):
    """A potentially temporary condition (model timeout, rate limit) that may
    be retried safely under the Job Type's database-owned retry settings."""


class QuotaExhaustedFailure(Exception):
    """The provider quota behind the Job's bound model profile is exhausted
    (ADR-0003's closed failure set). Terminal: no automatic retry — adding
    quota is a human decision, and a retry would only burn the attempt."""


class UpstreamInvalidFailure(Exception):
    """The upstream provider rejected the request or returned output the
    Job's typed contract cannot accept for reasons its inputs cannot fix
    (ADR-0003's closed failure set). Terminal: retrying identical work would
    fail identically."""


class JobCancelled(Exception):
    """The Job observed a cancellation request at a safe checkpoint and
    stopped without continuing its normal outcome."""


class JobPaused(Exception):
    """The Job observed a pause request at a safe checkpoint and stopped: the
    attempt ends paused, the Job consumes no lease, and a resume requeues it
    for a fresh attempt that continues from the checkpoint."""


class LeaseLostError(Exception):
    """This attempt's lease was taken over by a newer attempt; every further
    write would be fenced off. The handler must stop immediately."""


class JobContext:
    """What a Job handler sees: its bound payload, its attempt identity, and
    durable step/checkpoint writes. Control signals arrive as asyncio events
    set by the runtime from the durable request the API wrote to the Job row
    (surfaced by the heartbeat), or as :class:`LeaseLostError` raised from
    the next context call (lease taken over)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        job: ActiveJob,
        *,
        cancel_requested: asyncio.Event,
        pause_requested: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        self._session_factory = session_factory
        self._cancel_requested = cancel_requested
        self._pause_requested = pause_requested
        self._lease_lost = lease_lost
        self.job_id = job.id
        self.project_id = job.project_id
        self.workflow_run_id = job.workflow_run_id
        self.job_type = job.job_type
        self.payload: dict[str, Any] = job.payload
        self.attempt = job.attempt
        self.fencing_token = job.fencing_token

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def pause_requested(self) -> bool:
        return self._pause_requested.is_set()

    def _raise_if_lease_lost(self) -> None:
        if self._lease_lost.is_set():
            raise LeaseLostError(
                f"the lease on job {self.job_id} was taken over by a newer attempt"
            )

    async def run_step(
        self, step_key: str, effect: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """Run one step under its stable idempotency key (job_id, step_key).

        A step that already succeeded — because a previous attempt committed
        its effect before crashing — is never executed again: its recorded
        result is returned as-is, so replaying a step does not duplicate
        effects. The effect callable runs outside any transaction; only its
        result commits, fenced by the current attempt's token."""
        self._raise_if_lease_lost()
        async with self._session_factory() as session:
            try:
                existing = await jobs.begin_step(
                    session, job_id=self.job_id, step_key=step_key, fencing_token=self.fencing_token
                )
            except jobs.LeaseLost:
                self._lease_lost.set()
                raise LeaseLostError(
                    f"step {step_key!r} could not begin: the lease was taken over"
                ) from None
        if existing is not None:
            return existing

        self._raise_if_lease_lost()
        if self._cancel_requested.is_set():
            raise JobCancelled(
                f"the cancellation request was honored at the safe checkpoint "
                f"before step {step_key!r}"
            )
        if self._pause_requested.is_set():
            raise JobPaused(
                f"the pause request was honored at the safe checkpoint before step {step_key!r}"
            )

        result = await effect()

        async with self._session_factory() as session:
            try:
                await jobs.complete_step(
                    session,
                    job_id=self.job_id,
                    step_key=step_key,
                    attempt=self.attempt,
                    fencing_token=self.fencing_token,
                    result=result,
                )
            except jobs.LeaseLost:
                self._lease_lost.set()
                raise LeaseLostError(
                    f"step {step_key!r} could not commit: the lease was taken over"
                ) from None
        return result

    async def checkpoint(self, checkpoint_key: str, state: dict[str, Any]) -> None:
        """Record a resumable position, fenced by the attempt's token."""
        self._raise_if_lease_lost()
        async with self._session_factory() as session:
            try:
                await jobs.record_checkpoint(
                    session,
                    job_id=self.job_id,
                    attempt=self.attempt,
                    fencing_token=self.fencing_token,
                    checkpoint_key=checkpoint_key,
                    state=state,
                )
            except jobs.LeaseLost:
                self._lease_lost.set()
                raise LeaseLostError(
                    f"checkpoint {checkpoint_key!r} could not commit: the lease was taken over"
                ) from None

    async def checkpoint_history(self) -> list[tuple[str, dict[str, Any]]]:
        """The Job's checkpoints, oldest first — the durable record a new
        attempt resumes from (ADR-0003). Reading mutates nothing, but a stale
        attempt is still stopped here before it does further work."""
        self._raise_if_lease_lost()
        async with self._session_factory() as session:
            checkpoints = await jobs.list_job_checkpoints(session, self.job_id)
        return [(checkpoint.checkpoint_key, dict(checkpoint.state)) for checkpoint in checkpoints]
