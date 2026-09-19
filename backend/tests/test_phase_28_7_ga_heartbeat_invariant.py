"""Phase 28.7 -- heartbeat AsyncSession isolation ARCH INVARIANT (GA closure).

Commit 2ba8bec fixed a production concurrency defect: the execution-time
heartbeat task shared the runtime's AsyncSession with the main execute flow
(start/commit/rollback/release). Two tasks on one AsyncSession is unsupported
by SQLAlchemy and corrupted the session state machine under load:

  - IllegalStateChangeError: "rollback() can't be called here; commit()
    is already in progress"
  - postgres: "This session is provisioning a new connection; concurrent
    operations are not permitted"
  - sqlite/aiosqlite: "Cannot operate on a closed database"

A dying heartbeat stops renewals, so a HEALTHY run was then falsely
reclaimed by another worker. This module pins the invariant permanently:

  INVARIANT: every WorkerRuntime renews its execution lease on a session NOT
  shared with the main execute flow. The heartbeat task is created
  unconditionally by execute(), so this does not depend on how long a caller
  expects an operation to run.

Enforced three ways:
  1. BY CONSTRUCTION: WorkerRuntime derives a renewal-only session factory from
     the runtime session's bind when a site omits one, so no construction site
     -- production or test -- can put the heartbeat and the main execute flow on
     the same AsyncSession.
  2. STATIC: every ``WorkerRuntime(`` construction in app/ must still name its
     ``heartbeat_session_factory=`` (production wiring stays visible at the call
     site), and no site anywhere may opt out with an explicit ``None``.
  3. BEHAVIORAL: with a deliberately-open transaction on the runtime session, a
     concurrent renewal through the dedicated-session path must succeed without
     touching the open transaction; and a renewal that fails for a TRANSIENT
     reason (SQLite "database is locked" under writer contention) must be
     retried on the next tick rather than end the heartbeat.

The transient-retry half is not theoretical: run 35430453285
(cap-production-certification, healthy 3.5s acquisition on a loaded runner)
lost one renewal, the heartbeat died silently, the 2s execution lease lapsed
unrenewed, the fenced commit was correctly rejected, and the run landed
CANCELLED -- a healthy run falsely cancelled.

Historical forbidden failures are asserted never to reappear.
"""

from __future__ import annotations

import ast
import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.database import Base
from app.exceptions import WorkerLeaseConflict
from app.models.worker import SandboxExecution
from app.models.worker import WorkerLease as WorkerLeaseModel
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.worker.contracts import WorkerRecord
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler

BACKEND = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"

# historical driver errors that must NEVER reappear in heartbeat paths
FORBIDDEN_ERRORS = (
    "IllegalStateChangeError",
    "provisioning a new connection",
    "Cannot operate on a closed database",
)


# -- 1. STATIC architecture scan ----------------------------------------------


def _worker_runtime_calls(
    directory: Path, relative_to: Path = BACKEND
) -> list[tuple[Path, ast.Call]]:
    calls: list[tuple[Path, ast.Call]] = []
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name == "WorkerRuntime":
                    calls.append((path.relative_to(relative_to), node))
    return calls


def _keyword(node: ast.Call, name: str) -> ast.keyword | None:
    return next((kw for kw in node.keywords if kw.arg == name), None)


def test_arch_every_app_worker_runtime_site_uses_dedicated_heartbeat_session() -> None:
    sites = _worker_runtime_calls(APP_DIR)
    assert sites, "scanner found no WorkerRuntime sites -- scanner is broken"
    offenders = [
        str(path)
        for path, node in sites
        if "heartbeat_session_factory" not in {kw.arg for kw in node.keywords}
    ]
    assert not offenders, (
        "ARCH INVARIANT VIOLATED: WorkerRuntime constructed WITHOUT "
        f"heartbeat_session_factory in {offenders}. The execution-time "
        "heartbeat must renew on its own AsyncSession -- sharing one session "
        "between the heartbeat task and the main execute flow corrupts the "
        "session state machine (see commit 2ba8bec). WorkerRuntime now derives "
        "a factory from the session bind when one is omitted, so an app/ site "
        "that omits it is relying on that fallback instead of naming its own "
        "session maker -- name it."
    )


