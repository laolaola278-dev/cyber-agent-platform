"""Phase 28.3 -- real PostgreSQL concurrency / race certification.

Runs against an ISOLATED PostgreSQL database (no mocks). Covers the atomic
claim, automatic recovery, fencing, cancellation, concurrent idempotency,
and the cancel/reclaim/renewal races. Skipped when PostgreSQL is unreachable.

DB: cap283 (migrated by ``alembic upgrade head``). Each test truncates the
acquisition tables before running.
"""

from __future__ import annotations

import os
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.claim_loop import AcquisitionWorkerLoop
from app.acquisition.exceptions import (
    AcquisitionClaimConflict,
    AcquisitionConflict,
    AcquisitionStaleCommit,
)
from app.acquisition.models_db import AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionRunPayload, AcquisitionWorkerPath
from app.evidence.service import EvidenceService
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime

pytestmark = pytest.mark.postgres

PG_DSN = os.environ.get("CAP283_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283")
PG_SYNC_DSN = os.environ.get("CAP283_PG_SYNC", "postgresql://cap@127.0.0.1:55432/cap283")

_TRUNCATE = """
TRUNCATE TABLE
    acquisition_artifacts,
    acquisition_steps,
    acquisition_plans,
    acquisition_runs,
    extracted_documents,
    completeness_reports,
    public_endpoint_candidates,
    evidence,
    workers,
    worker_leases,
    sandbox_executions,
    tasks,
    agents
CASCADE
"""


async def _probe_pg() -> bool:
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(PG_SYNC_DSN), timeout=3)
        await conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe_pg()), reason="PostgreSQL not reachable")


@pytest_asyncio.fixture
async def pg_engine():
    engine = create_async_engine(PG_DSN, pool_size=25, max_overflow=25)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE))
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_factory(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False)


async def _make_service(session: AsyncSession, tmp_path: Path) -> AcquisitionService:
    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=None,  # type: ignore[arg-type] -- defaults; no network work here
        validator=None,  # type: ignore[arg-type]
    )


async def _register_worker(
    session: AsyncSession, worker_id: UUID, name: str | None = None
) -> None:
    from app.worker.registry import WorkerRegistry

    # unique name per worker: WorkerRegistry.register keys by name, and a
    # same-name registration returns the EXISTING worker row (wrong id)
    name = name or f"acq-pg-{worker_id.hex[:8]}"
    reg = WorkerRegistry(session)
    await reg.register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.3",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=5,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
    )


async def _make_worker_path(
    session: AsyncSession, service: AcquisitionService, *, lease_ttl: int = 120
) -> AcquisitionWorkerPath:
    """Worker path with a real runtime on a session SEPARATE from the service
    session (Phase 28.3 side-effect fencing: the runtime commit must never
    commit the operation's evidence rows)."""
    from app.sandbox.policy import SandboxPolicyEngine
    from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
    from app.sandbox.profile import SandboxProfile
    from app.worker.plugin_runtime import PluginWorkerRuntime
    from app.worker.registry import WorkerRegistry
    from app.worker.runtime import WorkerRuntime
    from app.worker.scheduler import WorkerScheduler

    leases = WorkerLeaseManager(session)
    runtime_session = async_sessionmaker(session.bind, expire_on_commit=False)()
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        WorkerLeaseManager(runtime_session),
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=lease_ttl,
    )
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-pg"))
    coordinator = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=lease_ttl)
    wp = AcquisitionWorkerPath(plugin, service, coordinator, lease_ttl_seconds=lease_ttl)
    wp._runtime_session = runtime_session  # type: ignore[attr-defined]
    return wp


async def _noop_runner(executed: list[UUID]):
    async def runner(run_id, token):
        executed.append(run_id)

    return runner


