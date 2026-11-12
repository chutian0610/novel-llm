"""The story structure at the system black-box seam (ADR-0004/0009): Volumes
and Chapters with stable identities and mutable positions, reordered through
domain actions, observed through the Project structure resource. Reference
validation failures are domain problems (404/400 with stable codes), never
database integrity errors — that is the observable face of having no
physical foreign keys.

Everything is observed through HTTP resources; nothing queries tables."""

import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest

from tests.blackbox.test_outline_api import _plans as _outline_plans
from tests.blackbox.test_outline_api import _publish as _publish_outline

CreateProject = Callable[..., Awaitable[dict]]


async def _create_volume(client: httpx.AsyncClient, project_id: str, title: str) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/volumes",
        json={"title": title},
        headers={"Idempotency-Key": f"volume-{uuid.uuid4()}"},
    )


async def _create_chapter(
    client: httpx.AsyncClient, project_id: str, volume_id: str, title: str
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/chapters",
        json={"volume_id": volume_id, "title": title},
        headers={"Idempotency-Key": f"chapter-{uuid.uuid4()}"},
    )


async def _structure(client: httpx.AsyncClient, project_id: str) -> dict:
    response = await client.get(f"/api/v1/projects/{project_id}/structure")
    assert response.status_code == 200, response.text
    return response.json()


def _volume_titles(structure: dict) -> list[str]:
    return [volume["title"] for volume in structure["volumes"]]


def _chapter_titles(structure: dict, volume_title: str) -> list[str]:
    volume = next(v for v in structure["volumes"] if v["title"] == volume_title)
    return [chapter["title"] for chapter in volume["chapters"]]


async def test_a_created_volume_appends_to_the_structure(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]

    first = await _create_volume(client, project_id, "第一卷 山门")
    second = await _create_volume(client, project_id, "第二卷 入世")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.headers["Location"] == f"/api/v1/volumes/{first.json()['id']}"

    structure = await _structure(client, project_id)
    assert _volume_titles(structure) == ["第一卷 山门", "第二卷 入世"]
    assert [volume["position"] for volume in structure["volumes"]] == [1, 2]
    assert all(volume["chapters"] == [] for volume in structure["volumes"])


