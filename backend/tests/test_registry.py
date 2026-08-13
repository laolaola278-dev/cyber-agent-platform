"""Registry API tests."""

from httpx import AsyncClient


async def test_register_update_and_delete_agent(client: AsyncClient) -> None:
    payload = {
        "name": "registry-agent",
        "version": "1.0.0",
        "author": "cap-team",
        "permissions": ["task:execute"],
        "runtime": {"entrypoint": "example:Agent"},
    }
    created = await client.post("/registry/agents", json=payload)
    assert created.status_code == 201
    agent_id = created.json()["id"]

    updated = await client.put(
        f"/registry/agents/{agent_id}",
        json={"status": "STARTING", "description": "updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "STARTING"

    deleted = await client.delete(f"/registry/agents/{agent_id}")
    assert deleted.status_code == 204
    listed = await client.get("/registry/agents")
    assert listed.json() == {"items": [], "page": 1, "page_size": 100, "total": 0}


async def test_register_tool_and_disable(client: AsyncClient) -> None:
    created = await client.post(
        "/registry/tools",
        json={
            "name": "abstract-tool",
            "version": "1.0.0",
            "tool_type": "adapter",
            "required_permissions": ["tool:execute"],
        },
    )
    assert created.status_code == 201
    disabled = await client.post(f"/registry/tools/{created.json()['id']}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"


async def test_registry_status_uses_request_database(client: AsyncClient) -> None:
    response = await client.get("/registry/status")
    assert response.status_code == 200
    assert response.json() == {
        "agents_total": 0,
        "agents_online": 0,
        "tools_enabled": 0,
    }
