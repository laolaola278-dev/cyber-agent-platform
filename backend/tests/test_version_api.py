"""Version history and uniform pagination API tests."""

from httpx import AsyncClient


async def test_agent_versions_and_page_response(client: AsyncClient) -> None:
    first = await client.post(
        "/registry/agents",
        json={"name": "versioned-agent", "version": "1.0.0", "author": "test"},
    )
    second = await client.post(
        "/registry/agents",
        json={"name": "versioned-agent", "version": "1.1.0", "author": "test"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    versions = await client.get(
        f"/registry/agents/{first.json()['id']}/versions?page=1&page_size=1"
    )
    assert versions.status_code == 200
    assert versions.json()["total"] == 2
    assert versions.json()["page_size"] == 1
    assert versions.json()["items"][0]["version"] == "1.1.0"

    agents = await client.get("/registry/agents?page=1&page_size=1")
    assert agents.json()["total"] == 1
    assert len(agents.json()["items"]) == 1


async def test_tool_versions_endpoint(client: AsyncClient) -> None:
    first = await client.post(
        "/registry/tools",
        json={"name": "versioned-tool", "version": "1.0.0", "tool_type": "adapter"},
    )
    await client.post(
        "/registry/tools",
        json={"name": "versioned-tool", "version": "2.0.0", "tool_type": "adapter"},
    )

    versions = await client.get(f"/registry/tools/{first.json()['id']}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()["items"]] == ["2.0.0", "1.0.0"]