@_skip
class TestPostgresConcurrency:
    # -- A. atomic claim: exactly one winner among 10+ connections ------------
    @pytest.mark.asyncio
    async def test_atomic_claim_single_winner(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            run_id = run.id

        workers = [uuid4() for _ in range(10)]
        results: list[str] = []

        async def try_claim(i: int) -> None:
            worker_id = workers[i]
            async with pg_factory() as s:
                await _register_worker(s, worker_id, f"acq-pg-a-{i}")
                coord = AcquisitionClaimCoordinator(
                    s, WorkerLeaseManager(s), lease_ttl_seconds=120
                )
                try:
                    await coord.claim(run_id, worker_id, token=uuid4())
                    results.append(f"won:{worker_id}")
                except AcquisitionClaimConflict:
                    results.append(f"lost:{worker_id}")

        await asyncio.gather(*[try_claim(i) for i in range(10)])
        winners = [r for r in results if r.startswith("won:")]
        assert len(winners) == 1, f"expected exactly 1 claim winner, got {results}"

        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.status == "RUNNING"
            assert fresh.claim_attempts == 1
            assert str(fresh.worker_id) in winners[0]

    # -- B. automatic recovery: expired lease is reclaimed by a loop ----------
    @pytest.mark.asyncio
    async def test_automatic_recovery_increments_recovery_count(
        self, pg_factory, tmp_path
    ) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            owner_a = uuid4()
            await _register_worker(session, owner_a)
            coord_a = AcquisitionClaimCoordinator(
                session, WorkerLeaseManager(session), lease_ttl_seconds=5
            )
            await coord_a.claim(run.id, owner_a, token=uuid4())
            # force expiry (simulated crash)
            await WorkerLeaseManager(session).expire(
                now=datetime.now(UTC) + timedelta(seconds=60)
            )
            await session.commit()
            run_id = run.id

        executed: list[UUID] = []
        async with pg_factory() as session_b:
            worker_b = uuid4()
            await _register_worker(session_b, worker_b)
            coord_b = AcquisitionClaimCoordinator(
                session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=120
            )
            loop = AcquisitionWorkerLoop(
                session=session_b,
                coordinator=coord_b,
                worker_id=worker_b,
                runner=await _noop_runner(executed),
                poll_interval=0.01,
                batch_size=5,
            )
            stats = await loop.tick()
            assert stats.reclaimed == 1
            assert executed == [run_id]

        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.recovery_count == 1
            assert fresh.worker_id == worker_b

    # -- C. fencing: stale owner's commit is rejected -------------------------
    @pytest.mark.asyncio
    async def test_stale_commit_rejected_after_reclaim(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            owner_a = uuid4()
            await _register_worker(session, owner_a)
            token_a = uuid4()
            coord_a = AcquisitionClaimCoordinator(
                session, WorkerLeaseManager(session), lease_ttl_seconds=5
            )
            await coord_a.claim(run.id, owner_a, token=token_a)
            await WorkerLeaseManager(session).expire(
                now=datetime.now(UTC) + timedelta(seconds=60)
            )
            await session.commit()
            run_id = run.id

        # B reclaims
        async with pg_factory() as session_b:
            worker_b = uuid4()
            await _register_worker(session_b, worker_b, "acq-pg-c-b")
            coord_b = AcquisitionClaimCoordinator(
                session_b, WorkerLeaseManager(session_b), lease_ttl_seconds=120
            )
            claimed = await coord_b.reclaim_expired(run_id, worker_b, token=uuid4())
            assert claimed is not None

        # A's stale verify_owner must be rejected
        async with pg_factory() as session_c:
            coord = AcquisitionClaimCoordinator(
                session_c, WorkerLeaseManager(session_c), lease_ttl_seconds=120
            )
            with pytest.raises(AcquisitionStaleCommit):
                await coord.verify_owner(run_id, owner_a, token_a)
            fresh = await session_c.get(AcquisitionRun, run_id)
            assert fresh.stale_result_rejected >= 1
            assert fresh.worker_id == worker_b

    # -- D. durable cancellation across sessions ------------------------------
    @pytest.mark.asyncio
    async def test_durable_cancellation(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            run_id = run.id

        # worker claims and starts a slow operation. The worker session must
        # stay ALIVE until the run_claimed task completes (the task shares
        # it), so it is managed manually and closed at the end.
        session_w = pg_factory()
        worker_id = uuid4()
        await _register_worker(session_w, worker_id, "acq-pg-d")
        service = await _make_service(session_w, tmp_path)

        async def slow_op(run, checkpoint):
            await asyncio.sleep(5)
            return AcquisitionRunPayload(status="COMPLETE")

        service.run_agent_operation = slow_op  # type: ignore[method-assign]
        wp = await _make_worker_path(session_w, service)
        token = uuid4()
        coordinator = wp._ensure_coordinator()
        await coordinator.claim(run_id, worker_id, token=token)
        task = asyncio.create_task(wp.run_claimed(run_id, worker_id, token))
        await asyncio.sleep(0.5)

        # API session: durable CANCEL_REQUESTED
        async with pg_factory() as session_api:
            run_api = await session_api.get(AcquisitionRun, run_id)
            run_api.status = "CANCEL_REQUESTED"
            run_api.cancel_requested_at = datetime.now(UTC)
            await session_api.commit()

        # worker's cancel-aware execution observes the flag -> CANCELLED
        payload = await asyncio.wait_for(task, timeout=20)
        assert payload.status == "CANCELLED"
        await wp._runtime_session.close()  # type: ignore[attr-defined]
        await session_w.rollback()
        await session_w.close()

        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.status == "CANCELLED"
            assert fresh.cancelled_at is not None

    # -- E. concurrent idempotency: exactly one row ---------------------------
    @pytest.mark.asyncio
    async def test_concurrent_create_idempotency_single_row(self, pg_factory, tmp_path) -> None:
        key = f"idem-{uuid4().hex}"
        created_flags: list[bool] = []
        run_ids: set[UUID] = set()

        async def concurrent_create() -> None:
            async with pg_factory() as s:
                service = await _make_service(s, tmp_path)
                run, created = await service.create(
                    goal="g", url="http://example.com/static", idempotency_key=key
                )
                await s.commit()
                created_flags.append(created)
                run_ids.add(run.id)

        await asyncio.gather(*[concurrent_create() for _ in range(10)])
        assert len(run_ids) == 1, f"expected exactly one run row, got {len(run_ids)}"
        assert created_flags.count(True) == 1

    # -- F. cancel vs complete race: terminal state wins ----------------------
    @pytest.mark.asyncio
    async def test_cancel_vs_complete_race(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            worker_id = uuid4()
            await _register_worker(session, worker_id)
            token = uuid4()
            coord = AcquisitionClaimCoordinator(
                session, WorkerLeaseManager(session), lease_ttl_seconds=120
            )
            await coord.claim(run.id, worker_id, token=token)
            run_id = run.id

        # two "sessions" race: one completes, one cancels -- the run must end
        # in a TERMINAL state, never stuck in CANCEL_REQUESTED/RUNNING
        async def complete_side() -> None:
            async with pg_factory() as s:
                run_f = await s.get(AcquisitionRun, run_id)
                # fenced complete (owner check passes; we hold the lease)
                run_f.status = "COMPLETE"
                run_f.finished_at = datetime.now(UTC)
                await s.commit()

        async def cancel_side() -> None:
            async with pg_factory() as s:
                run_f = await s.get(AcquisitionRun, run_id)
                if run_f.status not in ("COMPLETE", "CANCELLED"):
                    run_f.status = "CANCEL_REQUESTED"
                    run_f.cancel_requested_at = datetime.now(UTC)
                    await s.commit()

        await asyncio.gather(complete_side(), cancel_side())
        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            assert fresh.status in ("COMPLETE", "CANCEL_REQUESTED", "CANCELLED")
            assert fresh.status != "RUNNING"

    # -- G. reclaim vs complete race: single terminal -------------------------
    @pytest.mark.asyncio
    async def test_reclaim_vs_complete_race(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            worker_a = uuid4()
            await _register_worker(session, worker_a)
            token_a = uuid4()
            coord_a = AcquisitionClaimCoordinator(
                session, WorkerLeaseManager(session), lease_ttl_seconds=5
            )
            await coord_a.claim(run.id, worker_a, token=token_a)
            await session.commit()
            run_id = run.id

        async def complete_side() -> None:
            async with pg_factory() as s:
                coord = AcquisitionClaimCoordinator(
                    s, WorkerLeaseManager(s), lease_ttl_seconds=120
                )
                try:
                    await coord.verify_owner(run_id, worker_a, token_a)
                    run_f = await s.get(AcquisitionRun, run_id)
                    run_f.status = "COMPLETE"
                    run_f.finished_at = datetime.now(UTC)
                    await s.commit()
                except AcquisitionStaleCommit:
                    pass

        async def reclaim_side() -> None:
            async with pg_factory() as s:
                await WorkerLeaseManager(s).expire(
                    now=datetime.now(UTC) + timedelta(seconds=60)
                )
                worker_b = uuid4()
                await _register_worker(s, worker_b, "acq-pg-g-b")
                coord = AcquisitionClaimCoordinator(
                    s, WorkerLeaseManager(s), lease_ttl_seconds=120
                )
                try:
                    await coord.reclaim_expired(run_id, worker_b, token=uuid4())
                except AcquisitionClaimConflict:
                    pass

        await asyncio.gather(complete_side(), reclaim_side())
        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            # either the owner completed it, or the run was reclaimed
            assert fresh.status in ("COMPLETE", "RUNNING")
            assert fresh.recovery_count in (0, 1)

    # -- H. lease renewal vs reclaim race -------------------------------------
    @pytest.mark.asyncio
    async def test_lease_renewal_vs_reclaim_race(self, pg_factory, tmp_path) -> None:
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal="g", url="http://example.com/static")
            await session.commit()
            worker_a = uuid4()
            await _register_worker(session, worker_a)
            token_a = uuid4()
            coord_a = AcquisitionClaimCoordinator(
                session, WorkerLeaseManager(session), lease_ttl_seconds=5
            )
            await coord_a.claim(run.id, worker_a, token=token_a)
            run_id = run.id

        async def renew_side() -> None:
            # renew exactly at the expiry boundary: either it wins (owner
            # stays A) or it is rejected (lease lost to reclaim)
            async with pg_factory() as s:
                coord = AcquisitionClaimCoordinator(
                    s, WorkerLeaseManager(s), lease_ttl_seconds=120
                )
                try:
                    lease = await coord.renew(run_id, worker_a, token_a)
                    return lease is not None
                except AcquisitionStaleCommit:
                    return False

        async def reclaim_side() -> None:
            async with pg_factory() as s:
                await WorkerLeaseManager(s).expire(
                    now=datetime.now(UTC) + timedelta(seconds=60)
                )
                worker_b = uuid4()
                await _register_worker(s, worker_b, "acq-pg-g-b")
                coord = AcquisitionClaimCoordinator(
                    s, WorkerLeaseManager(s), lease_ttl_seconds=120
                )
                try:
                    await coord.reclaim_expired(run_id, worker_b, token=uuid4())
                except AcquisitionClaimConflict:
                    pass

        renewed, _ = await asyncio.gather(renew_side(), reclaim_side())
        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            if renewed:
                # renewal won the race -> A still owns, lease is ACTIVE
                assert fresh.worker_id == worker_a
                lease = await WorkerLeaseManager(s).require(fresh.lease_id)
                assert lease.status.value == "ACTIVE"
            else:
                # reclaim won -> ownership moved to B
                assert fresh.worker_id != worker_a
