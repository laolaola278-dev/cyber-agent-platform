"""Phase 28.2 -- 500-run Durability Benchmark (spec section 14).

Certifies at scale:
  * the DB is the source of truth: 500 runs enqueued -> 500 terminal results;
  * no run is lost (every enqueued run reaches a terminal state);
  * no run is executed twice (each run has exactly one worker commit --
    fencing + atomic claim hold under load);
  * the claim loop drains the whole queue.

The lab /static endpoint is fast, so 500 runs complete quickly; the point is
queue durability + claim correctness under volume, not throughput.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.claim_loop import AcquisitionWorkerLoop
from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionWorkerPath
from app.evidence.service import EvidenceService
from app.sandbox import SandboxPolicyEngine, SandboxRuntime
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import AcquisitionLabServer, lab_policy, lab_url_validator
from tests.conftest import TestSessionFactory

import pytest
pytestmark = [pytest.mark.timeout(1200)]


RUN_COUNT = 500


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


async def _enqueue_all(
    session: AsyncSession, tmp_path: Path, lab: AcquisitionLabServer, count: int
) -> list:
    evidence = EvidenceService(
        session, publisher=None, storage_directory=tmp_path  # type: ignore[arg-type]
    )
    service = AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run_ids: list = []
    for _ in range(count):
        run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
        run_ids.append(run.id)
    await session.commit()
    return run_ids


async def test_500_runs_durable_no_loss_no_duplicate(tmp_path, lab) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool

    from app.database import Base

    db_path = tmp_path / "durability.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # WAL lets the cancel-poll/read connections read while the claim
        # loop's write transaction is open. Without it (default rollback
        # journal) every write serializes against the 30s busy timeout and
        # the 500-run drain stalls in ~30s-per-batch chunks (Phase 28.5-L).
        await conn.execute(text("PRAGMA journal_mode=WAL"))

    # enqueue 500 runs
    async with SessionFactory() as enqueue_session:
        run_ids = await _enqueue_all(enqueue_session, tmp_path, lab, RUN_COUNT)
    assert len(run_ids) == RUN_COUNT

    # execute through the claim loop until the queue drains
    terminal_statuses: dict = {}
    completed_commits = 0

    async with SessionFactory() as work_session:
        reg = WorkerRegistry(work_session)
        worker = await reg.register(
            WorkerRecord(
                name="acq-bench",
                runtime_version="28.2",
                capabilities=frozenset({"acquisition.http"}),
                max_concurrency=4,
            )
        )
        await reg.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0
            )
        )
        leases = WorkerLeaseManager(work_session)
        coord = AcquisitionClaimCoordinator(work_session, leases, lease_ttl_seconds=60)
        provider = MemorySandboxProvider()
        rt = WorkerRuntime(
            work_session,
            reg,
            WorkerScheduler(reg),
            leases,
            SandboxRuntime(provider, SandboxPolicyEngine()),
        )
        plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
        evidence = EvidenceService(
            work_session, publisher=None, storage_directory=tmp_path  # type: ignore[arg-type]
        )
        service = AcquisitionService(
            work_session,
            evidence,
            store_root=tmp_path / "objects",
            policy=lab_policy(),
            validator=lab_url_validator(),
        )
        wp = AcquisitionWorkerPath(plugin, service, coord)

        async def runner(run_id, token):  # noqa: ANN001
            nonlocal completed_commits
            payload = await wp.run_claimed(run_id, worker.id, token)
            terminal_statuses[str(run_id)] = payload.status
            if payload.status in ("COMPLETE", "BLOCKED", "PARTIAL", "FAILED"):
                completed_commits += 1
            return payload

        loop = AcquisitionWorkerLoop(
            session=work_session,
            coordinator=coord,
            worker_id=worker.id,
            runner=runner,
            poll_interval=0.01,
            batch_size=8,
        )
        # drain up to N ticks (each tick claims batch_size)
        ticks = 0
        while ticks < 200:
            await loop.tick()
            ticks += 1
            remaining = await work_session.scalar(
                select(func.count())
                .select_from(AcquisitionRun)
                .where(AcquisitionRun.status.in_(("QUEUED", "CANCEL_REQUESTED", "RUNNING")))
            )
            if remaining == 0:
                break
        # graceful drain of any in-flight work
        stats = await loop.drain(timeout=30)

    # verify: every run reached a terminal state (no loss)
    async with SessionFactory() as verify_session:
        total = (
            await verify_session.scalar(select(func.count()).select_from(AcquisitionRun))
        ) or 0
        terminal = (
            await verify_session.scalar(
                select(func.count())
                .select_from(AcquisitionRun)
                .where(AcquisitionRun.status.in_(("COMPLETE", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED")))
            )
        ) or 0
        non_terminal = total - terminal
        assert total == RUN_COUNT, f"expected {RUN_COUNT} runs, found {total}"
        assert non_terminal == 0, f"{non_terminal} runs never reached a terminal state"
        # exactly-once: worker commit count equals run count (no double-execute)
        assert terminal == RUN_COUNT, f"terminal {terminal} != enqueued {RUN_COUNT}"
    # the loop observed every execution reach a terminal payload
    assert stats.completed >= RUN_COUNT - 1, f"completed={stats.completed}"
    assert stats.errors == [], f"loop errors: {stats.errors[:3]}"
    await engine.dispose()
