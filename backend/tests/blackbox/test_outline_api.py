"""Outline publication at the system black-box seam (ADR-0004): one
immutable version per publication containing work, volume, chapter, scene,
foreshadowing, and constraint plans — generation always references a fixed
plan. Referenced story objects are validated transactionally by the domain
service (ADR-0009: no physical foreign keys).

Everything is observed through HTTP resources; nothing queries tables."""

import uuid
from collections.abc import Awaitable, Callable

import httpx

CreateProject = Callable[..., Awaitable[dict]]


def _plans(
    *,
    synopsis: str = "少年执灯入山，照见百年前旧事。",
    volume_id: str | None = None,
    chapter_id: str | None = None,
) -> dict:
    return {
        "work": {"synopsis": synopsis},
        "volumes": [{"volume_id": volume_id, "summary": "入山"}] if volume_id else [],
        "chapters": [{"chapter_id": chapter_id, "summary": "夜行遇灯", "goal": "立主角与灯的羁绊"}]
        if chapter_id
        else [],
        "scenes": [],
        "foreshadowings": [],
        "constraints": [{"description": "全程志怪语感，不用现代词"}],
    }


async def _publish(
    client: httpx.AsyncClient,
    project_id: str,
    plans: dict,
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/outline/publish",
        json=plans,
        headers={"Idempotency-Key": key or f"outline-{uuid.uuid4()}"},
    )


async def test_the_outline_publishes_as_one_immutable_version_and_changes_require_a_new_version(
    client: httpx.AsyncClient,
    create_project: CreateProject,
) -> None:
    project_id = (await create_project())["id"]
    volume_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/volumes",
            json={"title": "第一卷"},
            headers={"Idempotency-Key": f"v-{uuid.uuid4()}"},
        )
    ).json()["id"]
    chapter_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/chapters",
            json={"volume_id": volume_id, "title": "第一章 夜行"},
            headers={"Idempotency-Key": f"c-{uuid.uuid4()}"},
        )
    ).json()["id"]

    first = await _publish(client, project_id, _plans(volume_id=volume_id, chapter_id=chapter_id))
    assert first.status_code == 201, first.text
    assert first.json()["version_number"] == 1
    assert first.json()["payload_schema_version"] == 1
    assert first.headers["Location"] == f"/api/v1/projects/{project_id}/outline/versions/1"

    # The current version answers for generation until a new one publishes.
    current = await client.get(f"/api/v1/projects/{project_id}/outline")
    assert current.status_code == 200
    assert current.json()["version_number"] == 1
    assert current.json()["plans"]["work"]["synopsis"].startswith("少年执灯")

    # Changes require a new version: the second publication binds the same
    # story objects but a new plan, and lands as version 2.
    second = await _publish(
        client,
        project_id,
        _plans(synopsis="修订：灯是引路者。", volume_id=volume_id, chapter_id=chapter_id),
    )
    assert second.status_code == 201
    assert second.json()["version_number"] == 2

    current = await client.get(f"/api/v1/projects/{project_id}/outline")
    assert current.json()["version_number"] == 2
    assert current.json()["plans"]["work"]["synopsis"].startswith("修订")

    # Version 1 is immutable history: exactly what version 1 published is
    # still there, untouched by version 2.
    frozen = await client.get(f"/api/v1/projects/{project_id}/outline/versions/1")
    assert frozen.status_code == 200
    assert frozen.json()["plans"]["work"]["synopsis"].startswith("少年执灯")
    assert frozen.json()["plans"]["work"]["synopsis"] == first.json()["plans"]["work"]["synopsis"]
    assert frozen.json()["version_number"] == 1

    missing = await client.get(f"/api/v1/projects/{project_id}/outline/versions/3")
    assert missing.status_code == 404
    assert missing.json()["code"] == "outline_not_found"


async def test_outline_references_are_validated_in_the_domain_transaction(
    client: httpx.AsyncClient,
    create_project: CreateProject,
) -> None:
    """No physical foreign keys (ADR-0009): plans referencing unknown (or
    other-Project) story objects are a domain problem, never a 500."""
    project_id = (await create_project())["id"]
    other_project_id = (await create_project())["id"]
    other_chapter_id = (
        await client.post(
            f"/api/v1/projects/{other_project_id}/chapters",
            json={
                "volume_id": (
                    await client.post(
                        f"/api/v1/projects/{other_project_id}/volumes",
                        json={"title": "别家卷"},
                        headers={"Idempotency-Key": f"v-{uuid.uuid4()}"},
                    )
                ).json()["id"],
                "title": "别家章",
            },
            headers={"Idempotency-Key": f"c-{uuid.uuid4()}"},
        )
    ).json()["id"]

    unknown = await _publish(
        client, project_id, _plans(chapter_id="00000000-0000-0000-0000-000000000000")
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "chapter_not_found"

    cross = await _publish(client, project_id, _plans(chapter_id=other_chapter_id))
    assert cross.status_code == 404
    assert cross.json()["code"] == "chapter_not_found"

    # Nothing published along the way.
    current = await client.get(f"/api/v1/projects/{project_id}/outline")
    assert current.status_code == 404
    assert current.json()["code"] == "outline_not_found"


async def test_a_replayed_publish_with_the_same_key_returns_the_original_version(
    client: httpx.AsyncClient,
    create_project: CreateProject,
) -> None:
    project_id = (await create_project())["id"]
    key = f"outline-{uuid.uuid4()}"

    first = await _publish(client, project_id, _plans(), key=key)
    replay = await _publish(client, project_id, _plans(), key=key)
    assert first.status_code == 201
    assert replay.status_code == 200, replay.text
    assert replay.json()["version_number"] == 1
    assert replay.json()["id"] == first.json()["id"]


async def test_an_outline_payload_that_does_not_match_the_schema_is_rejected(
    client: httpx.AsyncClient,
    create_project: CreateProject,
) -> None:
    """Typed contracts (ADR-0008): an unknown key in a plan is a malformed
    payload, not a silently unfired plan."""
    project_id = (await create_project())["id"]

    response = await _publish(
        client,
        project_id,
        {"work": {"synopsis": "…", "typo_key": True}},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_outline_payload"
