"""Black-box (thin usage) tests at the HTTP boundary: Project creation, listing,
and archive (ADR-0008 contract: problem-details, stable error codes, cursor
pagination, idempotency keys, ETag/If-Match).

These drive the ASGI app against real PostgreSQL; no Worker or workflow runs.
"""

from uuid import uuid4

import httpx


async def test_create_project_returns_201_with_etag_and_location(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/projects",
        json={"title": "长夜灯", "description": "一部志怪长篇"},
        headers={"Idempotency-Key": "create-001"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "长夜灯"
    assert body["description"] == "一部志怪长篇"
    assert body["language"] == "zh-Hans"
    assert body["status"] == "active"
    assert body["created_at"] == body["updated_at"]
    assert response.headers["etag"]
    assert response.headers["location"] == f"/api/v1/projects/{body['id']}"


async def test_retried_create_with_same_idempotency_key_returns_original_project(
    client: httpx.AsyncClient,
):
    headers = {"Idempotency-Key": "create-replay-001"}
    first = await client.post("/api/v1/projects", json={"title": "山海旧闻"}, headers=headers)
    assert first.status_code == 201

    replay = await client.post(
        "/api/v1/projects",
        json={"title": "山海旧闻"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.headers["etag"] == first.headers["etag"]


async def test_same_idempotency_key_with_different_payload_conflicts(client: httpx.AsyncClient):
    headers = {"Idempotency-Key": "create-conflict-001"}
    first = await client.post("/api/v1/projects", json={"title": "雾都异闻"}, headers=headers)
    assert first.status_code == 201

    conflicting = await client.post(
        "/api/v1/projects",
        json={"title": "雾都异闻", "description": "不同的请求"},
        headers=headers,
    )
    assert conflicting.status_code == 409
    body = conflicting.json()
    assert body["code"] == "idempotency_key_conflict"
    assert conflicting.headers["content-type"] == "application/problem+json"


async def test_create_without_idempotency_key_is_rejected(client: httpx.AsyncClient):
    # The contract declares Idempotency-Key required (ADR-0008): the missing
    # header fails request validation as a 400 problem.
    response = await client.post("/api/v1/projects", json={"title": "无键之书"})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert response.headers["content-type"] == "application/problem+json"


async def test_get_project_returns_resource_with_matching_etag(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects",
        json={"title": "镜中城", "language": "zh-Hans"},
        headers={"Idempotency-Key": "get-001"},
    )
    project_id = created.json()["id"]

    response = await client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == project_id
    assert body["title"] == "镜中城"
    assert body["language"] == "zh-Hans"
    assert response.headers["etag"] == created.headers["etag"]


async def test_get_unknown_project_returns_problem_details(client: httpx.AsyncClient):
    response = await client.get(f"/api/v1/projects/{uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "project_not_found"
    assert body["status"] == 404
    assert body["title"] == "Project not found"
    assert body["type"].endswith("/problems/project_not_found")
    assert response.headers["content-type"] == "application/problem+json"


async def test_list_projects_walks_cursor_pages_newest_first(client: httpx.AsyncClient):
    titles = ["卷一之书", "卷二之书", "卷三之书", "卷四之书", "卷五之书"]
    for index, title in enumerate(titles):
        response = await client.post(
            "/api/v1/projects",
            json={"title": title},
            headers={"Idempotency-Key": f"list-{index:03d}"},
        )
        assert response.status_code == 201

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": "2"} if cursor is None else {"limit": "2", "cursor": cursor}
        response = await client.get("/api/v1/projects", params=params)
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) <= 2
        seen.extend(item["title"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert isinstance(cursor, str)

    assert pages == 3
    assert seen == list(reversed(titles))


async def test_list_projects_rejects_malformed_cursor(client: httpx.AsyncClient):
    response = await client.get("/api/v1/projects", params={"cursor": "not-a-cursor"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


async def test_update_project_with_if_match_returns_new_etag(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects", json={"title": "旧稿"}, headers={"Idempotency-Key": "patch-001"}
    )
    project_id = created.json()["id"]
    etag = created.headers["etag"]

    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"title": "新稿", "description": "改题之后"},
        headers={"If-Match": etag},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "新稿"
    assert body["description"] == "改题之后"
    assert body["updated_at"] > body["created_at"]
    assert response.headers["etag"] != etag

    fetched = await client.get(f"/api/v1/projects/{project_id}")
    assert fetched.json()["title"] == "新稿"
    assert fetched.headers["etag"] == response.headers["etag"]


async def test_update_without_if_match_requires_precondition(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects", json={"title": "无预备之书"}, headers={"Idempotency-Key": "patch-002"}
    )
    response = await client.patch(
        f"/api/v1/projects/{created.json()['id']}", json={"title": "改名"}
    )
    assert response.status_code == 428
    assert response.json()["code"] == "missing_if_match"


async def test_update_with_stale_if_match_conflicts(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects", json={"title": "并发之书"}, headers={"Idempotency-Key": "patch-003"}
    )
    project_id = created.json()["id"]
    stale_etag = created.headers["etag"]

    first = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"title": "第一个作者"},
        headers={"If-Match": stale_etag},
    )
    assert first.status_code == 200

    second = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"title": "第二个作者"},
        headers={"If-Match": stale_etag},
    )
    assert second.status_code == 412
    assert second.json()["code"] == "etag_mismatch"


async def test_update_unknown_project_returns_problem_details(client: httpx.AsyncClient):
    response = await client.patch(
        f"/api/v1/projects/{uuid4()}", json={"title": "不存在"}, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


async def test_archive_project_is_terminal_and_offers_no_delete(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects", json={"title": "封存之书"}, headers={"Idempotency-Key": "archive-001"}
    )
    project_id = created.json()["id"]
    etag = created.headers["etag"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/archive", headers={"If-Match": etag}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "archived"
    assert response.headers["etag"] != etag

    # Archive is the only removal path: there is no DELETE route at all.
    deleted = await client.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 405

    # And archive is terminal: edits are refused.
    edit = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"title": "试图复活"},
        headers={"If-Match": response.headers["etag"]},
    )
    assert edit.status_code == 409
    assert edit.json()["code"] == "project_archived"


async def test_archive_twice_conflicts(client: httpx.AsyncClient):
    created = await client.post(
        "/api/v1/projects",
        json={"title": "不可再封之书"},
        headers={"Idempotency-Key": "archive-002"},
    )
    project_id = created.json()["id"]
    etag = created.headers["etag"]

    first = await client.post(f"/api/v1/projects/{project_id}/archive", headers={"If-Match": etag})
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/projects/{project_id}/archive", headers={"If-Match": first.headers["etag"]}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "project_archived"


async def test_list_projects_filters_by_status(client: httpx.AsyncClient):
    for index, title in enumerate(["留存之书", "亦留存", "将封存"]):
        response = await client.post(
            "/api/v1/projects",
            json={"title": title},
            headers={"Idempotency-Key": f"filter-{index}"},
        )
        assert response.status_code == 201

    listing = await client.get("/api/v1/projects")
    newest = listing.json()["items"][0]
    assert newest["title"] == "将封存"
    detail = await client.get(f"/api/v1/projects/{newest['id']}")
    archived = await client.post(
        f"/api/v1/projects/{newest['id']}/archive",
        headers={"If-Match": detail.headers["etag"]},
    )
    assert archived.status_code == 200

    active = await client.get("/api/v1/projects", params={"status": "active"})
    assert [item["title"] for item in active.json()["items"]] == ["亦留存", "留存之书"]

    archived_list = await client.get("/api/v1/projects", params={"status": "archived"})
    assert [item["title"] for item in archived_list.json()["items"]] == ["将封存"]

    everything = await client.get("/api/v1/projects", params={"status": "all"})
    assert len(everything.json()["items"]) == 3
