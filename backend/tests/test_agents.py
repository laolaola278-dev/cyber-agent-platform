"""Backward-compatible Agent API tests."""

from httpx import AsyncClient


async def test_register_and_list_agent(client: AsyncClient) -> None:
    payload = {
        "name": "example-agent",
        "version": "1.0.0",
        "description": "Phase 1 registry fixture",
        "author": "cap-team",
        "permissions": ["task:read"],
        "tools": ["example-tool"],
    }
    created = await client.post("/agents", json=payload)
    listed = await client.get("/agents")
    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["items"][0]["version"] == "1.0.0"


async def test_reject_duplicate_agent_version(client: AsyncClient) -> None:
    payload = {"name": "duplicate", "version": "1.0.0", "author": "cap-team"}
    assert (await client.post("/agents", json=payload)).status_code == 201
    duplicate = await client.post("/agents", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REGISTRY_ERROR"
