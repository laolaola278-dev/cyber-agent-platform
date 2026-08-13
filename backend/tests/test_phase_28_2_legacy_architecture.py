"""Phase 28.2 -- Legacy API Deprecation + Architecture Guardrails (spec 11/12).

Certifies:
  * create_and_run is DEPRECATED (DeprecationWarning) and its synchronous
    path is NOT the execution entry point anymore;
  * the HTTP API surface never exposes create_and_run (no legacy bypass);
  * every run produced through the supported path is QUEUED and executed by
    the Worker Claim Loop (durable queue is the only execution path);
  * architecture guardrails: acquisition execution depends on the Worker
    runtime boundary, never on request-path synchronous execution.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.service import AcquisitionService
from app.evidence.service import EvidenceService
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


# -- 1. create_and_run is deprecated ---------------------------------------------


async def test_create_and_run_emits_deprecation_warning(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await service.create_and_run(goal="g", url=f"{lab.origin}/static")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        "create_and_run must emit a DeprecationWarning"
    )


async def test_create_and_run_produces_pending_not_queued(session, tmp_path, lab) -> None:
    """The legacy path bypasses the durable queue (PENDING, not QUEUED)."""
    service = await _make_service(session, tmp_path, lab)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        run = await service.create_and_run(goal="g", url=f"{lab.origin}/static")
    assert run.status in ("PENDING", "COMPLETE", "FAILED")
    # the legacy path does NOT enqueue into the claim-loop queue
    assert run.status != "QUEUED"


# -- 2. the HTTP API never exposes the legacy synchronous path ---------------------


async def test_api_router_has_no_create_and_run_endpoint() -> None:
    from app.api.routes.acquisition import router as acq_router

    paths = [getattr(r, "path", "") for r in acq_router.routes]
    # the legacy synchronous method is NOT exposed as an API endpoint
    assert not any("create-and-run" in p or "create_and_run" in p for p in paths)
    # the supported enqueue endpoint exists (plural resource name)
    assert any("acquisitions" in p for p in paths)
    # cancel/resume endpoints exist (durable control plane)
    assert any("cancel" in p for p in paths)
    assert any("resume" in p for p in paths)


async def test_router_has_no_bypass_capability_endpoint() -> None:
    from app.api.routes.acquisition import router as acq_router

    paths = [getattr(r, "path", "") for r in acq_router.routes]
    # no direct execution / run-now bypass endpoints
    assert not any("run-now" in p or "execute" in p for p in paths)


# -- 3. supported path: create -> QUEUED (durable queue is the only path) -----------


async def test_supported_create_enqueues_durably(session, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    assert run.status == "QUEUED"
    await session.commit()
    # durable across sessions: another session sees the QUEUED run
    async with TestSessionFactory() as fresh:
        reloaded = await fresh.get(type(run), run.id)
        assert reloaded is not None and reloaded.status == "QUEUED"


# -- 4. architecture: execution goes through the Worker boundary ---------------------

async def test_worker_path_executes_claimed_run(session, tmp_path, lab) -> None:
    """A claimed QUEUED run is executed by the Worker path (the claim loop's
    runner), proving execution is worker-boundary owned, not request-path."""
    from app.acquisition.claim import AcquisitionClaimCoordinator
    from app.acquisition.worker_path import AcquisitionWorkerPath
    from app.sandbox import SandboxPolicyEngine, SandboxRuntime
    from app.sandbox.profile import SandboxProfile
    from app.sandbox.runtime import MemorySandboxProvider
    from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
    from app.worker.lease import WorkerLeaseManager
    from app.worker.plugin_runtime import PluginWorkerRuntime
    from app.worker.registry import WorkerRegistry
    from app.worker.runtime import WorkerRuntime
    from app.worker.scheduler import WorkerScheduler

    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-arch",
            runtime_version="28.2",
            capabilities=frozenset({"acquisition.http"}),
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    leases = WorkerLeaseManager(session)
    provider = MemorySandboxProvider()
    rt = WorkerRuntime(
        session,
        reg,
        WorkerScheduler(reg),
        leases,
        SandboxRuntime(provider, SandboxPolicyEngine()),
    )
    plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    wp = AcquisitionWorkerPath(plugin, service, coord)
    token = uuid4()
    await coord.claim(run.id, worker.id, token=token)
    payload = await wp.run_claimed(run.id, worker.id, token)
    assert payload.status in ("COMPLETE", "PARTIAL", "FAILED")
    await session.refresh(run)
    assert run.status != "QUEUED"  # execution moved the run out of the queue
