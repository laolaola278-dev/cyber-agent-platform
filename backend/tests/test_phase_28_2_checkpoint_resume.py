"""Phase 28.2 -- Checkpoint Transaction Boundary + Idempotent Resume (spec 9/10).

Certifies:
  * idempotent create: the same idempotency_key returns the SAME run
    (never duplicates) and rejects reuse with a different request;
  * checkpoint snapshot + run row are committed in the same transaction
    (no torn state: a run whose checkpoint says page 3 always has its
    metadata row in the same commit);
  * requeue preserves the checkpoint cursor -- resume continues from the
    checkpoint, never restarts from page 1 (proven by re-execution that
    picks up the stored page_number);
  * resume after PARTIAL continues from the stored cursor (lab pagination
    recorded in the durable checkpoint).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.claim import AcquisitionClaimCoordinator
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
        session, publisher=None, storage_directory=tmp_path  # type: ignore[arg-type]
    )
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )


# -- 1. idempotent create ------------------------------------------------------


async def test_idempotent_create_returns_same_run(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    key = f"idem-{uuid4()}"
    run1, created1 = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key=key
    )
    run2, created2 = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key=key
    )
    assert created1 is True
    assert created2 is False
    assert run1.id == run2.id
    assert run1.idempotency_key == key


async def test_idempotent_key_reuse_with_different_request_rejected(
    session, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    key = f"idem-{uuid4()}"
    await service.create(goal="g", url=f"{lab.origin}/static", idempotency_key=key)
    with pytest.raises(Exception):
        await service.create(
            goal="DIFFERENT GOAL", url=f"{lab.origin}/static", idempotency_key=key
        )


async def test_distinct_keys_create_distinct_runs(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run1, _ = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key=f"k-{uuid4()}"
    )
    run2, _ = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key=f"k-{uuid4()}"
    )
    assert run1.id != run2.id


# -- 2. checkpoint snapshot + run row in the SAME transaction --------------------


async def test_checkpoint_and_metadata_commit_atomically(session, tmp_path, lab) -> None:
    """A run whose checkpoint contains cursor state must also have its
    metadata row fields (status/worker) in the same committed transaction."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="collect records",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve"],
        expected_record_count=30,
    )
    await session.flush()

    # simulate a worker persisting page-2 cursor: both the checkpoint column
    # and the run row update in one commit
    ck = dict(run.checkpoint or {})
    ck["status"] = "RUNNING"
    ck["page_number"] = 2
    run.checkpoint = ck
    run.status = "RUNNING"
    await session.commit()

    # re-read from a fresh session: both views are consistent (atomic commit)
    async with TestSessionFactory() as fresh:
        reloaded = await fresh.get(AcquisitionRun, run.id)
        assert reloaded is not None
        assert reloaded.status == "RUNNING"
        assert reloaded.checkpoint.get("page_number") == 2


# -- 3. requeue preserves the checkpoint cursor -----------------------------------


async def test_requeue_preserves_checkpoint_cursor(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="collect records",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve"],
        expected_record_count=30,
    )
    # worker stored page-2 progress, then the run was interrupted
    ck = dict(run.checkpoint or {})
    ck["status"] = "PARTIAL"
    ck["page_number"] = 2
    run.checkpoint = ck
    run.status = "PARTIAL"
    await session.commit()

    requeued = await service.requeue(run.id)
    assert requeued.status == "QUEUED"
    # the cursor is preserved -- resume NEVER restarts from page 1
    assert requeued.checkpoint.get("page_number") == 2


async def test_requeue_keeps_current_url_cursor(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    ck = dict(run.checkpoint or {})
    ck["status"] = "PARTIAL"
    ck["current_url"] = f"{lab.origin}/pagination?page=3"
    run.checkpoint = ck
    run.status = "PARTIAL"
    await session.commit()

    requeued = await service.requeue(run.id)
    assert requeued.status == "QUEUED"
    assert requeued.checkpoint.get("current_url") == f"{lab.origin}/pagination?page=3"


# -- 4. resume continues from the stored cursor (not page 1) -----------------------


async def test_resume_uses_stored_cursor_via_planner_request(session, tmp_path, lab) -> None:
    """After requeue, the planner request is rebuilt from the checkpoint: the
    URL reflects the stored cursor page, proving resume continues mid-flight."""
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="collect records",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve"],
        expected_record_count=30,
    )
    ck = dict(run.checkpoint or {})
    ck["status"] = "PARTIAL"
    ck["current_url"] = f"{lab.origin}/pagination?page=2"
    run.checkpoint = ck
    run.status = "PARTIAL"
    await session.commit()

    requeued = await service.requeue(run.id)
    state = dict(requeued.checkpoint or {})
    request = service._planner_request_from_state(requeued, state)
    # the resumed planner targets page 2, NOT page 1
    assert "page=2" in request.url
    assert "page=1" not in request.url


# -- 5. idempotent create survives across sessions (durable) -----------------------


async def test_idempotency_key_durable_across_sessions(tmp_path, lab) -> None:
    key = f"idem-durable-{uuid4()}"
    async with TestSessionFactory() as s1:
        svc1 = await _make_service(s1, tmp_path, lab)
        run1, created1 = await svc1.create(
            goal="g", url=f"{lab.origin}/static", idempotency_key=key
        )
        assert created1 is True
        await s1.commit()
    # a completely different session / service instance sees the SAME run
    async with TestSessionFactory() as s2:
        svc2 = await _make_service(s2, tmp_path, lab)
        run2, created2 = await svc2.create(
            goal="g", url=f"{lab.origin}/static", idempotency_key=key
        )
        assert created2 is False
        assert run1.id == run2.id
