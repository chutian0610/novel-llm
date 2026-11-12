"""Model execution at the system black-box seam (ADR-0008): a system
Workflow Run drives the deterministic fake provider through both contract
shapes — Markdown-plus-metadata for writing, Pydantic-validated JSON for
evaluation — with full traces (rendered prompt, completion, metadata,
latency, token use, cost) observable over the API, and at most one repair
call whose trace is retained and linked.

Everything is observed through HTTP resources; nothing queries tables."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import httpx

CreateProject = Callable[..., Awaitable[dict]]

SEED_MOCK_PROFILE = "01900000-0000-7000-8000-00000000c002"

_TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "skipped"}


async def _create_run(client: httpx.AsyncClient, project_id: str, payload: dict) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/workflow-runs",
        json={"definition_key": "system.model_probe", "payload": payload},
        headers={"Idempotency-Key": f"probe-{uuid.uuid4()}"},
    )


async def _get_run(client: httpx.AsyncClient, run_id: str) -> dict:
    response = await client.get(f"/api/v1/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_job(
    client: httpx.AsyncClient, job_id: str, status: str, timeout: float = 30.0
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(f"/api/v1/jobs/{job_id}")
        job = response.json()
        if job["status"] == status:
            return job
        assert job["status"] not in _TERMINAL_JOB_STATES, (
            f"Job reached terminal state {job['status']} while waiting for {status}: "
            f"{job.get('last_error')}"
        )
        await asyncio.sleep(0.05)
    raise AssertionError(f"Job {job_id} never reached {status}")


async def _traces(client: httpx.AsyncClient, project_id: str) -> list[dict]:
    response = await client.get(f"/api/v1/projects/{project_id}/model-traces")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_a_probe_run_writes_markdown_and_leaves_a_full_trace(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """The writing contract: Markdown plus metadata; the trace keeps the
    fully rendered prompt, the completion, metadata, latency, token use, and
    cost — no repair involved."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        {"model_profile_id": SEED_MOCK_PROFILE, "output_kind": "write"},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    job_id = (await _get_run(client, run_id))["jobs"][0]["id"]

    start_worker()
    await _wait_for_job(client, job_id, "succeeded")
    assert (await _get_run(client, run_id))["status"] == "succeeded"

    traces = await _traces(client, project_id)
    assert len(traces) == 1
    trace = traces[0]
    # The template rendered fully: the variables became part of the prompt.
    assert "《长夜灯》" in trace["prompt"]
    assert "第 1 章" in trace["prompt"]
    assert trace["template_key"] == "probe.write"
    assert trace["template_version"] == 1
    assert trace["output_kind"] == "write"
    assert trace["completion"].startswith("#")  # Markdown
    assert trace["metadata"]["provider"] == "mock"
    assert trace["input_tokens"] > 0
    assert trace["output_tokens"] > 0
    assert trace["latency_ms"] >= 0
    assert "cost" in trace
    assert trace["repair_of_id"] is None

    # The step result carries the parsed writing output for the workflow.
    job = await _wait_for_job(client, job_id, "succeeded")
    step = job["steps"][0]
    # For the writing contract the validated output IS the Markdown itself.
    assert step["result"]["parsed"].startswith("#")


async def test_an_evaluate_run_returns_typed_json(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """The evaluation contract: the completion is JSON the gateway validated
    against the typed contract; the parsed result rides the step."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        {"model_profile_id": SEED_MOCK_PROFILE, "output_kind": "evaluate"},
    )
    run_id = run.json()["id"]
    job_id = (await _get_run(client, run_id))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "succeeded")

    step = job["steps"][0]
    parsed = step["result"]["parsed"]
    assert isinstance(parsed["score"], int) and 0 <= parsed["score"] <= 100
    assert parsed["verdict"] in ("approve", "revise")
    assert isinstance(parsed["notes"], str)

    traces = await _traces(client, project_id)
    assert len(traces) == 1
    assert traces[0]["output_kind"] == "evaluate"
    assert traces[0]["repair_of_id"] is None


async def test_structurally_invalid_output_triggers_one_repair_with_both_traces(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """The repair contract: the fake provider's fault lever emits broken JSON
    once; the gateway makes at most one repair call, and BOTH traces —
    original and repair — are retained in full and linked."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        {
            "model_profile_id": SEED_MOCK_PROFILE,
            "output_kind": "evaluate",
            "force_invalid": True,
        },
    )
    run_id = run.json()["id"]
    job_id = (await _get_run(client, run_id))["jobs"][0]["id"]

    start_worker()
    job = await _wait_for_job(client, job_id, "succeeded")

    traces = await _traces(client, project_id)
    assert len(traces) == 2  # the original + exactly one repair, never more
    original, repair = sorted(traces, key=lambda trace: trace["created_at"])
    assert original["repair_of_id"] is None
    assert repair["repair_of_id"] == original["id"]
    # The repair prompt names the structural error and re-states the task.
    assert "JSON" in repair["prompt"]
    # The repair's completion validated: the step carries it.
    parsed = job["steps"][0]["result"]["parsed"]
    assert 0 <= parsed["score"] <= 100
    # The job's own failure_class stays clean: the repair rescued the call.
    assert job["failure_class"] is None


