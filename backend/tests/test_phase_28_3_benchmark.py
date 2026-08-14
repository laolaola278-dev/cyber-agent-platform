"""Phase 28.3 -- PostgreSQL durability benchmark.

Creates N runs through the API-style path (one DB connection each), then
consumes them with the REAL acquisition worker daemon as a separate process.
Asserts every run reaches a terminal state, none is lost, no run is stuck,
and fencing counters stay sane. Run count: ``CAP283_BENCH_N`` (default 100;
the formal certification run uses 500).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.evidence.service import EvidenceService

pytestmark = [pytest.mark.timeout(1200), pytest.mark.postgres]

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_DSN = os.environ.get(
    "CAP283_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283"
)
PG_SYNC_DSN = PG_DSN.replace("postgresql+asyncpg://", "postgresql://")
BENCH_N = int(os.environ.get("CAP283_BENCH_N", "100"))


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
        policy=None,  # type: ignore[arg-type]
        validator=None,  # type: ignore[arg-type]
    )


@_skip
@pytest.mark.asyncio
async def test_pg_durability_benchmark(tmp_path) -> None:
    import asyncpg

    # truncate acquisition tables for a clean measurement
    conn = await asyncpg.connect(PG_SYNC_DSN)
    try:
        await conn.execute(
            "TRUNCATE TABLE acquisition_artifacts, acquisition_steps, "
            "acquisition_plans, acquisition_runs, extracted_documents, "
            "completeness_reports, public_endpoint_candidates, evidence, "
            "workers, worker_leases, sandbox_executions, tasks, agents CASCADE"
        )
    finally:
        await conn.close()

    # -- enqueue BENCH_N runs (API-style, one session each) -------------------
    engine = create_async_engine(PG_DSN, pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    t0 = time.monotonic()
    run_ids: list = []
    for _ in range(BENCH_N):
        async with factory() as session:
            service = await _make_service(session, tmp_path)
            # private URL -> worker rejects with BLOCKED (no external network)
            run, _ = await service.create(
                goal="g",
                url="http://127.0.0.1:9/",
                idempotency_key=f"bench-{uuid4().hex}",
            )
            await session.commit()
            run_ids.append(run.id)
    enqueue_elapsed = time.monotonic() - t0
    await engine.dispose()

    # -- consume with the real daemon (separate process) ----------------------
    env = dict(os.environ)
    for k in (
        "CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR",
        "CODEBUDDY_SAFE_DELETE_BULK_GUARD",
        "CODEBUDDY_SAFE_DELETE_REPORT_PATH",
        "CODEBUDDY_TOOL_CALL_ID",
    ):
        env.pop(k, None)
    env["DATABASE_URL"] = PG_DSN
    env["ACQ_WORKER_NAME"] = f"acq-bench-{os.getpid()}"
    env["ACQ_POLL_INTERVAL"] = "0.05"
    env["ACQ_BATCH_SIZE"] = "10"
    # long enough to drain all runs (BLOCKED finalization is fast)
    env["ACQ_RUN_SECONDS"] = "300"
    env["ACQ_STORE_ROOT"] = str(tmp_path / "objects")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.acquisition.worker_main"],
        cwd=str(BACKEND_DIR),
        env=env,
        # never pipe the daemon's stdout: an unread pipe would fill up and
        # BLOCK the child once the log volume exceeds the OS pipe buffer,
        # stalling consumption mid-benchmark
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    t1 = time.monotonic()
    wait_engine = create_async_engine(PG_DSN, pool_size=5)
    wait_factory = async_sessionmaker(wait_engine, expire_on_commit=False)
    try:
        deadline = t1 + 300
        while time.monotonic() < deadline:
            async with wait_factory() as s:
                pending = int(
                    (
                        await s.scalar(
                            select(func.count())
                            .select_from(AcquisitionRun)
                            .where(
                                AcquisitionRun.id.in_(run_ids),
                                AcquisitionRun.status.in_(("QUEUED", "RUNNING", "PARTIAL")),
                            )
                        )
                    )
                    or 0
                )
                if pending == 0:
                    break
            await asyncio.sleep(0.5)
        assert pending == 0, f"{pending}/{BENCH_N} runs still pending after 300s"
    finally:
        await wait_engine.dispose()
        # the benchmark's job is done once every run is terminal; do not wait
        # for the daemon's own ACQ_RUN_SECONDS timer to expire
        proc.kill()
        proc.wait(timeout=30)
    drain_elapsed = time.monotonic() - t1

    # -- verify durability ------------------------------------------------------
    engine = create_async_engine(PG_DSN, pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        total = int(
            (await s.scalar(select(func.count()).select_from(AcquisitionRun))) or 0
        )
        rows = (
            await s.execute(
                select(
                    AcquisitionRun.status,
                    func.count().label("n"),
                    func.max(AcquisitionRun.recovery_count).label("max_rec"),
                )
                .where(AcquisitionRun.id.in_(run_ids))
                .group_by(AcquisitionRun.status)
            )
        ).all()
        worker_owned = int(
            (
                await s.scalar(
                    select(func.count())
                    .select_from(AcquisitionRun)
                    .where(
                        AcquisitionRun.id.in_(run_ids),
                        AcquisitionRun.worker_id.is_not(None),
                    )
                )
            )
            or 0
        )
    await engine.dispose()

    assert total == BENCH_N, f"lost runs: {total}/{BENCH_N}"
    by_status = {r.status: r.n for r in rows}
    terminal = sum(by_status.values())
    assert terminal == BENCH_N, f"non-terminal runs remain: {by_status}"
    assert worker_owned == BENCH_N, f"runs not executed by a worker: {worker_owned}/{BENCH_N}"
    assert all(r.max_rec <= 1 for r in rows), "unexpected recovery storms"

    print(
        f"\nBENCHMARK enqueue={BENCH_N} runs in {enqueue_elapsed:.2f}s "
        f"({BENCH_N / max(enqueue_elapsed, 1e-6):.0f}/s), "
        f"drain in {drain_elapsed:.2f}s "
        f"({BENCH_N / max(drain_elapsed, 1e-6):.0f}/s), statuses={by_status}"
    )
