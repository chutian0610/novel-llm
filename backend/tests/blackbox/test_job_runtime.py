"""The Job runtime at the system black-box seam (ADR-0003), driven through
the API and the real Worker loop: the trivial Workflow Run claims, runs, and
completes a Job; leases and heartbeats prevent double execution; steps
replay without duplicating effects; per-Project serialization holds; retries
follow the database-owned settings; shutdown records interrupted checkpoints.
Crash recovery is exercised one level blacker: the real Worker process is
killed mid-job — SIGTERM for the graceful interrupted checkpoint, SIGKILL for
the lease-expiry takeover — and a restarted process resumes the Job.

Everything is observed through HTTP resources — job state, attempt history,
step results, checkpoints, and the durable event feed — never by querying
tables directly."""

import asyncio
import json
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
import pytest

from tests.blackbox.conftest import WorkerHandle, WorkerProcessHandle

CreateProject = Callable[..., Awaitable[dict]]

TRIVIAL_DEFINITION = "system.trivial"

_TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "skipped"}

# The trivial Job Type's lease, seeded on the job_types row (migration 0004):
# the configured magnitude the takeover-timing assertions measure against.
_TRIVIAL_LEASE_SECONDS = 6.0


def _assert_takeover_waited_out_the_lease(job: dict) -> None:
    """Takeover magnitude (the timing-assertion rule): attempt 2 started no
    earlier than the database-owned lease after attempt 1 started — the lease
    expiry, not the claim poll, gated the takeover."""
    gap = datetime.fromisoformat(job["attempts"][1]["started_at"]) - datetime.fromisoformat(
        job["attempts"][0]["started_at"]
    )
    assert gap.total_seconds() >= _TRIVIAL_LEASE_SECONDS


def _run_body(**payload: object) -> dict[str, object]:
    body: dict[str, object] = {"definition_key": TRIVIAL_DEFINITION}
    if payload:
        body["payload"] = payload
    return body


