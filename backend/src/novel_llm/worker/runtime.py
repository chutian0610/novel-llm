"""The Worker runtime (ADR-0003): a claim loop per concurrency slot, leases
renewed by heartbeats, cooperative pause/cancel honored at safe checkpoints,
and the SIGTERM shutdown sequence — stop claiming, request cooperative
cancellation for active Jobs, wait up to the graceful timeout, record an
interrupted checkpoint if it must exit first.

Concurrency is configurable and defaults to one; Jobs for the same Project
are serialized by the database claim regardless of this number.
"""

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from novel_llm.config import WorkerSettings
from novel_llm.domain import jobs
from novel_llm.domain.job_execution import (
    JobCancelled,
    JobContext,
    LeaseLostError,
    TransientJobFailure,
)
from novel_llm.domain.jobs import ActiveJob
from novel_llm.workflows.handlers import resolve_handler

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerFaults:
    """Deterministic fault injection for the framework acceptance tests (the
    fake-provider philosophy): with heartbeats disabled, a stalled handler's
    lease really expires, so lease-expiry takeover and stale-fencing
    rejection are exercisable end to end."""

    skip_heartbeats: bool = False


@dataclass(slots=True)
class _Execution:
    """One Job executing in this Worker: its snapshot and its control
    signals."""

    job: ActiveJob
    cancel_requested: asyncio.Event
    lease_lost: asyncio.Event


