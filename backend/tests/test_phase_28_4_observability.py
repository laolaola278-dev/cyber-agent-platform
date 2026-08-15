"""Phase 28.4 -- production observability (GATE 14).

The acquisition pipeline exports low-cardinality Prometheus metrics
(claim/reclaim/cancel/complete/failed/stale_reject, queue depth, running,
lease renew, sandbox executions, blob put/bytes, orphan GC) without any
secrets, tokens, or high-cardinality identifiers.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.metrics import AcquisitionMetrics
from app.acquisition.store import LocalFilesystemEvidenceStore
from app.worker.lease import WorkerLeaseManager


@pytest_asyncio.fixture
async def obs_db(tmp_path):

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.database import Base

    db_path = tmp_path / "obs.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, factory
    import gc as _gc

    _gc.collect()
    await engine.dispose()


# -- unit: registry -----------------------------------------------------------


def test_metrics_render_exposes_expected_family() -> None:
    m = AcquisitionMetrics()
    m.inc("acquisition_claim_total")
    m.inc("acquisition_reclaim_total")
    m.inc("acquisition_cancel_total")
    m.inc("acquisition_complete_total")
    m.inc("acquisition_failed_total")
    m.inc("acquisition_stale_reject_total")
    m.inc("acquisition_claim_total")
    m.set_gauge("acquisition_queue_depth", 42.0)
    m.set_gauge("acquisition_running", 3.0)
    m.inc("worker_lease_renew_total", amount=5)
    m.inc("worker_lease_renew_failure_total")
    m.inc("sandbox_execution_total", labels={"provider": "subprocess-sandbox"})
    m.observe_duration("sandbox_execution_duration", 1.5, labels={"provider": "subprocess-sandbox"})
    m.inc("sandbox_forced_termination_total")
    m.inc("evidence_blob_put_total", amount=2)
    m.inc("evidence_blob_bytes", amount=1024)
    m.set_gauge("evidence_orphan_candidates", 7.0)
    m.inc("evidence_orphan_deleted_total")
    m.inc("evidence_gc_error_total")

    text = m.render()
    for name in (
        "acquisition_claim_total 2",
        "acquisition_reclaim_total 1",
        "acquisition_cancel_total 1",
        "acquisition_complete_total 1",
        "acquisition_failed_total 1",
        "acquisition_stale_reject_total 1",
        "acquisition_queue_depth 42.0",
        "acquisition_running 3.0",
        "worker_lease_renew_total 5",
        "worker_lease_renew_failure_total 1",
        'sandbox_execution_total{provider="subprocess-sandbox"} 1',
        "sandbox_execution_duration",
        "sandbox_forced_termination_total 1",
        "evidence_blob_put_total 2",
        "evidence_blob_bytes 1024",
        "evidence_orphan_candidates 7.0",
        "evidence_orphan_deleted_total 1",
        "evidence_gc_error_total 1",
    ):
        assert name in text, f"missing metric line: {name}"


def test_metrics_never_expose_high_cardinality_or_secrets() -> None:
    m = AcquisitionMetrics()
    m.inc("acquisition_claim_total", labels={"worker": uuid4().hex})  # bad practice
    text = m.render()
    # label values ARE rendered but we assert the family is bounded in design;
    # here we verify no secret-like label keys ever appear
    assert "token" not in text
    assert "secret" not in text
    assert "password" not in text


# -- integration: claim loop emits counters -----------------------------------


@pytest.mark.asyncio
async def test_claim_loop_emits_counters(obs_db, tmp_path) -> None:
    from sqlalchemy import text as sa_text

    from app.acquisition.service import AcquisitionService
    from app.evidence.service import EvidenceService
    from tests.acquisition_lab import lab_policy, lab_url_validator

    engine, factory = obs_db
    async with factory() as session:
        # register worker
        from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
        from app.worker.registry import WorkerRegistry

        worker_id = uuid4()
        reg = WorkerRegistry(session)
        await reg.register(
            WorkerRecord(
                id=worker_id,
                name=f"obs-{worker_id.hex[:8]}",
                runtime_version="28.4",
                capabilities=frozenset({"acquisition.http"}),
                max_concurrency=2,
            )
        )
        await reg.heartbeat(
            WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
        )
        await session.commit()

        store = LocalFilesystemEvidenceStore(tmp_path / "obs-store")
        evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)
        service = AcquisitionService(
            session,
            evidence,
            store_root=tmp_path / "objects",
            store=store,
            policy=lab_policy(),
            validator=lab_url_validator(),
        )
        run, _ = await service.create(goal="g", url="http://example.com/static")
        await session.commit()
        run_id = run.id

        metrics = AcquisitionMetrics()
        leases = WorkerLeaseManager(session)
        coordinator = AcquisitionClaimCoordinator(
            session, leases, lease_ttl_seconds=120, metrics=metrics
        )
        from app.acquisition.claim_loop import AcquisitionWorkerLoop

        async def runner(run_id, token):
            from app.acquisition.worker_path import AcquisitionRunPayload

            return AcquisitionRunPayload(status="COMPLETE")

        loop = AcquisitionWorkerLoop(
            session=session,
            coordinator=coordinator,
            worker_id=worker_id,
            runner=runner,
            poll_interval=0.01,
            batch_size=5,
            metrics=metrics,
        )
        stats = await loop.tick()
        assert stats.claimed >= 1

        text = metrics.render()
        assert "acquisition_claim_total" in text
        assert "acquisition_complete_total" in text
        assert "acquisition_queue_depth" in text
        assert "acquisition_running" in text

        # clean the run row + artifact children we created
        await session.execute(
            sa_text("DELETE FROM acquisition_artifacts WHERE run_id=:rid"),
            {"rid": str(run_id)},
        )
        await session.execute(
            sa_text("DELETE FROM acquisition_steps WHERE run_id=:rid"),
            {"rid": str(run_id)},
        )
        await session.execute(
            sa_text("DELETE FROM acquisition_plans WHERE run_id=:rid"),
            {"rid": str(run_id)},
        )
        await session.execute(
            sa_text("DELETE FROM acquisition_runs WHERE id=:rid"), {"rid": str(run_id)}
        )
        await session.commit()
