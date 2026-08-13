"""Phase 28.3 -- side-effect fencing audit tests.

Answers the audit: a stale worker (whose lease expired and whose run was
reclaimed) must NOT be able to attach evidence / artifacts / results to the
CURRENT run. All acquisition detail rows (artifacts, documents, completeness,
evidence) are written into the worker's own session and become durable ONLY
through the fenced final commit (verify_owner). A rejected stale commit rolls
the session back, so no stale rows survive. Object-store blobs ARE written
immediately (content-addressed, immutable) -- they may exist as orphans but
are never attached to a run without a fenced artifact row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.exceptions import AcquisitionStaleCommit
from app.acquisition.models import AcquisitionResult, BlockReason, RawArtifact
from app.acquisition.models_db import (
    AcquisitionArtifactRecord,
    AcquisitionRun,
)
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionRunPayload, AcquisitionWorkerPath
from app.database import Base
from app.evidence.service import EvidenceService
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.sandbox.profile import SandboxProfile
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import lab_policy, lab_url_validator


@pytest_asyncio.fixture
async def sef_db(tmp_path: Path) -> tuple:
    db_path = tmp_path / "sef.db"
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
async def session(sef_db) -> AsyncSession:
    _engine, SessionFactory = sef_db
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


async def _make_worker_path(session: AsyncSession, service: AcquisitionService) -> AcquisitionWorkerPath:
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
        lease_ttl_seconds=60,
    )
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-lab"))
    coordinator = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    wp = AcquisitionWorkerPath(plugin, service, coordinator, lease_ttl_seconds=60)
    # expose the isolated runtime session so tests can close it after the run
    wp._runtime_session = runtime_session  # type: ignore[attr-defined]
    return wp


async def _register_worker(session: AsyncSession, worker_id: UUID, name: str) -> None:
    reg = WorkerRegistry(session)
    await reg.register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.3",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=2,
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker_id, status=WorkerStatus.ONLINE, active_executions=0)
    )


async def _artifact_count(session: AsyncSession, run_id) -> int:
    return int(
        (
            await session.scalar(
                select(func.count()).select_from(AcquisitionArtifactRecord).where(
                    AcquisitionArtifactRecord.run_id == run_id
                )
            )
        )
        or 0
    )


def _stale_op_with_evidence(service: AcquisitionService, tmp_path: Path, run_id: UUID):
    """An operation that writes intermediate artifacts into the worker session
    (exactly like the real agent would mid-run) before returning COMPLETE."""

    async def stub_operation(run, checkpoint):
        # delay first: the lease must expire and B must reclaim WHILE A is
        # still mid-operation (before any row is written), so A's writes
        # happen against a stale ownership that can never commit.
        await asyncio.sleep(1.5)
        from datetime import UTC as _UTC

        artifact = RawArtifact(
            object_key="deadbeef" * 8,
            sha256="deadbeef" * 8,
            size=4,
            content_type="text/html",
            source_url="http://example.com/page1",
            final_url="http://example.com/page1",
            captured_at=datetime.now(_UTC),
            task_id=str(run_id),
        )
        from app.acquisition.models import AcquisitionStatus

        result = AcquisitionResult(
            run_id=str(run_id),
            status=AcquisitionStatus.COMPLETE,
            artifacts=[artifact],
            blocked_reason=BlockReason.NONE,
        )
        # Writes rows into the WORKER session (uncommitted until the fenced
        # final commit) -- simulating mid-run evidence/artifact persistence.
        await service._persist_result(run, result, run_id)
        return AcquisitionRunPayload(
            status="COMPLETE", checkpoint={"status": "COMPLETE", "dirty": True}
        )

    return stub_operation


# -- 1. stale worker's intermediate evidence cannot attach to the run ---------


async def test_stale_worker_cannot_attach_intermediate_data(sef_db, session, tmp_path) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()

    worker_a = uuid4()
    await _register_worker(session, worker_a, "acq-stale-a")
    wp_a = await _make_worker_path(session, service)
    token_a = uuid4()
    coordinator_a = wp_a._ensure_coordinator()
    await coordinator_a.claim(run.id, worker_a, token=token_a)

    # A's operation writes intermediate rows, but before its commit the lease
    # expires and worker B reclaims the run. The expire + reclaim run on a
    # SEPARATE connection/session (production: the recovery loop is another
    # process) -- never on the worker's own session.
    service.run_agent_operation = _stale_op_with_evidence(service, tmp_path, run.id)  # type: ignore[method-assign]
    task = asyncio.create_task(wp_a.run_claimed(run.id, worker_a, token_a))

    _engine, SessionFactory = sef_db
    await asyncio.sleep(0.2)
    async with SessionFactory() as admin:
        await WorkerLeaseManager(admin).expire(
            now=datetime.now(UTC) + timedelta(seconds=60)
        )
        worker_b = uuid4()
        await _register_worker(admin, worker_b, "acq-stale-b")
        coord_b = AcquisitionClaimCoordinator(
            admin, WorkerLeaseManager(admin), lease_ttl_seconds=60
        )
        claimed = await coord_b.reclaim_expired(run.id, worker_b, token=uuid4())
        assert claimed is not None

    # A's stale commit is REJECTED (fencing) -- no stale rows survive. All
    # asserts read from a FRESH connection: the worker session's identity map
    # still holds the pre-reclaim snapshot (worker_id=A), which is NOT the
    # durable truth.
    payload = await asyncio.wait_for(task, timeout=15)
    assert payload.status != "COMPLETE" or payload.error != ""
    _engine, SessionFactory = sef_db
    async with SessionFactory() as fresh_sess:
        fresh = await fresh_sess.get(AcquisitionRun, run.id)
        assert fresh.worker_id == worker_b
        assert fresh.status in ("RUNNING", "PARTIAL", "COMPLETE", "BLOCKED", "FAILED")
        # the stale intermediate artifact rows were rolled back with A's session
        assert await _artifact_count(fresh_sess, run.id) == 0
    await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 2. cancel during object write: no post-CANCELLED evidence attachment -----


async def test_cancel_during_object_write_no_post_cancelled_attachment(
    session, tmp_path
) -> None:
    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()
    worker_a = uuid4()
    await _register_worker(session, worker_a, "acq-cancel-write")
    wp_a = await _make_worker_path(session, service)
    token_a = uuid4()
    coordinator_a = wp_a._ensure_coordinator()
    await coordinator_a.claim(run.id, worker_a, token=token_a)

    async def slow_op(run, checkpoint):
        # the object-store blob is written to disk immediately (content
        # addressed); then the cancel flag arrives and the operation task is
        # cancelled before any artifact ROW is committed
        from app.acquisition.store import LocalFilesystemEvidenceStore

        store = LocalFilesystemEvidenceStore(tmp_path / "objects")
        await store.put(b"<html>page</html>", metadata={"url": "http://example.com"})
        await asyncio.sleep(10)  # long enough for the cancel to land
        return AcquisitionRunPayload(status="COMPLETE")

    service.run_agent_operation = slow_op  # type: ignore[method-assign]
    task = asyncio.create_task(wp_a.run_claimed(run.id, worker_a, token_a))
    await asyncio.sleep(0.5)
    # durable cancel flag (API path)
    run.status = "CANCEL_REQUESTED"
    run.cancel_requested_at = datetime.now(UTC)
    await session.commit()

    payload = await asyncio.wait_for(task, timeout=15)
    assert payload.status == "CANCELLED"
    fresh = await session.get(AcquisitionRun, run.id)
    assert fresh.status == "CANCELLED"
    # no artifact ROW attached after cancellation (the blob may exist on disk
    # as a content-addressed orphan -- never attached to the run)
    assert await _artifact_count(session, run.id) == 0
    await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session


# -- 3. no orphan evidence ROW from a stale worker ----------------------------


async def test_stale_worker_leaves_no_orphan_evidence_rows(sef_db, session, tmp_path) -> None:
    from app.models import Evidence

    service = await _make_service(session, tmp_path)
    run, _ = await service.create(goal="g", url="http://example.com/static")
    await session.commit()

    worker_a = uuid4()
    await _register_worker(session, worker_a, "acq-orphan-a")
    wp_a = await _make_worker_path(session, service)
    token_a = uuid4()
    coordinator_a = wp_a._ensure_coordinator()
    await coordinator_a.claim(run.id, worker_a, token=token_a)

    async def evidence_op(run, checkpoint):
        # simulate the evidence sink: a row is PENDING in the worker session
        # (added, NOT flushed). The flush would hold SQLite's single-writer
        # lock and block the concurrent recovery admin writes -- a test-only
        # artifact of SQLite serialization (PostgreSQL MVCC never serializes
        # writers). The invariant under test is identical either way: an
        # evidence row that is not committed through the fenced final commit
        # must not survive a stale rejection.
        from app.evidence.service import EvidenceType
        from app.models import Evidence

        pending = Evidence(
            task_id=run.id,
            agent_id=UUID(int=0),
            trace_id="t",
            url="http://example.com",
            http_status=200,
            title="t",
            evidence_type=EvidenceType.HTML.value,
            sha256="ab" * 32,
            content_type="text/html",
            content_hash="ab" * 32,
            captured_at=datetime.now(UTC),
        )
        service._evidence._session.add(pending)
        # keep running so the lease expires and B reclaims before A's
        # (doomed) commit; the pending row rides A's session until the
        # fenced rejection rolls it back
        await asyncio.sleep(1.2)
        return AcquisitionRunPayload(status="COMPLETE")

    service.run_agent_operation = evidence_op  # type: ignore[method-assign]
    task = asyncio.create_task(wp_a.run_claimed(run.id, worker_a, token_a))
    _engine, SessionFactory = sef_db
    await asyncio.sleep(0.2)
    # SQLite is single-writer: A's flushed (uncommitted) evidence row holds
    # the write lock until A's stale commit is rejected (~1.2s later). The
    # recovery admin operations must retry transient "database is locked"
    # (production PG has no writer serialization; this is test-only).
    from sqlalchemy.exc import OperationalError

    async with SessionFactory() as admin:
        for _attempt in range(120):
            try:
                await WorkerLeaseManager(admin).expire(
                    now=datetime.now(UTC) + timedelta(seconds=60)
                )
                break
            except OperationalError as error:
                if "locked" not in str(error).lower():
                    raise
                await admin.rollback()
                await asyncio.sleep(0.25)
        worker_b = uuid4()
        await _register_worker(admin, worker_b, "acq-orphan-b")
        coord_b = AcquisitionClaimCoordinator(
            admin, WorkerLeaseManager(admin), lease_ttl_seconds=60
        )
        claimed = await coord_b.reclaim_expired(run.id, worker_b, token=uuid4())
        assert claimed is not None

    payload = await asyncio.wait_for(task, timeout=15)
    assert payload.status != "COMPLETE" or payload.error != ""
    # release the worker session's lingering transaction so teardown closes
    # the connection cleanly (filterwarnings=error treats leaked handles as
    # failures)
    await session.rollback()
    # A's evidence row was in A's session -> rolled back -> never committed.
    # Assert on a FRESH connection: the worker session may still hold the
    # rolled-back object and SQLAlchemy's autoflush would make it visible to
    # the same-session query -- the durable truth is the committed DB state.
    async with SessionFactory() as fresh_sess:
        committed = int(
            (await fresh_sess.scalar(select(func.count()).select_from(Evidence))) or 0
        )
    assert committed == 0
    await wp_a._runtime_session.close()  # noqa: BLE001 -- release runtime session