async def test_an_unknown_model_profile_is_rejected_at_run_creation(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Jobs bind their model profile at creation; an unknown one is a stable
    problem before any Job exists."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        {"model_profile_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert run.status_code in (400, 404)
    assert run.json()["code"] in ("model_profile_not_found", "invalid_workflow_payload")


async def test_the_remaining_typed_kinds_run_end_to_end(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """Diagnose, extract, and project complete the four typed kinds: each
    run's parsed result validates against its contract and leaves one trace."""
    project_id = (await create_project())["id"]
    start_worker()
    for output_kind in ("diagnose", "extract", "project"):
        run = await _create_run(
            client, project_id, {"model_profile_id": SEED_MOCK_PROFILE, "output_kind": output_kind}
        )
        run_id = run.json()["id"]
        job_id = (await _get_run(client, run_id))["jobs"][0]["id"]
        job = await _wait_for_job(client, job_id, "succeeded")
        assert job["steps"][0]["result"]["parsed"], output_kind

    traces = await _traces(client, project_id)
    assert len(traces) == 3
    assert {trace["output_kind"] for trace in traces} == {"diagnose", "extract", "project"}


async def test_trace_pagination_roundtrips_its_own_cursor(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """The list endpoint walks its own next_cursor (ADR-0008): feeding it
    back yields the next page, never a 400."""
    project_id = (await create_project())["id"]
    start_worker()
    for _ in range(3):
        run = await _create_run(
            client,
            project_id,
            {"model_profile_id": SEED_MOCK_PROFILE, "output_kind": "write"},
        )
        job_id = (await _get_run(client, run.json()["id"]))["jobs"][0]["id"]
        await _wait_for_job(client, job_id, "succeeded")

    first_page = await client.get(f"/api/v1/projects/{project_id}/model-traces?limit=2")
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 2

    second_page = await client.get(
        f"/api/v1/projects/{project_id}/model-traces?limit=2",
        params={"cursor": first_page.json()["next_cursor"]},
    )
    assert second_page.status_code == 200, second_page.text
    first_ids = {item["id"] for item in first_page.json()["items"]}
    second_ids = {item["id"] for item in second_page.json()["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 3


async def test_traces_of_an_unknown_project_are_a_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/model-traces"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


async def test_a_short_credential_key_never_echoes_itself(
    client: httpx.AsyncClient,
) -> None:
    """A key of four characters or fewer would echo itself through the
    last-four suffix — it masks to the bare ellipsis (ADR-0006)."""
    created = await client.post(
        "/api/v1/model-credentials",
        json={"name": "短密钥", "provider": "mock", "api_key": "abc"},
        headers={"Idempotency-Key": f"credential-{uuid.uuid4()}"},
    )
    assert created.status_code == 201
    assert "abc" not in created.text
    assert created.json()["api_key_hint"] == "…"


async def test_the_repair_trace_carries_the_full_six_elements(
    client: httpx.AsyncClient, create_project: CreateProject, start_worker: Callable
) -> None:
    """Both traces retain all six elements in full: prompt, completion,
    metadata, latency, token use, cost (ADR-0008)."""
    project_id = (await create_project())["id"]
    run = await _create_run(
        client,
        project_id,
        {
            "model_profile_id": SEED_MOCK_PROFILE,
            "output_kind": "evaluate",
            "force_invalid": True,
        },
    )
    job_id = (await _get_run(client, run.json()["id"]))["jobs"][0]["id"]
    start_worker()
    await _wait_for_job(client, job_id, "succeeded")

    traces = await _traces(client, project_id)
    for trace in traces:
        assert trace["prompt"]
        assert trace["completion"]
        assert trace["metadata"]
        assert trace["latency_ms"] >= 0
        assert trace["input_tokens"] > 0
        assert trace["output_tokens"] > 0
        assert "cost" in trace
