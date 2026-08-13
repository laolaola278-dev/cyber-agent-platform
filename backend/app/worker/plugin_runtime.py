"""Generic bridge that prevents platform runtimes from calling plugins directly."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.exceptions import PlatformError, WorkerCancelledError, WorkerExecutionError
from app.sandbox import (
    MemorySandboxProvider,
    SandboxPolicyEngine,
    SandboxProfile,
    SandboxRuntime,
)
from app.worker.contracts import (
    PluginExecutionRequest,
    SandboxExecutionStatus,
    WorkerExecutionResult,
    WorkerHeartbeat,
    WorkerRecord,
    WorkerStatus,
)
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler

ResultT = TypeVar("ResultT", bound=BaseModel)

_SYNTHETIC_ENGINE = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SYNTHETIC_SESSIONS = async_sessionmaker(_SYNTHETIC_ENGINE, expire_on_commit=False)
_SYNTHETIC_INITIALIZED = False
_SYNTHETIC_LOCK = asyncio.Lock()
_SYNTHETIC_PROVIDER = MemorySandboxProvider()


def _platform_error_types() -> dict[str, type[PlatformError]]:
    pending = [PlatformError]
    discovered: dict[str, type[PlatformError]] = {}
    while pending:
        base = pending.pop()
        for candidate in base.__subclasses__():
            pending.append(candidate)
            discovered[candidate.code] = candidate
    return discovered


async def _initialize_synthetic_database() -> None:
    global _SYNTHETIC_INITIALIZED
    if _SYNTHETIC_INITIALIZED:
        return
    async with _SYNTHETIC_LOCK:
        if _SYNTHETIC_INITIALIZED:
            return
        async with _SYNTHETIC_ENGINE.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        _SYNTHETIC_INITIALIZED = True


class PluginWorkerRuntime:
    """Serialize plugin lifecycle results across the Worker/Sandbox boundary."""

    def __init__(
        self,
        runtime: WorkerRuntime | None,
        profile: SandboxProfile,
        *,
        synthetic_capabilities: frozenset[str] | None = None,
    ) -> None:
        self._runtime = runtime
        self._profile = profile
        self._synthetic_capabilities = synthetic_capabilities
        self.last_execution: WorkerExecutionResult | None = None

    @classmethod
    def synthetic(cls, capabilities: frozenset[str]) -> PluginWorkerRuntime:
        """Use a database-backed synthetic control plane without claiming OS isolation."""

        return cls(
            None,
            SandboxProfile(name="default-plugin-sandbox"),
            synthetic_capabilities=capabilities,
        )

    async def terminate(self, execution_id: UUID) -> bool:
        """Forward cancellation to the sandbox boundary (best effort)."""
        if self._runtime is None:
            return False
        return await self._runtime.terminate(execution_id)

    async def execute(
        self,
        *,
        plugin_name: str,
        plugin_version: str,
        capability: str,
        operation_name: str,
        owner: str,
        operation: Callable[[], Awaitable[ResultT]],
        result_type: type[ResultT],
        retry_limit: int = 0,
        timeout_seconds: int | None = None,
        on_execution_start: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> ResultT:
        async def serialized() -> dict[str, object]:
            result = await operation()
            return result.model_dump(mode="json")

        request = PluginExecutionRequest(
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            capability=capability,
            operation=operation_name,
            sandbox_profile=(
                self._profile.model_copy(update={"timeout_seconds": timeout_seconds})
                if timeout_seconds is not None
                else self._profile
            ),
            retry_limit=retry_limit,
        )
        if self._runtime is not None:
            execution = await self._runtime.execute(
                request,
                serialized,
                owner=owner,
                execution_id=uuid4(),
                on_execution_start=on_execution_start,
            )
            self.last_execution = execution
        else:
            execution = await self._execute_synthetic(
                request, serialized, owner=owner, on_execution_start=on_execution_start
            )
            self.last_execution = execution
        if execution.status == SandboxExecutionStatus.CANCELLED.value:
            # cancellation is a legitimate terminal outcome, not a failure:
            # surface an empty result so the plugin caller can finalize the
            # run as CANCELLED (resources already closed by the sandbox).
            raise WorkerCancelledError(
                "Plugin execution cancelled", details={"execution_id": str(execution.execution_id)}
            )
        if execution.status != "SUCCEEDED":
            error_message = execution.error or "Plugin Worker execution failed"
            error_type = _platform_error_types().get(execution.error_code or "")
            if error_type is not None:
                raise error_type(error_message, details=execution.error_details)
            raise WorkerExecutionError(
                error_message,
                details={"timed_out": execution.timed_out},
            )
        try:
            return result_type.model_validate(execution.result)
        except ValueError as error:
            raise WorkerExecutionError("Worker returned an invalid plugin result") from error

    async def _execute_synthetic(
        self,
        request: PluginExecutionRequest,
        operation: Callable[[], Awaitable[dict[str, object]]],
        *,
        owner: str,
        on_execution_start: Callable[[UUID], Awaitable[None]] | None = None,
    ):
        await _initialize_synthetic_database()
        capabilities = self._synthetic_capabilities or frozenset({request.capability})
        identity = sha256("\n".join(sorted(capabilities)).encode()).hexdigest()[:16]
        async with _SYNTHETIC_SESSIONS() as session:
            registry = WorkerRegistry(session)
            worker = await registry.register(
                WorkerRecord(
                    name=f"synthetic-db-worker-{identity}",
                    runtime_version="phase-18.1",
                    capabilities=capabilities,
                    max_concurrency=1024,
                ),
                actor="synthetic-test-runtime",
            )
            if worker.status is WorkerStatus.REGISTERED:
                await registry.heartbeat(
                    WorkerHeartbeat(
                        worker_id=worker.id,
                        status=WorkerStatus.ONLINE,
                        active_executions=0,
                    ),
                    actor="synthetic-test-runtime",
                )
            leases = WorkerLeaseManager(session)
            runtime = WorkerRuntime(
                session,
                registry,
                WorkerScheduler(registry),
                leases,
                SandboxRuntime(_SYNTHETIC_PROVIDER, SandboxPolicyEngine()),
            )
            return await runtime.execute(
                request,
                operation,
                owner=owner,
                execution_id=uuid4(),
                on_execution_start=on_execution_start,
            )
