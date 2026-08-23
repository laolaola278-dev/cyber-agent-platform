"""Phase 28.7 GA PRE-GATES A-D -- run-lease heartbeat critical certification.

FACT-CHECK context (mid-term report claimed "per-run lease has no renewal"):
the Phase 28.3 execution-time heartbeat EXISTS and is wired by default
(worker_main -> AcquisitionWorkerPath -> poll-loop renewal every TTL/3,
fencing-gated). These gates lock that behavior into the GA release block so
a future regression can never silently reintroduce a false-reclaim /
recovery-storm bug.

PRE-GATE A  a healthy run whose operation outlives 2x the lease TTL survives
            without reclaim; expires_at visibly advances; recovery_count == 0.
PRE-GATE B  a second worker's recovery scan NEVER reclaims a healthy renewed
            run; ownership never changes.
PRE-GATE C  genuine heartbeat loss (no renewal) causes automatic reclaim with
            recovery_count incremented.
PRE-GATE D  a stale old owner CANNOT commit its result after a reclaim (the
            fencing gate rejects the terminal write; the reclaimed run keeps
            its new owner).
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
async def pregate_db(tmp_path: Path) -> tuple:
    db_path = tmp_path / "pregate_hb.db"
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
async def session(pregate_db) -> AsyncSession:
    _engine, SessionFactory = pregate_db
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
    lease_ttl: int,
    renew_interval: float | None,
) -> AcquisitionWorkerPath:
    """renew_interval=None -> production default cadence (max(1, TTL/3));
    renew_interval=0 -> heartbeat DISABLED (simulated process death)."""
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
    coordinator = AcquisitionClaimCoordinator(
        session, WorkerLeaseManager(session), lease_ttl_seconds=lease_ttl
    )
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
    session: AsyncSession, worker_id: UUID, name: str = "pregate-hb"
) -> None:
    reg = WorkerRegistry(session)
    await reg.register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.7",
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


async def _noop_runner(executed: list[UUID]):
    async def runner(run_id, token):
        executed.append(run_id)

    return runner


def _future(seconds: int):
    return datetime.now(UTC) + timedelta(seconds=seconds)


# == PRE-GATE A: healthy run outlives 2x TTL, zero false reclaim ==============


async def test_pregate_a_run_outlives_2x_ttl_without_reclaim(pregate_db, tmp_path) -> None:
    _engine, SessionFactory = pregate_db
    async with SessionFactory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        worker_id = uuid4()
        await _register_worker(session, worker_id)

        service.run_agent_operation = _sleep_payload(4.5)  # type: ignore[method-assign]
        # production renewal cadence (TTL/3); TTL=2s -> op runs >2.25x TTL
        wp = await _make_worker_path(session, service, lease_ttl=2, renew_interval=None)
        token = uuid4()
        coordinator = wp._ensure_coordinator()
        await coordinator.claim(run.id, worker_id, token=token)

        async def sampler() -> list[tuple[datetime, datetime]]:
            seen: list[tuple[datetime, datetime]] = []
            for _ in range(16):
                await asyncio.sleep(0.3)
                async with SessionFactory() as s2:
                    fresh = await s2.get(AcquisitionRun, run.id)
                    if fresh is None or fresh.lease_id is None:
                        continue
                    lease = await WorkerLeaseManager(s2).require(fresh.lease_id)
                    if lease.status.value == "ACTIVE":
                        seen.append((lease.renewed_at, lease.expires_at))
                if len(seen) >= 3:
                    break
            return seen

        exec_task = asyncio.create_task(wp.run_claimed(run.id, worker_id, token))
        sampled, payload = await asyncio.gather(sampler(), exec_task)

        assert payload.status == "COMPLETE"
        # heartbeat visibly advanced the lease while the op was still running
        assert len(sampled) >= 2, (
            f"expected >=2 ACTIVE lease samples during a >2xTTL operation, got {len(sampled)}"
        )
        expiries = [e for _, e in sampled]
        assert expiries[-1] > expiries[0], "expires_at did not advance: renewal NOT happening"

        final = await session.get(AcquisitionRun, run.id)
        assert final.status == "COMPLETE"
        assert final.recovery_count == 0, "healthy renewed run was falsely reclaimed"
        assert final.worker_id == worker_id, "ownership changed during healthy execution"
        await wp._runtime_session.close()  # noqa: BLE001 -- release runtime session


# == PRE-GATE B: second worker never reclaims a healthy renewed run ==========


async def test_pregate_b_second_worker_never_reclaims_healthy_run(pregate_db, tmp_path) -> None:
    _engine, SessionFactory = pregate_db
    async with SessionFactory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        worker_a = uuid4()
        await _register_worker(session, worker_a, name="pregate-hb-a")
        run_id = run.id

        service.run_agent_operation = _sleep_payload(4.0)  # type: ignore[method-assign]
        wp_a = await _make_worker_path(session, service, lease_ttl=2, renew_interval=0.3)
        token_a = uuid4()
        coord_a = wp_a._ensure_coordinator()
        await coord_a.claim(run_id, worker_a, token=token_a)

        exec_task = asyncio.create_task(wp_a.run_claimed(run_id, worker_a, token_a))

        # worker B: aggressive recovery scanning for the whole execution window
        async with SessionFactory() as session_b:
            worker_b = uuid4()
            await _register_worker(session_b, worker_b, name="pregate-hb-b")
            coord_b = AcquisitionClaimCoordinator(
                session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=60
            )
            executed_b: list[UUID] = []
            loop_b = AcquisitionWorkerLoop(
                session=session_b,
                coordinator=coord_b,
                worker_id=worker_b,
                runner=await _noop_runner(executed_b),
                poll_interval=0.05,
                batch_size=5,
            )
            reclaimed_total = 0
            ticks = 0
            while True:
                stats = await loop_b.tick()
                reclaimed_total += stats.reclaimed
                ticks += 1
                if exec_task.done() and ticks >= 8:
                    break
                await asyncio.sleep(0.25)

            payload = await exec_task
            assert payload.status == "COMPLETE"
            # THE CRITICAL INVARIANT: B never stole healthy A's run
            assert reclaimed_total == 0, (
                f"second worker reclaimed a HEALTHY renewed run {reclaimed_total} time(s)"
            )
            assert executed_b == [], "worker B executed work owned by worker A"
            final = await session.get(AcquisitionRun, run_id)
            assert final.status == "COMPLETE"
            assert final.recovery_count == 0
            assert final.worker_id == worker_a, "current owner must remain worker A"
        await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session


# == PRE-GATE C: genuine heartbeat loss -> automatic reclaim ==================


async def test_pregate_c_heartbeat_loss_causes_reclaim(pregate_db, tmp_path) -> None:
    _engine, SessionFactory = pregate_db
    async with SessionFactory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        worker_a = uuid4()
        await _register_worker(session, worker_a, name="pregate-hb-a")
        run_id = run.id

        # renewal DISABLED: simulates process death while the lease is held
        wp_a = await _make_worker_path(session, service, lease_ttl=1, renew_interval=0.0)
        token_a = uuid4()
        coord_a = wp_a._ensure_coordinator()
        await coord_a.claim(run_id, worker_a, token=token_a)
        await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session

        # let the lease lapse past TTL and force expiry
        await asyncio.sleep(1.3)
        await WorkerLeaseManager(session).expire(now=_future(5))

    async with SessionFactory() as session_b:
        executed_b: list[UUID] = []
        worker_b = uuid4()
        await _register_worker(session_b, worker_b, name="pregate-hb-b")
        coord_b = AcquisitionClaimCoordinator(
            session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=60
        )
        loop_b = AcquisitionWorkerLoop(
            session=session_b,
            coordinator=coord_b,
            worker_id=worker_b,
            runner=await _noop_runner(executed_b),
            poll_interval=0.01,
            batch_size=5,
        )
        stats = await loop_b.tick()
        assert stats.reclaimed == 1, "expired-lease run was NOT automatically reclaimed"
        assert executed_b == [run_id]

        final = await session_b.get(AcquisitionRun, run_id)
        assert final.worker_id == worker_b, "run must have a NEW current owner"
        assert final.recovery_count == 1, "reclaim must increment recovery_count exactly once"


# == PRE-GATE D: stale old owner cannot commit after reclaim ==================


async def test_pregate_d_stale_owner_cannot_commit_after_reclaim(pregate_db, tmp_path) -> None:
    _engine, SessionFactory = pregate_db
    async with SessionFactory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        worker_a = uuid4()
        await _register_worker(session, worker_a, name="pregate-hb-a")
        worker_b = uuid4()
        await _register_worker(session, worker_b, name="pregate-hb-b")
        run_id = run.id

        # A executes a slow op with NO heartbeat (its lease WILL lapse mid-run)
        service.run_agent_operation = _sleep_payload(3.0)  # type: ignore[method-assign]
        wp_a = await _make_worker_path(session, service, lease_ttl=1, renew_interval=0.0)
        token_a = uuid4()
        coord_a = wp_a._ensure_coordinator()
        await coord_a.claim(run_id, worker_a, token=token_a)
        stale_task = asyncio.create_task(wp_a.run_claimed(run_id, worker_a, token_a))

        # B reclaims the lapsed run while A's operation is still executing
        async with SessionFactory() as session_b:
            coord_b = AcquisitionClaimCoordinator(
                session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=60
            )
            executed_b: list[UUID] = []
            loop_b = AcquisitionWorkerLoop(
                session=session_b,
                coordinator=coord_b,
                worker_id=worker_b,
                runner=await _noop_runner(executed_b),
                poll_interval=0.01,
                batch_size=5,
            )
            deadline = asyncio.get_running_loop().time() + 10
            while not executed_b:
                assert asyncio.get_running_loop().time() < deadline, "B never reclaimed"
                await loop_b.tick()

            # B now OWNS the run; A's original fencing identity is stale
            with pytest.raises(AcquisitionStaleCommit):
                await coord_a.verify_owner(run_id, worker_a, token_a)

            # A finishes its doomed operation: the fencing gate must reject
            # its terminal commit -- the run NEVER becomes COMPLETE via the
            # stale owner.
            payload_a = await stale_task
            assert payload_a.status != "COMPLETE", (
                "stale owner's COMPLETE payload was accepted"
            )

            final = await session_b.get(AcquisitionRun, run_id)
            assert final.status != "COMPLETE", "stale owner committed a terminal write!"
            assert final.worker_id == worker_b, "ownership did not stay with the new owner"
            assert final.recovery_count == 1
        await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session
