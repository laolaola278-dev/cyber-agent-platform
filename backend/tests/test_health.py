"""Health API tests."""

from pathlib import Path

from httpx import AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Cyber Agent Platform",
        "version": (PROJECT_ROOT / "VERSION").read_text("utf-8").strip(),
    }
    assert response.headers["X-Request-ID"]
