"""Phase 28.3 -- API / Worker cross-process isolation tests.

Proves the durable queue and cancellation are REAL cross-process semantics:

  Process A (this test, acting as the API): enqueue a run -> QUEUED, and
  later set the durable CANCEL_REQUESTED flag.
  Process B (a real `python -m app.acquisition.worker_main` subprocess):
  polls the shared PostgreSQL, claims, executes, finalizes.

The two processes share ONLY the PostgreSQL database -- no Python objects,
no sessions, no in-memory events, no shared sandbox handles.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.evidence.service import EvidenceService

pytestmark = pytest.mark.postgres

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_DSN = os.environ.get("CAP283_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283")
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


async def _make_service(session, tmp_path: Path) -> AcquisitionService:
    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=None,  # type: ignore[arg-type] -- no network work in API process
        validator=None,  # type: ignore[arg-type]
    )


def _start_worker_daemon(worker_name: str, run_seconds: int, tmp_path: Path) -> subprocess.Popen:
    env = dict(os.environ)
    for k in (
        "CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR",
        "CODEBUDDY_SAFE_DELETE_BULK_GUARD",
        "CODEBUDDY_SAFE_DELETE_REPORT_PATH",
        "CODEBUDDY_TOOL_CALL_ID",
    ):
        env.pop(k, None)
    env["DATABASE_URL"] = PG_DSN
    env["ACQ_WORKER_NAME"] = worker_name
    env["ACQ_POLL_INTERVAL"] = "0.1"
    env["ACQ_BATCH_SIZE"] = "2"
    env["ACQ_RUN_SECONDS"] = str(run_seconds)
    env["ACQ_STORE_ROOT"] = str(tmp_path / "objects")
    return subprocess.Popen(
        [sys.executable, "-m", "app.acquisition.worker_main"],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def _wait_terminal(engine, run_id, timeout: float = 60) -> str:
    """Wait until the run reaches a REAL terminal state (CANCEL_REQUESTED is
    a transient request state, never a terminal -- the worker must finalize
    CANCELLED)."""
    import time

    factory = async_sessionmaker(engine, expire_on_commit=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with factory() as s:
            run = await s.get(AcquisitionRun, run_id)
            status = run.status if run is not None else None
        if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
            return status
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach a terminal state in {timeout}s")


@_skip
class TestProcessIsolation:
    @pytest.mark.asyncio
    async def test_api_enqueue_worker_process_executes(self, tmp_path) -> None:

        # Process A: API-style enqueue (this test process)
        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            service = await _make_service(session, tmp_path)
            # private + unreachable URL: the worker's URLPolicyValidator will
            # reject it -> BLOCKED terminal. No external network dependency.
            run, created = await service.create(
                goal="g", url="http://127.0.0.1:9/", idempotency_key=f"iso-{uuid4().hex}"
            )
            await session.commit()
            run_id = run.id
            assert created is True
            assert run.status == "QUEUED"
        await engine.dispose()

        # Process B: the real acquisition worker daemon (separate process)
        proc = _start_worker_daemon(f"acq-iso-{os.getpid()}", run_seconds=8, tmp_path=tmp_path)
        try:
            wait_engine = create_async_engine(PG_DSN, pool_size=5)
            try:
                status = await _wait_terminal(wait_engine, run_id)
            finally:
                await wait_engine.dispose()
            assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), status
        finally:
            _out, _ = proc.communicate(timeout=30)
        # the daemon consumed the run: owner is a real worker, not this process
        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.worker_id is not None
            assert fresh.claim_attempts >= 1
            assert fresh.status == status
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_api_cancel_worker_process_observes(self, tmp_path) -> None:

        from datetime import UTC, datetime

        # Process A: enqueue then durably cancel BEFORE the worker starts
        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(
                goal="g", url="http://127.0.0.1:9/", idempotency_key=f"iso-c-{uuid4().hex}"
            )
            await session.commit()
            run_id = run.id
            run.status = "CANCEL_REQUESTED"
            run.cancel_requested_at = datetime.now(UTC)
            await session.commit()
        await engine.dispose()

        # Process B: worker observes CANCEL_REQUESTED (never claimed -> direct
        # cancel, no network work) and finalizes CANCELLED
        proc = _start_worker_daemon(f"acq-iso-c-{os.getpid()}", run_seconds=6, tmp_path=tmp_path)
        try:
            wait_engine = create_async_engine(PG_DSN, pool_size=5)
            try:
                status = await _wait_terminal(wait_engine, run_id)
            finally:
                await wait_engine.dispose()
            assert status == "CANCELLED", status
        finally:
            _out, _ = proc.communicate(timeout=30)

        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.status == "CANCELLED"
            assert fresh.cancelled_at is not None
            # no network work was ever started
            assert fresh.total_requests == 0
        await engine.dispose()
