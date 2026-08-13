"""Database-consistent Worker -> Sandbox -> Plugin -> Result execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventType, PlatformEvent
from app.events.transactional import publish_audit
from app.exceptions import WorkerExecutionError, WorkerLeaseConflict
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
    ) -> None:
        self._session = session
        self._registry = registry
        self._scheduler = scheduler
        self._leases = leases
        self._sandbox = sandbox
        self._executions = SandboxExecutionRepository(session)
        self._lease_ttl_seconds = lease_ttl_seconds

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
        await self._registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id,
                status=WorkerStatus.BUSY,
                active_executions=worker.active_executions + 1,
            ),
            actor=owner,
        )
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
            with suppress(asyncio.CancelledError):
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
            current = await self._registry.require(worker.id)
            await self._registry.heartbeat(
                WorkerHeartbeat(
                    worker_id=worker.id,
                    status=(
                        WorkerStatus.DRAINING
                        if current.status is WorkerStatus.DRAINING
                        else WorkerStatus.ONLINE
                    ),
                    active_executions=max(0, current.active_executions - 1),
                ),
                actor=owner,
            )

    @property
    def registry(self) -> WorkerRegistry:
        return self._registry

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
