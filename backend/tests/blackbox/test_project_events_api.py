"""Black-box (thin usage) tests at the HTTP boundary: the event outbox and its
SSE delivery (ADR-0002/0003: atomic commit, append-only, versioned envelope
with cursor, at-least-once replay).

These drive the ASGI app against real PostgreSQL; no Worker or workflow runs.
"""

import asyncio
import json
from uuid import uuid4

import httpx


async def create_project(client: httpx.AsyncClient, key: str, title: str) -> httpx.Response:
    return await client.post(
        "/api/v1/projects", json={"title": title}, headers={"Idempotency-Key": key}
    )


async def test_creating_a_project_commits_its_event_atomically(client: httpx.AsyncClient):
    created = await create_project(client, "evt-001", "长夜灯")
    assert created.status_code == 201
    project_id = created.json()["id"]

    response = await client.get(f"/api/v1/projects/{project_id}/events")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1

    envelope = body["items"][0]
    assert envelope["envelope_version"] == 1
    assert envelope["event_type"] == "project.created"
    assert envelope["event_id"] == envelope["cursor"]
    assert envelope["occurred_at"]
    assert envelope["data"]["title"] == "长夜灯"
    assert envelope["data"]["project_id"] == project_id
    # next_cursor is pagination ("more to fetch now"); the resume position for
    # reconnect is each envelope's own cursor.
    assert body["next_cursor"] is None


async def test_reading_a_project_commits_no_event(client: httpx.AsyncClient):
    created = await create_project(client, "evt-002", "静默之书")
    project_id = created.json()["id"]

    await client.get(f"/api/v1/projects/{project_id}")
    await client.get("/api/v1/projects")

    response = await client.get(f"/api/v1/projects/{project_id}/events")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1  # still only project.created


async def test_replaying_with_the_cursor_returns_exactly_the_missed_events(
    client: httpx.AsyncClient,
):
    created = await create_project(client, "evt-003", "断线之书")
    project_id = created.json()["id"]

    first_page = await client.get(f"/api/v1/projects/{project_id}/events")
    envelopes = first_page.json()["items"]
    assert [envelope["event_type"] for envelope in envelopes] == ["project.created"]
    resume_cursor = envelopes[-1]["cursor"]

    # While the consumer is away: one edit and one archive.
    detail = await client.get(f"/api/v1/projects/{project_id}")
    patched = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"title": "断线之书(修)"},
        headers={"If-Match": detail.headers["etag"]},
    )
    assert patched.status_code == 200
    archived = await client.post(
        f"/api/v1/projects/{project_id}/archive",
        headers={"If-Match": patched.headers["etag"]},
    )
    assert archived.status_code == 200

    # Reconnect with the resume cursor: exactly the two missed events, in
    # delivery order.
    replay = await client.get(
        f"/api/v1/projects/{project_id}/events", params={"after": resume_cursor}
    )
    assert replay.status_code == 200
    missed = replay.json()["items"]
    assert [envelope["event_type"] for envelope in missed] == [
        "project.updated",
        "project.archived",
    ]
    # And a second reconnect with the newest cursor has nothing to add.
    again = await client.get(
        f"/api/v1/projects/{project_id}/events",
        params={"after": missed[-1]["cursor"]},
    )
    assert again.json()["items"] == []


async def test_idempotent_create_replay_commits_no_event(client: httpx.AsyncClient):
    headers = {"Idempotency-Key": "evt-004"}
    first = await create_project(client, "evt-004", "重放之书")
    assert first.status_code == 201
    project_id = first.json()["id"]

    replay = await client.post("/api/v1/projects", json={"title": "重放之书"}, headers=headers)
    assert replay.status_code == 200

    events = await client.get(f"/api/v1/projects/{project_id}/events")
    assert len(events.json()["items"]) == 1  # the create, not the replay


async def test_events_of_unknown_project_are_problem_details(client: httpx.AsyncClient):
    response = await client.get(f"/api/v1/projects/{uuid4()}/events")
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"

    stream = await client.get(f"/api/v1/projects/{uuid4()}/events/stream")
    assert stream.status_code == 404
    assert stream.json()["code"] == "project_not_found"


async def test_malformed_event_cursor_is_problem_details(client: httpx.AsyncClient):
    created = await create_project(client, "evt-bad-cursor", "坏游标之书")
    project_id = created.json()["id"]

    replay = await client.get(f"/api/v1/projects/{project_id}/events", params={"after": "junk"})
    assert replay.status_code == 400
    assert replay.json()["code"] == "invalid_cursor"

    stream = await client.get(
        f"/api/v1/projects/{project_id}/events/stream", params={"cursor": "junk"}
    )
    assert stream.status_code == 400
    assert stream.json()["code"] == "invalid_cursor"


