"""Task API compatibility tests."""

from httpx import AsyncClient


async def test_create_and_list_task(client: AsyncClient) -> None:
    agent = await client.post(
        "/registry/agents",
        json={"name": "task-agent", "version": "1.0.0", "author": "cap-team"},
    )
    agent_id = agent.json()["id"]
    await client.post("/heartbeat", json={"agent_id": agent_id, "health_status": "HEALTHY"})
    payload = {
        "name": "Prepare controlled job",
        "task_type": "platform.example",
        "input": {"target": "authorized-example"},
    }
    created = await client.post("/tasks", json=payload)
    listed = await client.get("/tasks")
    assert created.status_code == 201
    assert created.json()["status"] == "QUEUED"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["input"]["target"] == "authorized-example"
