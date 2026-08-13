"""Agent heartbeat API tests."""

from httpx import AsyncClient


async def test_heartbeat_updates_agent_health_and_status(client: AsyncClient) -> None:
    created = await client.post(
        "/registry/agents",
        json={"name": "heartbeat-agent", "version": "1.0.0", "author": "cap-team"},
    )
    agent_id = created.json()["id"]

    response = await client.post(
        "/heartbeat",
        json={
            "agent_id": agent_id,
            "health_status": "HEALTHY",
            "details": {"uptime": 1},
        },
    )
    assert response.status_code == 200
    assert response.json()["health_status"] == "HEALTHY"
    assert response.json()["status"] == "ONLINE"
    assert response.json()["heartbeat_time"] is not None

    health = await client.get(f"/agents/{agent_id}/health")
    assert health.status_code == 200
    assert health.json()["health_status"] == "HEALTHY"
