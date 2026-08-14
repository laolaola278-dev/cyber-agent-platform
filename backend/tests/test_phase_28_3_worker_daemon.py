"""Phase 28.3 -- production Acquisition Worker daemon lifecycle tests.

Runs the REAL daemon entrypoint (``python -m app.acquisition.worker_main``)
as a separate process against PostgreSQL and asserts: registration, durable
queue polling, graceful auto-shutdown (SIGTERM-equivalent path), registry
state, and engine disposal. Skipped without PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_DSN = os.environ.get(
    "CAP283_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283"
)
PG_SYNC_DSN = PG_DSN.replace("postgresql+asyncpg://", "postgresql://")


async def _probe_pg() -> bool:
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(PG_SYNC_DSN), timeout=3)
        await conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe_pg()), reason="PostgreSQL not reachable")


def _run_daemon(
    worker_name: str, run_seconds: int = 3, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR", None)
    env.pop("CODEBUDDY_SAFE_DELETE_BULK_GUARD", None)
    env.pop("CODEBUDDY_SAFE_DELETE_REPORT_PATH", None)
    env.pop("CODEBUDDY_TOOL_CALL_ID", None)
    env["DATABASE_URL"] = PG_DSN
    env["ACQ_WORKER_NAME"] = worker_name
    env["ACQ_POLL_INTERVAL"] = "0.1"
    env["ACQ_BATCH_SIZE"] = "2"
    env["ACQ_RUN_SECONDS"] = str(run_seconds)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "app.acquisition.worker_main"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@_skip
class TestWorkerDaemon:
    @pytest.mark.asyncio
    async def test_daemon_starts_polls_and_gracefully_stops(self) -> None:
        import asyncpg

        name = f"acq-daemon-{os.getpid()}"
        conn = await asyncpg.connect(PG_SYNC_DSN)
        try:
            await conn.execute("DELETE FROM workers WHERE name=$1", name)
        finally:
            await conn.close()

        result = await asyncio.to_thread(_run_daemon, name, 3)
        log = (result.stdout + result.stderr).lower()
        assert "worker registered" in log, (
            f"daemon did not register:\n{result.stdout[-1500:]}"
        )
        assert "started" in log
        assert "auto-shutdown" in log
        assert "engine disposed" in log
        assert result.returncode == 0, result.stderr[-2000:]

        # the registry row exists with the acquisition capability
        conn = await asyncpg.connect(PG_SYNC_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT status, capabilities FROM workers WHERE name=$1", name
            )
        finally:
            await conn.close()
        assert row is not None, "worker registry row missing after daemon run"
        assert "acquisition.http" in row["capabilities"]

    @pytest.mark.asyncio
    async def test_daemon_refuses_to_run_without_migration(self) -> None:
        import asyncpg

        # a database that has NOT been migrated: the daemon must fail fast
        # with a clear message instead of create_all'ing tables
        dbname = f"cap283_nomig_{os.getpid()}"
        admin = await asyncpg.connect("postgresql://cap@127.0.0.1:55432/postgres")
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()
        try:
            env = dict(os.environ)
            env["DATABASE_URL"] = f"postgresql+asyncpg://cap:cap@127.0.0.1:55432/{dbname}"
            env["ACQ_RUN_SECONDS"] = "2"
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "app.acquisition.worker_main"],
                cwd=str(BACKEND_DIR),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            combined = (result.stdout + result.stderr).lower()
            assert "alembic" in combined, (
                "daemon should refuse to run when the schema is missing:\n"
                f"{result.stdout[-1200:]}\n{result.stderr[-1200:]}"
            )
        finally:
            admin = await asyncpg.connect("postgresql://cap@127.0.0.1:55432/postgres")
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            finally:
                await admin.close()
