"""The Story Bible at the system black-box seam (ADR-0004): an editable
Bible Draft of typed Bible Entries, published as immutable Published Bible
Versions. Identity is split from content — stable identities and aliases
persist across versions while each version freezes its entries. The
generation-facing reference resolves only to published versions, never a
draft.

Everything is observed through HTTP resources; nothing queries tables."""

import uuid
from collections.abc import Awaitable, Callable

import httpx

CreateProject = Callable[..., Awaitable[dict]]


async def _create_identity(
    client: httpx.AsyncClient, project_id: str, name: str, aliases: list[str] | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/bible/identities",
        json={"name": name, "aliases": aliases or []},
        headers={"Idempotency-Key": f"identity-{uuid.uuid4()}"},
    )


async def _create_entry(
    client: httpx.AsyncClient,
    project_id: str,
    identity_id: str,
    entry_type: str,
    detail: dict,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/bible/entries",
        json={"identity_id": identity_id, "entry_type": entry_type, "detail": detail},
        headers={"Idempotency-Key": f"entry-{uuid.uuid4()}"},
    )


async def _draft(client: httpx.AsyncClient, project_id: str) -> dict:
    response = await client.get(f"/api/v1/projects/{project_id}/bible/draft")
    assert response.status_code == 200, response.text
    return response.json()


def _identity_names(draft: dict) -> list[str]:
    return [identity["name"] for identity in draft["identities"]]


def _draft_entry_types(draft: dict) -> list[str]:
    return [entry["entry_type"] for entry in draft["entries"]]


