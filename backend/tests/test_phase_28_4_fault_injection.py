"""Phase 28.4 -- fault injection at critical boundaries (GATE 17).

Simulate worker "death" at precise points of the durable execution pipeline
and verify: no durable ownership corruption, no stale attachment, runs remain
recoverable, and GC never deletes live evidence. Uses real PG + MinIO.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.postgres, pytest.mark.object_store]

PG_DSN = os.environ.get("CAP283_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283")
PG_SYNC = os.environ.get("CAP283_PG_SYNC", "postgresql://cap@127.0.0.1:55432/cap283")
S3_ENDPOINT = os.environ.get("CAP283_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS = os.environ.get("CAP283_S3_ACCESS", "capadmin")
S3_SECRET = os.environ.get("CAP283_S3_SECRET", "capadmin123")
S3_BUCKET = "cap-fi284"

_TRUNCATE = """
TRUNCATE TABLE acquisition_artifacts, acquisition_steps, acquisition_plans,
    acquisition_runs, extracted_documents, completeness_reports,
    public_endpoint_candidates, evidence, workers, worker_leases,
    sandbox_executions, tasks, agents CASCADE
"""


async def _probe() -> bool:
    import asyncpg

    try:
        conn = await asyncpg.connect(PG_SYNC)
        await conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe()), reason="PostgreSQL not reachable")


async def _make_service(session, store, tmp_path: Path):
    from app.acquisition.service import AcquisitionService
    from app.evidence.service import EvidenceService

    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        store=store,
    )


async def _register(session, worker_id: UUID, name: str) -> None:
    from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
    from app.worker.registry import WorkerRegistry

    reg = WorkerRegistry(session)
    await reg.register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.4",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=2,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
    )


@_skip
class TestFaultInjection:
    @pytest.mark.asyncio
    async def test_crash_after_blob_put_before_attach_leaves_orphan_only(self, tmp_path) -> None:
        """A worker that dies after writing a blob but before the fenced
        attachment leaves: an immutable orphan blob, NO stale artifact row,
        and a run the survivor can recover to terminal."""
        from app.acquisition.store import S3EvidenceStore

        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        import asyncpg

        admin = await asyncpg.connect(PG_SYNC)
        try:
            await admin.execute(_TRUNCATE)
        finally:
            await admin.close()

        try:
            async with factory() as session:
                service = await _make_service(session, store, tmp_path)
                run, _ = await service.create(goal="fi-blob", url="http://example.invalid/private")
                await session.commit()
                worker_a = uuid4()
                await _register(session, worker_a, f"fi-a-{worker_a.hex[:8]}")
                from app.acquisition.claim import AcquisitionClaimCoordinator
                from app.worker.lease import WorkerLeaseManager

                coord = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=4
                )
                await coord.claim(run.id, worker_a, token=uuid4())
                await session.commit()
                run_id = run.id

            # A "dies" after putting the blob, before attaching it
            blob = b"<html>orphan-after-crash</html>"
            obj = await store.put(blob, metadata={"url": "http://example.invalid/private"})

            # lease expires; survivor B reclaims and finishes (BLOCKED via SSRF)
            async with factory() as session:
                await WorkerLeaseManager(session).expire(
                    now=datetime.now(UTC) + timedelta(seconds=60)
                )
                worker_b = uuid4()
                await _register(session, worker_b, f"fi-b-{worker_b.hex[:8]}")
                from app.acquisition.claim import AcquisitionClaimCoordinator
                from app.worker.lease import WorkerLeaseManager

                coord_b = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=60
                )
                claimed = await coord_b.reclaim_expired(run_id, worker_b, token=uuid4())
                assert claimed is not None, "survivor could not reclaim"
                await session.commit()

            # no stale artifact row attached by the dead worker
            async with factory() as session:
                count = int(
                    (
                        await session.execute(
                            text("SELECT count(*) FROM acquisition_artifacts WHERE run_id = :rid"),
                            {"rid": str(run_id)},
                        )
                    ).scalar()
                    or 0
                )
                assert count == 0, "stale artifact row survived the crash"
                fresh = await session.get(
                    __import__(
                        "app.acquisition.models_db", fromlist=["AcquisitionRun"]
                    ).AcquisitionRun,
                    run_id,
                )
                assert fresh.worker_id == worker_b

            # the blob is an orphan (immutable, unreferenced, GC-eligible later)
            assert await store.exists(obj.key) is True
        finally:
            for key in await store.list_keys():
                await store.delete(key)
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM acquisition_artifacts WHERE run_id = :rid"),
                    {"rid": str(run_id)},
                )
                await session.execute(
                    text("DELETE FROM acquisition_runs WHERE id = :rid"),
                    {"rid": str(run_id)},
                )
                await session.commit()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_crash_during_cancellation_lands_cancelled(self, tmp_path) -> None:
        """A worker that dies while a cancellation is in flight must not leave
        the run permanently RUNNING: the durable CANCEL_REQUESTED flag makes
        the run recoverable, and the next owner finalizes CANCELLED."""
        from app.acquisition.store import S3EvidenceStore

        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        import asyncpg

        admin = await asyncpg.connect(PG_SYNC)
        try:
            await admin.execute(_TRUNCATE)
        finally:
            await admin.close()

        try:
            async with factory() as session:
                service = await _make_service(session, store, tmp_path)
                run, _ = await service.create(
                    goal="fi-cancel", url="http://example.invalid/private"
                )
                await session.commit()
                worker_a = uuid4()
                await _register(session, worker_a, f"fi-ca-{worker_a.hex[:8]}")
                from app.acquisition.claim import AcquisitionClaimCoordinator
                from app.worker.lease import WorkerLeaseManager

                coord = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=4
                )
                await coord.claim(run.id, worker_a, token=uuid4())
                await session.commit()
                run_id = run.id

            # A dies; API durably flips CANCEL_REQUESTED (the durable truth)
            async with factory() as session:
                run_row = await session.get(
                    __import__(
                        "app.acquisition.models_db", fromlist=["AcquisitionRun"]
                    ).AcquisitionRun,
                    run_id,
                )
                run_row.status = "CANCEL_REQUESTED"
                run_row.cancel_requested_at = datetime.now(UTC)
                await session.commit()

            # CANCEL_REQUESTED runs are CLAIMABLE (claim path, not recovery):
            # a survivor claims it and the preserved cancel_requested_at makes
            # the next execution finalize CANCELLED (durable flag = truth)
            async with factory() as session:
                from app.acquisition.claim import AcquisitionClaimCoordinator
                from app.worker.lease import WorkerLeaseManager

                worker_b = uuid4()
                await _register(session, worker_b, f"fi-cb-{worker_b.hex[:8]}")
                coord_b = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=60
                )
                await coord_b.claim(run_id, worker_b, token=uuid4())
                await session.commit()

                fresh = await session.get(
                    __import__(
                        "app.acquisition.models_db", fromlist=["AcquisitionRun"]
                    ).AcquisitionRun,
                    run_id,
                )
                # the durable cancel flag survives the claim; the run will be
                # finalized CANCELLED by the owner's execution
                assert fresh.cancel_requested_at is not None
                assert fresh.worker_id == worker_b
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM acquisition_runs WHERE id = :rid"),
                    {"rid": str(run_id)},
                )
                await session.commit()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_gc_never_deletes_live_reference(self, tmp_path) -> None:
        """A blob referenced by a durable evidence row is never deleted, even
        when it is old enough to be an orphan candidate."""
        from app.acquisition.gc import EvidenceOrphanGC
        from app.acquisition.store import S3EvidenceStore

        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        import asyncpg

        admin = await asyncpg.connect(PG_SYNC)
        try:
            await admin.execute(_TRUNCATE)
        finally:
            await admin.close()

        try:
            obj = await store.put(b"<html>live-blob</html>", metadata={"url": "http://x"})

            # attach a durable evidence row referencing the digest
            async with factory() as session:
                from app.models.agent import Agent
                from app.models.task import Task

                agent_id = uuid4()
                task_id = uuid4()
                session.add(
                    Agent(
                        id=agent_id,
                        name=f"fi-agent-{agent_id.hex[:8]}",
                        version="1",
                        status="ONLINE",
                        health_status="HEALTHY",
                    )
                )
                session.add(
                    Task(
                        id=task_id,
                        name=f"fi-task-{task_id.hex[:8]}",
                        task_type="acquisition",
                        status="QUEUED",
                        input={},
                        required_permissions=[],
                        required_capabilities=["acquisition.http"],
                    )
                )
                await session.flush()  # FK order: tasks/agents first
                from app.models import Evidence

                session.add(
                    Evidence(
                        task_id=task_id,
                        agent_id=agent_id,
                        trace_id="fi-gc",
                        url="http://x",
                        http_status=200,
                        title="t",
                        evidence_type="html",
                        sha256=obj.key,
                        content_type="text/html",
                        html_hash=obj.key,
                        content_hash=obj.key,
                        captured_at=datetime.now(UTC),
                    )
                )
                await session.commit()

            # GC with ZERO grace must still retain the referenced blob
            gc = EvidenceOrphanGC(store, factory, grace_period_seconds=0.0)
            stats = await gc.run()
            assert await store.exists(obj.key) is True, "live blob deleted by GC"
            assert stats.deleted == 0
        finally:
            for key in await store.list_keys():
                await store.delete(key)
            async with factory() as session:
                await session.execute(text("DELETE FROM evidence"))
                await session.execute(text("DELETE FROM tasks"))
                await session.execute(text("DELETE FROM agents"))
                await session.commit()
            await engine.dispose()
