"""Phase 28.3 -- Alembic migration certification on real PostgreSQL.

A fresh PostgreSQL database must become a COMPLETE acquisition schema by
running ONLY ``alembic upgrade head``: all 7 acquisition tables, the expected
columns / FKs / indexes / unique constraints must exist, and the
``idempotency_key`` UNIQUE constraint must really be enforced by the DB
(concurrent duplicate creates can never produce two rows).

These tests create a THROWAWAY database, migrate it, verify, and drop it.
They are skipped when no PostgreSQL is reachable (CAP283_PG_URL override
supported; default: localhost:55432 user cap).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres

BACKEND_DIR = Path(__file__).resolve().parent.parent

# default to the isolated Phase 28.3 cluster; override via env
_ADMIN_DSN = os.environ.get(
    "CAP283_PG_ADMIN_DSN", "postgresql://cap:cap@127.0.0.1:55432/postgres"
)
# Database-LESS async URL used to build per-test databases (f"{_DB_DSN}{dbname}").
# It must never carry a database name: CAP283_PG_DSN in CI points at the
# already-migrated cap283 DB, and appending the dbname produced
# "cap283cap283_mig_xxx" (InvalidCatalogNameError). Derive it from the admin
# DSN's host:port so the path is always empty regardless of the env value.
_admin_parsed = urlparse(_ADMIN_DSN)
_DB_DSN = f"postgresql+asyncpg://{_admin_parsed.netloc}/"

EXPECTED_TABLES = {
    "acquisition_runs",
    "acquisition_plans",
    "acquisition_steps",
    "acquisition_artifacts",
    "extracted_documents",
    "completeness_reports",
    "public_endpoint_candidates",
}

EXPECTED_COLUMNS = {
    "acquisition_runs": {
        "id",
        "idempotency_key",
        "request_fingerprint",
        "status",
        "worker_id",
        "lease_id",
        "sandbox_execution_id",
        "worker_execution_id",
        "claim_token_hash",
        "claim_attempts",
        "claimed_at",
        "recovery_count",
        "cancel_requested_at",
        "cancelled_at",
        "stale_result_rejected",
        "checkpoint",
    },
}


async def _probe_pg() -> bool:
    try:
        import asyncpg

        conn = await asyncio.wait_for(
            asyncpg.connect(_ADMIN_DSN), timeout=3
        )
        await conn.close()
        return True
    except Exception:  # noqa: BLE001 -- no PG -> skip
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe_pg()), reason="PostgreSQL not reachable")


def _alembic_upgrade(db_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@_skip
class TestMigration:
    @pytest.mark.asyncio
    async def test_fresh_database_upgrade_creates_full_schema(self) -> None:
        import asyncpg

        dbname = f"cap283_mig_{uuid4().hex[:8]}"
        admin = await asyncpg.connect(_ADMIN_DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()
        try:
            url = f"{_DB_DSN}{dbname}"
            result = _alembic_upgrade(url)
            assert result.returncode == 0, (
                f"alembic upgrade head failed:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
            )

            conn = await asyncpg.connect(f"postgresql://cap:cap@127.0.0.1:55432/{dbname}")
            try:
                rows = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
                actual = {r["table_name"] for r in rows}
                missing = EXPECTED_TABLES - actual
                assert not missing, f"missing tables after upgrade head: {missing}"

                for table, cols in EXPECTED_COLUMNS.items():
                    rows = await conn.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name=$1",
                        table,
                    )
                    actual_cols = {r["column_name"] for r in rows}
                    missing_cols = cols - actual_cols
                    assert not missing_cols, (
                        f"missing columns on {table}: {missing_cols}"
                    )

                # unique index on idempotency_key must exist
                idx = await conn.fetchrow(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='acquisition_runs' AND indexdef ILIKE '%idempotency_key%'"
                )
                assert idx is not None, "no idempotency_key index on acquisition_runs"
            finally:
                await conn.close()
        finally:
            admin = await asyncpg.connect(_ADMIN_DSN)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            finally:
                await admin.close()

    @pytest.mark.asyncio
    async def test_idempotency_key_unique_enforced_by_db(self) -> None:
        import asyncpg
        from asyncpg.exceptions import UniqueViolationError

        dbname = f"cap283_mig_{uuid4().hex[:8]}"
        admin = await asyncpg.connect(_ADMIN_DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()
        try:
            url = f"{_DB_DSN}{dbname}"
            result = _alembic_upgrade(url)
            assert result.returncode == 0

            conn = await asyncpg.connect(f"postgresql://cap:cap@127.0.0.1:55432/{dbname}")
            try:
                key = f"k-{uuid4().hex}"
                # FK targets first (acquisition_runs requires task_id/agent_id)
                task_id = uuid4()
                await conn.execute(
                    "INSERT INTO tasks (id, name, task_type, status, input, "
                    "required_permissions, required_capabilities, created_at, updated_at) "
                    "VALUES ($1, 't', 'acquisition', 'QUEUED', '{}', '{}', '{}', now(), now())",
                    task_id,
                )
                agent_id = uuid4()
                await conn.execute(
                    "INSERT INTO agents (id, name, version, status, permissions, "
                    "tools, author, runtime, network_policy, resource_limit, "
                    "approval_policy, health_status, capabilities, "
                    "minimum_runtime_version, platform_version, sdk_version, "
                    "created_at, updated_at) "
                    "VALUES ($1, 'a', '1', 'ONLINE', '{}', '{}', 'tester', "
                    "'\"memory\"', '{}', '{}', '\"none\"', 'HEALTHY', '{}', "
                    "'1.0', '1.0', '1.0', now(), now())",
                    agent_id,
                )
                insert = (
                    "INSERT INTO acquisition_runs "
                    "(id, idempotency_key, request_fingerprint, status, goal, "
                    "task_id, agent_id, trace_id, source_type, strategy, "
                    "blocked_reason, replans, retries, total_bytes, total_requests, "
                    "duration_seconds, strategy_history, checkpoint, claim_attempts, "
                    "recovery_count, stale_result_rejected, created_at, updated_at) "
                    "VALUES ($1, $2, $3, 'QUEUED', 'g', $4, $5, 'tr', "
                    "'web', 'paged', 'NONE', 0, 0, 0, 0, 0.0, '[]', '{}', 0, 0, 0, "
                    "now(), now())"
                )
                await conn.execute(insert, uuid4(), key, f"fp-{uuid4().hex}", task_id, agent_id)
                # second insert with the SAME key must be rejected by the DB
                with pytest.raises(UniqueViolationError):
                    await conn.execute(insert, uuid4(), key, f"fp-{uuid4().hex}", task_id, agent_id)
            finally:
                await conn.close()
        finally:
            admin = await asyncpg.connect(_ADMIN_DSN)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            finally:
                await admin.close()

    @pytest.mark.asyncio
    async def test_downgrade_removes_acquisition_objects_keeps_worker_tables(self) -> None:
        import asyncpg

        dbname = f"cap283_mig_{uuid4().hex[:8]}"
        admin = await asyncpg.connect(_ADMIN_DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()
        try:
            url = f"{_DB_DSN}{dbname}"
            env = dict(os.environ)
            env["DATABASE_URL"] = url
            result = _alembic_upgrade(url)
            assert result.returncode == 0
            down = subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "20260808_0020"],
                cwd=str(BACKEND_DIR),
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert down.returncode == 0, down.stderr[-1500:]

            conn = await asyncpg.connect(f"postgresql://cap:cap@127.0.0.1:55432/{dbname}")
            try:
                rows = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
                actual = {r["table_name"] for r in rows}
                leftover = EXPECTED_TABLES & actual
                assert not leftover, f"acquisition tables survived downgrade: {leftover}"
                # Phase 16/17 worker/sandbox tables must be preserved
                for keep in ("workers", "worker_leases", "sandbox_executions"):
                    assert keep in actual, f"downgrade dropped pre-existing table {keep}"
            finally:
                await conn.close()
        finally:
            admin = await asyncpg.connect(_ADMIN_DSN)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            finally:
                await admin.close()
