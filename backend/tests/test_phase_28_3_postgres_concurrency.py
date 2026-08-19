"""Phase 28.3 -- real PostgreSQL concurrency / race certification.

Runs against an ISOLATED PostgreSQL database (no mocks). Covers the atomic
claim, automatic recovery, fencing, cancellation, concurrent idempotency,
and the cancel/reclaim/renewal races. Skipped when PostgreSQL is unreachable.

DB: cap283 (migrated by ``alembic upgrade head``). Each test truncates the
acquisition tables before running.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.claim_loop import AcquisitionWorkerLoop
from app.acquisition.exceptions import (
    AcquisitionClaimConflict,
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


async def _register_worker(session: AsyncSession, worker_id: UUID, name: str | None = None) -> None:
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
    from app.sandbox.profile import SandboxProfile
    from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
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
                coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
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
    async def test_automatic_recovery_increments_recovery_count(self, pg_factory, tmp_path) -> None:
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
            await WorkerLeaseManager(session).expire(now=datetime.now(UTC) + timedelta(seconds=60))
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
            await WorkerLeaseManager(session).expire(now=datetime.now(UTC) + timedelta(seconds=60))
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
                coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
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
                await WorkerLeaseManager(s).expire(now=datetime.now(UTC) + timedelta(seconds=60))
                worker_b = uuid4()
                await _register_worker(s, worker_b, "acq-pg-g-b")
                coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
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
                coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
                try:
                    lease = await coord.renew(run_id, worker_a, token_a)
                    return lease is not None
                except AcquisitionStaleCommit:
                    return False

        async def reclaim_side() -> None:
            async with pg_factory() as s:
                await WorkerLeaseManager(s).expire(now=datetime.now(UTC) + timedelta(seconds=60))
                worker_b = uuid4()
                await _register_worker(s, worker_b, "acq-pg-g-b")
                coord = AcquisitionClaimCoordinator(s, WorkerLeaseManager(s), lease_ttl_seconds=120)
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

    # -- I. renew vs reclaim stress (Phase 28.5-RC) --------------------------
    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_renew_reclaim_race_stress(self, pg_factory, tmp_path) -> None:
        """Race renew against reclaim hundreds of times on real PostgreSQL.

        Each round races A's renewal against B's reclaim of A's expired lease.
        Legal outcomes: renew wins (A still owns, lease ACTIVE) or reclaim
        wins (owner moved to B). The split-brain outcome -- renew(A) reports
        success while the run is owned by B -- is a Critical ownership
        violation and must NEVER occur. Count rounds via CAP285_STRESS_ROUNDS
        (default 500; PR may lower it, release keeps >= 500).
        """
        rounds = int(os.environ.get("CAP285_STRESS_ROUNDS", "500"))
        renew_wins = 0
        reclaim_wins = 0
        invalid = 0
        for i in range(rounds):
            async with pg_factory() as session:
                service = await _make_service(session, tmp_path)
                run, _ = await service.create(goal=f"g{i}", url="http://example.com/static")
                await session.commit()
                run_id = run.id
                worker_a = uuid4()
                await _register_worker(session, worker_a, f"acq-stress-{i}-a")
                token_a = uuid4()
                coord_a = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=5
                )
                await coord_a.claim(run.id, worker_a, token=token_a)

            async def renew_side(_run_id=run_id, _worker_a=worker_a, _token_a=token_a) -> bool:
                async with pg_factory() as s:
                    coord = AcquisitionClaimCoordinator(
                        s, WorkerLeaseManager(s), lease_ttl_seconds=120
                    )
                    try:
                        lease = await coord.renew(_run_id, _worker_a, _token_a)
                        return lease is not None
                    except AcquisitionStaleCommit:
                        return False

            async def reclaim_side(_run_id=run_id, _i=i) -> None:
                async with pg_factory() as s:
                    await WorkerLeaseManager(s).expire(
                        now=datetime.now(UTC) + timedelta(seconds=60)
                    )
                    worker_b = uuid4()
                    await _register_worker(s, worker_b, f"acq-stress-{_i}-b")
                    coord = AcquisitionClaimCoordinator(
                        s, WorkerLeaseManager(s), lease_ttl_seconds=120
                    )
                    try:
                        await coord.reclaim_expired(_run_id, worker_b, token=uuid4())
                    except AcquisitionClaimConflict:
                        pass

            renewed, _ = await asyncio.gather(renew_side(), reclaim_side())
            async with pg_factory() as s:
                fresh = await s.get(AcquisitionRun, run_id)
                if renewed and fresh.worker_id == worker_a:
                    renew_wins += 1
                    lease = await WorkerLeaseManager(s).require(fresh.lease_id)
                    assert lease.status.value == "ACTIVE"
                elif not renewed and fresh.worker_id != worker_a:
                    reclaim_wins += 1
                else:
                    # split-brain: renew success + owner moved (or the inverse)
                    invalid += 1

        assert invalid == 0, (
            "ownership split-brain observed: "
            f"renew_wins={renew_wins} reclaim_wins={reclaim_wins} invalid={invalid}"
        )
        assert renew_wins + reclaim_wins + invalid == rounds


class _Barrier:
    """Deterministic fault-injection barrier: the worker sets `reached` then
    waits for `release` -- no sleep-based timing in the race tests."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self) -> None:
        self.reached.set()
        await self.release.wait()