async def _create_run(client: httpx.AsyncClient, project_id: str, body: dict) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/workflow-runs",
        json=body,
        headers={"Idempotency-Key": f"run-{id(body)}-{asyncio.get_event_loop().time()}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _get_job(client: httpx.AsyncClient, job_id: str) -> dict:
    response = await client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _get_run(client: httpx.AsyncClient, run_id: str) -> dict:
    response = await client.get(f"/api/v1/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_job(
    client: httpx.AsyncClient, job_id: str, status: str, timeout: float = 30.0
) -> dict:
    """Poll the Job resource until it reaches ``status`` — the API is the
    only observation point, so waiting polls it."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await _get_job(client, job_id)
        if job["status"] == status:
            return job
        assert job["status"] not in _TERMINAL_JOB_STATES, (
            f"Job reached terminal state {job['status']} while waiting for {status}: "
            f"{job.get('last_error')}"
        )
        await asyncio.sleep(0.05)
    pytest.fail(f"Job {job_id} never reached {status} within {timeout}s")


async def _wait_for_job_attempt(
    client: httpx.AsyncClient, job_id: str, attempt: int, timeout: float = 30.0
) -> dict:
    """Poll until a given attempt is executing — pins which Worker holds the
    lease before the next step of a scenario."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await _get_job(client, job_id)
        if job["attempt"] == attempt and job["status"] == "running":
            return job
        assert job["status"] not in _TERMINAL_JOB_STATES, (
            f"Job reached terminal state {job['status']} while waiting for "
            f"attempt {attempt}: {job.get('last_error')}"
        )
        await asyncio.sleep(0.05)
    pytest.fail(f"Job {job_id} never reached attempt {attempt} within {timeout}s")


async def _wait_for_checkpoint(
    client: httpx.AsyncClient, job_id: str, checkpoint_key: str, timeout: float = 30.0
) -> None:
    """Poll until the Job's checkpoint history contains ``checkpoint_key`` —
    pins exactly how far the doomed attempt got before the scenario kills
    it."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await _get_job(client, job_id)
        if any(c["checkpoint_key"] == checkpoint_key for c in job["checkpoints"]):
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"Job {job_id} never recorded the {checkpoint_key!r} checkpoint")


async def _replay_events(client: httpx.AsyncClient, project_id: str) -> list[dict]:
    events: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100}
        if cursor is not None:
            params["after"] = cursor
        response = await client.get(f"/api/v1/projects/{project_id}/events", params=params)
        body = response.json()
        events.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return events


def _job_state_path(events: list[dict], job_id: str) -> list[str]:
    """The ordered `to` states of one Job's state events, deduplicating the
    at-least-once replay by event id."""
    seen: set[str] = set()
    states: list[str] = []
    for event in events:
        if event["event_type"] != "job.state_changed":
            continue
        if event["data"]["job_id"] != job_id:
            continue
        event_id = event["event_id"]
        if event_id in seen:
            continue
        seen.add(event_id)
        states.append(event["data"]["to"])
    return states


async def test_a_trivial_workflow_run_completes_its_job(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    project_id = (await create_project())["id"]
    run = await _create_run(client, project_id, _run_body())
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "succeeded")

    assert job["attempt"] == 1
    assert job["failure_class"] is None
    # The run reached its business-facing terminal state through its Job.
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"
    # Both steps committed their deterministic results under the idempotency
    # key (job_id, step_key); a checkpoint marks progress after each.
    assert [(step["step_key"], step["status"]) for step in job["steps"]] == [
        ("draft", "succeeded"),
        ("polish", "succeeded"),
    ]
    assert [step["result"]["attempt"] for step in job["steps"]] == [1, 1]
    assert [checkpoint["checkpoint_key"] for checkpoint in job["checkpoints"]] == [
        "after:draft",
        "after:polish",
    ]
    # The whole lifecycle is job-state events, replayable from the durable
    # feed: ready -> leased -> running -> succeeded.
    events = await _replay_events(client, project_id)
    assert _job_state_path(events, job_id) == ["ready", "leased", "running", "succeeded"]
    run_states = [
        event["data"]["to"]
        for event in events
        if event["event_type"] == "workflow_run.state_changed"
        and event["data"]["workflow_run_id"] == run["id"]
    ]
    assert run_states == ["pending", "running", "succeeded"]


async def test_the_job_lifecycle_is_observable_over_sse(
    live_server: str,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=30.0) as client:
        project_id = (await create_project())["id"]

        # Subscribe before creating the run: the stream must deliver the
        # whole lifecycle live, not just what predates the connection.
        frames: list[dict] = []
        stream_ready = asyncio.Event()

        async def read_stream() -> None:
            async with client.stream(
                "GET", f"/api/v1/projects/{project_id}/events/stream"
            ) as response:
                assert response.status_code == 200
                stream_ready.set()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        frame = json.loads(line[len("data: ") :])
                        frames.append(frame)
                        if (
                            frame["event_type"] == "job.state_changed"
                            and frame["data"]["to"] == "succeeded"
                        ):
                            return

        stream_task = asyncio.create_task(read_stream())
        await asyncio.wait_for(stream_ready.wait(), timeout=10.0)

        run = await _create_run(client, project_id, _run_body())
        job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]
        start_worker()

        await asyncio.wait_for(stream_task, timeout=30.0)
        events = [frame for frame in frames if frame["event_type"] == "job.state_changed"]
        job_events = [frame for frame in events if frame["data"]["job_id"] == job_id]
        assert [frame["data"]["to"] for frame in job_events] == [
            "ready",
            "leased",
            "running",
            "succeeded",
        ]


async def test_jobs_for_one_project_never_run_concurrently(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    project_id = (await create_project())["id"]
    first = await _create_run(client, project_id, _run_body(step_delay_ms=1500))
    second = await _create_run(client, project_id, _run_body(step_delay_ms=1500))
    first_job_id = (await _get_run(client, first["id"]))["jobs"][0]["id"]
    second_job_id = (await _get_run(client, second["id"]))["jobs"][0]["id"]

    start_worker(concurrency=2)
    await _wait_for_job(client, first_job_id, "succeeded", timeout=30.0)
    await _wait_for_job(client, second_job_id, "succeeded", timeout=30.0)

    first_job = await _get_job(client, first_job_id)
    second_job = await _get_job(client, second_job_id)
    first_finished = first_job["attempts"][0]["finished_at"]
    second_started = second_job["attempts"][0]["started_at"]
    assert first_finished <= second_started, (
        "two Jobs of one Project ran concurrently: "
        f"{first_job['attempts']} vs {second_job['attempts']}"
    )


async def test_jobs_of_different_projects_may_run_in_parallel(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    slow_project = (await create_project())["id"]
    fast_project = (await create_project())["id"]
    slow = await _create_run(client, slow_project, _run_body(step_delay_ms=1500))
    fast = await _create_run(client, fast_project, _run_body())
    slow_job_id = (await _get_run(client, slow["id"]))["jobs"][0]["id"]
    fast_job_id = (await _get_run(client, fast["id"]))["jobs"][0]["id"]

    start_worker(concurrency=2)
    await _wait_for_job(client, slow_job_id, "succeeded", timeout=30.0)
    await _wait_for_job(client, fast_job_id, "succeeded", timeout=30.0)

    slow_job = await _get_job(client, slow_job_id)
    fast_job = await _get_job(client, fast_job_id)
    slow_finished = slow_job["attempts"][0]["finished_at"]
    fast_started = fast_job["attempts"][0]["started_at"]
    fast_finished = fast_job["attempts"][0]["finished_at"]
    assert fast_started < slow_finished and fast_finished < slow_finished


async def test_a_transient_failure_retries_under_the_database_owned_settings(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        _run_body(fail_step="draft", failure_class="transient", fail_on_attempt=1),
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "succeeded")

    # max_attempts=3 from the job_types row: the first attempt failed and was
    # retried automatically; the second succeeded.
    assert job["attempt"] == 2
    assert [attempt["outcome"] for attempt in job["attempts"]] == ["failed", "succeeded"]
    assert job["failure_class"] == "transient"
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"
    # The retry waited out the database-owned initial backoff (1s): the gap
    # between the failed attempt finishing and the retry starting must be at
    # least the configured delay, not just the next poll.
    retry_gap = datetime.fromisoformat(job["attempts"][1]["started_at"]) - datetime.fromisoformat(
        job["attempts"][0]["finished_at"]
    )
    assert retry_gap.total_seconds() >= 1.0
    events = await _replay_events(client, project_id)
    assert _job_state_path(events, job_id) == [
        "ready",
        "leased",
        "running",
        "ready",  # the transient retry is scheduled, not failed
        "leased",
        "running",
        "succeeded",
    ]


async def test_a_permanent_failure_never_retries(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    project_id = (await create_project())["id"]
    run = await _create_run(
        client, project_id, _run_body(fail_step="draft", failure_class="permanent")
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "failed")

    assert job["attempt"] == 1  # terminal: no second attempt was scheduled
    assert job["failure_class"] == "permanent"
    assert "injected permanent failure" in job["last_error"]
    assert [attempt["outcome"] for attempt in job["attempts"]] == ["failed"]
    assert (await _get_run(client, run["id"]))["status"] == "failed"


@pytest.mark.parametrize(
    ("failure_class", "error_fragment"),
    [
        ("quota_exhausted", "injected quota exhausted failure"),
        ("upstream_invalid", "injected upstream invalid failure"),
    ],
)
async def test_quota_exhausted_and_upstream_invalid_failures_are_terminal(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
    failure_class: str,
    error_fragment: str,
) -> None:
    """The closed failure set (ADR-0003): like a permanent failure, exhausted
    quota and upstream-invalid output never retry automatically — only a
    transient failure does."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client, project_id, _run_body(fail_step="draft", failure_class=failure_class)
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "failed")

    assert job["attempt"] == 1  # terminal: no second attempt was scheduled
    assert job["failure_class"] == failure_class
    assert error_fragment in job["last_error"]
    assert [attempt["outcome"] for attempt in job["attempts"]] == ["failed"]
    assert (await _get_run(client, run["id"]))["status"] == "failed"


