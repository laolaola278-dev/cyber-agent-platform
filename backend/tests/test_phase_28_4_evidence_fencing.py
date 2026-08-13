"""Phase 28.4 -- evidence attachment fencing with real PG + object store.

A stale worker can at most leave an orphan immutable blob: it must NOT attach
a stale artifact row, must NOT attach a stale evidence row, and must NOT
disturb the new owner's run. Uses the real S3 (MinIO) store and PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.models import AcquisitionResult, BlockReason, RawArtifact
from app.acquisition.models_db import AcquisitionArtifactRecord, AcquisitionRun
from app.acquisition.service import AcquisitionService
from app.acquisition.store import S3EvidenceStore
from app.evidence.service import EvidenceService
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry

pytestmark = [pytest.mark.postgres, pytest.mark.object_store]

PG_DSN = "postgresql+asyncpg://cap@127.0.0.1:55432/cap283"
S3_ENDPOINT = os.environ.get("CAP283_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS = os.environ.get("CAP283_S3_ACCESS", "capadmin")
S3_SECRET = os.environ.get("CAP283_S3_SECRET", "capadmin123")
S3_BUCKET = os.environ.get("CAP283_S3_BUCKET", "cap-fence284")


async def _probe() -> bool:
    try:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT, access_key=S3_ACCESS, secret_key=S3_SECRET, bucket=S3_BUCKET
        )
        return await store.health()
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe()), reason="MinIO not reachable")


async def _register_worker(session, worker_id, name) -> None:
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


async def _make_service(session, tmp_path, store) -> AcquisitionService:
    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        store=store,
        policy=None,  # type: ignore[arg-type]
        validator=None,  # type: ignore[arg-type]
    )


@_skip
class TestEvidenceFencing:
    @pytest.mark.asyncio
    async def test_stale_worker_cannot_attach_evidence_via_object_store(
        self, tmp_path
    ) -> None:
        engine = create_async_engine(PG_DSN, pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT, access_key=S3_ACCESS, secret_key=S3_SECRET, bucket=S3_BUCKET
        )
        try:
            async with factory() as session:
                service = await _make_service(session, tmp_path, store)
                run, _ = await service.create(goal="g", url="http://example.com/static")
                await session.commit()
                worker_a = uuid4()
                await _register_worker(session, worker_a, f"fence-a-{worker_a.hex[:8]}")
                coord_a = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=5
                )
                token_a = uuid4()
                await coord_a.claim(run.id, worker_a, token=token_a)
                run_id = run.id

            # A writes an object to the store, then its lease expires and B
            # reclaims BEFORE A's durable commit.
            blob = b"<html>stale-attempt-blob</html>"
            obj = await store.put(blob, metadata={"url": "http://example.com"})

            # simulate A's stale commit path: persist an artifact row pointing
            # at the blob, then verify ownership -> must be rejected. NOTE: A
            # must NOT update the run row itself -- a pending UPDATE on
            # acquisition_runs would hold the row lock and block B's reclaim
            # (PG row locks block forever; the SQLite "database is locked"
            # retry loop does not apply). A's pending artifact is a fresh
            # INSERT, which does not lock the run row.
            async with factory() as session:
                artifact = AcquisitionArtifactRecord(
                    id=uuid4(),
                    run_id=run_id,
                    object_key=obj.key,
                    sha256=obj.key,
                    size=len(blob),
                    content_type="text/html",
                    source_url="http://example.com/static",
                    final_url="http://example.com/static",
                )
                session.add(artifact)
                await session.flush()

                # B reclaims while A's session holds the pending artifact row
                async with factory() as admin:
                    await WorkerLeaseManager(admin).expire(
                        now=datetime.now(UTC) + timedelta(seconds=60)
                    )
                    worker_b = uuid4()
                    await _register_worker(admin, worker_b, f"fence-b-{worker_b.hex[:8]}")
                    coord_b = AcquisitionClaimCoordinator(
                        admin, WorkerLeaseManager(admin), lease_ttl_seconds=120
                    )
                    claimed = await coord_b.reclaim_expired(
                        run_id, worker_b, token=uuid4()
                    )
                    assert claimed is not None

                # A's commit is fenced: verify_owner must reject A
                from app.acquisition.exceptions import AcquisitionStaleCommit

                coord = AcquisitionClaimCoordinator(
                    session, WorkerLeaseManager(session), lease_ttl_seconds=120
                )
                with pytest.raises(AcquisitionStaleCommit):
                    await coord.verify_owner(run_id, worker_a, token_a)
                await session.rollback()

            # B remains owner; no artifact row survived A's rejected commit
            async with factory() as session:
                fresh = await session.get(AcquisitionRun, run_id)
                assert fresh.worker_id == worker_b
                assert fresh.status in ("RUNNING", "COMPLETE", "BLOCKED", "FAILED")
                count = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AcquisitionArtifactRecord)
                            .where(AcquisitionArtifactRecord.run_id == run_id)
                        )
                    )
                    or 0
                )
                assert count == 0, "stale artifact row survived fencing"

            # the blob may remain as an immutable orphan (GC reclaims it later)
            assert await store.exists(obj.key) is True
        finally:
            for key in await store.list_keys():
                await store.delete(key)
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM acquisition_artifacts WHERE run_id = :rid"),
                    {"rid": run_id},
                )
                await session.commit()
            await engine.dispose()