def test_arch_no_worker_runtime_site_opts_out_of_heartbeat_isolation() -> None:
    """``heartbeat_session_factory=None`` is the one way to defeat the invariant.

    Scanned across BOTH app/ and tests/: a test that opts out reintroduces the
    load-dependent false cancellation that certification caught in run
    35430453285, so the exemption tests/ used to enjoy is gone.
    """
    sites = _worker_runtime_calls(APP_DIR) + _worker_runtime_calls(BACKEND / "tests")
    assert sites, "scanner found no WorkerRuntime sites -- scanner is broken"
    opted_out = [
        f"{path}:{node.lineno}"
        for path, node in sites
        if (kw := _keyword(node, "heartbeat_session_factory")) is not None
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is None
    ]
    assert not opted_out, (
        f"WorkerRuntime sites opt out of heartbeat isolation with None: {opted_out}"
    )


# -- 2. BEHAVIORAL invariant (SQLite) -----------------------------------------


@pytest.fixture
async def hb_engine(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'hb_invariant.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


def _pending_execution(worker_id: uuid.UUID) -> SandboxExecution:
    """An uncommitted SandboxExecution row: the main flow's open write."""
    return SandboxExecution(
        id=uuid4(),
        execution_id=uuid4(),
        worker_id=worker_id,
        plugin_name="hb-invariant",
        plugin_version="1.0.0",
        operation="probe",
        provider="memory",
        status="RUNNING",
        started_at=datetime.now(UTC),
    )


async def _register_test_worker(
    session: AsyncSession, worker_id: uuid.UUID, name: str
) -> None:
    """Register the workers row BEFORE acquiring its lease.

    worker_leases.worker_id carries a real FK to workers. SQLite does not
    enforce FKs by default, which let bare-uuid4() fixtures pass silently,
    while PostgreSQL (authoritative) correctly rejected them with
    fk_worker_leases_worker_id_workers violations. DB semantic mismatch,
    fixed at the fixture.
    """
    await WorkerRegistry(session).register(
        WorkerRecord(
            id=worker_id,
            name=name,
            runtime_version="28.7",
            capabilities=frozenset({"acquisition.http"}),
            max_concurrency=2,
        )
    )


async def test_heartbeat_renewal_is_isolated_from_open_main_transaction(
    hb_engine,
) -> None:
    """SQLite basic variant: with the main execution transaction DELIBERATELY
    OPEN (uncommitted write held), a concurrent renewal through the dedicated
    heartbeat session must not corrupt either side. SQLite enforces a
    database-level single-writer lock, so the uncommitted-write contention
    case is covered separately (see the contention test below); here we pin
    the state-machine invariant with an open read transaction plus the
    full write scenario against PostgreSQL (authoritative test below)."""
    factory = async_sessionmaker(hb_engine, expire_on_commit=False)
    runtime_session = factory()

    leases = WorkerLeaseManager(runtime_session)
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        leases,
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=120,
        heartbeat_session_factory=factory,
    )

    # identity: dedicated sessions are distinct objects AND distinct from the
    # runtime/main session (different connection ownership per checkout)
    s1 = runtime._heartbeat_session_factory()
    s2 = runtime._heartbeat_session_factory()
    try:
        assert s1 is not runtime._session
        assert s2 is not runtime._session
        assert s1 is not s2
    finally:
        await s1.close()
        await s2.close()

    worker_id = uuid4()
    execution_id = uuid4()
    await _register_test_worker(runtime_session, worker_id, "hb-invariant")
    lease = await leases.acquire(
        worker_id=worker_id,
        execution_id=execution_id,
        owner="acquisition:hb-invariant",
        ttl_seconds=120,
    )
    version_after_acquire = lease.version

    # MAIN EXECUTION TRANSACTION DELIBERATELY OPEN: pin an open transaction
    # on the runtime session for the whole renewal window.
    pending = _pending_execution(worker_id)
    runtime_session.add(pending)
    probe_row = (
        await runtime_session.execute(text("SELECT count(*) FROM workers"))
    ).scalar_one()  # tx OPEN on runtime session, NOT committed

    assert probe_row >= 0

    # concurrent heartbeat renewal through the DEDICATED path must succeed
    # while the main transaction stays open -- and must raise none of the
    # historical shared-session corruption errors
    renewed = await runtime._renew_on_dedicated_session(lease, owner="acquisition:hb-invariant")
    assert renewed.version == version_after_acquire + 1

    # the renewal committed on its OWN connection: visible to a FRESH one
    check = factory()
    try:
        rows = (
            (
                await check.execute(
                    select(WorkerLeaseModel.version).where(WorkerLeaseModel.id == lease.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == [version_after_acquire + 1]
    finally:
        await check.close()

    # the open main transaction/session survived untouched and remains fully
    # usable afterwards (rollback + reuse without IllegalStateChangeError)
    await runtime_session.rollback()
    workers_now = (await runtime_session.execute(text("SELECT count(*) FROM workers"))).scalar_one()
    assert workers_now >= 0

    await runtime_session.close()


async def test_heartbeat_write_contention_uses_separate_connections(
    hb_engine,
) -> None:
    """SQLite single-writer reality check: while the main session holds an
    UNCOMMITTED WRITE, a renewal on the dedicated connection can only fail
    with a connection-level lock error ('database is locked') -- NEVER with
    the shared-session state-machine corruption errors. Afterwards both
    sessions recover cleanly, which proves independent connection ownership.
    (PostgreSQL has row-level locking: the success path under an uncommitted
    write is asserted authoritatively by the PG test below.)"""
    factory = async_sessionmaker(hb_engine, expire_on_commit=False)
    runtime_session = factory()

    leases = WorkerLeaseManager(runtime_session)
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        leases,
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=120,
        heartbeat_session_factory=factory,
    )

    worker_id = uuid4()
    await _register_test_worker(runtime_session, worker_id, "hb-contention")
    lease = await leases.acquire(
        worker_id=worker_id,
        execution_id=uuid4(),
        owner="acquisition:hb-contention",
        ttl_seconds=120,
    )

    pending = _pending_execution(worker_id)
    runtime_session.add(pending)
    await runtime_session.flush()  # write tx OPEN, NOT committed

    try:
        await runtime._renew_on_dedicated_session(lease, owner="acquisition:hb-contention")
        raised = None
    except Exception as exc:  # noqa: BLE001 -- asserted below
        raised = exc

    if raised is not None:
        message = str(raised)
        # ONLY the database-level single-writer limit may appear
        assert (
            "database is locked" in message or "database table is locked" in message
        ), f"unexpected failure class under deliberate contention: {message}"
        for forbidden in FORBIDDEN_ERRORS:
            assert forbidden not in message, f"shared-session corruption resurfaced: {forbidden}"

    # BOTH sides recover cleanly -> connections were never shared
    await runtime_session.rollback()  # no IllegalStateChangeError allowed
    retried = await runtime._renew_on_dedicated_session(lease, owner="acquisition:hb-contention")
    assert retried.version == lease.version + 1
    await runtime_session.close()


async def test_heartbeat_is_dedicated_even_when_the_site_omits_the_factory(
    hb_engine, monkeypatch
) -> None:
    """The 28.3 construction shape can no longer share the runtime session.

    ``test_phase_28_3_lease_heartbeat.py`` builds a WorkerRuntime without
    ``heartbeat_session_factory``, which put the heartbeat task and the main
    execute flow on one AsyncSession -- the unsupported configuration that made
    renewals stop under contention. WorkerRuntime now derives a renewal-only
    factory from the session bind, so omitting the argument is safe everywhere.
    """
    factory = async_sessionmaker(hb_engine, expire_on_commit=False)
    runtime_session = factory()

    leases = WorkerLeaseManager(runtime_session)
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        leases,
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=3,
        # heartbeat_session_factory DELIBERATELY omitted -- this is the shape
        # that used to renew on the shared session.
    )

    assert runtime._heartbeat_session_factory is not None
    opened = runtime._heartbeat_session_factory()
    try:
        assert opened is not runtime_session
    finally:
        await opened.close()

    worker_id = uuid4()
    await _register_test_worker(runtime_session, worker_id, "hb-derived")
    lease = await leases.acquire(
        worker_id=worker_id,
        execution_id=uuid4(),
        owner="acquisition:hb-derived",
        ttl_seconds=3,
    )

    route: list[str] = []

    async def dedicated(current, owner):
        route.append("dedicated")
        return current

    async def shared(*args, **kwargs):  # pragma: no cover - must never run
        route.append("shared")
        raise AssertionError("renewal ran on the shared runtime session")

    monkeypatch.setattr(runtime, "_renew_on_dedicated_session", dedicated)
    monkeypatch.setattr(leases, "renew", shared)
    await runtime._renew_lease(lease, "acquisition:hb-derived")
    assert route == ["dedicated"]

    await runtime_session.close()


async def test_transient_renewal_failure_is_retried_and_ownership_loss_stops(
    hb_engine, monkeypatch
) -> None:
    """One locked renewal must not end the heartbeat; a lost lease must.

    Deterministic form of run 35430453285: the acquisition was healthy, a
    renewal hit SQLite's single-writer limit, the heartbeat coroutine died with
    it, renewals stopped for the rest of the operation, the execution lease
    lapsed, and the fenced commit was rejected -- cancelling a run that should
    have completed. Ownership loss (WorkerLeaseConflict) must still stop the
    loop immediately, or this would become a way to renew over a reclaimed
    lease.
    """
    factory = async_sessionmaker(hb_engine, expire_on_commit=False)
    runtime_session = factory()

    leases = WorkerLeaseManager(runtime_session)
    runtime = WorkerRuntime(
        runtime_session,
        WorkerRegistry(runtime_session),
        WorkerScheduler(WorkerRegistry(runtime_session)),
        leases,
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        lease_ttl_seconds=3,
        heartbeat_session_factory=factory,
    )
    worker_id = uuid4()
    await _register_test_worker(runtime_session, worker_id, "hb-retry")
    lease = await leases.acquire(
        worker_id=worker_id,
        execution_id=uuid4(),
        owner="acquisition:hb-retry",
        ttl_seconds=3,
    )
    version_at_start = lease.version

    ticks: list[tuple[str, int]] = []

    async def flaky_renewal(current, owner):
        # tick 1: transient writer lock; tick 2: same version, succeeds;
        # tick 3: the lease is genuinely gone -- stop.
        if not ticks:
            ticks.append(("transient", current.version))
            raise OperationalError(
                "UPDATE worker_leases", {}, Exception("database is locked")
            )
        if len(ticks) == 1:
            ticks.append(("renewed", current.version))
            return await runtime._renew_on_dedicated_session(current, owner)
        ticks.append(("conflict", current.version))
        raise WorkerLeaseConflict("Worker lease renewal failed fencing validation")

    monkeypatch.setattr(runtime, "_renew_lease", flaky_renewal)

    holder = [lease]
    stop = asyncio.Event()
    task = asyncio.create_task(runtime._heartbeat_lease(holder, "acquisition:hb-retry", stop))
    # three ticks at the 1.0s renewal floor; the loop returns on the conflict
    await asyncio.wait_for(task, timeout=30)

    assert [kind for kind, _ in ticks] == ["transient", "renewed", "conflict"]
    # the retry reused the version the failed CAS never advanced
    assert ticks[0][1] == ticks[1][1] == version_at_start
    # the successful renewal is the holder's state going forward
    assert holder[0].version == version_at_start + 1

    await runtime_session.close()


PG_DSN_VARIABLE = "CAP_PG_TEST_DSN"

#: What a working DSN for this test has to look like. The driver matters as much
#: as the server: the whole point of this variant is PostgreSQL's row-level
#: locking, which SQLite's single-writer limit cannot express.
PG_DSN_PREFIX = "postgresql+asyncpg://"


@pytest.mark.skipif(
    os.environ.get("CAP_PG_TEST") != "1",
    reason="authoritative PostgreSQL run: set CAP_PG_TEST=1 + "
    f"{PG_DSN_VARIABLE} (GA certification workflow provides a real postgres cluster)",
)
async def test_heartbeat_renewal_isolation_postgres_authoritative() -> None:
    """AUTHORITATIVE variant against PostgreSQL (the GA backend): with the
    main execution transaction holding an UNCOMMITTED WRITE, a concurrent
    renewal through the dedicated session must SUCCEED -- row-level locking
    allows exactly what SQLite's single-writer limit forbids.

    The DSN comes from ``CAP_PG_TEST_DSN``, NOT from ``DATABASE_URL``:
    tests/conftest.py pins ``DATABASE_URL`` to in-memory SQLite for every test
    process, so a variant that trusted it could never reach PostgreSQL. It then
    died on ``no such table: workers`` -- in-memory SQLite plus ``NullPool``
    means the create_all connection and the session's connection are different,
    empty databases -- which is how the strict GA job (run 35429972509) finally
    caught a test that had been unable to run since the day it was written.
    Skipping this variant is a certification GAP, never a pass.
    """
    url = os.environ.get(PG_DSN_VARIABLE, "").strip()
    assert url.startswith(PG_DSN_PREFIX), (
        f"CAP_PG_TEST=1 but {PG_DSN_VARIABLE} is "
        f"{url!r}: this variant must run against a real PostgreSQL through "
        f"{PG_DSN_PREFIX}. Point it at the certification cluster (the GA workflow "
        "port-forwards one); SQLite cannot express the case under test."
    )
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    runtime_session = factory()
    try:
        leases = WorkerLeaseManager(runtime_session)
        runtime = WorkerRuntime(
            runtime_session,
            WorkerRegistry(runtime_session),
            WorkerScheduler(WorkerRegistry(runtime_session)),
            leases,
            SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
            lease_ttl_seconds=120,
            heartbeat_session_factory=factory,
        )
        worker_id = uuid4()
        await _register_test_worker(runtime_session, worker_id, "pg-hb-invariant")
        lease = await leases.acquire(
            worker_id=worker_id,
            execution_id=uuid4(),
            owner="acquisition:pg-hb-invariant",
            ttl_seconds=120,
        )

        # main execution transaction DELIBERATELY OPEN with an uncommitted write
        pending = _pending_execution(worker_id)
        runtime_session.add(pending)
        await runtime_session.flush()

        # concurrent renewal on the dedicated connection must succeed
        renewed = await runtime._renew_on_dedicated_session(
            lease, owner="acquisition:pg-hb-invariant"
        )
        assert renewed.version == lease.version + 1
        assert datetime.now(UTC) >= renewed.renewed_at.replace(tzinfo=UTC)

        # fresh connection sees the committed renewal; the uncommitted write is
        # still invisible there but present in the open main transaction
        check = factory()
        try:
            rows = (
                (
                    await check.execute(
                        select(WorkerLeaseModel.version).where(
                            WorkerLeaseModel.id == lease.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [lease.version + 1]
            invisible = await check.get(SandboxExecution, pending.id)
            assert invisible is None
        finally:
            await check.close()
        again = await runtime_session.get(SandboxExecution, pending.id)
        assert again is not None

        await runtime_session.rollback()  # clean recovery, no state-machine error
    finally:
        # A mid-test failure must NOT leak the checked-out asyncpg connection:
        # its GC finalizer fires as an unraisable SAWarning minutes later and
        # pytest's unraisable hook then attributes it to whichever long test
        # happens to be running (that is how a fixture bug here poisoned the
        # 27-minute capacity gate in the 2e4d0b1 CI run).
        await runtime_session.close()
        await engine.dispose()
