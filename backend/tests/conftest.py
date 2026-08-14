"""Shared test database and API client fixtures."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db_session
from app.main import create_app
from app.models import (  # noqa: F401
    Agent,
    AgentCapability,
    AuditLog,
    Capability,
    Knowledge,
    KnowledgeSource,
    KnowledgeVersion,
    Task,
    TaskExecution,
    Tool,
)

TEST_DATABASE_URL = "sqlite+aiosqlite://"

engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.state.audit_session_factory = TestSessionFactory
    app.state.secret_provider.put("zap-api-key", "test-zap-api-key")
    transport = ASGITransport(app=app)
    headers = {
        "X-CAP-User": "administrator",
        "X-CAP-Proxy-Secret": "change-me-proxy-secret",
    }
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
    ) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Phase 28.5-CI: strict certification mode
# ---------------------------------------------------------------------------
# CAP_CERTIFICATION_STRICT=1 turns environment-availability SKIPS on
# certification-critical tests into FAILURES. A certification job must never
# end green with "12 passed, 8 skipped" when a critical runtime was absent.
import os as _os
import shutil as _shutil

CERT_STRICT = _os.environ.get("CAP_CERTIFICATION_STRICT") == "1"


def _tool_available(name: str) -> bool:
    return _shutil.which(name) is not None


def docker_available() -> bool:
    try:
        import subprocess

        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=15,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def postgres_available(host: str = "127.0.0.1", port: int = 55432) -> bool:
    try:
        import socket

        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True
    except OSError:
        return False


def minio_available(host: str = "127.0.0.1", port: int = 9000) -> bool:
    try:
        import socket

        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True
    except OSError:
        return False


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Strict mode: fail certification tests whose skipif would skip them.

    Runs BEFORE fixture setup (tryfirst), so it inspects the skipif marker
    conditions directly and raises a plain failure. The previous hookwrapper
    implementation re-raised pytest.skip from the teardown, which pytest
    reports as PluggyTeardownRaisedWarning -- and under filterwarnings=error
    that warning turned every skipped test into an ERROR instead of a clean
    SKIP or FAIL.
    """
    if not CERT_STRICT:
        return
    if item.get_closest_marker("certification") is None:
        return
    for marker in item.iter_markers("skipif"):
        condition = marker.args[0] if marker.args else False
        if condition:
            reason = marker.kwargs.get("reason", "condition met")
            raise pytest.fail(
                f"CAP_CERTIFICATION_STRICT: critical certification test would "
                f"skip: {reason}",
                pytrace=False,
            )
