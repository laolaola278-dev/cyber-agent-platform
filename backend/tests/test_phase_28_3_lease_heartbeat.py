"""Phase 28.3 -- execution-time lease heartbeat / renewal tests.

A healthy long-running acquisition must renew its lease (fencing-gated: run
ownership + lease version/token CAS) so it is NEVER falsely reclaimed by the
recovery loop. A stale owner cannot renew; renewal stops after completion /
cancellation; a simulated process death (no renewal) makes the run reclaimable.
No background heartbeat task may leak.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.claim_loop import AcquisitionWorkerLoop
from app.acquisition.exceptions import AcquisitionStaleCommit
from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionRunPayload, AcquisitionWorkerPath
from app.database import Base
from app.evidence.service import EvidenceService
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.sandbox.profile import SandboxProfile
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import lab_policy, lab_url_validator


@pytest_asyncio.fixture
async def hb_db(tmp_path: Path) -> tuple:
    db_path = tmp_path / "hb.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, SessionFactory
    await asyncio.sleep(0.05)
    import gc as _gc

    _gc.collect()
    await engine.dispose()


@pytest_asyncio.fixture
async def session(hb_db) -> AsyncSession:
    _engine, SessionFactory = hb_db
    async with SessionFactory() as session:
        yield session


async def _make_service(session: AsyncSession, tmp_path: Path) -> AcquisitionService:
    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )


async def _make_worker_path(
    session: AsyncSession,
    service: AcquisitionService,
    *,
    lease_ttl: int = 2,
    renew_interval: float = 0.3,
) -> AcquisitionWorkerPath:
    leases = WorkerLeaseManager(session)
    # Phase 28.3 side-effect fencing: WorkerRuntime MUST use a session that
    # is SEPARATE from the service/evidence session. The runtime's commit
    # (sandbox execution row) must never commit the operation's
    # evidence/artifact rows -- only the fenced final commit may do that.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    runtime_session = async_sessionmaker(session.bind, expire_on_commit=False)()
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        WorkerLeaseManager(runtime_session),
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=lease_ttl,
    )
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-lab"))
    coordinator = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=lease_ttl)
    wp = AcquisitionWorkerPath(
        plugin,
        service,
        coordinator,
        lease_ttl_seconds=lease_ttl,
        lease_renew_interval=renew_interval,
    )
    wp._runtime_session = runtime_session  # type: ignore[attr-defined]
    return wp


async def _register_worker(
    session: AsyncSession, worker_id: UUID, name: str = "acq-hb"
) -> None:
    reg = WorkerRegistry(session)
    await reg.register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.3",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=2,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
    )


def _sleep_payload(seconds: float):
    """Operation that sleeps (long-running acquisition) then completes."""

    async def operation(run, checkpoint):
        await asyncio.sleep(seconds)
        return AcquisitionRunPayload(status="COMPLETE", checkpoint={"status": "COMPLETE"})

    return operation


def _future(seconds: int):
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def _noop_runner(executed: list[UUID]):
    async def runner(run_id, token):
        executed.append(run_id)

    return runner


# -- 1. a healthy long operation survives beyond the lease TTL ----------------


async def test_long_operation_survives_lease_ttl(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    worker_id = uuid4()
    await _register_worker(session, worker_id)

    service.run_agent_operation = _sleep_payload(3.5)  # type: ignore[method-assign]
    wp = await _make_worker_path(session, service, lease_ttl=2, renew_interval=0.3)

    token = uuid4()
    coordinator = wp._ensure_coordinator()
    await coordinator.claim(run.id, worker_id, token=token)

    payload = await wp.run_claimed(run.id, worker_id, token)
    assert payload.status == "COMPLETE"
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.status == "COMPLETE"
    assert fresh.recovery_count == 0  # never falsely reclaimed
    await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 2. renewal actually extends expires_at -----------------------------------


async def test_renewal_extends_expires_at(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    worker_id = uuid4()
    await _register_worker(session, worker_id)

    service.run_agent_operation = _sleep_payload(1.5)  # type: ignore[method-assign]
    wp = await _make_worker_path(session, service, lease_ttl=60, renew_interval=0.2)
    token = uuid4()
    coordinator = wp._ensure_coordinator()
    await coordinator.claim(run.id, worker_id, token=token)

    # directly renew twice and observe expires_at move forward
    lease = await coordinator.renew(run.id, worker_id, token)
    assert lease is not None
    first_expiry = lease.expires_at
    await asyncio.sleep(0.3)
    lease2 = await coordinator.renew(run.id, worker_id, token)
    assert lease2.expires_at > first_expiry
    await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 3. stale fencing token cannot renew -------------------------------------


async def test_stale_token_cannot_renew(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    worker_a = uuid4()
    await _register_worker(session, worker_a)

    wp = await _make_worker_path(session, service, lease_ttl=60, renew_interval=0.2)
    coordinator = wp._ensure_coordinator()
    token_a = uuid4()
    await coordinator.claim(run.id, worker_a, token=token_a)

    # wrong token => verify_owner rejects the renewal (fencing gate)
    with pytest.raises(AcquisitionStaleCommit):
        await coordinator.renew(run.id, worker_a, uuid4())
    await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 4. heartbeat stops after completion --------------------------------------


async def test_heartbeat_stops_after_completion(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    worker_id = uuid4()
    await _register_worker(session, worker_id)

    service.run_agent_operation = _sleep_payload(0.3)  # type: ignore[method-assign]
    wp = await _make_worker_path(session, service, lease_ttl=60, renew_interval=0.2)
    token = uuid4()
    coordinator = wp._ensure_coordinator()
    await coordinator.claim(run.id, worker_id, token=token)

    await wp.run_claimed(run.id, worker_id, token)
    # after completion the loop/path released the lease -> RELEASED, and no
    # renewal is running anymore (no infinite background task)
    leases = WorkerLeaseManager(session)
    fresh = await session.get(AcquisitionRun, run.id)
    lease = await leases.require(fresh.lease_id)
    assert lease.status.value in ("RELEASED", "EXPIRED")
    await asyncio.sleep(0.6)
    # still no renewal activity: expires_at unchanged
    lease_again = await leases.require(fresh.lease_id)
    assert lease_again.expires_at == lease.expires_at
    await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 5. simulated process death: no renewal -> reclaim succeeds ---------------


async def test_no_renewal_allows_reclaim(hb_db, tmp_path) -> None:
    _engine, SessionFactory = hb_db
    async with SessionFactory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        worker_a = uuid4()
        await _register_worker(session, worker_a)

        # lease_renew_interval=0 disables the heartbeat entirely (simulated
        # process death / renewal never started)
        wp = await _make_worker_path(session, service, lease_ttl=1, renew_interval=0.0)
        token_a = uuid4()
        coordinator = wp._ensure_coordinator()
        await coordinator.claim(run.id, worker_a, token=token_a)
        run_id = run.id

        # let the lease lapse (TTL=1s) and force expiry
        await asyncio.sleep(1.2)
        await WorkerLeaseManager(session).expire(now=_future(5))
        await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session

    # a fresh worker's loop reclaims the dead owner's run
    async with SessionFactory() as session_b:
        executed: list[UUID] = []
        worker_b = uuid4()
        await _register_worker(session_b, worker_b, name="acq-hb-b")
        coord_b = AcquisitionClaimCoordinator(
            session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=60
        )
        loop_b = AcquisitionWorkerLoop(
            session=session_b,
            coordinator=coord_b,
            worker_id=worker_b,
            runner=await _noop_runner(executed),
            poll_interval=0.01,
            batch_size=5,
        )
        stats = await loop_b.tick()
        assert stats.reclaimed == 1
        assert executed == [run_id]