async def test_a_created_volume_replays_idempotently(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    key = f"volume-{uuid.uuid4()}"

    first = await client.post(
        f"/api/v1/projects/{project_id}/volumes",
        json={"title": "第一卷"},
        headers={"Idempotency-Key": key},
    )
    replay = await client.post(
        f"/api/v1/projects/{project_id}/volumes",
        json={"title": "第一卷"},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    structure = await _structure(client, project_id)
    assert len(structure["volumes"]) == 1


async def test_a_created_chapter_lives_inside_its_volume(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    volume_id = (await _create_volume(client, project_id, "第一卷")).json()["id"]

    chapter = await _create_chapter(client, project_id, volume_id, "第一章 夜行")
    assert chapter.status_code == 201, chapter.text
    body = chapter.json()
    assert body["volume_id"] == volume_id
    assert body["position"] == 1

    structure = await _structure(client, project_id)
    assert _chapter_titles(structure, "第一卷") == ["第一章 夜行"]


async def test_reordering_volumes_renumbers_positions_and_keeps_identities(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Reorder is the whole point of stable identities with mutable
    positions: the same Volumes, in a new order, references intact."""
    project_id = (await create_project())["id"]
    created = [
        (await _create_volume(client, project_id, title)).json()
        for title in ("第一卷", "第二卷", "第三卷")
    ]
    volume_ids = [volume["id"] for volume in created]

    response = await client.post(
        f"/api/v1/projects/{project_id}/volumes/reorder",
        json={"ordered_volume_ids": [volume_ids[2], volume_ids[0], volume_ids[1]]},
    )
    assert response.status_code == 200, response.text

    structure = await _structure(client, project_id)
    assert _volume_titles(structure) == ["第三卷", "第一卷", "第二卷"]
    assert [volume["position"] for volume in structure["volumes"]] == [1, 2, 3]
    # The identities survive: same ids, same titles, same chapters.
    assert [volume["id"] for volume in structure["volumes"]] == [
        volume_ids[2],
        volume_ids[0],
        volume_ids[1],
    ]


async def test_reordering_chapters_stays_inside_its_volume(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    volume_id = (await _create_volume(client, project_id, "第一卷")).json()["id"]
    created = [
        (await _create_chapter(client, project_id, volume_id, title)).json()
        for title in ("第一章", "第二章", "第三章")
    ]

    response = await client.post(
        f"/api/v1/volumes/{volume_id}/chapters/reorder",
        json={"ordered_chapter_ids": [created[2]["id"], created[0]["id"], created[1]["id"]]},
    )
    assert response.status_code == 200, response.text

    structure = await _structure(client, project_id)
    chapters = structure["volumes"][0]["chapters"]
    assert [chapter["title"] for chapter in chapters] == ["第三章", "第一章", "第二章"]
    assert [chapter["position"] for chapter in chapters] == [1, 2, 3]
    assert all(chapter["volume_id"] == volume_id for chapter in chapters)


async def test_moving_a_chapter_to_another_volume_appends_it_there(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Restructure: a Chapter moves between Volumes with its identity intact."""
    project_id = (await create_project())["id"]
    first_volume_id = (await _create_volume(client, project_id, "第一卷")).json()["id"]
    second_volume_id = (await _create_volume(client, project_id, "第二卷")).json()["id"]
    chapter_id = (await _create_chapter(client, project_id, first_volume_id, "第一章")).json()["id"]
    # Give the target volume an existing chapter: the move appends after it.
    await _create_chapter(client, project_id, second_volume_id, "第二章")

    response = await client.post(
        f"/api/v1/chapters/{chapter_id}/move",
        json={"to_volume_id": second_volume_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["volume_id"] == second_volume_id
    assert response.json()["position"] == 2

    structure = await _structure(client, project_id)
    assert _chapter_titles(structure, "第一卷") == []
    assert _chapter_titles(structure, "第二卷") == ["第二章", "第一章"]


async def test_a_reorder_missing_a_sibling_is_refused_as_a_domain_problem(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    await _create_volume(client, project_id, "第一卷")
    second_id = (await _create_volume(client, project_id, "第二卷")).json()["id"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/volumes/reorder",
        json={"ordered_volume_ids": [second_id]},  # 第一卷 is missing
    )
    assert response.status_code == 400, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "structure_order_invalid"


async def test_a_chapter_referencing_an_unknown_volume_is_a_domain_problem_not_a_database_error(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """No physical foreign keys (ADR-0009): the domain service validates the
    reference in its transaction, so the failure surfaces as a stable
    problem, never a 500 integrity error."""
    project_id = (await create_project())["id"]

    response = await _create_chapter(
        client, project_id, "00000000-0000-0000-0000-000000000000", "第一章"
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "volume_not_found"


async def test_a_chapter_cannot_move_into_another_projects_volume(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    other_project_id = (await create_project())["id"]
    other_volume_id = (await _create_volume(client, other_project_id, "别家卷")).json()["id"]
    chapter_id = (
        await _create_chapter(
            client,
            project_id,
            (await _create_volume(client, project_id, "第一卷")).json()["id"],
            "第一章",
        )
    ).json()["id"]

    response = await client.post(
        f"/api/v1/chapters/{chapter_id}/move",
        json={"to_volume_id": other_volume_id},
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "volume_not_found"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/volumes", {"title": ""}),
        ("/chapters", {"volume_id": "00000000-0000-0000-0000-000000000000", "title": "  "}),
    ],
)
async def test_blank_titles_are_rejected_before_any_reference_lookup(
    client: httpx.AsyncClient, create_project: CreateProject, path: str, body: dict
) -> None:
    project_id = (await create_project())["id"]
    response = await client.post(
        f"/api/v1/projects/{project_id}{path}",
        json=body,
        headers={"Idempotency-Key": f"blank-{uuid.uuid4()}"},
    )
    assert response.status_code in (400, 422), response.text


async def test_volume_title_updates_flow_through_if_match(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Mutable objects carry ETag/If-Match (ADR-0008): the create response's
    ETag updates the title; a stale or missing If-Match is refused; identities
    and positions never move."""
    project_id = (await create_project())["id"]
    created = await _create_volume(client, project_id, "第一卷 旧名")
    volume_id = created.json()["id"]
    etag = created.headers["ETag"]

    updated = await client.patch(
        f"/api/v1/volumes/{volume_id}",
        json={"title": "第一卷 新名"},
        headers={"If-Match": etag},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "第一卷 新名"
    assert updated.json()["position"] == 1
    assert updated.headers["ETag"] != etag

    stale = await client.patch(
        f"/api/v1/volumes/{volume_id}",
        json={"title": "第一卷 再改"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412

    missing = await client.patch(f"/api/v1/volumes/{volume_id}", json={"title": "第一卷"})
    assert missing.status_code == 428

    unknown = await client.patch(
        "/api/v1/volumes/00000000-0000-0000-0000-000000000000",
        json={"title": "无"},
        headers={"If-Match": "*"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "volume_not_found"


async def test_chapter_title_updates_flow_through_if_match(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    volume_id = (await _create_volume(client, project_id, "第一卷")).json()["id"]
    created = await _create_chapter(client, project_id, volume_id, "第一章 旧名")
    chapter_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/chapters/{chapter_id}",
        json={"title": "第一章 新名"},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "第一章 新名"
    assert updated.json()["volume_id"] == volume_id
    assert updated.json()["position"] == 1


async def test_an_archived_project_refuses_structure_and_outline_edits(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Archive is the only removal path (ADR-0002): an archived Project stops
    accepting structure edits and Outline publications."""
    project_id = (await create_project())["id"]
    volume_id = (await _create_volume(client, project_id, "第一卷")).json()["id"]

    archive = await client.post(
        f"/api/v1/projects/{project_id}/archive",
        headers={"If-Match": "*"},
    )
    assert archive.status_code == 200, archive.text

    volume_response = await _create_volume(client, project_id, "第二卷")
    assert volume_response.status_code == 409
    assert volume_response.json()["code"] == "project_archived"

    chapter_response = await _create_chapter(client, project_id, volume_id, "第一章")
    assert chapter_response.status_code == 409
    assert chapter_response.json()["code"] == "project_archived"

    outline_response = await _publish_outline(client, project_id, _outline_plans())
    assert outline_response.status_code == 409
    assert outline_response.json()["code"] == "project_archived"


async def test_an_archived_project_refuses_title_edits_reorders_and_moves(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Archive is the only removal path and rejects every edit (ADR-0002):
    not just creations — title updates, reorders, and moves too."""
    project_id = (await create_project())["id"]
    created = await _create_volume(client, project_id, "第一卷")
    volume_id = created.json()["id"]
    chapter_id = (await _create_chapter(client, project_id, volume_id, "第一章")).json()["id"]
    other_volume_id = (await _create_volume(client, project_id, "第二卷")).json()["id"]

    archive = await client.post(f"/api/v1/projects/{project_id}/archive", headers={"If-Match": "*"})
    assert archive.status_code == 200

    renamed = await client.patch(
        f"/api/v1/volumes/{volume_id}",
        json={"title": "第一卷 改"},
        headers={"If-Match": "*"},
    )
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "project_archived"

    renamed_chapter = await client.patch(
        f"/api/v1/chapters/{chapter_id}",
        json={"title": "第一章 改"},
        headers={"If-Match": "*"},
    )
    assert renamed_chapter.status_code == 409
    assert renamed_chapter.json()["code"] == "project_archived"

    reordered = await client.post(
        f"/api/v1/projects/{project_id}/volumes/reorder",
        json={"ordered_volume_ids": [other_volume_id, volume_id]},
    )
    assert reordered.status_code == 409
    assert reordered.json()["code"] == "project_archived"

    moved = await client.post(
        f"/api/v1/chapters/{chapter_id}/move",
        json={"to_volume_id": other_volume_id},
    )
    assert moved.status_code == 409
    assert moved.json()["code"] == "project_archived"


async def test_reordering_advances_the_volume_revision_so_stale_etags_are_refused(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Every mutation advances the revision (the projects.py precedent): a
    title PATCH holding an ETag from before a reorder is a 412, so the two
    writers cannot silently interleave."""
    project_id = (await create_project())["id"]
    created = await _create_volume(client, project_id, "第一卷")
    volume_id = created.json()["id"]
    etag = created.headers["ETag"]
    other_id = (await _create_volume(client, project_id, "第二卷")).json()["id"]

    reordered = await client.post(
        f"/api/v1/projects/{project_id}/volumes/reorder",
        json={"ordered_volume_ids": [other_id, volume_id]},
    )
    assert reordered.status_code == 200

    stale = await client.patch(
        f"/api/v1/volumes/{volume_id}",
        json={"title": "第一卷 改"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412


async def test_a_volume_and_a_chapter_are_fetchable_with_their_etags(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """A client that loses an ETag can re-fetch the current version — the
    optimistic-concurrency loop needs the read side too (ADR-0008)."""
    project_id = (await create_project())["id"]
    volume = await _create_volume(client, project_id, "第一卷")
    volume_id = volume.json()["id"]
    chapter = await _create_chapter(client, project_id, volume_id, "第一章")

    fetched_volume = await client.get(f"/api/v1/volumes/{volume_id}")
    assert fetched_volume.status_code == 200
    assert fetched_volume.json()["title"] == "第一卷"
    assert fetched_volume.headers["ETag"] == volume.headers["ETag"]

    fetched_chapter = await client.get(f"/api/v1/chapters/{chapter.json()['id']}")
    assert fetched_chapter.status_code == 200
    assert fetched_chapter.headers["ETag"] == chapter.headers["ETag"]

    missing = await client.get("/api/v1/volumes/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["code"] == "volume_not_found"
