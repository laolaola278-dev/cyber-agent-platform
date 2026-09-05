"""Phase 28.2 -- Security Regression under the durable execution model (spec 15).

Certifies the security invariants that MUST survive the 28.2 refactor:
  * SSRF policy still enforced: the LAB-localhost validator is a distinct,
    explicitly-opted-in variant; the PRODUCTION validator still blocks
    private addresses;
  * cancelled runs leave no partial sensitive artifacts behind (no evidence
    rows after CANCELLED);
  * sandbox boundary is not bypassed: execution goes through the Worker
    runtime with policy checks, never a raw request-path fetch;
  * capability registry is not a bypass: workers without the capability
    cannot execute.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.service import AcquisitionService
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


@pytest_asyncio.fixture
async def db(tmp_path) -> tuple:
    """Per-test file-backed SQLite with per-session connections.

    The in-memory StaticPool engine in conftest shares ONE aiosqlite
    connection across ALL sessions. The worker-path execution opens its
    own poll sessions (``async_sessionmaker(service.session.bind)``), so
    operation COMMITs and poll SELECTs interleave on that single
    connection -- the load-sensitive
    ``cannot commit transaction - SQL statements in progress`` flake seen
    in CI (run 33946618296; classified harness race in
    docs/quality/cert-rerun-triage-33946618296.md). A per-test file DB
    gives each session its own connection, mirroring the production
    PostgreSQL topology. Same pattern as the 28.2 cancellation suite.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.database import Base

    db_path = tmp_path / "security_regression.db"
    # NullPool: every session/connection is brand new, so a poll session can
    # never reuse a pooled connection carrying a stale WAL snapshot.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={
            "check_same_thread": False,
            # WAL + busy timeout: readers never block the writer and a
            # contended write waits instead of erroring (mirrors MVCC).
            "timeout": 30,
        },
        poolclass=NullPool,
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
    yield engine, SessionFactory
    import asyncio as _asyncio
    import gc as _gc

    await _asyncio.sleep(0.05)
    _gc.collect()
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db) -> AsyncSession:
    _engine, SessionFactory = db
    async with SessionFactory() as session:
        yield session


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


# -- 1. production SSRF policy still blocks private addresses ------------------------


async def test_production_ssrf_policy_blocks_private() -> None:
    from app.acquisition.urlpolicy import URLPolicyValidator

    policy = URLPolicyValidator(allow_private=False)
    for url in ("http://127.0.0.1:8080/x", "http://10.0.0.5/", "http://192.168.1.1/"):
        assert bool(policy.validate_url(url)) is False, f"production policy must block {url}"


async def test_production_ssrf_policy_allows_public() -> None:
    from app.acquisition.urlpolicy import URLPolicyValidator

    policy = URLPolicyValidator(allow_private=False)
    assert bool(policy.validate_url("https://example.com/advisory")) is True


async def test_lab_validator_is_distinct_from_production() -> None:
    """The lab allow-private validator must NOT leak into production policy."""
    prod = __import__(
        "app.acquisition.urlpolicy", fromlist=["URLPolicyValidator"]
    ).URLPolicyValidator(allow_private=False)
    lab_val = lab_url_validator()
    # the lab variant allows localhost only because it was EXPLICITLY opted in
    assert bool(lab_val.validate_url("http://127.0.0.1:1/")) is True
    assert bool(prod.validate_url("http://127.0.0.1:1/")) is False
    # distinct object identities (never shared)
    assert prod is not lab_val


# -- 2. cancelled runs leave no sensitive partial artifacts ---------------------------