async def test_an_expired_lease_is_taken_over_and_the_stale_attempt_is_fenced(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    """Worker A stalls between step 'draft' and its progress checkpoint with
    heartbeats off (a deadlocked Worker); the lease expires, Worker B takes
    over with a new attempt and fencing token, replays the committed 'draft'
    without re-running its effect, and finishes the Job. When A finally wakes
    it still holds the old token and attempts its late checkpoint write —
    rejected: the record stays exactly B's."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        # A stalls 7.5s; the lease expires at 6s, so B recovers first and A
        # wakes into a Job it no longer owns.
        _run_body(stall_before_checkpoint="draft", stall_on_attempt=1, stall_ms=7_500),
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    stalled = start_worker(skip_heartbeats=True)  # worker A: its own slot stalls
    # Pin the lease to A before B exists: otherwise a healthy Worker could
    # win the first claim and finish the Job without any takeover.
    await _wait_for_job_attempt(client, job_id, attempt=1)
    recovering = start_worker()  # worker B: healthy

    job = await _wait_for_job(client, job_id, "succeeded", timeout=40.0)

    assert job["attempt"] == 2  # recovery creates a new attempt
    assert [attempt["attempt"] for attempt in job["attempts"]] == [1, 2]
    assert job["attempts"][0]["outcome"] == "abandoned"
    assert job["attempts"][1]["outcome"] == "succeeded"
    assert job["attempts"][0]["worker_id"] != job["attempts"][1]["worker_id"]
    assert job["attempts"][0]["finished_at"] <= job["attempts"][1]["started_at"]

    # Step replay does not duplicate effects: 'draft' keeps attempt 1's
    # committed result (its recorded result names attempt 1), while 'polish'
    # belongs to attempt 2.
    steps = {step["step_key"]: step for step in job["steps"]}
    assert steps["draft"]["attempt"] == 1
    assert steps["draft"]["result"]["attempt"] == 1
    assert steps["polish"]["attempt"] == 2
    assert steps["polish"]["result"]["attempt"] == 2
    # Checkpoints are append-only history: attempt 1's stall marker stays,
    # and attempt 2 appends the progress attempt 1 never reached — 'draft'
    # via the replayed committed step, 'polish' as its own work.
    assert [
        (checkpoint["checkpoint_key"], checkpoint["attempt"]) for checkpoint in job["checkpoints"]
    ] == [
        ("stalled-before:draft", 1),
        ("after:draft", 2),
        ("after:polish", 2),
    ]

    # Takeover magnitude: B had to wait out the database-owned lease (6s for
    # this Job Type, counted from attempt 1's start), not just the next poll.
    _assert_takeover_waited_out_the_lease(job)

    # Monotonic fencing tokens (ADR-0003): attempt 2's token is strictly
    # greater than attempt 1's, and every checkpoint carries the token of the
    # attempt that wrote it.
    assert [attempt["fencing_token"] for attempt in job["attempts"]] == [1, 2]
    assert [checkpoint["fencing_token"] for checkpoint in job["checkpoints"]] == [1, 2, 2]

    # A finally wakes from its stall, still holding the old token, and
    # attempts its late checkpoint write for 'draft'. It is fenced off: the
    # record stays exactly as B left it. The choreography bounds the race:
    # A's stall (7.5s) outlasts the lease (6s), so B's takeover always comes
    # first, and the wait below outlasts A's wake-up before A is stopped.
    assert recovering.worker.worker_id == job["attempts"][1]["worker_id"]
    assert stalled.worker.worker_id == job["attempts"][0]["worker_id"]
    await asyncio.sleep(3.0)  # outlast A's wake-up (7.5s) and its fenced write
    stalled.worker.stop()
    await asyncio.gather(stalled.task, return_exceptions=True)
    after = await _get_job(client, job_id)
    assert after == job  # A's late writes changed nothing
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"


async def test_shutdown_records_an_interrupted_checkpoint_and_recovers(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    """SIGTERM mid-job: the Worker stops claiming, requests cooperative
    cancellation, waits the graceful timeout, records an interrupted
    checkpoint, and exits; a fresh Worker then recovers the Job with a new
    attempt."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        _run_body(stall_after_step="draft", stall_on_attempt=1, stall_ms=20_000),
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    # Heartbeats ON: the lease stays alive while the handler stalls, so the
    # Job is genuinely mid-flight when the shutdown sequence runs. Pin the
    # attempt first: stopping before the claim would leave the Job queued.
    shutting_down = start_worker(graceful_timeout_seconds=1.0)
    await _wait_for_job_attempt(client, job_id, attempt=1)

    shutting_down.worker.stop()
    await asyncio.gather(shutting_down.task, return_exceptions=True)

    interrupted = await _wait_for_job(client, job_id, "interrupted")
    assert interrupted["attempts"][0]["outcome"] == "interrupted"
    assert interrupted["attempts"][0]["finished_at"] is not None
    assert any(
        checkpoint["checkpoint_key"] == "interrupted" for checkpoint in interrupted["checkpoints"]
    ), "the forced exit must record an interrupted checkpoint"

    start_worker()
    job = await _wait_for_job(client, job_id, "succeeded", timeout=40.0)
    assert job["attempt"] == 2
    steps = {step["step_key"]: step for step in job["steps"]}
    assert steps["draft"]["attempt"] == 1  # resumed from the checkpoint, not re-executed
    assert steps["polish"]["attempt"] == 2
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"


async def test_a_killed_worker_process_loses_the_lease_and_the_restart_takes_over(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker_process: Callable[..., Awaitable[WorkerProcessHandle]],
) -> None:
    """The real Worker process is killed mid-job — SIGKILL, so nothing
    graceful can run. Its lease expires; the restarted real Worker claims the
    recovery as a new attempt with a strictly greater fencing token, resumes
    from the checkpoint, and completes. Recovery never reopens history: the
    dead attempt stays abandoned in the record."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        _run_body(stall_after_step="draft", stall_on_attempt=1, stall_ms=60_000),
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    killed = await start_worker_process()
    await _wait_for_job_attempt(client, job_id, attempt=1)
    # Wait out the handler's full progress (the stall marker commits right
    # after the checkpoint): killing any earlier would leave the exact
    # checkpoint history below non-deterministic.
    await _wait_for_checkpoint(client, job_id, "stalled:draft")

    killed.process.kill()  # SIGKILL: no shutdown sequence, no interrupted checkpoint
    assert await killed.process.wait() == -signal.SIGKILL

    await start_worker_process()  # the restart, a fresh real Worker process
    job = await _wait_for_job(client, job_id, "succeeded", timeout=40.0)

    assert job["attempt"] == 2
    dead, recovery = job["attempts"]
    assert dead["outcome"] == "abandoned"
    assert recovery["outcome"] == "succeeded"
    assert dead["worker_id"] != recovery["worker_id"]
    # Takeover magnitude: the restart waited out the database-owned lease,
    # not merely the next claim poll.
    _assert_takeover_waited_out_the_lease(job)
    assert dead["fencing_token"] < recovery["fencing_token"]

    steps = {step["step_key"]: step for step in job["steps"]}
    assert steps["draft"]["attempt"] == 1  # resumed from the checkpoint
    assert steps["polish"]["attempt"] == 2
    assert [(c["checkpoint_key"], c["attempt"]) for c in job["checkpoints"]] == [
        ("after:draft", 1),
        ("stalled:draft", 1),
        ("after:polish", 2),
    ]
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"
    events = await _replay_events(client, project_id)
    assert _job_state_path(events, job_id) == [
        "ready",
        "leased",
        "running",
        "interrupted",  # the lease expiry marks the dead Worker's interruption
        "leased",  # the restart's takeover, under a new fencing token
        "running",
        "succeeded",
    ]


async def test_a_terminated_worker_process_resumes_from_the_interrupted_checkpoint(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker_process: Callable[..., Awaitable[WorkerProcessHandle]],
) -> None:
    """SIGTERM mid-job to the real Worker process: the entrypoint's shutdown
    sequence runs — stop claiming, request cooperative cancellation, wait the
    graceful timeout — and, the handler being stuck, records the interrupted
    checkpoint and exits. The restarted Worker claims the interrupted Job
    with a new attempt and resumes from the checkpoint."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        _run_body(stall_after_step="draft", stall_on_attempt=1, stall_ms=60_000),
    )
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    terminated = await start_worker_process()
    await _wait_for_job_attempt(client, job_id, attempt=1)
    # Wait out the handler's full progress, as in the SIGKILL scenario, so
    # the checkpoint history the restart leaves behind is deterministic.
    await _wait_for_checkpoint(client, job_id, "stalled:draft")

    terminated.process.send_signal(signal.SIGTERM)
    await asyncio.wait_for(terminated.process.wait(), timeout=15.0)

    interrupted = await _wait_for_job(client, job_id, "interrupted")
    assert interrupted["attempts"][0]["outcome"] == "interrupted"
    assert "interrupted" in [c["checkpoint_key"] for c in interrupted["checkpoints"]], (
        "the forced exit must record an interrupted checkpoint"
    )

    await start_worker_process()  # the restart
    job = await _wait_for_job(client, job_id, "succeeded", timeout=40.0)

    assert job["attempt"] == 2
    dead, recovery = job["attempts"]
    assert (dead["outcome"], recovery["outcome"]) == ("interrupted", "succeeded")
    assert dead["fencing_token"] < recovery["fencing_token"]
    steps = {step["step_key"]: step for step in job["steps"]}
    assert steps["draft"]["attempt"] == 1  # resumed from the checkpoint
    assert steps["polish"]["attempt"] == 2
    assert [(c["checkpoint_key"], c["attempt"]) for c in job["checkpoints"]] == [
        ("after:draft", 1),
        ("stalled:draft", 1),
        ("interrupted", 1),
        ("after:polish", 2),
    ]
    assert (await _get_run(client, run["id"]))["status"] == "succeeded"
    events = await _replay_events(client, project_id)
    assert _job_state_path(events, job_id) == [
        "ready",
        "leased",
        "running",
        "cancel_requested",  # the graceful sequence's cooperative request
        "interrupted",  # forced out at the graceful timeout
        "leased",  # the restart's new attempt
        "running",
        "succeeded",
    ]


