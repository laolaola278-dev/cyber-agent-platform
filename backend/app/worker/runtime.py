"""Database-consistent Worker -> Sandbox -> Plugin -> Result execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventType, PlatformEvent
from app.events.transactional import publish_audit
from app.exceptions import WorkerConflict, WorkerExecutionError, WorkerLeaseConflict
from app.models.worker import SandboxExecution
from app.repositories.worker import SandboxExecutionRepository
from app.sandbox.runtime import SandboxResult, SandboxRuntime
from app.worker.contracts import (
    PluginExecutionRequest,
    SandboxExecutionStatus,
    WorkerExecutionResult,
    WorkerHeartbeat,
    WorkerLease,
    WorkerStatus,
)
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry
from app.worker.scheduler import WorkerScheduler

PluginOperation = Callable[[], Awaitable[dict[str, Any]]]

logger = logging.getLogger("cap.worker.runtime")

# Phase 28.6 (GATE 14 fix): concurrent worker Pods share ONE registry row
# (same name -> same row -> same state_version). Every registry heartbeat in
# the execution path is therefore an optimistic-concurrency CAS that can lose
# a routine race against another Pod's loop heartbeat. A lost race must NEVER
# kill an execution: it would leak the execution lease, inflate the shared
# row's active_executions (claim gating then deadlocks at zero slots and runs
# stick QUEUED forever), and -- in the exit path -- DISCARD an already
# successful result. Both call sites below retry on WorkerConflict with a
# fresh read; the exit path is additionally best-effort (never raises).
_HEARTBEAT_ATTEMPTS = 4


class WorkerRuntime:
    """Persist execution history and accept results only from the current lease holder."""

    def __init__(
        self,
        session: AsyncSession,
        registry: WorkerRegistry,
        scheduler: WorkerScheduler,
        leases: WorkerLeaseManager,
        sandbox: SandboxRuntime,
        *,
        lease_ttl_seconds: int = 120,
        heartbeat_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._scheduler = scheduler
        self._leases = leases
        self._sandbox = sandbox
        self._executions = SandboxExecutionRepository(session)
        self._lease_ttl_seconds = lease_ttl_seconds
        # The heartbeat task runs CONCURRENTLY with the main execute flow
        # (start/commit/rollback all use ``self._session``). Sharing one
        # AsyncSession between two tasks is unsupported by SQLAlchemy and
        # blew up under load with IllegalStateChangeError ("rollback() can't
        # be called here; commit() is already in progress"), sqlite
        # "closed database", and postgres "provisioning a new connection".
        # When a factory is provided the heartbeat renews through its OWN
        # short-lived session -- renewal is fencing-gated on
        # (owner, fencing_token, version), so any session works.
        self._heartbeat_session_factory = heartbeat_session_factory

    async def execute(
        self,
        request: PluginExecutionRequest,
        operation: PluginOperation,
        *,
        owner: str,
        execution_id: UUID | None = None,
        on_execution_start: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> WorkerExecutionResult:
        identifier = execution_id or uuid4()
        worker = await self._scheduler.select(request.capability)
        lease = await self._leases.acquire(
            worker_id=worker.id,
            execution_id=identifier,
            owner=owner,
            ttl_seconds=self._lease_ttl_seconds,
        )
        started = datetime.now(UTC)
        # GATE 14 fix: conflict-resilient BUSY heartbeat. A CAS loss here must
        # not abort the execution (it used to raise WorkerConflict BEFORE the
        # try block, leaking the freshly acquired lease and inflating the
        # shared row's active_executions until claiming deadlocked).
        await self._heartbeat_busy_resilient(worker.id, owner)
        attempts = 0
        last_execution_id: UUID | None = None
        # Phase 28.3 execution-time lease heartbeat: renew the execution lease
        # while the sandbox operation runs so a healthy long-running operation
        # is never fenced out at commit (commit_result checks expires_at).
        lease_holder: list[WorkerLease] = [lease]
        heartbeat_task = asyncio.create_task(self._heartbeat_lease(lease_holder, owner))
        try:
            while attempts <= request.retry_limit:
                attempts += 1
                sandbox_execution_id = uuid4()
                await self._start_execution(
                    sandbox_execution_id,
                    request,
                    worker.id,
                    lease_holder[0],
                    attempts,
                    last_execution_id,
                    owner,
                )
                if on_execution_start is not None:
                    await on_execution_start(sandbox_execution_id)
                try:
                    sandbox_result = await self._sandbox.execute(
                        request.sandbox_profile,
                        operation,
                        execution_id=sandbox_execution_id,
                    )
                except asyncio.CancelledError:
                    await self._commit_cancelled(
                        sandbox_execution_id, lease_holder[0], owner, attempts
                    )
                    raise
                terminal = self._terminal_status(sandbox_result, recovered=attempts > 1)
                await self._commit_result(
                    sandbox_execution_id,
                    sandbox_result,
                    terminal,
                    lease_holder[0],
                    owner,
                    attempts,
                )
                last_execution_id = sandbox_execution_id
                if sandbox_result.status == SandboxExecutionStatus.SUCCEEDED.value:
                    return WorkerExecutionResult(
                        execution_id=identifier,
                        worker_id=worker.id,
                        sandbox_execution_id=sandbox_execution_id,
                        status=SandboxExecutionStatus.SUCCEEDED.value,
                        result=sandbox_result.output,
                        attempts=attempts,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                if terminal is SandboxExecutionStatus.CANCELLED:
                    # cancellation is NOT a retryable failure: the operation
                    # was terminated on purpose (cancel / timeout). Propagate
                    # the CANCELLED status so the plugin layer can finalize.
                    return WorkerExecutionResult(
                        execution_id=identifier,
                        worker_id=worker.id,
                        sandbox_execution_id=sandbox_execution_id,
                        status=SandboxExecutionStatus.CANCELLED.value,
                        result=sandbox_result.output,
                        error="Sandbox execution cancelled",
                        timed_out=sandbox_result.timed_out,
                        attempts=attempts,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                if attempts > request.retry_limit:
                    return WorkerExecutionResult(
                        execution_id=identifier,
                        worker_id=worker.id,
                        sandbox_execution_id=sandbox_execution_id,
                        status=SandboxExecutionStatus.FAILED.value,
                        error=sandbox_result.error or "Plugin sandbox execution failed",
                        error_code=sandbox_result.error_code,
                        error_details=sandbox_result.error_details,
                        timed_out=sandbox_result.timed_out,
                        attempts=attempts,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                await asyncio.sleep(0)
            raise WorkerExecutionError("Worker retry loop exhausted unexpectedly")
        finally:
            # stop the execution-lease heartbeat (no renewal after exit)
            heartbeat_task.cancel()
            # Cancellation can land while the heartbeat is mid-DB-operation;
            # drivers surface that as driver-specific errors (aiosqlite
            # "closed database", asyncpg "provisioning a new connection")
            # instead of CancelledError. Teardown is BEST-EFFORT by design:
            # commit_result fences on the lease version, so a lost final
            # renewal can never produce a stale commit (Phase 28.3).
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat_task
            # tolerate a broken transaction (e.g. concurrent cancel released
            # the lease mid-run) so the best-effort release can still proceed
            try:
                await self._session.rollback()
            except Exception:  # noqa: BLE001 -- cleanup is best-effort
                pass
            try:
                await self._leases.release(
                    lease_holder[0].id,
                    owner=owner,
                    fencing_token=lease_holder[0].fencing_token,
                    expected_version=lease_holder[0].version,
                )
            except WorkerLeaseConflict:
                pass
            # GATE 14 fix: the execution is DONE at this point -- the result
            # (or the cancellation) has been committed. This decrement
            # heartbeat is pure bookkeeping on the SHARED worker row and must
            # never raise: an exception escaping the finally block used to
            # discard an already-successful payload AND skip the
            # active_executions decrement, permanently inflating the shared
            # row until the claim loop gated itself to zero slots (runs stuck
            # QUEUED). Best-effort with conflict retry; failures only log.
            try:
                await self._heartbeat_exit_resilient(worker.id, owner)
            except Exception as error:  # noqa: BLE001 -- bookkeeping never fails the run
                logger.warning(
                    "exit heartbeat for worker %s failed after retries "
                    "(bookkeeping only, execution result unaffected): %s",
                    worker.id,
                    error,
                )

    @property
    def registry(self) -> WorkerRegistry:
        return self._registry

    async def _heartbeat_busy_resilient(self, worker_id: UUID, owner: str) -> None:
        """BUSY heartbeat with fresh-read retry on shared-row CAS conflicts.

        The active_executions increment is computed from a FRESH read inside
        every attempt (never from the scheduler's possibly stale snapshot), so
        concurrent executions of sibling Pods converge instead of clobbering
        each other's counts.
        """
        last_error: Exception | None = None
        for _attempt in range(_HEARTBEAT_ATTEMPTS):
            try:
                current = await self._registry.require(worker_id)
                await self._registry.heartbeat(
                    WorkerHeartbeat(
                        worker_id=worker_id,
                        status=WorkerStatus.BUSY,
                        active_executions=current.active_executions + 1,
                    ),
                    actor=owner,
                )
                return
            except WorkerConflict as error:
                last_error = error
                with suppress(Exception):
                    await self._session.rollback()
        logger.warning(
            "BUSY heartbeat for worker %s skipped after %d conflicts "
            "(execution continues; the claim loop's periodic heartbeat "
            "re-converges active_executions): %s",
            worker_id,
            _HEARTBEAT_ATTEMPTS,
            last_error,
        )

    async def _heartbeat_exit_resilient(self, worker_id: UUID, owner: str) -> None:
        """Exit heartbeat (ONLINE/DRAINING, decrement) with conflict retry.

        Raises only after exhausting retries -- the caller treats this as
        best-effort bookkeeping and never fails the finished execution.
        """
        last_error: Exception | None = None
        for _attempt in range(_HEARTBEAT_ATTEMPTS):
            try:
                current = await self._registry.require(worker_id)
                await self._registry.heartbeat(
                    WorkerHeartbeat(
                        worker_id=worker_id,
                        status=(
                            WorkerStatus.DRAINING
                            if current.status is WorkerStatus.DRAINING
                            else WorkerStatus.ONLINE
                        ),
                        active_executions=max(0, current.active_executions - 1),
                    ),
                    actor=owner,
                )
                return
            except WorkerConflict as error:
                last_error = error
                with suppress(Exception):
                    await self._session.rollback()
        raise WorkerConflict(
            f"exit heartbeat for worker {worker_id} kept losing the CAS race"
        ) from last_error

    async def _heartbeat_lease(self, holder: list[WorkerLease], owner: str) -> None:
        """Renew the execution lease while the sandbox operation runs.

        Phase 28.3: ``commit_result`` fences on ``expires_at > now``, so an
        unrenewed execution lease would make a healthy long-running operation
        look stale at commit. Renewal is fencing-gated (version + token CAS):
        only the current lease holder can renew; if the lease is lost (e.g. a
        concurrent release/expiry), the heartbeat stops and the final commit
        is correctly fenced out. The heartbeat is cancelled in ``execute``'s
        finally -- no background task leaks.
        """
        interval = max(1.0, self._lease_ttl_seconds / 3.0)
        try:
            while True:
                await asyncio.sleep(interval)
                current = holder[0]
                try:
                    if self._heartbeat_session_factory is not None:
                        holder[0] = await self._renew_on_dedicated_session(current, owner)
                    else:
                        holder[0] = await self._leases.renew(
                            current.id,
                            owner=owner,
                            fencing_token=current.fencing_token,
                            expected_version=current.version,
                            ttl_seconds=self._lease_ttl_seconds,
                        )
                except WorkerLeaseConflict:
                    # lost the lease -- stop renewing; the commit path will
                    # be rejected by the fencing gate (correct stale outcome)
                    return
        except asyncio.CancelledError:
            raise

    async def _renew_on_dedicated_session(self, current: WorkerLease, owner: str) -> WorkerLease:
        """One renewal on a private short-lived session (concurrency-safe)."""
        session = self._heartbeat_session_factory()
        try:
            renewed = await WorkerLeaseManager(session).renew(
                current.id,
                owner=owner,
                fencing_token=current.fencing_token,
                expected_version=current.version,
                ttl_seconds=self._lease_ttl_seconds,
            )
            return renewed
        finally:
            await session.close()

    async def health(self) -> bool:
        return bool(await self._registry.list()) and await self._sandbox.health()

    async def _start_execution(
        self,
        execution_id: UUID,
        request: PluginExecutionRequest,
        worker_id: UUID,
        lease: WorkerLease,
        attempt: int,
        recovery_of: UUID | None,
        owner: str,
    ) -> None:
        started = datetime.now(UTC)
        await self._executions.add(
            SandboxExecution(
                execution_id=execution_id,
                worker_id=worker_id,
                profile_id=None,
                plugin_name=request.plugin_name,
                plugin_version=request.plugin_version,
                operation=request.operation,
                provider=self._sandbox.provider.provider_name,
                status=SandboxExecutionStatus.RUNNING.value,
                result_metadata={},
                error=None,
                started_at=started,
                finished_at=None,
                timed_out=False,
                terminated=False,
                lease_id=lease.id,
                lease_version=lease.version,
                attempt=attempt,
                recovery_of_execution_id=recovery_of,
            )
        )
        await publish_audit(
            self._session,
            PlatformEvent(
                type=EventType.SANDBOX_EXECUTION_STARTED,
                trace_id=str(lease.execution_id),
                aggregate_id=execution_id,
                actor=owner,
                resource=f"sandbox-execution:{execution_id}",
                payload={"attempt": attempt, "worker_id": str(worker_id)},
            ),
        )
        await self._session.commit()

    async def _commit_result(
        self,
        execution_id: UUID,
        result: SandboxResult,
        status: SandboxExecutionStatus,
        lease: WorkerLease,
        owner: str,
        attempt: int,
    ) -> None:
        # tolerate a broken transaction (concurrent cancel may have torn down
        # the sandbox mid-flush leaving this session pending rollback): retry
        # once after rollback -- fencing still decides whether the commit is
        # accepted.
        try:
            committed = await self._executions.commit_result(
                execution_id=execution_id,
                lease_id=lease.id,
                owner=owner,
                fencing_token=lease.fencing_token,
                expected_lease_version=lease.version,
                now=datetime.now(UTC),
                values={
                    "status": status.value,
                    "result_metadata": result.output,
                    "error": result.error,
                    "finished_at": result.finished_at,
                    "timed_out": result.timed_out,
                    "terminated": result.terminated,
                },
            )
        except Exception:  # noqa: BLE001 -- broken transaction
            await self._session.rollback()
            committed = await self._executions.commit_result(
                execution_id=execution_id,
                lease_id=lease.id,
                owner=owner,
                fencing_token=lease.fencing_token,
                expected_lease_version=lease.version,
                now=datetime.now(UTC),
                values={
                    "status": status.value,
                    "result_metadata": result.output,
                    "error": result.error,
                    "finished_at": result.finished_at,
                    "timed_out": result.timed_out,
                    "terminated": result.terminated,
                },
            )
        if committed is None:
            await self._session.rollback()
            raise WorkerLeaseConflict("Sandbox result rejected by lease fencing validation")
        event_type = {
            SandboxExecutionStatus.SUCCEEDED: EventType.SANDBOX_EXECUTION_COMPLETED,
            SandboxExecutionStatus.RECOVERED: EventType.SANDBOX_EXECUTION_RECOVERED,
            SandboxExecutionStatus.TIMED_OUT: EventType.SANDBOX_EXECUTION_TIMED_OUT,
        }.get(status, EventType.SANDBOX_EXECUTION_FAILED)
        await publish_audit(
            self._session,
            PlatformEvent(
                type=event_type,
                trace_id=str(lease.execution_id),
                aggregate_id=execution_id,
                actor=owner,
                resource=f"sandbox-execution:{execution_id}",
                payload={"status": status.value, "attempt": attempt},
                result=result.output or None,
                error=result.error,
            ),
        )
        await self._session.commit()

    async def _commit_cancelled(
        self, execution_id: UUID, lease: WorkerLease, owner: str, attempt: int
    ) -> None:
        observed = datetime.now(UTC)
        try:
            committed = await self._executions.commit_result(
                execution_id=execution_id,
                lease_id=lease.id,
                owner=owner,
                fencing_token=lease.fencing_token,
                expected_lease_version=lease.version,
                now=observed,
                values={
                    "status": SandboxExecutionStatus.CANCELLED.value,
                    "error": "Sandbox execution cancelled",
                    "finished_at": observed,
                    "terminated": True,
                },
            )
        except Exception:  # noqa: BLE001 -- broken transaction
            await self._session.rollback()
            committed = await self._executions.commit_result(
                execution_id=execution_id,
                lease_id=lease.id,
                owner=owner,
                fencing_token=lease.fencing_token,
                expected_lease_version=lease.version,
                now=observed,
                values={
                    "status": SandboxExecutionStatus.CANCELLED.value,
                    "error": "Sandbox execution cancelled",
                    "finished_at": observed,
                    "terminated": True,
                },
            )
        if committed is None:
            await self._session.rollback()
            return
        await publish_audit(
            self._session,
            PlatformEvent(
                type=EventType.SANDBOX_EXECUTION_CANCELLED,
                trace_id=str(lease.execution_id),
                aggregate_id=execution_id,
                actor=owner,
                resource=f"sandbox-execution:{execution_id}",
                payload={"status": SandboxExecutionStatus.CANCELLED.value, "attempt": attempt},
            ),
        )
        try:
            await self._session.commit()
        except Exception:  # noqa: BLE001 -- best-effort
            await self._session.rollback()

    @staticmethod
    def _terminal_status(result: SandboxResult, *, recovered: bool) -> SandboxExecutionStatus:
        if result.status == SandboxExecutionStatus.SUCCEEDED.value:
            return (
                SandboxExecutionStatus.RECOVERED if recovered else SandboxExecutionStatus.SUCCEEDED
            )
        if result.terminated and not result.timed_out:
            # provider-side cancellation (terminate() was invoked)
            return SandboxExecutionStatus.CANCELLED
        if result.timed_out:
            return SandboxExecutionStatus.TIMED_OUT
        return SandboxExecutionStatus.FAILED

    async def terminate(self, execution_id: UUID) -> bool:
        """Forward cancellation to the sandbox boundary (releases browser /
        network / file resources held by the executing operation)."""
        return await self._sandbox.terminate(execution_id)
