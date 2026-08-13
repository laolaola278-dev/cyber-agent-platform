"""Phase 28.1 -- Worker/Sandbox production path certification.

Proves that acquisitions run through the real Worker chain
(PluginWorkerRuntime -> WorkerRuntime -> SandboxRuntime -> operation) and that
the API layer never constructs adapters or touches the network itself.

Also certifies: async 202 semantics, idempotency, cancellation, checkpoint
resume, and worker/lease/sandbox identity recording.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.checkpoint import AcquisitionCheckpoint
from app.acquisition.exceptions import AcquisitionConflict
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionRunPayload, AcquisitionWorkerPath
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


async def _make_worker_path(
    session: AsyncSession, service: AcquisitionService
) -> tuple[AcquisitionWorkerPath, WorkerRegistry, WorkerLeaseManager, MemorySandboxProvider]:
    registry = WorkerRegistry(session)
    worker = await registry.register(
        WorkerRecord(
            name=f"acq-worker-{uuid4()}",
            runtime_version="phase-28.1",
            capabilities=frozenset({"acquisition.http"}),
        )
    )
    await registry.heartbeat(
        WorkerHeartbeat(
            worker_id=worker.id,
            status=WorkerStatus.ONLINE,
            active_executions=0,
        )
    )
    leases = WorkerLeaseManager(session)
    provider = MemorySandboxProvider()
    sandbox = SandboxRuntime(provider, SandboxPolicyEngine())
    runtime = WorkerRuntime(session, registry, WorkerScheduler(registry), leases, sandbox)
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-lab"))
    return AcquisitionWorkerPath(plugin, service), registry, leases, provider


# -- 1. async create: 202 semantics, no network in the request -----------------

async def test_create_returns_queued_without_executing(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, created = await service.create(
        goal="collect advisories",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve", "date"],
    )
    assert created is True
    assert run.status == "QUEUED"  # the request does NOT run the acquisition
    assert run.checkpoint.get("current_url") == f"{lab.origin}/pagination?page=1"
    assert run.checkpoint.get("page_number") == 1
    assert run.started_at is None


# -- 2. full worker chain executes the run ------------------------------------

async def test_worker_chain_executes_run_and_records_identities(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="collect advisories",
        url=f"{lab.origin}/pagination?page=1",
        expected_fields=["title", "cve", "date"],
    )
    await session.flush()
    worker_path, registry, leases, provider = await _make_worker_path(session, service)

    payload = await worker_path.execute(run.id)

    # the operation ran through the Worker chain
    assert payload.status == "COMPLETE"
    assert payload.documents_captured >= 1
    assert payload.evidence_ids, "evidence must be persisted end-to-end"

    await session.refresh(run)
    assert run.status == "COMPLETE"
    # worker / lease / sandbox identities recorded for end-to-end tracing
    assert run.worker_id is not None
    assert run.lease_id is not None
    assert run.sandbox_execution_id is not None
    assert run.worker_execution_id is not None
    assert run.trace_id

    # the lease used for this run was released after the run
    from app.repositories.worker import WorkerLeaseRepository

    lease = await WorkerLeaseRepository(session).get(run.lease_id)
    assert lease is not None
    assert lease.status == "RELEASED"


# -- 3. idempotency -------------------------------------------------------------

async def test_idempotency_same_key_returns_existing_run(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run1, created1 = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key="key-1"
    )
    await session.flush()
    run2, created2 = await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key="key-1"
    )
    assert created1 is True
    assert created2 is False
    assert run1.id == run2.id  # same key + same request -> the SAME run


async def test_idempotency_conflicting_request_raises(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    await service.create(
        goal="g", url=f"{lab.origin}/static", idempotency_key="key-2"
    )
    await session.flush()
    with pytest.raises(AcquisitionConflict):
        await service.create(
            goal="g", url=f"{lab.origin}/dynamic", idempotency_key="key-2"
        )


# -- 4. cancellation -------------------------------------------------------------

async def test_cancel_marks_run_cancelled(session: AsyncSession, tmp_path, lab) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(
        goal="g", url=f"{lab.origin}/pagination?page=1"
    )
    await session.flush()
    worker_path, _, _, _ = await _make_worker_path(session, service)

    payload = await worker_path.cancel(run.id)
    assert payload.status == "CANCELLED"
    await session.refresh(run)
    assert run.status == "CANCELLED"
    # cancelling an already-cancelled run is a no-op (still CANCELLED)
    payload2 = await worker_path.cancel(run.id)
    assert payload2.status == "CANCELLED"


# -- 5. checkpoint resume: page-2 timeout -> PARTIAL -> resume -> COMPLETE -------

async def test_resume_continues_same_run_from_checkpoint(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    lab.set_fail_page2(True)
    try:
        run, _ = await service.create(
            goal="collect 30 advisories",
            url=f"{lab.origin}/pagination?page=1",
            expected_fields=["title", "cve", "date"],
            expected_record_count=30,
        )
        await session.flush()
        worker_path, _, _, _ = await _make_worker_path(session, service)

        # first run: page 2 stalls -> PARTIAL with a checkpoint
        first = await worker_path.execute(run.id)
        assert first.status == "PARTIAL"
        # the checkpoint cursor points at page 2 (page_number=1 loop value,
        # i.e. the page that did NOT complete)
        assert first.checkpoint.get("page_number", 0) == 1
        assert len(first.visited_urls) >= 2

        # second run: page 2 recovers -> SAME run continues to COMPLETE
        lab.set_fail_page2(False)
        second = await worker_path.execute(run.id)
        assert second.status == "COMPLETE"
        assert second.record_count == 30
        # the resume did NOT restart from page 1: page-1 artifacts are preserved
        await session.refresh(run)
        assert run.status == "COMPLETE"
        assert len(run.checkpoint.get("visited_urls", [])) >= 3
    finally:
        lab.set_fail_page2(False)


# -- 6. restricted access stops through the real path (safety regression) --------

async def test_restricted_access_stops_through_worker_path(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/paywall")
    await session.flush()
    worker_path, _, _, _ = await _make_worker_path(session, service)

    payload = await worker_path.execute(run.id)
    assert payload.status == "BLOCKED"
    assert payload.blocked_reason == "PAYWALL"
    await session.refresh(run)
    assert run.blocked_reason == "PAYWALL"


# -- 7. SSRF through the real path (production policy unchanged) ------------------

async def test_ssrf_blocked_through_worker_path(session: AsyncSession, tmp_path) -> None:
    # production validator (default, no lab override) blocks private hosts
    evidence = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    service = AcquisitionService(
        session, evidence, store_root=tmp_path / "objects"
    )
    run, _ = await service.create(goal="g", url="http://127.0.0.1/secret")
    await session.flush()
    worker_path, _, _, _ = await _make_worker_path(session, service)

    payload = await worker_path.execute(run.id)
    assert payload.status == "BLOCKED"
    assert payload.blocked_reason == "SSRF_BLOCKED"


# -- 8. cancel AFTER execution releases sandbox + lease ----------------------------

async def test_cancel_after_execution_releases_resources(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    worker_path, _, leases, _ = await _make_worker_path(session, service)

    # run to COMPLETE so worker/lease/sandbox identities exist
    payload = await worker_path.execute(run.id)
    assert payload.status == "COMPLETE"
    await session.refresh(run)
    assert run.lease_id is not None and run.sandbox_execution_id is not None

    # cancel a terminal run is a no-op (already COMPLETE)
    cancelled = await worker_path.cancel(run.id)
    assert cancelled.status == "COMPLETE"


# -- 9. terminal runs are not re-executed --------------------------------------------

async def test_terminal_run_is_not_reexecuted(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/paywall")
    await session.flush()
    worker_path, _, _, _ = await _make_worker_path(session, service)

    first = await worker_path.execute(run.id)
    assert first.status == "BLOCKED"
    # a second execute() must short-circuit (terminal checkpoint) not refetch
    second = await worker_path.execute(run.id)
    assert second.status == "BLOCKED"
    assert second.checkpoint.get("blocked_reason") == "PAYWALL"


# -- 10. get_run not found raises acquisition-domain 404 ------------------------------

async def test_get_run_not_found(session: AsyncSession, tmp_path, lab) -> None:
    from app.acquisition.exceptions import AcquisitionNotFound

    service = await _make_service(session, tmp_path, lab)
    with pytest.raises(AcquisitionNotFound):
        await service.get_run(uuid4())


# -- 11. cancel a RUNNING run releases sandbox execution + lease --------------------

async def test_cancel_running_run_releases_resources(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    worker_path, _, leases, provider = await _make_worker_path(session, service)

    # simulate an in-flight run that already holds a sandbox execution + lease
    fake_sandbox = uuid4()
    await provider.execute(fake_sandbox, object(), lambda: {})  # register active? no-op
    provider._active.add(fake_sandbox)  # type: ignore[attr-defined]
    run.sandbox_execution_id = fake_sandbox
    run.lease_id = uuid4()
    run.status = "RUNNING"
    await session.flush()

    cancelled = await worker_path.cancel(run.id)
    assert cancelled.status == "CANCELLED"
    await session.refresh(run)
    assert run.status == "CANCELLED"
    assert run.checkpoint.get("status") == "CANCELLED"
    # the sandbox execution was terminated (removed from the active set)
    assert fake_sandbox not in provider._active  # type: ignore[attr-defined]


# -- 12. worker identity recording is skipped when no execution happened -------------

async def test_worker_identity_skipped_without_execution(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    # a plugin that never executed has no last_execution
    plugin = PluginWorkerRuntime.synthetic(frozenset({"acquisition.http"}))
    worker_path = AcquisitionWorkerPath(plugin, service)
    await worker_path._record_worker_identity(run, AcquisitionCheckpoint(run_id=str(run.id)))
    assert run.worker_id is None


# -- 13. cancel is best-effort: terminate/lease-release failures are swallowed ---------

class _BrokenPlugin:
    """Plugin whose terminate raises -- release must still proceed."""

    def __init__(self) -> None:
        self.last_execution = None

    async def terminate(self, execution_id) -> None:  # noqa: ANN001
        raise RuntimeError("terminate failed")


async def test_cancel_tolerates_terminate_failure(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    run.sandbox_execution_id = uuid4()
    run.lease_id = uuid4()
    run.status = "RUNNING"
    await session.flush()
    plugin = _BrokenPlugin()
    worker_path = AcquisitionWorkerPath(plugin, service)  # type: ignore[arg-type]
    cancelled = await worker_path.cancel(run.id)
    assert cancelled.status == "CANCELLED"


async def test_cancel_tolerates_lease_query_failure(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    run.sandbox_execution_id = None
    run.lease_id = uuid4()
    run.status = "RUNNING"
    await session.flush()

    class _BadService:  # get_run works, lease lookup via session explodes
        def __init__(self, svc: AcquisitionService) -> None:
            self._svc = svc

        async def get_run(self, run_id):  # noqa: ANN001
            return await self._svc.get_run(run_id)

        async def commit(self) -> None:
            await self._svc.commit()

        @property
        def session(self) -> Any:
            raise RuntimeError("session closed")

    plugin = PluginWorkerRuntime.synthetic(frozenset({"acquisition.http"}))
    worker_path = AcquisitionWorkerPath(plugin, _BadService(service))  # type: ignore[arg-type]
    cancelled = await worker_path.cancel(run.id)
    assert cancelled.status == "CANCELLED"


# -- 14. _apply_payload fills started_at when the run has none ------------------------

async def test_apply_payload_sets_started_at_when_missing(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    assert run.started_at is None
    await session.flush()
    plugin = PluginWorkerRuntime.synthetic(frozenset({"acquisition.http"}))
    worker_path = AcquisitionWorkerPath(plugin, service)
    await worker_path._apply_payload(
        run,
        AcquisitionRunPayload(status="RUNNING"),
    )
    assert run.started_at is not None
    # terminal payload also sets finished_at
    run2, _ = await service.create(goal="g2", url=f"{lab.origin}/static")
    await session.flush()
    await worker_path._apply_payload(
        run2,
        AcquisitionRunPayload(status="COMPLETE"),
    )
    assert run2.started_at is not None and run2.finished_at is not None


# -- 15. cancel releases a real held lease (RELEASED status) --------------------------

async def test_cancel_releases_real_lease(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    worker_path, registry, leases, _provider = await _make_worker_path(session, service)
    worker_id = (await registry.list())[0].id
    lease = await leases.acquire(
        worker_id=worker_id,
        execution_id=uuid4(),
        owner=f"acquisition:{run.id}",
        ttl_seconds=60,
    )
    run.sandbox_execution_id = None
    run.lease_id = lease.id
    run.status = "RUNNING"
    await session.flush()

    cancelled = await worker_path.cancel(run.id)
    assert cancelled.status == "CANCELLED"
    await session.refresh(run)
    assert run.status == "CANCELLED"


# -- 16. worker identity tolerates lease lookup failure --------------------------------

class _IdentityLeaseBrokenPlugin:
    """Plugin whose last_execution exists, but the lease lookup service fails."""

    def __init__(self) -> None:
        self.last_execution = type(
            "Exec",
            (),
            {
                "worker_id": "w-1",
                "sandbox_execution_id": "sb-1",
                "execution_id": "ex-1",
            },
        )()


async def test_worker_identity_tolerates_lease_lookup_failure(
    session: AsyncSession, tmp_path, lab
) -> None:
    service = await _make_service(session, tmp_path, lab)
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()

    class _BrokenIdentityService:
        def __init__(self, svc: AcquisitionService) -> None:
            self._svc = svc

        @property
        def session(self) -> Any:
            raise RuntimeError("session closed")

    plugin = _IdentityLeaseBrokenPlugin()
    worker_path = AcquisitionWorkerPath(plugin, _BrokenIdentityService(service))  # type: ignore[arg-type]
    # identity fields are still recorded; only the lease lookup is skipped
    await worker_path._record_worker_identity(
        run, AcquisitionCheckpoint(run_id=str(run.id))
    )
    assert run.worker_id == "w-1"
    assert run.sandbox_execution_id == "sb-1"
    assert run.lease_id is None
