"""Phase 28.2 -- Durable Claim, Fencing, Stale Result Protection.

Certifies (spec sections 2/3/4/5/7):
  * DB is the source of truth: no in-memory queue drives execution;
  * atomic claim: only ONE worker wins a QUEUED run under concurrency
    (10 workers competing for 1 run -> exactly one owner);
  * fencing: the current owner's token gates commit; a stale worker (whose
    lease expired and whose run was reclaimed) is REJECTED (Critical Gate);
  * crash recovery: lease expiry -> reclaim -> resume from checkpoint.

Uses the REAL Worker/Sandbox chain and the localhost Acquisition Lab.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.claim import AcquisitionClaimCoordinator, fencing_hash
from app.acquisition.exceptions import (
    AcquisitionClaimConflict,
    AcquisitionStaleCommit,
)
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
        session,
        publisher=None,
        storage_directory=tmp_path,  # type: ignore[arg-type]
    )
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )


async def _register_worker(
    session: AsyncSession, *, name: str = "acq-w"
) -> tuple[WorkerRegistry, WorkerRecord]:
    registry = WorkerRegistry(session)
    worker = await registry.register(
        WorkerRecord(
            name=name,
            runtime_version="phase-28.2",
            capabilities=frozenset({"acquisition.http"}),
        )
    )
    await registry.heartbeat(
        WorkerHeartbeat(
            worker_id=worker.id,
            status=WorkerStatus.ONLINE,
            active_executions=0,
        )
    )
    return registry, worker


async def _make_chain(
    session: AsyncSession, worker: WorkerRecord
) -> tuple[PluginWorkerRuntime, WorkerLeaseManager, MemorySandboxProvider]:
    leases = WorkerLeaseManager(session)
    provider = MemorySandboxProvider()
    sandbox = SandboxRuntime(provider, SandboxPolicyEngine())
    registry = WorkerRegistry(session)
    runtime = WorkerRuntime(session, registry, WorkerScheduler(registry), leases, sandbox)
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-lab"))
    return plugin, leases, provider


def _coordinator(session: AsyncSession, leases: WorkerLeaseManager) -> AcquisitionClaimCoordinator:
    return AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)


# -- 3. atomic claim: 10 workers compete for 1 run -----------------------------


async def test_atomic_claim_exactly_one_winner(session: AsyncSession, tmp_path, lab) -> None:
    # A real multi-worker race needs per-worker connections: use a file-backed
    # SQLite database so each worker session gets its own DB connection.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import Base

    db_path = tmp_path / "race.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as seed_session:
        service = await _make_service(seed_session, tmp_path, lab)
        run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
        await seed_session.commit()

    # 10 concurrent claim attempts (different worker ids, same run). Each
    # worker uses its OWN session/connection (realistic multi-worker race).
    async def attempt(i: int) -> bool:
        async with SessionFactory() as own:
            _, worker = await _register_worker(own, name=f"acq-c{i}")
            coordinator = _coordinator(own, WorkerLeaseManager(own))
            try:
                await coordinator.claim(run.id, worker.id)
                return True
            except Exception:  # noqa: BLE001 -- claim conflict or lock
                await own.rollback()
                return False

    results = await asyncio.gather(*[attempt(i) for i in range(10)])
    winners = sum(1 for ok in results if ok)
    assert winners == 1, f"expected exactly one winner, got {winners}"

    async with SessionFactory() as check:
        refreshed = await check.get(AcquisitionRun, run.id)
        assert refreshed is not None
        assert refreshed.status == "RUNNING"
        assert refreshed.claim_token_hash is not None
        assert refreshed.claim_attempts == 1
    await engine.dispose()


async def test_claim_records_token_hash_not_plaintext(session: AsyncSession, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    _, worker = await _register_worker(session, name="acq-tok")
    token = uuid4()
    coordinator = _coordinator(session, WorkerLeaseManager(session))
    _, lease = await coordinator.claim(run.id, worker.id, token=token)
    await session.refresh(run)
    # plaintext token is NEVER stored -- only its sha256
    assert str(token) not in (run.claim_token_hash or "")
    assert run.claim_token_hash == fencing_hash(token)
    assert lease.id == run.lease_id


# -- 4/5. fencing: stale writer rejected ---------------------------------------


async def test_stale_commit_rejected_after_reclaim(session: AsyncSession, tmp_path, lab) -> None:
    """Worker A owns the run; A's lease expires; Worker B reclaims;
    A then tries to commit -> MUST be rejected (Critical Gate)."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker_a = await _register_worker(session, name="acq-a")
    _, worker_b = await _register_worker(session, name="acq-b")

    token_a = uuid4()
    await coordinator.claim(run.id, worker_a.id, token=token_a)

    # simulate A's lease expiry (crash / heartbeat lost) -- advance beyond TTL
    await leases.expire(now=datetime.now(UTC) + timedelta(seconds=120))

    # B reclaims the run from its checkpoint
    token_b = uuid4()
    await coordinator.reclaim_expired(run.id, worker_b.id, token=token_b)

    # A attempts to commit its stale result -> rejected
    with pytest.raises(AcquisitionStaleCommit):
        await coordinator.verify_owner(run.id, worker_a.id, token_a)

    await session.refresh(run)
    assert run.stale_result_rejected == 1
    assert run.worker_id == worker_b.id  # B is now the owner


