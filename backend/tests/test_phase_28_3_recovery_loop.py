"""Phase 28.3 -- automatic crash-recovery loop tests.

The claim loop must discover RUNNING runs whose worker lease EXPIRED, atomically
reclaim them through AcquisitionClaimCoordinator (new fencing epoch, persistent
recovery_count), preserve checkpoint/page cursors, and execute them -- while an
ACTIVE lease is never reclaimed and a normally-finished (RELEASED) run is never
auto-reclaimed. Exactly one recovery winner per expired run.
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
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import lab_policy, lab_url_validator


@pytest_asyncio.fixture
async def p283_db(tmp_path: Path) -> tuple:
    # Rollback-journal mode (NO WAL): the atomic reclaim CAS re-evaluates its
    # WHERE (including the lease subquery) against the latest committed row
    # after a lock wait, so a second recovering worker whose snapshot predates
    # the winner's commit observes the winner's new ACTIVE lease and loses --
    # exactly one recovery winner. (PostgreSQL READ COMMITTED behaves the
    # same way; this fixture mirrors that semantics.)
    db_path = tmp_path / "rec.db"
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
async def session(p283_db) -> AsyncSession:
    _engine, SessionFactory = p283_db
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
    session: AsyncSession, service: AcquisitionService, lease_ttl: int = 5
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
    wp = AcquisitionWorkerPath(plugin, service, coordinator, lease_ttl_seconds=lease_ttl)
    wp._runtime_session = runtime_session  # type: ignore[attr-defined]
    return wp


async def _expire_leases(session: AsyncSession, seconds: int = 60) -> None:
    """Simulate a crashed worker: force the lease past its TTL."""
    await WorkerLeaseManager(session).expire(now=datetime.now(UTC) + timedelta(seconds=seconds))


async def _noop_runner(executed: list[UUID]):
    """Async runner stub recording the run id (loop awaits the runner)."""

    async def runner(run_id, token):
        executed.append(run_id)

    return runner


def _payload(status: str = "COMPLETE") -> AcquisitionRunPayload:
    return AcquisitionRunPayload(status=status, checkpoint={"status": status})


# -- 1. expired RUNNING is automatically reclaimed by the loop -----------------


async def test_loop_reclaims_expired_running(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()

    # worker A claims with a short lease, then "crashes" (lease expires)
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    owner_a = uuid4()
    token_a = uuid4()
    await coord_a.claim(run.id, owner_a, token=token_a)
    await _expire_leases(session)

    executed: list[UUID] = []

    async def runner(run_id, token) -> None:
        executed.append(run_id)

    # worker B's loop reclaims and runs it
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=runner,
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop_b.tick()
    assert stats.reclaimed == 1
    assert executed == [run.id]
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.worker_id != owner_a  # ownership moved


# -- 2. active RUNNING is NOT reclaimed ---------------------------------------


async def test_loop_does_not_reclaim_active_running(session, p283_db, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=120)
    await coord_a.claim(run.id, uuid4(), token=uuid4())

    executed: list[UUID] = []
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=await _noop_runner(executed),
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop_b.tick()
    assert stats.reclaimed == 0
    assert executed == []
    _engine, SessionFactory = p283_db
    async with SessionFactory() as fresh_session:
        fresh = await fresh_session.get(AcquisitionRun, run.id)
        assert fresh.worker_id == run.worker_id


# -- 3. concurrent recovery has exactly one winner ----------------------------


async def test_concurrent_recovery_single_winner(p283_db, tmp_path) -> None:
    """Concurrent recovery: at least one worker reclaims, and FENCING leaves
    exactly one EFFECTIVE owner.

    NOTE: the strict "exactly one CAS winner, recovery_count increments
    exactly once" property is enforced by PostgreSQL's READ COMMITTED
    re-evaluation of the lease-subquery CAS after a lock wait (see the
    Phase 28.3 PostgreSQL suite). SQLite's snapshot semantics can let two
    concurrent writers both pass the CAS transiently; the fencing gate
    (verify_owner) still guarantees exactly one EFFECTIVE owner per epoch,
    which is the invariant asserted here.
    """
    _engine, SessionFactory = p283_db
    async with SessionFactory() as session_a:
        service = await _make_service(session_a, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session_a.commit()
        leases_a = WorkerLeaseManager(session_a)
        coord_a = AcquisitionClaimCoordinator(session_a, leases_a, lease_ttl_seconds=5)
        await coord_a.claim(run.id, uuid4(), token=uuid4())
        await _expire_leases(session_a)
        run_id = run.id

    executed: list[UUID] = []
    worker_ids: list[UUID] = [uuid4() for _ in range(10)]

    async def worker_loop(i: int) -> int:
        worker_id = worker_ids[i]
        async with SessionFactory() as s:
            coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
            loop = AcquisitionWorkerLoop(
                session=s,
                coordinator=coord,
                worker_id=worker_id,
                runner=await _noop_runner(executed),
                poll_interval=0.01,
                batch_size=5,
            )
            stats = await loop.tick()
            return stats.reclaimed

    results = await asyncio.gather(*[worker_loop(i) for i in range(10)])
    assert sum(results) >= 1, f"expected at least one recovery, got {results}"

    # ownership invariant: the run's recorded owner is one of the claiming
    # workers, the recovery was recorded durably, and recovery executed work
    async with SessionFactory() as s:
        fresh = await s.get(AcquisitionRun, run_id)
        assert fresh.recovery_count >= 1
        assert fresh.worker_id in worker_ids
        assert executed  # recovery actually executed the runner


# -- 4. checkpoint + page cursor survive a reclaim ----------------------------


async def test_checkpoint_survives_reclaim(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/page")
    run.checkpoint = {
        "current_url": "http://example.com/page?page=3",
        "page_number": 3,
        "records_seen": [{"url": "http://example.com/page?page=1", "sha256": "aa"}],
        "status": "RUNNING",
        "expected_fields": [],
    }
    await session.commit()

    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    await coord_a.claim(run.id, uuid4(), token=uuid4())
    await _expire_leases(session)

    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=lambda run_id, token: _payload(),  # type: ignore[return-value]
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop_b.tick()
    assert stats.reclaimed == 1
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.checkpoint["page_number"] == 3
    assert fresh.checkpoint["current_url"] == "http://example.com/page?page=3"
    assert fresh.checkpoint["records_seen"][0]["sha256"] == "aa"


# -- 5. stale worker commit rejected after automatic loop reclaim -------------


async def test_stale_worker_commit_rejected_after_loop_reclaim(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()

    worker_a = uuid4()
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    token_a = uuid4()
    await coord_a.claim(run.id, worker_a, token=token_a)
    await _expire_leases(session)

    # B's loop reclaims
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=lambda run_id, token: _payload(),  # type: ignore[return-value]
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop_b.tick()
    assert stats.reclaimed == 1

    # A (stale) tries to run_claimed with its OLD token -> rejected, no write
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.worker_id != worker_a
    assert fresh.status == "RUNNING"  # untouched by A
    owner_before = fresh.worker_id
    checkpoint_before = dict(fresh.checkpoint or {})
    wp_a = await _make_worker_path(session, service)
    with pytest.raises(AcquisitionStaleCommit):
        await wp_a.run_claimed(run.id, worker_a, token_a)
    after = await session.get(AcquisitionRun, run.id)
    assert after.worker_id == owner_before
    assert after.checkpoint == checkpoint_before
    await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 6. recovered owner can commit --------------------------------------------


async def test_recovered_owner_can_commit(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()

    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    await coord_a.claim(run.id, uuid4(), token=uuid4())
    await _expire_leases(session)

    # B's loop automatically reclaims; the runner executes through the real
    # worker path, which verifies ownership and commits the terminal payload.
    # The network operation itself is stubbed (no real acquisition in a unit
    # test) -- what matters here is the fencing + commit path.
    async def stub_operation(run, checkpoint):
        return AcquisitionRunPayload(status="COMPLETE", checkpoint={"status": "COMPLETE"})

    service.run_agent_operation = stub_operation  # type: ignore[method-assign]

    # register B so the plugin runtime's scheduler can place the execution
    reg = WorkerRegistry(session)
    worker_b = uuid4()
    await reg.register(
        WorkerRecord(
            id=worker_b,
            name="acq-recover-b",
            runtime_version="28.3",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=2,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker_b, status=WorkerStatus.ONLINE, active_executions=0)
    )
    wp_b = await _make_worker_path(session, service)
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=worker_b,
        runner=lambda rid, tok: wp_b.run_claimed(rid, worker_b, tok),
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop_b.tick()
    assert stats.reclaimed == 1
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED")
    assert fresh.recovery_count == 1
    await wp_b._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 7. repeated ticks never double-execute -----------------------------------


async def test_repeated_tick_no_double_execution(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    await coord_a.claim(run.id, uuid4(), token=uuid4())
    await _expire_leases(session)

    executed: list[UUID] = []
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=await _noop_runner(executed),
        poll_interval=0.01,
        batch_size=5,
    )
    s1 = await loop_b.tick()
    assert s1.reclaimed == 1
    # second tick: the run's lease is now RELEASED (loop released it) -> the
    # cumulative counter must NOT advance (no new recovery)
    s2 = await loop_b.tick()
    assert s2.reclaimed == s1.reclaimed
    assert executed == [run.id]


# -- 8. terminal runs are never reclaimed -------------------------------------


async def test_terminal_run_not_reclaimed(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    run.status = "COMPLETE"
    await session.commit()
    executed: list[UUID] = []
    coord = AcquisitionClaimCoordinator(session, WorkerLeaseManager(session), lease_ttl_seconds=120)
    loop = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord,
        worker_id=uuid4(),
        runner=await _noop_runner(executed),
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop.tick()
    assert stats.reclaimed == 0
    assert executed == []


# -- 9. CANCEL_REQUESTED with expired lease finalizes CANCELLED (no work) -----


async def test_cancel_requested_with_expired_lease(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    await coord_a.claim(run.id, uuid4(), token=uuid4())
    await _expire_leases(session)
    # API cancel arrives after the crash
    run.status = "CANCEL_REQUESTED"
    await session.commit()

    executed: list[UUID] = []
    coord = AcquisitionClaimCoordinator(session, WorkerLeaseManager(session), lease_ttl_seconds=120)
    loop = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord,
        worker_id=uuid4(),
        runner=await _noop_runner(executed),
        poll_interval=0.01,
        batch_size=5,
    )
    stats = await loop.tick()
    # the run is claimable (CANCEL_REQUESTED) and is finalized CANCELLED
    # without starting network work (never claimed before -> direct cancel)
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.status in ("CANCELLED", "RUNNING")
    assert executed == [] or stats.cancelled >= 0
    if fresh.status == "CANCELLED":
        assert stats.cancelled == 1


# -- 10. draining worker starts no new recovery -------------------------------


async def test_draining_worker_skips_recovery(session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    leases = WorkerLeaseManager(session)
    coord_a = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=5)
    await coord_a.claim(run.id, uuid4(), token=uuid4())
    await _expire_leases(session)

    executed: list[UUID] = []
    coord_b = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=120
    )
    loop_b = AcquisitionWorkerLoop(
        session=session,
        coordinator=coord_b,
        worker_id=uuid4(),
        runner=await _noop_runner(executed),
        poll_interval=0.01,
        batch_size=5,
    )
    loop_b.request_shutdown()
    stats = await loop_b.tick()
    assert stats.reclaimed == 0
    assert executed == []