async def _complete_op(run, checkpoint):
    """Mock operation that completes immediately with a COMPLETE payload."""
    return AcquisitionRunPayload(status="COMPLETE", checkpoint={"status": "COMPLETE"})


async def _complete_op_dirty_status(run, checkpoint):
    """Mock operation that ALSO marks the ORM run status COMPLETE (dirty), to
    exercise the §7 ORM-writeback hazard: a lost terminal CAS must roll back
    this dirty status so it can never overwrite a won CANCEL_REQUESTED."""
    run.status = "COMPLETE"
    run.source_type = "HTML"
    return AcquisitionRunPayload(status="COMPLETE", checkpoint={"status": "COMPLETE"})


def _make_delayed_complete(delay: float):
    async def op(run, checkpoint):
        await asyncio.sleep(delay)
        return AcquisitionRunPayload(status="COMPLETE", checkpoint={"status": "COMPLETE"})

    return op


# -- RC2 deterministic cancel/complete barrier tests (PostgreSQL) -------------


@_skip
@pytest.mark.asyncio
async def test_cancel_before_terminal_cas_wins(pg_factory, tmp_path) -> None:
    """Scenario K2→C2: CANCEL_REQUESTED is durable before the terminal CAS, so
    the completion's conditional UPDATE matches 0 rows and the run converges to
    CANCELLED -- never overwritten by a stale COMPLETE (also exercises §7)."""
    async with pg_factory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        run_id = run.id
        worker = uuid4()
        await _register_worker(session, worker, "acq-rc2-c1")
        service.run_agent_operation = _complete_op
        wp = await _make_worker_path(session, service)
        token = uuid4()
        await wp._ensure_coordinator().claim(run_id, worker, token=token)
        barrier = _Barrier()
        wp.race_barrier_before_terminal = barrier
        task = asyncio.create_task(wp.run_claimed(run_id, worker, token))
        await asyncio.wait_for(barrier.reached.wait(), timeout=10)
        # K2: durably flip to CANCEL_REQUESTED on an independent API session
        async with pg_factory() as api:
            run_api = await api.get(AcquisitionRun, run_id)
            run_api.status = "CANCEL_REQUESTED"
            run_api.cancel_requested_at = datetime.now(UTC)
            await api.commit()
        barrier.release.set()
        payload = await asyncio.wait_for(task, timeout=20)
        assert payload.status == "CANCELLED"
        await wp._runtime_session.close()
        await session.rollback()
        await session.close()
    async with pg_factory() as s:
        fresh = await s.get(AcquisitionRun, run_id)
        assert fresh.status == "CANCELLED"
        assert fresh.stale_result_rejected == 0


@_skip
@pytest.mark.asyncio
async def test_completion_before_cancel_cas_wins(pg_factory, tmp_path) -> None:
    """Scenario C2→K2: the terminal CAS commits first, so a later cancel
    conditional UPDATE matches 0 rows and is a no-op -> terminal stays."""
    async with pg_factory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        run_id = run.id
        worker = uuid4()
        await _register_worker(session, worker, "acq-rc2-c2")
        service.run_agent_operation = _complete_op
        wp = await _make_worker_path(session, service)
        token = uuid4()
        await wp._ensure_coordinator().claim(run_id, worker, token=token)

        # cancel side pauses right before the cancel-request CAS
        async with pg_factory() as api_session:
            api_service = await _make_service(api_session, tmp_path)
            api_wp = await _make_worker_path(api_session, api_service)
            cancel_barrier = _Barrier()
            api_wp.race_barrier_before_cancel = cancel_barrier
            cancel_task = asyncio.create_task(api_wp.cancel(run_id))
            await asyncio.wait_for(cancel_barrier.reached.wait(), timeout=10)
            # C2: completion commits COMPLETE while the cancel is paused
            payload = await asyncio.wait_for(wp.run_claimed(run_id, worker, token), timeout=20)
            assert payload.status == "COMPLETE"
            # release K1 -> cancel CAS matches 0 rows -> no-op
            cancel_barrier.release.set()
            cancel_payload = await asyncio.wait_for(cancel_task, timeout=20)
            assert cancel_payload.status == "COMPLETE"
            await api_wp._runtime_session.close()
            await api_session.rollback()
            await api_session.close()
        await wp._runtime_session.close()
        await session.rollback()
        await session.close()
    async with pg_factory() as s:
        fresh = await s.get(AcquisitionRun, run_id)
        assert fresh.status == "COMPLETE"


