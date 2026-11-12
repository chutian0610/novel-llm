"""Model configuration at the system black-box seam (ADR-0006/0008): provider
credentials and Model Profiles are separate database objects, and the API
never returns a complete key. Seeds carry the default mock configuration
only — no sample data.

Everything is observed through HTTP resources; nothing queries tables."""

import uuid
from collections.abc import Awaitable, Callable

import httpx

CreateProject = Callable[..., Awaitable[dict]]


async def _create_credential(
    client: httpx.AsyncClient,
    name: str,
    *,
    provider: str = "openai-compatible",
    api_key: str = "",
) -> httpx.Response:
    return await client.post(
        "/api/v1/model-credentials",
        json={"name": name, "provider": provider, "api_key": api_key},
        headers={"Idempotency-Key": f"credential-{uuid.uuid4()}"},
    )


async def _create_profile(
    client: httpx.AsyncClient, name: str, credential_id: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/model-profiles",
        json={
            "name": name,
            "model": "some-provider/writer-v1",
            "credential_id": credential_id,
        },
        headers={"Idempotency-Key": f"profile-{uuid.uuid4()}"},
    )


async def test_a_credential_is_created_but_its_key_is_never_echoed(
    client: httpx.AsyncClient,
) -> None:
    """ADR-0006: credentials are plaintext local rows, and the API never
    returns a complete key — not on the create response, not on any read."""
    api_key = "sk-local-secret-do-not-echo-9f3a"
    created = await _create_credential(client, "主力凭据", api_key=api_key)
    assert created.status_code == 201, created.text

    body = created.json()
    assert api_key not in created.text
    assert body["api_key_hint"] == f"…{api_key[-4:]}"  # last four, nothing more
    assert created.headers["Location"] == f"/api/v1/model-credentials/{body['id']}"

    fetched = await client.get(f"/api/v1/model-credentials/{body['id']}")
    assert fetched.status_code == 200
    assert api_key not in fetched.text
    assert fetched.json()["api_key_hint"] == f"…{api_key[-4:]}"

    listed = await client.get("/api/v1/model-credentials")
    assert api_key not in listed.text
    names = [item["name"] for item in listed.json()["items"]]
    assert "主力凭据" in names
    # The seeded default mock configuration exists (ADR-0002: default mock,
    # model, and job configuration only — never sample data).
    assert any(item["provider"] == "mock" for item in listed.json()["items"])


async def test_a_credential_without_a_key_masks_as_none(
    client: httpx.AsyncClient,
) -> None:
    """The mock provider needs no key: an empty key is honest — present as
    null, not as a fake hint."""
    created = await _create_credential(client, "本地 Mock", provider="mock", api_key="")
    assert created.status_code == 201
    assert created.json()["api_key_hint"] is None


async def test_a_replayed_credential_create_returns_the_original(
    client: httpx.AsyncClient,
) -> None:
    key = f"credential-{uuid.uuid4()}"
    first = await client.post(
        "/api/v1/model-credentials",
        json={"name": "重放凭据", "provider": "mock", "api_key": ""},
        headers={"Idempotency-Key": key},
    )
    replay = await client.post(
        "/api/v1/model-credentials",
        json={"name": "重放凭据", "provider": "mock", "api_key": ""},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]


async def test_a_model_profile_references_a_credential_and_stays_separate(
    client: httpx.AsyncClient,
) -> None:
    """Model Profiles and provider credentials are separate objects (ADR-0006):
    the profile references the credential, and no response ever carries key
    material. An unknown credential is a stable 404 (ADR-0009)."""
    credential_id = (await _create_credential(client, "配置用凭据")).json()["id"]

    created = await _create_profile(client, "writer-1", credential_id)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["credential_id"] == credential_id
    assert "api_key" not in created.text.replace("api_key_hint", "")
    assert created.headers["Location"] == f"/api/v1/model-profiles/{body['id']}"

    unknown = await _create_profile(client, "writer-2", "00000000-0000-0000-0000-000000000000")
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "credential_not_found"

    # The seeded default profile points at the seeded mock credential.
    listed = await client.get("/api/v1/model-profiles")
    seeded = [item for item in listed.json()["items"] if item["model"].startswith("fake/")]
    assert len(seeded) == 1


async def test_credential_and_profile_updates_flow_through_if_match(
    client: httpx.AsyncClient,
) -> None:
    """Mutable config objects carry ETag/If-Match (ADR-0008); rotating a key
    keeps it masked; a stale If-Match is a 412."""
    created = await _create_credential(client, "轮换凭据", api_key="sk-first-key-aaaa")
    credential_id = created.json()["id"]

    rotated = await client.patch(
        f"/api/v1/model-credentials/{credential_id}",
        json={"api_key": "sk-second-key-bbbb"},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert rotated.status_code == 200, rotated.text
    assert "sk-second-key-bbbb" not in rotated.text
    assert rotated.json()["api_key_hint"] == "…bbbb"

    stale = await client.patch(
        f"/api/v1/model-credentials/{credential_id}",
        json={"api_key": "sk-third-key-cccc"},
        headers={"If-Match": created.headers["ETag"]},
    )
    assert stale.status_code == 412

    profile_id = (await _create_profile(client, "轮换配置", credential_id)).json()["id"]
    tuned = await client.patch(
        f"/api/v1/model-profiles/{profile_id}",
        json={"temperature": 0.2},
        headers={"If-Match": "*"},
    )
    assert tuned.status_code == 200, tuned.text
    assert tuned.json()["sampling"]["temperature"] == 0.2
