"""Phase 28.4 -- worker liveness/readiness (GATE 15).

Readiness reflects critical dependencies (DB / schema / registration /
object store / sandbox provider). A failed acquisition never flips readiness;
an unreachable dependency does.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.acquisition.health import WorkerHealth
from app.database import Base


@pytest_asyncio.fixture
async def db_factory(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'h.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # health.schema_compatible checks the alembic_version row
        await conn.execute(
            __import__("sqlalchemy").text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        await conn.execute(
            __import__("sqlalchemy").text(
                "INSERT OR IGNORE INTO alembic_version (version_num) VALUES ('health-test')"
            )
        )
    yield factory
    import gc as _gc

    _gc.collect()
    await engine.dispose()


class _FakeStore:
    async def health(self) -> bool:
        return self._ok

    def __init__(self, ok: bool = True) -> None:
        self._ok = ok


class _FakeSandbox:
    async def health(self) -> bool:
        return self._ok

    def __init__(self, ok: bool = True) -> None:
        self._ok = ok


async def _register_worker(factory, worker_id) -> None:
    from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
    from app.worker.registry import WorkerRegistry

    async with factory() as session:
        reg = WorkerRegistry(session)
        await reg.register(
            WorkerRecord(
                id=worker_id,
                name=f"health-{worker_id.hex[:8]}",
                runtime_version="28.4",
                capabilities=frozenset({"acquisition.http"}),
                max_concurrency=2,
            )
        )
        await reg.heartbeat(
            WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
        )


@pytest.mark.asyncio
async def test_readiness_healthy_when_all_dependencies_up(db_factory) -> None:
    worker_id = uuid4()
    await _register_worker(db_factory, worker_id)
    health = WorkerHealth(
        session_factory=db_factory,
        object_store=_FakeStore(True),
        sandbox_runtime=_FakeSandbox(True),
        worker_id=worker_id,
    )
    result = await health.readiness()
    assert result.healthy is True
    assert all(result.checks.values())


@pytest.mark.asyncio
async def test_readiness_false_when_db_down() -> None:
    # SQLite auto-creates missing files, so an unreachable DB is simulated
    # with a factory whose connect raises (what a down PostgreSQL does)
    class _BrokenEngine:
        async def connect(self):
            raise ConnectionRefusedError("db down")

    class _BrokenFactory:
        def __call__(self):
            raise ConnectionRefusedError("db down")

    class _BrokenSession:
        def __init__(self):
            raise ConnectionRefusedError("db down")

    def broken_factory():
        raise ConnectionRefusedError("db down")

    health = WorkerHealth(
        session_factory=broken_factory,
        object_store=_FakeStore(True),
        sandbox_runtime=_FakeSandbox(True),
        worker_id=uuid4(),
    )
    result = await health.readiness()
    assert result.healthy is False
    assert result.checks["db_connectivity"] is False
    assert result.checks["schema_compatible"] is False


@pytest.mark.asyncio
async def test_readiness_false_when_object_store_down(db_factory) -> None:
    worker_id = uuid4()
    await _register_worker(db_factory, worker_id)
    health = WorkerHealth(
        session_factory=db_factory,
        object_store=_FakeStore(False),
        sandbox_runtime=_FakeSandbox(True),
        worker_id=worker_id,
    )
    result = await health.readiness()
    assert result.healthy is False
    assert result.checks["object_store"] is False


@pytest.mark.asyncio
async def test_readiness_false_when_sandbox_down(db_factory) -> None:
    worker_id = uuid4()
    await _register_worker(db_factory, worker_id)
    health = WorkerHealth(
        session_factory=db_factory,
        object_store=_FakeStore(True),
        sandbox_runtime=_FakeSandbox(False),
        worker_id=worker_id,
    )
    result = await health.readiness()
    assert result.healthy is False
    assert result.checks["sandbox_provider"] is False


@pytest.mark.asyncio
async def test_readiness_false_without_registration(db_factory) -> None:
    # worker never registered -> not ready to claim
    health = WorkerHealth(
        session_factory=db_factory,
        object_store=_FakeStore(True),
        sandbox_runtime=_FakeSandbox(True),
        worker_id=uuid4(),
    )
    result = await health.readiness()
    assert result.checks["worker_registration"] is False


@pytest.mark.asyncio
async def test_claim_loop_stops_claiming_when_unready(db_factory, tmp_path) -> None:
    """A worker whose readiness is false must skip claiming new work."""
    from app.acquisition.claim import AcquisitionClaimCoordinator
    from app.acquisition.claim_loop import AcquisitionWorkerLoop
    from app.acquisition.service import AcquisitionService
    from app.evidence.service import EvidenceService
    from app.worker.lease import WorkerLeaseManager
    from tests.acquisition_lab import lab_policy, lab_url_validator

    worker_id = uuid4()
    await _register_worker(db_factory, worker_id)

    async with db_factory() as session:
        evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)
        service = AcquisitionService(
            session,
            evidence,
            store_root=tmp_path / "objects",
            policy=lab_policy(),
            validator=lab_url_validator(),
        )
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        run_id = run.id

        leases = WorkerLeaseManager(session)
        coordinator = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=120)
        executed: list[str] = []

        async def runner(run_id, token):
            executed.append(str(run_id))
            from app.acquisition.worker_path import AcquisitionRunPayload

            return AcquisitionRunPayload(status="COMPLETE")

        loop = AcquisitionWorkerLoop(
            session=session,
            coordinator=coordinator,
            worker_id=worker_id,
            runner=runner,
            poll_interval=0.01,
            batch_size=5,
            readiness=lambda: _never_ready(),
        )
        stats = await loop.tick()
        assert stats.claimed == 0
        assert executed == [], "runner must not run while unready"

        # run still QUEUED
        from app.acquisition.models_db import AcquisitionRun

        fresh = await session.get(AcquisitionRun, run_id)
        assert fresh.status == "QUEUED"
        await session.execute(
            __import__("sqlalchemy").text("DELETE FROM acquisition_runs WHERE id=:rid"),
            {"rid": str(run_id)},
        )
        await session.commit()


async def _never_ready() -> bool:
    return False
