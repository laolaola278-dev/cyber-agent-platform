"""Shared test database and API client fixtures.

Imports are deliberately split: the configuration pin below has to run before
any app module is imported, so isort is told to leave this file alone (see the
comment above _TEST_CONFIG) while E402 stays suppressed per line.
"""

# isort: skip_file

import os as _os
import shutil as _shutil

# Pin the configuration the suite asserts against BEFORE any app module is
# imported (Settings is lru_cached and app.main builds engines at import time).
#
# app.config.settings resolves ``env_file=".env"`` against the *current working
# directory*, and repository-root env vars outrank its defaults. So a developer
# who followed the documented docker-compose step -- which writes a .env holding
# a real RBAC_TRUSTED_PROXY_SECRET and a PostgreSQL DATABASE_URL -- silently
# reconfigured the test process: the client fixture's proxy header stopped
# matching the middleware and every authenticated API test failed with a bare
# 401, while /settings reported a database the tests were never using.
#
# Each value below is the application's own default, so this reproduces a clean
# checkout on any machine -- except DATABASE_URL, which is deliberately forced
# to the in-memory SQLite the fixtures build: a test process must never be able
# to dial the deployment database named in a .env.
#
# That pin is total, so DATABASE_URL is not available as an escape hatch either:
# a test that genuinely needs a real server reads a dedicated name and asserts
# its shape first (CAP_PG_TEST_DSN, CAP283_PG_DSN, CAP283_S3_ENDPOINT). The
# "authoritative PostgreSQL" heartbeat variant used to read DATABASE_URL, got
# the SQLite pinned here, and died on `no such table: workers` in the strict GA
# job (run 35429972509) -- see test_settings_dotenv_hermeticity, which now
# forbids that read pattern outright.
_TEST_CONFIG = {
    "APP_ENVIRONMENT": "development",
    "DEBUG": "false",
    "DATABASE_URL": "sqlite+aiosqlite://",
    "REDIS_URL": "redis://redis:6379/0",
    "SECRET_KEY": "change-me",
    "JWT_SECRET": "change-me-too",
    "RBAC_TRUSTED_PROXY_SECRET": "change-me-proxy-secret",
    "OBJECT_STORE_BACKEND": "local",
    "API_DOCS_ENABLED": "true",
    "TRACING_ENABLED": "true",
    # Integration credential. create_app() seeds MemorySecretProvider from it
    # since 41bbc49, so a developer's .env could provision ZAP inside the test
    # process and change what the incident plane does; the client fixture below
    # puts the test secret into the provider explicitly instead.
    "CAP_ZAP_API_KEY": "",
}
_os.environ.update(_TEST_CONFIG)

# Pinning a whitelist of keys is still whack-a-mole, and two leaks got through
# the list above: CAP_ZAP_API_KEY (a developer .env provisioned ZAP inside the
# incident-plane tests) and APP_VERSION (a stale .env made /health report the
# *previous* release, so test_health failed on a tree whose 16 version carriers
# all agreed). Both are the same root cause as the 401 incident described in
# docs/releases -- settings resolve ``env_file=".env"`` against the CWD, so any
# untracked key can reconfigure the suite. Stop loading the file itself: with
# env_file cleared, Settings sees process env + declared defaults only, which is
# exactly what a container gets in deployment, and no untracked key can reach a
# test. pydantic-settings reads model_config per instantiation, so patching the
# class before app.main is imported covers every Settings() call in the process.
from app.config.settings import Settings as _Settings  # noqa: E402

_Settings.model_config = {**_Settings.model_config, "env_file": None}

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402, F401
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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_module_engines() -> AsyncIterator[None]:
    """Dispose the module-level SQLAlchemy engines at session end.

    ``create_async_engine("sqlite+aiosqlite://", ...)`` performs a dialect
    initialization "first connect" (a probe connection). With aiosqlite that
    probe connection runs in a background worker thread and is not cleanly
    closed by SQLAlchemy, so it is only reclaimed by ``__del__`` during a
    later ``gc.collect()`` -- which emits a ``ResourceWarning`` and, under
    pytest's unraisable-exception hook, fails the test. Disposing every
    module-level engine at session end closes the probe + pool connections
    before they can be garbage-collected mid-run (third-party teardown defect:
    SQLAlchemy + aiosqlite).
    """
    from app.worker.plugin_runtime import (
        _SYNTHETIC_ENGINE as _synth_engine,
    )

    yield
    for eng in (engine, _synth_engine):
        try:
            await eng.dispose()
        except Exception:  # noqa: BLE001 -- best-effort teardown
            pass


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
CERT_STRICT = _os.environ.get("CAP_CERTIFICATION_STRICT") == "1"


def _tool_available(name: str) -> bool:
    return _shutil.which(name) is not None


def docker_available() -> bool:
    try:
        import subprocess

        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
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
                f"CAP_CERTIFICATION_STRICT: critical certification test would skip: {reason}",
                pytrace=False,
            )