async def test_the_draft_holds_typed_entries_of_every_first_version_type(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """All nine first-version entry types (ADR-0004) edit on the draft with
    typed details."""
    project_id = (await create_project())["id"]
    lin_id = (await _create_identity(client, project_id, "林灯", ["阿灯"])).json()["id"]
    chen_id = (await _create_identity(client, project_id, "陈守一")).json()["id"]
    await _create_identity(client, project_id, "落灯山")

    typed_details = {
        "character": {"description": "执灯的少年，怕黑却与灯同行。", "role": "主角"},
        "relationship": {
            "from_identity": lin_id,
            "to_identity": chen_id,
            "description": "师徒，亦是灯火的交接。",
        },
        "location": {"description": "黔地山中，灯楼遗址所在。"},
        "organization": {"description": "守灯人一脉，百年前离散。"},
        "item": {"description": "一盏百年不熄的青铜灯。"},
        "rule": {"description": "灯在人在：灯灭之处，记忆随之熄灭。"},
        "event": {
            "description": "灯楼大火",
            "narrative_time": "百年前",
            "disclosure_order": "第三章才揭示",
        },
        "style": {"description": "志怪语感，短句克制。"},
        "open_question": {"question": "灯芯的裂痕是谁刻的？"},
    }
    for entry_type, detail in typed_details.items():
        owner = lin_id if entry_type != "relationship" else chen_id
        response = await _create_entry(client, project_id, owner, entry_type, detail)
        assert response.status_code == 201, f"{entry_type}: {response.text}"

    draft = await _draft(client, project_id)
    assert _identity_names(draft) == ["林灯", "陈守一", "落灯山"]
    assert sorted(_draft_entry_types(draft)) == sorted(typed_details)

    # The event entry keeps Narrative Time and Disclosure Order apart (the
    # schema distinguishes when it happened from when the reader learns it).
    event_entry = next(entry for entry in draft["entries"] if entry["entry_type"] == "event")
    assert event_entry["detail"]["narrative_time"] == "百年前"
    assert event_entry["detail"]["disclosure_order"] == "第三章才揭示"


async def test_a_relationship_entry_references_identities_of_the_same_project(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """References are validated transactionally (ADR-0009): an unknown or
    cross-Project identity is a stable 404 problem, and the entry never
    lands."""
    project_id = (await create_project())["id"]
    lin_id = (await _create_identity(client, project_id, "林灯")).json()["id"]

    unknown = await _create_entry(
        client,
        project_id,
        lin_id,
        "relationship",
        {
            "from_identity": "00000000-0000-0000-0000-000000000000",
            "to_identity": lin_id,
            "description": "不存在的关系",
        },
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "identity_not_found"
    assert (await _draft(client, project_id))["entries"] == []

    other_project_id = (await create_project())["id"]
    other_identity_id = (await _create_identity(client, other_project_id, "别家角色")).json()["id"]
    cross = await _create_entry(
        client,
        project_id,
        lin_id,
        "relationship",
        {
            "from_identity": lin_id,
            "to_identity": other_identity_id,
            "description": "跨项目的关系",
        },
    )
    assert cross.status_code == 404
    assert cross.json()["code"] == "identity_not_found"
    assert (await _draft(client, project_id))["entries"] == []


async def test_an_entry_detail_that_breaks_its_type_contract_is_rejected(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Typed contracts (ADR-0008): an unknown key or a wrong shape in a
    detail is a malformed payload, not a silently unfired entry."""
    project_id = (await create_project())["id"]
    lin_id = (await _create_identity(client, project_id, "林灯")).json()["id"]

    typo = await _create_entry(
        client, project_id, lin_id, "character", {"description": "…", "unheard": True}
    )
    assert typo.status_code == 400
    assert typo.json()["code"] == "bible_entry_payload_invalid"

    wrong_type = await _create_entry(
        client, project_id, lin_id, "open_question", {"description": "这不是问题字段"}
    )
    assert wrong_type.status_code == 400
    assert wrong_type.json()["code"] == "bible_entry_payload_invalid"

    unknown_type = await _create_entry(
        client, project_id, lin_id, "prophecy", {"description": "不在封闭集里"}
    )
    assert unknown_type.status_code in (400, 422)


async def test_publishing_freezes_a_version_and_aliases_persist_across_versions(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """The heart of the identity/content split: entries freeze per version,
    while identity names and aliases persist — a draft alias edit flows into
    version 2, and version 1 keeps exactly what it froze."""
    project_id = (await create_project())["id"]
    created = await _create_identity(client, project_id, "林灯", ["阿灯"])
    identity_id = created.json()["id"]
    await _create_entry(
        client,
        project_id,
        identity_id,
        "character",
        {"description": "执灯的少年。", "role": "主角"},
    )

    first = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": f"publish-{uuid.uuid4()}"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["version_number"] == 1
    frozen_v1 = first.json()["entries"]
    # The snapshot is self-contained: every identity freezes (name + aliases
    # as of now), each entry keeps its id for relationship resolution.
    assert frozen_v1 == {
        "identities": [{"identity_id": identity_id, "name": "林灯", "aliases": ["阿灯"]}],
        "entries": [
            {
                "entry_id": frozen_v1["entries"][0]["entry_id"],
                "identity_id": identity_id,
                "entry_type": "character",
                "detail": {"description": "执灯的少年。", "role": "主角"},
            }
        ],
    }

    # The alias edit lands on the draft's identity — never on version 1.
    updated = await client.patch(
        f"/api/v1/projects/{project_id}/bible/identities/{identity_id}",
        json={"name": "林灯", "aliases": ["阿灯", "执灯人"]},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["aliases"] == ["阿灯", "执灯人"]
    still_v1 = await client.get(f"/api/v1/projects/{project_id}/bible/versions/1")
    assert still_v1.json()["entries"]["entries"][0]["detail"] == {
        "description": "执灯的少年。",
        "role": "主角",
    }
    assert still_v1.json()["entries"]["identities"][0]["aliases"] == ["阿灯"]

    # The next publication carries the persisted identity forward.
    second = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": f"publish-{uuid.uuid4()}"},
    )
    assert second.status_code == 201
    assert second.json()["version_number"] == 2
    assert second.json()["entries"]["identities"][0]["aliases"] == ["阿灯", "执灯人"]

    # Version 1 is still exactly what it froze.
    refrozen_v1 = await client.get(f"/api/v1/projects/{project_id}/bible/versions/1")
    assert refrozen_v1.json()["entries"] == frozen_v1


async def test_the_generation_facing_reference_resolves_only_published_versions(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Generation reads the Bible through the published version — the draft
    never answers that call, and archiving an entry on the draft does not
    rewrite a frozen version."""
    project_id = (await create_project())["id"]
    identity_id = (await _create_identity(client, project_id, "林灯")).json()["id"]
    created_entry = await _create_entry(
        client, project_id, identity_id, "character", {"description": "执灯的少年。"}
    )

    before = await client.get(f"/api/v1/projects/{project_id}/bible")
    assert before.status_code == 404
    assert before.json()["code"] == "bible_not_found"

    published = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": f"publish-{uuid.uuid4()}"},
    )
    assert published.status_code == 201

    generation_view = await client.get(f"/api/v1/projects/{project_id}/bible")
    assert generation_view.status_code == 200
    assert generation_view.json()["version_number"] == 1
    assert len(generation_view.json()["entries"]["entries"]) == 1

    archived = await client.post(
        f"/api/v1/bible/entries/{created_entry.json()['id']}/archive",
        headers={"If-Match": created_entry.headers["ETag"]},
    )
    assert archived.status_code == 200, archived.text
    draft = await _draft(client, project_id)
    assert draft["entries"] == []  # gone from the working set
    still_there = await client.get(f"/api/v1/projects/{project_id}/bible")
    assert len(still_there.json()["entries"]["entries"]) == 1  # frozen forever


async def test_a_replayed_publish_with_the_same_key_returns_the_original_version(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    project_id = (await create_project())["id"]
    key = f"publish-{uuid.uuid4()}"

    first = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": key},
    )
    replay = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201
    assert replay.status_code == 200, replay.text
    assert replay.json()["version_number"] == 1
    assert replay.json()["id"] == first.json()["id"]

    versions = await client.get(f"/api/v1/projects/{project_id}/bible/versions/2")
    assert versions.status_code == 404  # the replay created no duplicate


async def test_an_archived_project_refuses_bible_edits_and_publication(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Archive rejects every edit (ADR-0002): identity creation, entry
    creation, identity updates, and publication alike."""
    project_id = (await create_project())["id"]
    created = await _create_identity(client, project_id, "林灯")
    identity_id = created.json()["id"]

    archive = await client.post(f"/api/v1/projects/{project_id}/archive", headers={"If-Match": "*"})
    assert archive.status_code == 200

    new_identity = await _create_identity(client, project_id, "陈守一")
    assert new_identity.status_code == 409
    assert new_identity.json()["code"] == "project_archived"

    new_entry = await _create_entry(
        client, project_id, identity_id, "character", {"description": "…"}
    )
    assert new_entry.status_code == 409
    assert new_entry.json()["code"] == "project_archived"

    renamed = await client.patch(
        f"/api/v1/projects/{project_id}/bible/identities/{identity_id}",
        json={"name": "林灯改", "aliases": []},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "project_archived"

    published = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": f"publish-{uuid.uuid4()}"},
    )
    assert published.status_code == 409
    assert published.json()["code"] == "project_archived"


async def test_identity_and_entry_updates_flow_through_if_match(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """Optimistic concurrency (ADR-0008): a stale If-Match is a 412, a
    missing one a 428; the entry's type is fixed — only its detail edits."""
    project_id = (await create_project())["id"]
    created_identity = await _create_identity(client, project_id, "林灯")
    identity_id = created_identity.json()["id"]
    created_entry = await _create_entry(
        client, project_id, identity_id, "character", {"description": "旧描述"}
    )

    renamed = await client.patch(
        f"/api/v1/projects/{project_id}/bible/identities/{identity_id}",
        json={"name": "林灯", "aliases": ["阿灯"]},
        headers={"If-Match": created_identity.headers["ETag"]},
    )
    assert renamed.status_code == 200
    assert renamed.json()["aliases"] == ["阿灯"]

    stale = await client.patch(
        f"/api/v1/projects/{project_id}/bible/identities/{identity_id}",
        json={"name": "林灯", "aliases": []},
        headers={"If-Match": created_identity.headers["ETag"]},
    )
    assert stale.status_code == 412

    missing = await client.patch(
        f"/api/v1/projects/{project_id}/bible/identities/{identity_id}",
        json={"name": "林灯", "aliases": []},
    )
    assert missing.status_code == 428

    updated_entry = await client.patch(
        f"/api/v1/bible/entries/{created_entry.json()['id']}",
        json={"detail": {"description": "新描述"}},
        headers={"If-Match": created_entry.headers["ETag"]},
    )
    assert updated_entry.status_code == 200
    assert updated_entry.json()["entry_type"] == "character"  # the type is fixed
    # The typed contract's canonical form: optional fields carry their defaults.
    assert updated_entry.json()["detail"] == {"description": "新描述", "role": None}

    retyped = await client.patch(
        f"/api/v1/bible/entries/{created_entry.json()['id']}",
        json={"detail": {"question": "换成问题也没用"}},
        headers={"If-Match": updated_entry.headers["ETag"]},
    )
    assert retyped.status_code == 400
    assert retyped.json()["code"] == "bible_entry_payload_invalid"


async def test_an_identity_update_from_another_project_is_not_found(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """The identity is addressed inside its Project (ADR-0009): the same id
    under another Project's path simply does not exist."""
    project_id = (await create_project())["id"]
    other_project_id = (await create_project())["id"]
    created = await _create_identity(client, project_id, "林灯")

    response = await client.patch(
        f"/api/v1/projects/{other_project_id}/bible/identities/{created.json()['id']}",
        json={"name": "林灯", "aliases": []},
        headers={"If-Match": "*"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "identity_not_found"


async def test_a_duplicate_identity_name_is_a_stable_conflict(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """(project_id, name) is unique (ADR-0002: PostgreSQL enforces the
    boundary) — the domain refuses the duplicate with a stable 409 problem,
    never an integrity error."""
    project_id = (await create_project())["id"]
    await _create_identity(client, project_id, "林灯")

    duplicate = await _create_identity(client, project_id, "林灯")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "identity_name_taken"


async def test_publishing_an_empty_draft_yields_an_empty_baseline_version(
    client: httpx.AsyncClient, create_project: CreateProject
) -> None:
    """An empty draft publishes as an empty baseline version — legal by
    intent (a Project may start published-empty), pinned here so the
    behavior is a decision, not an accident."""
    project_id = (await create_project())["id"]

    published = await client.post(
        f"/api/v1/projects/{project_id}/bible/publish",
        headers={"Idempotency-Key": f"publish-{uuid.uuid4()}"},
    )
    assert published.status_code == 201
    assert published.json()["version_number"] == 1
    assert published.json()["entries"] == {"identities": [], "entries": []}
