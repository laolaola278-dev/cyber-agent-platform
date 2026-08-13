"""Agent Runtime lifecycle manager and execution boundary."""

import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AgentStatus, HealthStatus
from app.core.state_machine import AgentStateMachine
from app.events import EventPublisher, EventType, PlatformEvent
from app.models import Agent, AgentRuntime, Task
from app.runtime.context import RuntimeContext
from app.runtime.manifest import AgentManifest, ManifestLoader


class RuntimeManager:
    """Load and own Agent instances; the Dispatcher never invokes Agents directly."""

    def __init__(
        self,
        session: AsyncSession,
        publisher: EventPublisher,
        services: "ServiceProvider",
        report_service: "ReportService",
        runtime_configuration: dict[str, Any] | None = None,
        manifest_loader: ManifestLoader | None = None,
    ) -> None:
        self._session = session
        self._publisher = publisher
        self._services = services
        self._report_service = report_service
        self._runtime_configuration = runtime_configuration or {}
        self._loader = manifest_loader or ManifestLoader()
        self._instances: dict[UUID, BaseAgent] = {}

    async def load(self, agent: Agent, manifest_path: Path, *, trace_id: str) -> AgentRuntime:
        """Validate a manifest, instantiate an Agent and persist its Runtime record."""

        manifest = self._loader.load(manifest_path)
        if manifest.name != agent.name or manifest.version != agent.version:
            raise ValueError("Manifest identity must match its registered Agent")
        implementation = self._instantiate(manifest, manifest_path.parent)
        runtime = AgentRuntime(
            agent_id=agent.id,
            manifest_path=str(manifest_path),
            entrypoint=manifest.runtime.entrypoint,
            status=AgentStatus.OFFLINE.value,
            loaded_at=datetime.now(UTC),
        )
        self._session.add(runtime)
        await self._session.flush()
        self._instances[agent.id] = implementation
        await self._publish(
            EventType.RUNTIME_LOADED, agent, trace_id, {"runtime_id": str(runtime.id)}
        )
        return runtime

    async def start(
        self, runtime: AgentRuntime, agent: Agent, task: Task, *, trace_id: str
    ) -> AgentRuntime:
        """Initialize the loaded Agent using the narrow RuntimeContext."""

        instance = self._instance(runtime.agent_id)
        runtime.status = AgentStatus.STARTING.value
        if agent.status == AgentStatus.OFFLINE.value:
            AgentStateMachine.transition(agent, AgentStatus.STARTING)
        context = self._context(agent.id, task, trace_id)
        try:
            await instance.initialize(context)
        except (RuntimeError, OSError, ValueError, LookupError) as error:
            runtime.status = AgentStatus.ERROR.value
            runtime.last_error = str(error)
            if agent.status == AgentStatus.STARTING.value:
                AgentStateMachine.transition(agent, AgentStatus.ERROR)
            await self._publish(
                EventType.RUNTIME_TASK_FAILED,
                agent,
                trace_id,
                {"runtime_id": str(runtime.id), "phase": "initialize"},
                error=str(error),
            )
            raise
        runtime.status = AgentStatus.ONLINE.value
        runtime.started_at = datetime.now(UTC)
        if agent.status == AgentStatus.STARTING.value:
            AgentStateMachine.transition(agent, AgentStatus.ONLINE)
        agent.health_status = HealthStatus.HEALTHY.value
        await self._publish(
            EventType.RUNTIME_STARTED, agent, trace_id, {"runtime_id": str(runtime.id)}
        )
        return runtime

    async def execute(
        self, runtime: AgentRuntime, agent: Agent, task: Task, *, trace_id: str
    ) -> dict[str, Any]:
        """Execute one Task through RuntimeContext and return normalized output."""

        if runtime.status != AgentStatus.ONLINE.value:
            await self.start(runtime, agent, task, trace_id=trace_id)
        instance = self._instance(agent.id)
        from app.sdk.contracts import AgentContext, TaskRequest

        await self._publish(
            EventType.RUNTIME_TASK_STARTED, agent, trace_id, {"task_id": str(task.id)}
        )
        result = await instance.execute(
            TaskRequest(
                id=task.id,
                task_type=task.task_type,
                input=task.input,
                required_permissions=set(task.required_permissions),
            ),
            AgentContext(
                trace_id=trace_id,
                task_id=task.id,
                agent_id=agent.id,
                actor="runtime",
                metadata={"runtime_context": self._context(agent.id, task, trace_id)},
            ),
        )
        payload = result.model_dump(mode="json")
        report = await self._report_service.generate(
            task=task,
            agent_id=agent.id,
            trace_id=trace_id,
            status="SUCCESS" if result.success else "FAILED",
            error=result.error,
        )
        payload["report_id"] = str(report.id)
        await self._publish(
            (EventType.RUNTIME_TASK_FINISHED if result.success else EventType.RUNTIME_TASK_FAILED),
            agent,
            trace_id,
            {
                "task_id": str(task.id),
                "success": result.success,
                "report_id": str(report.id),
            },
            result=payload,
            error=result.error,
        )
        return payload

    async def stop(self, runtime: AgentRuntime, agent: Agent, *, trace_id: str) -> AgentRuntime:
        """Shut down a loaded Agent and persist the terminal runtime state."""

        if runtime.agent_id in self._instances:
            runtime.status = AgentStatus.STOPPING.value
            if agent.status == AgentStatus.ONLINE.value:
                AgentStateMachine.transition(agent, AgentStatus.STOPPING)
            await self._instances[runtime.agent_id].shutdown()
            self._instances.pop(runtime.agent_id, None)
        runtime.status = AgentStatus.OFFLINE.value
        runtime.stopped_at = datetime.now(UTC)
        if agent.status == AgentStatus.STOPPING.value:
            AgentStateMachine.transition(agent, AgentStatus.OFFLINE)
        await self._publish(
            EventType.RUNTIME_STOPPED, agent, trace_id, {"runtime_id": str(runtime.id)}
        )
        return runtime

    async def restart(
        self, runtime: AgentRuntime, agent: Agent, task: Task, *, trace_id: str
    ) -> AgentRuntime:
        """Stop and start a runtime using the same registered definition."""

        await self.stop(runtime, agent, trace_id=trace_id)
        return await self.start(runtime, agent, task, trace_id=trace_id)

    async def health(self, runtime: AgentRuntime, agent: Agent, *, trace_id: str) -> dict[str, Any]:
        """Query Agent health through the runtime boundary."""

        check = await self._instance(runtime.agent_id).health_check()
        runtime.last_health = check.model_dump(mode="json")
        agent.health_status = (
            HealthStatus.HEALTHY.value if check.healthy else HealthStatus.UNHEALTHY.value
        )
        await self._publish(EventType.RUNTIME_HEALTH_CHECKED, agent, trace_id, runtime.last_health)
        return runtime.last_health

    async def reload(
        self, runtime: AgentRuntime, agent: Agent, manifest_path: Path, *, trace_id: str
    ) -> AgentRuntime:
        """Replace a stopped runtime instance after re-validating its manifest."""

        await self.stop(runtime, agent, trace_id=trace_id)
        manifest = self._loader.load(manifest_path)
        self._instances[agent.id] = self._instantiate(manifest, manifest_path.parent)
        runtime.manifest_path = str(manifest_path)
        runtime.entrypoint = manifest.runtime.entrypoint
        runtime.loaded_at = datetime.now(UTC)
        await self._publish(
            EventType.RUNTIME_RELOADED, agent, trace_id, {"runtime_id": str(runtime.id)}
        )
        return runtime

    async def destroy(self, runtime: AgentRuntime, agent: Agent, *, trace_id: str) -> None:
        """Stop and remove a runtime persistence record."""

        await self.stop(runtime, agent, trace_id=trace_id)
        await self._session.delete(runtime)
        await self._publish(
            EventType.RUNTIME_DESTROYED,
            agent,
            trace_id,
            {"runtime_id": str(runtime.id)},
        )

    def is_loaded(self, agent_id: UUID) -> bool:
        """Return whether this process currently owns the Agent instance."""

        return agent_id in self._instances

    def _context(self, agent_id: UUID, task: Task, trace_id: str) -> RuntimeContext:
        return RuntimeContext(
            task=task,
            trace_id=trace_id,
            logger=logging.getLogger("cap.runtime"),
            configuration=self._runtime_configuration,
            publisher=self._publisher,
            services=self._services,
            agent_id=agent_id,
        )

    def _instantiate(self, manifest: AgentManifest, agent_directory: Path) -> "BaseAgent":
        module_name, class_name = manifest.runtime.entrypoint.rsplit(":", maxsplit=1)
        if str(agent_directory) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(agent_directory))
        module = importlib.import_module(module_name)
        candidate = getattr(module, class_name)
        instance = candidate()
        from app.sdk.base_agent import BaseAgent

        if not isinstance(instance, BaseAgent):
            raise TypeError("Manifest entrypoint must create a BaseAgent")
        return instance

    def _instance(self, agent_id: UUID) -> "BaseAgent":
        try:
            return self._instances[agent_id]
        except KeyError as error:
            raise RuntimeError(f"Agent {agent_id} is not loaded") from error

    async def _publish(
        self,
        event_type: "EventType",
        agent: Agent,
        trace_id: str,
        payload: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=trace_id,
                aggregate_id=agent.id,
                actor="runtime-manager",
                resource=f"agent:{agent.id}",
                agent_id=agent.id,
                payload=payload,
                result=result,
                error=error,
            )
        )


from app.report.service import ReportService  # noqa: E402
from app.runtime.services import ServiceProvider  # noqa: E402
from app.sdk.base_agent import BaseAgent  # noqa: E402
