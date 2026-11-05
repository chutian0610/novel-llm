"""The Workflow Run and Job API contract at the system black-box seam
(ADR-0008): problem-details with stable codes, idempotency-key replay on
creation, cursor pagination on lists, and the definition binding the run
records. No worker runs in these tests — nothing is claimed."""

import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest

TRIVIAL_DEFINITION = "system.trivial"

CreateProject = Callable[..., Awaitable[dict]]


def _create_run_body(
    definition_key: str = TRIVIAL_DEFINITION, **payload_overrides: object
) -> dict[str, object]:
    body: dict[str, object] = {"definition_key": definition_key}
    if payload_overrides:
        body["payload"] = payload_overrides
    return body


async def create_run(
    client: httpx.AsyncClient, project_id: str, body: dict, key: str | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/workflow-runs",
        json=body,
        headers={"Idempotency-Key": key or f"run-{uuid.uuid4()}"},
    )


async def test_creating_a_workflow_run_schedules_its_job(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    response = await create_run(client, project_id, _create_run_body())
    assert response.status_code == 201
    run = response.json()
    assert run["definition_key"] == TRIVIAL_DEFINITION
    assert run["definition_version"] == 1
    assert run["payload_schema_version"] == 1
    assert run["status"] == "pending"
    assert response.headers["Location"] == f"/api/v1/workflow-runs/{run['id']}"

    detail = await client.get(f"/api/v1/workflow-runs/{run['id']}")
    assert detail.status_code == 200
    jobs = detail.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "trivial_work"
    assert jobs[0]["status"] == "ready"
    assert jobs[0]["attempt"] == 0


async def test_creating_a_workflow_run_is_idempotent(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    key = "same-create-intent"
    first = await create_run(client, project_id, _create_run_body(), key=key)
    second = await create_run(client, project_id, _create_run_body(), key=key)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


async def test_reusing_the_key_with_a_different_payload_conflicts(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    key = "one-intent"
    await create_run(client, project_id, _create_run_body(), key=key)
    conflict = await create_run(client, project_id, _create_run_body(steps=["draft"]), key=key)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"


async def test_an_unknown_workflow_definition_is_rejected(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    response = await create_run(client, project_id, _create_run_body("system.nonexistent"))
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_workflow_definition"
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_payload_that_violates_the_definition_schema_is_rejected(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    response = await create_run(client, project_id, _create_run_body(steps=[]))
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_workflow_payload"


async def test_an_unknown_payload_key_is_rejected_not_silently_dropped(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Fault-injection choreography keys must not silently vanish: a misspelled
    key would leave the fault unfired and the scenario meaningless. The
    definition forbids unknown keys, so the typo is a 400 at creation."""
    project_id = (await create_project())["id"]
    response = await create_run(client, project_id, _create_run_body(stall_on_attemp=1))
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_workflow_payload"


async def test_a_workflow_run_for_an_unknown_project_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await create_run(client, str(uuid.uuid4()), _create_run_body())
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


async def test_an_archived_project_accepts_no_new_workflow_runs(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    await client.patch(f"/api/v1/projects/{project_id}", json={}, headers={"If-Match": "*"})
    await client.post(f"/api/v1/projects/{project_id}/archive", headers={"If-Match": "*"})
    response = await create_run(client, project_id, _create_run_body())
    assert response.status_code == 409
    assert response.json()["code"] == "project_archived"


async def test_workflow_run_listing_is_cursor_paginated(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    created = [
        (await create_run(client, project_id, _create_run_body(), key=f"run-{i}")).json()
        for i in range(3)
    ]
    page_one = await client.get(f"/api/v1/projects/{project_id}/workflow-runs", params={"limit": 2})
    assert page_one.status_code == 200
    body = page_one.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    page_two = await client.get(
        f"/api/v1/projects/{project_id}/workflow-runs",
        params={"limit": 2, "cursor": body["next_cursor"]},
    )
    items = page_two.json()["items"]
    assert len(items) == 1
    assert [run["id"] for run in body["items"] + items] == [
        run["id"] for run in sorted(created, key=lambda run: run["created_at"], reverse=True)
    ]


async def test_an_unknown_job_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "job_not_found"


@pytest.mark.parametrize(
    ("key", "expected"),
    [(" ", 400), ("x" * 201, 400)],
)
async def test_a_malformed_idempotency_key_is_rejected(
    client: httpx.AsyncClient, create_project: CreateProject, key: str, expected: int
) -> None:
    project_id = (await create_project())["id"]
    response = await client.post(
        f"/api/v1/projects/{project_id}/workflow-runs",
        json=_create_run_body(),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == expected
    assert response.json()["code"] == "invalid_idempotency_key"