async def test_cancelled_run_leaves_no_evidence_after_cancel(session, tmp_path, lab) -> None:
    from sqlalchemy import select

    from app.acquisition.claim import AcquisitionClaimCoordinator
    from app.acquisition.worker_path import AcquisitionWorkerPath
    from app.models import Evidence

    evidence = EvidenceService(
        session,
        publisher=None,
        storage_directory=tmp_path,  # type: ignore[arg-type]
    )
    service = AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-sec", runtime_version="28.2", capabilities=frozenset({"acquisition.http"})
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    leases = WorkerLeaseManager(session)
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    provider = MemorySandboxProvider()
    rt = WorkerRuntime(
        session,
        reg,
        WorkerScheduler(reg),
        leases,
        SandboxRuntime(provider, SandboxPolicyEngine()),
    )
    plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
    wp = AcquisitionWorkerPath(plugin, service, coord)
    token = uuid4()
    await coord.claim(run.id, worker.id, token=token)

    # cancel before execution begins -> no evidence may be written
    cancelled = await wp.cancel(run.id)
    assert cancelled.status in ("CANCEL_REQUESTED", "CANCELLED")
    await session.refresh(run)
    if run.status == "CANCELLED":
        evidence_rows = (await session.scalars(select(Evidence))).all()
        for row in evidence_rows:
            assert (
                row.captured_at <= run.cancelled_at
            ), "cancelled run must not have evidence written after CANCELLED"


# -- 3. sandbox boundary is not bypassed ---------------------------------------------


async def test_execution_flows_through_sandbox_boundary(session, tmp_path, lab) -> None:
    """Execution MUST route through the Worker runtime's sandbox (policy
    engine attached), never through a raw request-path fetch."""
    from app.acquisition.claim import AcquisitionClaimCoordinator
    from app.acquisition.worker_path import AcquisitionWorkerPath

    evidence = EvidenceService(
        session,
        publisher=None,
        storage_directory=tmp_path,  # type: ignore[arg-type]
    )
    service = AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-sandbox", runtime_version="28.2", capabilities=frozenset({"acquisition.http"})
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    leases = WorkerLeaseManager(session)
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    provider = MemorySandboxProvider()
    runtime = WorkerRuntime(
        session,
        reg,
        WorkerScheduler(reg),
        leases,
        SandboxRuntime(provider, SandboxPolicyEngine()),
    )
    plugin = PluginWorkerRuntime(runtime, SandboxProfile(name="acquisition-lab"))
    wp = AcquisitionWorkerPath(plugin, service, coord)
    token = uuid4()
    await coord.claim(run.id, worker.id, token=token)
    payload = await wp.run_claimed(run.id, worker.id, token)
    # the Worker executed it through the sandbox -> a sandbox execution id
    # was recorded on the run
    await session.refresh(run)
    assert run.sandbox_execution_id is not None
    assert payload.status in ("COMPLETE", "PARTIAL", "FAILED")


# -- 4. capability registry is not a bypass -------------------------------------------


async def test_worker_without_capability_cannot_execute(session, tmp_path, lab) -> None:
    from app.acquisition.claim import AcquisitionClaimCoordinator
    from app.acquisition.worker_path import AcquisitionWorkerPath
    from app.exceptions import WorkerUnavailable

    evidence = EvidenceService(
        session,
        publisher=None,
        storage_directory=tmp_path,  # type: ignore[arg-type]
    )
    service = AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    reg = WorkerRegistry(session)
    worker = await reg.register(
        WorkerRecord(
            name="acq-wrong-cap",
            runtime_version="28.2",
            capabilities=frozenset({"unrelated.capability"}),
        )
    )
    await reg.heartbeat(
        WorkerHeartbeat(worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0)
    )
    leases = WorkerLeaseManager(session)
    coord = AcquisitionClaimCoordinator(session, leases, lease_ttl_seconds=60)
    provider = MemorySandboxProvider()
    rt = WorkerRuntime(
        session,
        reg,
        WorkerScheduler(reg),
        leases,
        SandboxRuntime(provider, SandboxPolicyEngine()),
    )
    plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
    AcquisitionWorkerPath(plugin, service, coord)
    uuid4()
    # claim requires the coordinator, but execution asks the scheduler to pick
    # a worker with acquisition.http -- this worker lacks it -> backpressure
    from app.worker.contracts import PluginExecutionRequest

    request = PluginExecutionRequest(
        plugin_name="acquisition",
        plugin_version="28.2",
        capability="acquisition.http",
        operation="acquire",
        payload={},
        sandbox_profile=SandboxProfile(name="acquisition-lab"),
    )
    with pytest.raises(WorkerUnavailable):
        await rt.execute(
            request,
            {},
            owner=f"acquisition:{run.id}",
            execution_id=uuid4(),
        )