async def test_a_cancellation_request_is_honored_at_the_next_safe_checkpoint(
    client: httpx.AsyncClient,
    create_project: CreateProject,
    start_worker: Callable[..., WorkerHandle],
) -> None:
    """The shutdown sequence's cooperative cancellation lands as a durable
    control request; a handler that reaches its next safe checkpoint (the
    boundary before a step) honors it, and the Job stops cancelled instead of
    continuing its normal outcome."""
    project_id = (await create_project())["id"]
    # A slow-but-healthy Job: each step waits 1.5s, so the handler is between
    # steps (a safe checkpoint) when the request lands.
    run = await _create_run(client, project_id, _run_body(step_delay_ms=1500))
    job_id = (await _get_run(client, run["id"]))["jobs"][0]["id"]

    worker = start_worker(graceful_timeout_seconds=10.0)
    await _wait_for_job_attempt(client, job_id, attempt=1)

    worker.worker.stop()  # requests cooperative cancellation, waits gracefully
    await asyncio.gather(worker.task, return_exceptions=True)

    job = await _wait_for_job(client, job_id, "cancelled", timeout=30.0)
    assert job["failure_class"] == "cancelled"
    assert [attempt["outcome"] for attempt in job["attempts"]] == ["cancelled"]
    assert (await _get_run(client, run["id"]))["status"] == "cancelled"
    events = await _replay_events(client, project_id)
    assert _job_state_path(events, job_id) == [
        "ready",
        "leased",
        "running",
        "cancel_requested",
        "cancelling",
        "cancelled",
    ]
