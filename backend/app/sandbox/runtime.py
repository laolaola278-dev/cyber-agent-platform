"""Platform sandbox runtime and synthetic provider used by the Worker Framework."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.exceptions import PlatformError, SandboxExecutionError
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.profile import SandboxProfile


class SandboxResult(BaseModel):
    """Serializable observed result of one plugin sandbox execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    provider: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime
    timed_out: bool = False
    terminated: bool = False
    exit_code: int | None = None


class SandboxProviderCapability(BaseModel):
    """Provider features used for fail-closed placement and profile validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    network: bool = False
    filesystem: bool = False
    secret: bool = False
    timeout: bool = True
    process: bool = False
    resource: bool = False
    container: bool = False
    vm: bool = False
    snapshot: bool = False


class SandboxProvider(Protocol):
    """Isolation provider port for future process, OCI, gVisor or microVM backends."""

    provider_name: str
    real_isolation: bool
    capabilities: SandboxProviderCapability

    async def execute(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        secrets: dict[str, str] | None = None,
    ) -> SandboxResult: ...

    async def terminate(self, execution_id: UUID) -> bool: ...

    async def health(self) -> bool: ...


class MemorySandboxProvider:
    """Deterministic provider certifying framework semantics without OS isolation claims."""

    provider_name = "memory-sandbox"
    real_isolation = False
    capabilities = SandboxProviderCapability(
        network=False,
        filesystem=False,
        secret=False,
        timeout=True,
    )

    def __init__(self) -> None:
        self._active: set[UUID] = set()
        self._terminated: set[UUID] = set()
        self._tasks: dict[UUID, asyncio.Task] = {}

    async def execute(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        secrets: dict[str, str] | None = None,
    ) -> SandboxResult:
        # Phase 28.4 secrets: the in-process provider has no isolation domain,
        # so injecting secrets here would expose them to the worker/API
        # process (capability secret=False -> fail closed: secrets are simply
        # NOT injected). Callers must only pass secrets to a provider whose
        # capability model declares secret support.
        del secrets
        started = datetime.now(UTC)
        self._active.add(execution_id)
        loop = asyncio.get_running_loop()

        async def _guarded() -> dict[str, Any]:
            return await operation()

        task = loop.create_task(_guarded())
        self._tasks[execution_id] = task
        try:
            try:
                async with asyncio.timeout(profile.timeout_seconds):
                    output = await task
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="SUCCEEDED",
                    output=output,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    exit_code=0,
                )
            except asyncio.CancelledError:
                # external terminate() cancelled the task
                self._terminated.add(execution_id)
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="CANCELLED",
                    error="Sandbox execution cancelled",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    terminated=True,
                    exit_code=130,
                )
            except TimeoutError:
                self._terminated.add(execution_id)
                if not task.done():
                    task.cancel()
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    error="Sandbox execution timed out",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    timed_out=True,
                    terminated=True,
                    exit_code=124,
                )
            except Exception as error:
                platform_error = error if isinstance(error, PlatformError) else None
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    error=str(error),
                    error_code=platform_error.code if platform_error else None,
                    error_details=platform_error.details if platform_error else {},
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    exit_code=1,
                )
        finally:
            self._active.discard(execution_id)
            self._tasks.pop(execution_id, None)

    async def terminate(self, execution_id: UUID) -> bool:
        existed = execution_id in self._active
        task = self._tasks.get(execution_id)
        if task is not None and not task.done():
            task.cancel()
        self._active.discard(execution_id)
        self._terminated.add(execution_id)
        return existed

    async def health(self) -> bool:
        return True


class SandboxRuntime:
    """Validate the profile and run a plugin operation only through a provider."""

    def __init__(
        self,
        provider: SandboxProvider,
        policy: SandboxPolicyEngine,
        metrics: Any | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._metrics = metrics

    @property
    def provider(self) -> SandboxProvider:
        return self._provider

    async def execute(
        self,
        profile: SandboxProfile,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        *,
        execution_id: UUID | None = None,
        secrets: dict[str, str] | None = None,
    ) -> SandboxResult:
        self._policy.validate(profile, self._provider.provider_name)
        capabilities = self._provider.capabilities
        if profile.network_enabled and not capabilities.network:
            raise SandboxExecutionError("Sandbox provider does not support network isolation")
        if profile.filesystem_writable and not capabilities.filesystem:
            raise SandboxExecutionError("Sandbox provider does not support writable filesystems")
        if profile.secret_references and not capabilities.secret:
            raise SandboxExecutionError("Sandbox provider does not support secret injection")
        if (secrets or profile.secret_references) and not capabilities.secret:
            raise SandboxExecutionError("Sandbox provider does not support secret injection")
        if profile.timeout_seconds and not capabilities.timeout:
            raise SandboxExecutionError("Sandbox provider does not support execution timeouts")
        identifier = execution_id or uuid4()
        import time as _t

        _started = _t.monotonic()
        result = await self._provider.execute(identifier, profile, operation, secrets=secrets)
        if self._metrics is not None:
            self._metrics.inc(
                "sandbox_execution_total", labels={"provider": self._provider.provider_name}
            )
            self._metrics.observe_duration(
                "sandbox_execution_duration",
                max(0.0, _t.monotonic() - _started),
                labels={"provider": self._provider.provider_name},
            )
            if result.terminated:
                self._metrics.inc("sandbox_forced_termination_total")
        if result.execution_id != identifier or result.provider != self._provider.provider_name:
            raise SandboxExecutionError("Sandbox provider returned an invalid execution identity")
        return result

    async def execute_typed(
        self,
        profile: SandboxProfile,
        request: Any,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        lease_id: str | None = None,
        attempt: int = 0,
        secrets: dict[str, str] | None = None,
    ) -> Any:
        """Phase 28.5 -- typed protocol execution (OCI provider).

        Validates the profile against the provider capabilities and forwards
        a typed SandboxRequest to the provider's container path. Providers
        without ``execute_request`` fail closed (no cloudpickle across the
        container trust boundary).
        """
        self._policy.validate(profile, self._provider.provider_name)
        capabilities = self._provider.capabilities
        if profile.network_enabled and not capabilities.network:
            raise SandboxExecutionError("Sandbox provider does not support network isolation")
        if profile.secret_references and not capabilities.secret:
            raise SandboxExecutionError("Sandbox provider does not support secret injection")
        if profile.timeout_seconds and not capabilities.timeout:
            raise SandboxExecutionError("Sandbox provider does not support execution timeouts")
        execute_request = getattr(self._provider, "execute_request", None)
        if execute_request is None:
            raise SandboxExecutionError(
                f"provider {self._provider.provider_name} does not support "
                "typed sandbox executions (execute_request)"
            )
        return await execute_request(
            profile,
            request,
            run_id=run_id,
            worker_id=worker_id,
            lease_id=lease_id,
            attempt=attempt,
            secrets=secrets,
        )

    async def terminate(self, execution_id: UUID) -> bool:
        return await self._provider.terminate(execution_id)

    async def health(self) -> bool:
        return await self._provider.health()