class Worker:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: WorkerSettings,
        *,
        worker_id: str | None = None,
        faults: WorkerFaults | None = None,
    ) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._settings = settings
        self._faults = faults or WorkerFaults()
        self.worker_id = worker_id or f"worker-{uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._stopping = False
        self._slots: list[asyncio.Task[None]] = []
        self._active: dict[UUID, _Execution] = {}

    def stop(self) -> None:
        """Request shutdown; the graceful sequence runs inside run()."""
        self._stop.set()

    async def run(self) -> None:
        """Run one claim loop per concurrency slot until stopped, then
        perform the graceful shutdown sequence (ADR-0003)."""
        self._slots = [
            asyncio.create_task(self._claim_loop(), name=f"{self.worker_id}-slot-{i}")
            for i in range(self._settings.concurrency)
        ]
        await self._stop.wait()
        self._stopping = True

        # Stop claiming happened above; now request cooperative cancellation
        # for every active Job — as a durable control request the database
        # can see, and as a signal the handler checks at its next checkpoint.
        for execution in list(self._active.values()):
            async with self._session_factory() as session:
                await jobs.request_cancellation(
                    session,
                    job_id=execution.job.id,
                    fencing_token=execution.job.fencing_token,
                )
            execution.cancel_requested.set()

        await asyncio.wait(self._slots, timeout=self._settings.graceful_timeout_seconds)

        # Forced to exit before the active Jobs finished: record interrupted
        # checkpoints so recovery starts a new attempt instead of losing work.
        for execution in list(self._active.values()):
            async with self._session_factory() as session:
                await jobs.force_interrupt(
                    session,
                    job_id=execution.job.id,
                    fencing_token=execution.job.fencing_token,
                    checkpoint_state={"reason": "worker_shutdown_graceful_timeout"},
                )

        for task in self._slots:
            task.cancel()
        await asyncio.gather(*self._slots, return_exceptions=True)
        log.info("worker %s stopped", self.worker_id)

    async def _claim_loop(self) -> None:
        while not self._stopping:
            claimed: ActiveJob | None = None
            try:
                async with self._session_factory() as session:
                    claimed = await jobs.claim_next_job(session, worker_id=self.worker_id)
            except Exception:
                log.exception("claim failed; retrying after the poll interval")
            if claimed is None:
                await asyncio.sleep(self._settings.poll_seconds)
                continue
            await self._execute(claimed)

    async def _heartbeat_loop(self, job: ActiveJob, lease_lost: asyncio.Event) -> None:
        """Renew the lease every heartbeat interval; the moment renewal is
        fenced off, this attempt must stop making writes."""
        try:
            while True:
                await asyncio.sleep(job.settings.heartbeat_seconds)
                async with self._session_factory() as session:
                    renewed = await jobs.extend_lease(
                        session,
                        job_id=job.id,
                        fencing_token=job.fencing_token,
                        lease_seconds=job.settings.lease_seconds,
                    )
                if not renewed:
                    lease_lost.set()
                    log.warning(
                        "worker %s lost the lease on job %s (attempt %s)",
                        self.worker_id,
                        job.id,
                        job.attempt,
                    )
                    return
        except asyncio.CancelledError:
            pass

    async def _execute(self, job: ActiveJob) -> None:
        execution = _Execution(
            job=job,
            cancel_requested=asyncio.Event(),
            lease_lost=asyncio.Event(),
        )
        self._active[job.id] = execution

        async with self._session_factory() as session:
            began = await jobs.begin_execution(
                session, job_id=job.id, fencing_token=job.fencing_token
            )
        if not began:
            # The lease was lost between claim and begin (fenced): nothing to
            # clean up — the owning attempt is elsewhere.
            self._active.pop(job.id, None)
            return

        handler = resolve_handler(job.job_type)
        if handler is None:
            async with self._session_factory() as session:
                await jobs.fail_job(
                    session,
                    job_id=job.id,
                    fencing_token=job.fencing_token,
                    failure_class="permanent",
                    error=f"no handler registered for job type {job.job_type!r}",
                    settings=job.settings,
                )
            self._active.pop(job.id, None)
            return

        heartbeat_task: asyncio.Task[None] | None = None
        if not self._faults.skip_heartbeats:
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job, execution.lease_lost))

        ctx = JobContext(
            self._session_factory,
            job,
            cancel_requested=execution.cancel_requested,
            lease_lost=execution.lease_lost,
        )
        log.info(
            "worker %s executing job %s (attempt %s, fencing token %s)",
            self.worker_id,
            job.id,
            job.attempt,
            job.fencing_token,
        )
        try:
            async with asyncio.timeout(job.settings.timeout_seconds):
                await handler(ctx)
        except JobCancelled:
            await self._finalize_cancel(job)
        except LeaseLostError:
            await self._abandon(job)
        except TimeoutError:
            await self._fail(job, "timeout", "the Job exceeded its database-owned timeout")
        except TransientJobFailure as error:
            await self._fail(job, "transient", str(error))
        except asyncio.CancelledError:
            await self._force_interrupt(job)
            raise
        except Exception as error:  # noqa: BLE001 — the boundary of handler classification
            await self._fail(job, "permanent", f"{type(error).__name__}: {error}")
        else:
            await self._finalize_success(job)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._active.pop(job.id, None)

    async def _finalize_success(self, job: ActiveJob) -> None:
        async with self._session_factory() as session:
            completed = await jobs.complete_job(
                session, job_id=job.id, fencing_token=job.fencing_token
            )
            if not completed:
                # The normal outcome was fenced off — most plausibly a
                # cancellation request landed (whose result never advances
                # state, ADR-0003). Land the cancellation instead.
                await jobs.cancel_job(session, job_id=job.id, fencing_token=job.fencing_token)

    async def _finalize_cancel(self, job: ActiveJob) -> None:
        async with self._session_factory() as session:
            await jobs.cancel_job(session, job_id=job.id, fencing_token=job.fencing_token)

    async def _fail(self, job: ActiveJob, failure_class: str, error: str) -> None:
        async with self._session_factory() as session:
            new_status = await jobs.fail_job(
                session,
                job_id=job.id,
                fencing_token=job.fencing_token,
                failure_class=failure_class,
                error=error,
                settings=job.settings,
            )
        if new_status is None:
            await self._abandon(job)

    async def _abandon(self, job: ActiveJob) -> None:
        """This attempt's lease is gone: a newer attempt owns the Job and
        every write of this attempt is discarded."""
        async with self._session_factory() as session:
            await jobs.abandon_attempt(session, job_id=job.id, attempt=job.attempt)

    async def _force_interrupt(self, job: ActiveJob) -> None:
        async with self._session_factory() as session:
            await jobs.force_interrupt(
                session,
                job_id=job.id,
                fencing_token=job.fencing_token,
                checkpoint_state={"reason": "worker_shutdown_graceful_timeout"},
            )
