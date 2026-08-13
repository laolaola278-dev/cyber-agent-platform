"""Phase 28.5 -- orphan container reaper tests (fencing-safe)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.sandbox.oci_provider import (
    LABEL_ATTEMPT,
    LABEL_EXECUTION,
    LABEL_LEASE,
    LABEL_RUN,
    LABEL_WORKER,
)
from app.sandbox.oci_reaper import OCIContainerReaper

pytestmark = [pytest.mark.certification, pytest.mark.oci]


class FakeDriver:
    driver_name = "fake"

    def __init__(self, containers: list[dict]) -> None:
        self.containers = containers
        self.killed: list[str] = []
        self.removed: list[str] = []

    async def health(self) -> bool:
        return True

    async def list_by_labels(self, labels: dict[str, str]) -> list[dict]:
        return self.containers

    async def kill(self, container_id: str) -> None:
        self.killed.append(container_id)

    async def rm(self, container_id: str, force: bool = True) -> None:
        self.removed.append(container_id)


def _container(execution_id: str, run_id: str, worker_id: str, lease_id: str) -> dict:
    return {
        "Id": f"c-{execution_id[:8]}",
        "Config": {
            "Labels": {
                LABEL_EXECUTION: execution_id,
                LABEL_RUN: run_id,
                LABEL_WORKER: worker_id,
                LABEL_LEASE: lease_id,
                LABEL_ATTEMPT: "1",
            }
        },
    }


@pytest_asyncio.fixture
async def db_factory(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'reaper.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    import gc as _gc

    _gc.collect()
    await engine.dispose()


async def _seed_run(factory, run_id: str, worker_id, lease_id) -> None:
    from app.acquisition.models_db import AcquisitionRun

    async with factory() as session:
        session.add(
            AcquisitionRun(
                id=__import__('uuid').UUID(run_id),
                idempotency_key=f"reaper-{uuid4().hex}",
                request_fingerprint=f"fp-{uuid4().hex}",
                status="RUNNING",
                worker_id=worker_id,
                lease_id=lease_id,
                goal="g",
                source_type="web",
                strategy="paged",
                task_id=uuid4(),
                agent_id=uuid4(),
                trace_id=f"reaper-{uuid4().hex[:8]}",
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        await session.commit()


async def _register_worker(factory, worker_id: str) -> None:
    from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
    from app.worker.registry import WorkerRegistry

    async with factory() as session:
        reg = WorkerRegistry(session)
        await reg.register(
            WorkerRecord(
                id=worker_id,
                name=f"reaper-w-{worker_id.hex[:6]}",
                runtime_version="28.5",
                capabilities=frozenset({"acquisition.http"}),
                max_concurrency=2,
            )
        )
        await reg.heartbeat(
            WorkerHeartbeat(
                worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0
            )
        )


@pytest.mark.asyncio
async def test_reaper_kills_stale_lease_and_keeps_current_owner(db_factory) -> None:
    """Fencing: the NEW owner's container (different lease/execution ids) is
    never touched; only the stale A container is removed."""
    run_id = str(uuid4())
    worker_a, worker_b = uuid4(), uuid4()
    lease_a = str(uuid4())           # A's OLD lease (stale)
    current_lease = uuid4()          # the run is now owned via THIS lease (B)
    # after B reclaims, the run's worker is B (the current owner)
    await _seed_run(db_factory, run_id, worker_b, current_lease)
    await _register_worker(db_factory, worker_b)

    stale_a = _container(str(uuid4()), run_id, str(worker_a), lease_a)
    current_b = _container(str(uuid4()), run_id, str(worker_b), str(current_lease))
    driver = FakeDriver([stale_a, current_b])
    reaper = OCIContainerReaper(driver, db_factory)

    stats = await reaper.reconcile_once()

    assert stats.scanned == 2
    assert stats.stale == 1
    assert stats.removed == 1
    # only A's container id removed; B's container is untouched
    assert driver.removed == [stale_a["Id"]]
    assert current_b["Id"] not in driver.removed


@pytest.mark.asyncio
async def test_reaper_keeps_live_current_owner(db_factory) -> None:
    worker = uuid4()
    current_lease = uuid4()
    run_id = str(uuid4())
    await _seed_run(db_factory, run_id, worker, current_lease)
    await _register_worker(db_factory, worker)

    live = _container(str(uuid4()), run_id, str(worker), str(current_lease))
    driver = FakeDriver([live])
    reaper = OCIContainerReaper(driver, db_factory)

    stats = await reaper.reconcile_once()
    assert stats.stale == 0
    assert stats.removed == 0
    assert driver.removed == []


@pytest.mark.asyncio
async def test_reaper_removes_orphan_with_no_run_row(db_factory) -> None:
    orphan = _container(str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4()))
    driver = FakeDriver([orphan])
    reaper = OCIContainerReaper(driver, db_factory)

    stats = await reaper.reconcile_once()
    assert stats.stale == 1
    assert stats.removed == 1


@pytest.mark.asyncio
async def test_reaper_removes_container_of_dead_worker(db_factory) -> None:
    worker = uuid4()  # NOT registered -> dead
    current_lease = uuid4()
    run_id = str(uuid4())
    await _seed_run(db_factory, run_id, worker, current_lease)

    orphan = _container(str(uuid4()), run_id, str(worker), str(current_lease))
    driver = FakeDriver([orphan])
    reaper = OCIContainerReaper(driver, db_factory)

    stats = await reaper.reconcile_once()
    assert stats.stale == 1
    assert stats.removed == 1
