"""Task dispatch and lifecycle API tests."""

from httpx import AsyncClient


async def _online_agent(client: AsyncClient) -> str:
    created = await client.post(
        "/registry/agents",
        json={
            "name": "dispatch-agent",
            "version": "1.0.0",
            "author": "cap-team",
            "permissions": ["task:execute"],
        },
    )
    agent_id = created.json()["id"]
    heartbeat = await client.post(
        "/heartbeat",
        json={"agent_id": agent_id, "health_status": "HEALTHY"},
    )
    assert heartbeat.status_code == 200
    return agent_id


async def test_task_dispatches_to_eligible_online_agent(client: AsyncClient) -> None:
    agent_id = await _online_agent(client)
    created = await client.post(
        "/tasks",
        json={
            "name": "dispatch task",
            "task_type": "platform.example",
            "required_permissions": ["task:execute"],
            "target_agent_id": agent_id,
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "QUEUED"

    task = await client.get(f"/tasks/{created.json()['id']}")
    assert task.status_code == 200
    assert task.json()["target_agent_id"] == agent_id


async def test_task_rejects_unavailable_agent(client: AsyncClient) -> None:
    created = await client.post(
        "/tasks",
        json={"name": "safe reject", "task_type": "platform.example"},
    )
    assert created.status_code == 409
    assert created.json()["error"]["code"] == "REGISTRY_ERROR"