async def test_stream_resumes_from_last_event_id_header(live_server: str):
    """A native EventSource auto-reconnect sends Last-Event-ID, not ?cursor=."""
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as client:
        created = await create_project(client, "sse-lei", "原生重连之书")
        project_id = created.json()["id"]

        page = await client.get(f"/api/v1/projects/{project_id}/events")
        last_event_id = page.json()["items"][-1]["event_id"]

        detail = await client.get(f"/api/v1/projects/{project_id}")
        patched = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"title": "原生重连之书(修)"},
            headers={"If-Match": detail.headers["etag"]},
        )
        assert patched.status_code == 200

        async with client.stream(
            "GET",
            f"/api/v1/projects/{project_id}/events/stream",
            headers={"Last-Event-ID": last_event_id, "accept": "text/event-stream"},
        ) as stream_response:
            assert stream_response.status_code == 200
            first_frame: dict | None = None
            async for line in stream_response.aiter_lines():
                if line.startswith("data: "):
                    first_frame = json.loads(line[len("data: ") :])
                    break
        assert first_frame is not None
        assert first_frame["event_type"] == "project.updated"


def _parse_sse_frames(raw: str) -> list[dict]:
    envelopes: list[dict] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                envelopes.append(json.loads(line[len("data: ") :]))
    return envelopes


async def test_sse_stream_replays_then_delivers_live(live_server: str):
    # SSE must be observed over a real listener (see conftest `live_server`).
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as client:
        created = await create_project(client, "sse-001", "直播之书")
        project_id = created.json()["id"]

        async def archive_when_seen() -> None:
            """Trigger the archive once the replayed create event is visible."""
            for _ in range(100):
                await asyncio.sleep(0.05)
                replay = await client.get(f"/api/v1/projects/{project_id}/events")
                if any(
                    envelope["event_type"] == "project.created"
                    for envelope in replay.json()["items"]
                ):
                    detail = await client.get(f"/api/v1/projects/{project_id}")
                    patched = await client.patch(
                        f"/api/v1/projects/{project_id}",
                        json={"title": "直播之书(修)"},
                        headers={"If-Match": detail.headers["etag"]},
                    )
                    assert patched.status_code == 200
                    await client.post(
                        f"/api/v1/projects/{project_id}/archive",
                        headers={"If-Match": patched.headers["etag"]},
                    )
                    return

        frames: list[dict] = []

        async with client.stream(
            "GET", f"/api/v1/projects/{project_id}/events/stream"
        ) as stream_response:
            assert stream_response.status_code == 200
            assert stream_response.headers["content-type"].startswith("text/event-stream")

            async def read_frames() -> None:
                async for line in stream_response.aiter_lines():
                    if line.startswith("data: "):
                        frames.append(json.loads(line[len("data: ") :]))
                        if frames[-1]["event_type"] == "project.archived":
                            return

            reader = asyncio.create_task(read_frames())
            archiver = asyncio.create_task(archive_when_seen())
            # Wait on the reader specifically: FIRST_COMPLETED would return as
            # soon as the trigger finishes, before the stream delivers the
            # archived frame.
            await asyncio.wait_for(reader, timeout=15)
            archiver.cancel()

    assert [envelope["event_type"] for envelope in frames][:1] == ["project.created"]
    assert frames[-1]["event_type"] == "project.archived"
    assert all(envelope["envelope_version"] == 1 for envelope in frames)
    # Delivery order is event id order (UUIDv7, time-ordered).
    ids = [envelope["event_id"] for envelope in frames]
    assert ids == sorted(ids)


async def test_sse_stream_with_cursor_skips_already_seen_events(live_server: str):
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as client:
        created = await create_project(client, "sse-002", "续传之书")
        project_id = created.json()["id"]

        page = await client.get(f"/api/v1/projects/{project_id}/events")
        resume_cursor = page.json()["items"][-1]["cursor"]

        # A new event lands while the consumer is away.
        detail = await client.get(f"/api/v1/projects/{project_id}")
        patched = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"title": "续传之书(修)"},
            headers={"If-Match": detail.headers["etag"]},
        )
        assert patched.status_code == 200

        # Reconnect with the cursor: the first delivered frame is the missed
        # update, not the already-seen create.
        async with client.stream(
            "GET",
            f"/api/v1/projects/{project_id}/events/stream",
            params={"cursor": resume_cursor},
        ) as stream_response:
            assert stream_response.status_code == 200
            first_frame: dict | None = None
            async for line in stream_response.aiter_lines():
                if line.startswith("data: "):
                    first_frame = json.loads(line[len("data: ") :])
                    break
        assert first_frame is not None
        assert first_frame["event_type"] == "project.updated"
