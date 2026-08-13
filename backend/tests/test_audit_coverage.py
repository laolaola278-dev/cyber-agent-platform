"""Audit coverage for API validation and dispatcher failures."""

from types import SimpleNamespace

from httpx import AsyncClient
from sqlalchemy import select

from app.api.errors import unhandled_exception_handler
from app.models import AuditLog
from tests.conftest import TestSessionFactory


async def test_validation_error_is_audited(client: AsyncClient) -> None:
    response = await client.post("/registry/agents", json={"name": ""})
    assert response.status_code == 422

    async with TestSessionFactory() as session:
        records = (await session.scalars(select(AuditLog))).all()
        assert any(record.action == "ValidationError" for record in records)


async def test_dispatch_failure_is_audited(client: AsyncClient) -> None:
    response = await client.post("/tasks", json={"name": "undispatchable", "task_type": "test"})
    assert response.status_code == 409

    async with TestSessionFactory() as session:
        actions = set((await session.scalars(select(AuditLog.action))).all())
        assert {"TaskCreated", "DispatchFailed"}.issubset(actions)


async def test_unhandled_exception_is_audited() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_session_factory=TestSessionFactory)),
        state=SimpleNamespace(request_id="trace-unhandled"),
        method="GET",
        url=SimpleNamespace(path="/raise-unhandled"),
        path_params={},
    )
    response = await unhandled_exception_handler(request, RuntimeError("controlled test failure"))
    assert response.status_code == 500

    async with TestSessionFactory() as session:
        records = (await session.scalars(select(AuditLog))).all()
        assert any(record.action == "UnhandledException" for record in records)