async def test_current_owner_can_commit(session: AsyncSession, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker = await _register_worker(session, name="acq-ok")
    token = uuid4()
    await coordinator.claim(run.id, worker.id, token=token)
    # current owner passes fencing
    owned = await coordinator.verify_owner(run.id, worker.id, token)
    assert owned.id == run.id


# -- 4. crash recovery: A crashes -> B reclaims -> resumes -----------------------


async def test_crash_recovery_reclaim_from_checkpoint(session: AsyncSession, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="collect 30 advisories",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve", "date"],
        expected_record_count=30,
    )
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker_a = await _register_worker(session, name="acq-a")
    _, worker_b = await _register_worker(session, name="acq-b")

    # A claims, then "crashes" without releasing (simulate via expiry)
    await coordinator.claim(run.id, worker_a.id)
    await leases.expire(now=datetime.now(UTC) + timedelta(seconds=120))

    # B reclaims -> recovery_count incremented, status RUNNING
    reclaimed = await coordinator.reclaim_expired(run.id, worker_b.id)
    assert reclaimed is not None
    await session.refresh(run)
    assert run.status == "RUNNING"
    assert run.recovery_count == 1
    assert run.worker_id == worker_b.id


async def test_reclaim_refused_while_lease_active(session: AsyncSession, tmp_path, lab) -> None:
    """A still-active lease means the run is NOT reclaimable (no double owner)."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker_a = await _register_worker(session, name="acq-a")
    _, worker_b = await _register_worker(session, name="acq-b")
    await coordinator.claim(run.id, worker_a.id)
    # B tries to reclaim while A's lease is still ACTIVE -> None
    assert await coordinator.reclaim_expired(run.id, worker_b.id) is None


async def test_duplicate_claim_rejected(session: AsyncSession, tmp_path, lab) -> None:
    """A second claim on an already-RUNNING run is rejected."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker_a = await _register_worker(session, name="acq-a")
    _, worker_b = await _register_worker(session, name="acq-b")
    await coordinator.claim(run.id, worker_a.id)
    with pytest.raises(AcquisitionClaimConflict):
        await coordinator.claim(run.id, worker_b.id)


# -- DB is the source of truth (no in-memory queue) ------------------------------


async def test_queued_run_visible_in_db_until_claimed(session: AsyncSession, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    # the durable row IS the queue
    rows = (await session.execute(select(AcquisitionRun))).scalars().all()
    assert any(r.id == run.id and r.status == "QUEUED" for r in rows)
    assert await AcquisitionClaimCoordinator.pending_count(session) >= 1


# -- full worker-path run through the claim loop runner --------------------------


async def test_run_claimed_full_chain_completes(session: AsyncSession, tmp_path, lab) -> None:
    """The Claim Loop runner executes a claimed run end-to-end (COMPLETE)."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    leases = WorkerLeaseManager(session)
    coordinator = _coordinator(session, leases)
    _, worker = await _register_worker(session, name="acq-full")
    plugin, _, _ = await _make_chain(session, worker)
    wp = AcquisitionWorkerPath(plugin, service, coordinator)

    token = uuid4()
    await coordinator.claim(run.id, worker.id, token=token)
    payload = await wp.run_claimed(run.id, worker.id, token)
    assert payload.status == "COMPLETE"
    await session.refresh(run)
    assert run.status == "COMPLETE"
    assert run.worker_id == worker.id
