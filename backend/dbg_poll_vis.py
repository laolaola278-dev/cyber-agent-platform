"""Phase 28.2 diagnostic -- does the worker cancel-poll observe a committed cancel?

Uses a per-test file SQLite + WAL exactly like test_phase_28_2_cancellation.py.
Simulates: worker claims (RUNNING) -> API writes CANCEL_REQUESTED on another
session -> worker poll reads. Verifies the rollback-per-poll makes the
committed flag visible.
"""

import asyncio
import sys
import tempfile

import app.worker.plugin_runtime  # noqa: F401  -- break the repositories->worker cycle first
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, r"F:/work/buddy_work/2026-07-29-12-17-38/cyber-agent-platform/backend")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.evidence.service import EvidenceService
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry
from tests.acquisition_lab import AcquisitionLabServer, lab_policy, lab_url_validator


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "poll.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        from app.database import Base

        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
    lab = AcquisitionLabServer().start()
    try:
        async with SessionFactory() as s:
            ev = EvidenceService(s, publisher=None, storage_directory=tmp)
            svc = AcquisitionService(
                s, ev, store_root=tmp / "o", policy=lab_policy(), validator=lab_url_validator()
            )
            run, _ = await svc.create(goal="g", url=f"{lab.origin}/static")
            await s.flush()
            reg = WorkerRegistry(s)
            w = await reg.register(
                WorkerRecord(
                    name="w1", runtime_version="28.2", capabilities=frozenset({"acquisition.http"})
                )
            )
            await reg.heartbeat(
                WorkerHeartbeat(worker_id=w.id, status=WorkerStatus.ONLINE, active_executions=0)
            )
            leases = WorkerLeaseManager(s)
            coord = AcquisitionClaimCoordinator(s, leases, lease_ttl_seconds=60)
            token = uuid4()
            await coord.claim(run.id, w.id, token=token)

            # API writes CANCEL_REQUESTED on a SEPARATE session
            async with SessionFactory() as api:
                r2 = await api.get(AcquisitionRun, run.id)
                print("api read status:", r2.status)
                r2.status = "CANCEL_REQUESTED"
                r2.cancel_requested_at = __import__("datetime").datetime.now(
                    __import__("datetime").UTC
                )
                await api.commit()
                print("api committed CANCEL_REQUESTED")

            # worker polls with fresh session + rollback-per-poll
            for i in range(3):
                async with SessionFactory() as poll:
                    await poll.rollback()
                    p = await poll.get(AcquisitionRun, run.id)
                    print(f"poll[{i}] status:", p.status if p else None)
                await asyncio.sleep(0.05)

            # raw connect read
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT status FROM acquisition_runs WHERE id=:i"),
                        {"i": str(run.id)},
                    )
                ).first()
                print("raw read status:", row[0] if row else None)
    finally:
        lab.stop()
    await engine.dispose()


asyncio.run(main())