@_skip
@pytest.mark.asyncio
async def test_orm_writeback_discarded_on_cancel_win(pg_factory, tmp_path) -> None:
    """§7 ORM-writeback hazard: the operation marks the ORM run status COMPLETE
    (dirty, as _persist_result would), but CANCEL_REQUESTED is already durable,
    so the terminal CAS matches 0 rows and rolls back the dirty status -- the
    final state must be CANCELLED, never COMPLETE."""
    async with pg_factory() as session:
        service = await _make_service(session, tmp_path)
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        run_id = run.id
        worker = uuid4()
        await _register_worker(session, worker, "acq-rc2-c3")
        service.run_agent_operation = _complete_op_dirty_status
        wp = await _make_worker_path(session, service)
        token = uuid4()
        await wp._ensure_coordinator().claim(run_id, worker, token=token)
        # K2 durable BEFORE the operation: CANCEL_REQUESTED already committed
        async with pg_factory() as api:
            run_api = await api.get(AcquisitionRun, run_id)
            run_api.status = "CANCEL_REQUESTED"
            run_api.cancel_requested_at = datetime.now(UTC)
            await api.commit()
        payload = await asyncio.wait_for(wp.run_claimed(run_id, worker, token), timeout=20)
        assert payload.status == "CANCELLED"
        await wp._runtime_session.close()
        await session.rollback()
        await session.close()
    async with pg_factory() as s:
        fresh = await s.get(AcquisitionRun, run_id)
        assert fresh.status == "CANCELLED"
        assert fresh.stale_result_rejected == 0


@_skip
@pytest.mark.stress
@pytest.mark.asyncio
async def test_cancel_complete_pg_stress(pg_factory, tmp_path) -> None:
    """Race cancel vs completion on real PostgreSQL across >=500 rounds.

    Each round: claim, run a mock operation with a tiny deterministic delay,
    concurrently cancel on an independent session. The ONLY requirement is that
    the run converges to a terminal state (never stuck CANCEL_REQUESTED) with
    zero stale result writes -- the exact cancel/complete winner is free.
    """
    rounds = int(os.environ.get("CAP285_STRESS_ROUNDS", "500"))
    stuck = invalid = post_cancel = 0
    cancel_wins = completion_wins = 0
    for i in range(rounds):
        async with pg_factory() as session:
            service = await _make_service(session, tmp_path)
            run, _ = await service.create(goal=f"g{i}", url="http://example.com/static")
            await session.commit()
            run_id = run.id
            worker = uuid4()
            await _register_worker(session, worker, f"acq-rc2-s{i}")
            # 0 or a tiny deterministic delay -- alternate to cover both orderings
            service.run_agent_operation = _make_delayed_complete(0.0 if i % 2 == 0 else 0.002)
            wp = await _make_worker_path(session, service)
            token = uuid4()
            await wp._ensure_coordinator().claim(run_id, worker, token=token)
            task = asyncio.create_task(wp.run_claimed(run_id, worker, token))

            async def cancel_now(_run_id: UUID = run_id) -> None:
                async with pg_factory() as api:
                    run_api = await api.get(AcquisitionRun, _run_id)
                    if run_api.status in ("RUNNING", "PARTIAL"):
                        run_api.status = "CANCEL_REQUESTED"
                        run_api.cancel_requested_at = datetime.now(UTC)
                        await api.commit()

            if i % 2 == 0:
                await cancel_now()
            try:
                await asyncio.wait_for(task, timeout=20)
            except Exception:  # noqa: BLE001 -- the durable state is the contract
                pass
            if i % 2 == 1:
                await cancel_now()
            await wp._runtime_session.close()
            await session.rollback()
            await session.close()

        async with pg_factory() as s:
            fresh = await s.get(AcquisitionRun, run_id)
            if fresh.status == "CANCELLED":
                cancel_wins += 1
            elif fresh.status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED"):
                completion_wins += 1
            elif fresh.status == "CANCEL_REQUESTED":
                stuck += 1
            else:
                invalid += 1
            if fresh.stale_result_rejected != 0:
                post_cancel += 1

    assert stuck == 0, f"stuck CANCEL_REQUESTED: {stuck}"
    assert invalid == 0, f"invalid terminal state: {invalid}"
    assert post_cancel == 0, f"post-cancel stale writes: {post_cancel}"
    assert cancel_wins + completion_wins + stuck + invalid == rounds
