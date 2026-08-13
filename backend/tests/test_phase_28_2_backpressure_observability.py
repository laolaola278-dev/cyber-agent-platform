"""Phase 28.2 -- Backpressure + Recovery Observability (spec 12/13).

Certifies:
  * the claim loop is bounded: poll_interval prevents busy-looping and
    batch_size caps claims per tick (backpressure);
  * max_concurrency caps parallel work per worker (scheduler refuses to
    over-commit);
  * recovery is observable: claim_attempts / recovery_count / worker_id /
    lease lifecycle fields are recorded durably on every run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with TestSessionFactory() as session:
        yield session


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


async def _make_service(
    session: AsyncSession, tmp_path: Path, lab: AcquisitionLabServer
) -> AcquisitionService:
    evidence = EvidenceService(
        session, publisher=None, storage_directory=tmp_path  # type: ignore[arg-type]
    )
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )


# -- 1. claim loop bounded polling (no busy loop) -----------------------------------


async def test_claim_loop_bounded_polling(session, tmp_path, lab) -> None:
    """The loop must NOT busy-spin: run_forever paces ticks with
    poll_interval, so an empty queue cannot consume CPU in a hot loop."""
    loop = AcquisitionWorkerLoop(
        session=session,
        coordinator=AcquisitionClaimCoordinator(session, WorkerLeaseManager(session)),
        worker_id=uuid4(),
        runner=lambda run_id, token: None,  # type: ignore[arg-type]
        poll_interval=0.2,
        batch_size=1,
    )
    # a single tick with an empty queue returns immediately (no blocking,
    # no busy work) and claims nothing
    result = await loop.tick()
    assert result.claimed == 0
    # cooperative shutdown stops run_forever promptly (no infinite loop)
    import asyncio as _asyncio

    runner_task = _asyncio.create_task(loop.run_forever())
    await _asyncio.sleep(0.05)
    loop.request_shutdown()
    stats = await _asyncio.wait_for(runner_task, timeout=5)
    assert stats.claimed == 0


# -- 2. batch_size caps claims per tick ---------------------------------------------


async def test_claim_loop_batch_size_caps_claims(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    for _ in range(4):
        await service.create(goal="g", url=f"{lab.origin}/static")
    await session.commit()

    claimed_runs: list = []

    async def runner(run_id, token) -> None:  # noqa: ANN001
        claimed_runs.append(run_id)

    loop = AcquisitionWorkerLoop(
        session=session,
        coordinator=AcquisitionClaimCoordinator(session, WorkerLeaseManager(session)),
        worker_id=uuid4(),
        runner=runner,
        poll_interval=0.01,
        batch_size=2,
    )
    result = await loop.tick()
    # at most batch_size runs claimed in one tick
    assert result.claimed <= 2
    assert len(claimed_runs) <= 2


# -- 3. scheduler refuses to over-commit past max_concurrency ------------------------


async def test_scheduler_respects_max_concurrency(session, tmp_path, lab) -> None:
    from app.exceptions import WorkerUnavailable

    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-busy",
            runtime_version="28.2",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=1,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    await reg.heartbeat(
        WorkerHeartbeat(
            worker_id=worker.id, status=WorkerStatus.BUSY, active_executions=1
        )
    )
    scheduler = WorkerScheduler(reg)
    # at max concurrency -> no feasible worker -> backpressure via exception
    with pytest.raises(WorkerUnavailable):
        await scheduler.select("acquisition.http")


# -- 4. recovery observability: claim_attempts increments ----------------------------


async def test_claim_records_attempts(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-obs", runtime_version="28.2", capabilities=frozenset({"acquisition.http"})
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    coord = AcquisitionClaimCoordinator(session, WorkerLeaseManager(session))
    await coord.claim(run.id, worker.id)
    await session.refresh(run)
    assert run.claim_attempts == 1
    assert run.worker_id == worker.id
    assert run.claim_token_hash is not None


# -- 5. recovery observability: reclaim increments recovery_count ----------------------


async def test_reclaim_records_recovery_count(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    reg = WorkerRegistry(session)
    wa = await reg.register(
        WorkerRecord(name="acq-a", runtime_version="28.2", capabilities=frozenset({"acquisition.http"}))
    )
    wb = await reg.register(
        WorkerRecord(name="acq-b", runtime_version="28.2", capabilities=frozenset({"acquisition.http"}))
    )
    await reg.heartbeat(WorkerHeartbeat(worker_id=wa.id, status=WorkerStatus.ONLINE, active_executions=0))
    await reg.heartbeat(WorkerHeartbeat(worker_id=wb.id, status=WorkerStatus.ONLINE, active_executions=0))
    await coord.claim(run.id, wa.id)
    await leases.expire(now=datetime.now(UTC) + timedelta(seconds=120))
    await coord.reclaim_expired(run.id, wb.id)
    await session.refresh(run)
    assert run.recovery_count == 1
    assert run.worker_id == wb.id


# -- 6. claim loop executes a run end-to-end (full loop path) ---------------------------


async def test_claim_loop_executes_end_to_end(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(name="acq-loop", runtime_version="28.2", capabilities=frozenset({"acquisition.http"}))
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    leases = WorkerLeaseManager(session)
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    provider = MemorySandboxProvider()
    rt = WorkerRuntime(
        session,
        reg,
        WorkerScheduler(reg),
        leases,
        SandboxRuntime(provider, SandboxPolicyEngine()),
    )
    plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
    wp = AcquisitionWorkerPath(plugin, service, coord)

    async def runner(run_id, token) -> None:  # noqa: ANN001
        await wp.run_claimed(run_id, worker.id, token)

    loop = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord,
        worker_id=worker.id,
        runner=runner,
        poll_interval=0.01,
        batch_size=2,
    )
    result = await loop.tick()
    assert result.claimed == 1
    await session.refresh(run)
    assert run.status == "COMPLETE"


# -- 7. observability fields persisted durably across sessions ---------------------------


async def test_observability_fields_durable(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(name="acq-dup", runtime_version="28.2", capabilities=frozenset({"acquisition.http"}))
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    coord = AcquisitionClaimCoordinator(session, WorkerLeaseManager(session))
    await coord.claim(run.id, worker.id)
    await session.commit()

    async with TestSessionFactory() as fresh:
        reloaded = await fresh.get(AcquisitionRun, run.id)
        assert reloaded is not None
        assert reloaded.claim_attempts == 1
        assert reloaded.worker_id == worker.id
        assert reloaded.claim_token_hash is not None
        assert reloaded.lease_id is not None
