"""Configurable, strategy-driven Task Dispatcher."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import OrchestratorConfig, RegistryConfig
from app.core.enums import TaskStatus
from app.core.state_machine import TaskStateMachine
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import PermissionDenied, RegistryError
from app.models import ExecutionLog, Task, TaskExecution, TaskLog
from app.orchestrator.strategies import SchedulingStrategy
from app.repositories import AgentRepository, CapabilityRepository, TaskRepository
from app.runtime.service import RuntimeService


class TaskDispatcher:
    """Map a Task to an eligible Agent through an injected scheduling strategy."""

    def __init__(
        self,
        session: AsyncSession,
        task_repository: TaskRepository,
        agent_repository: AgentRepository,
        publisher: EventPublisher,
        strategy: SchedulingStrategy,
        config: OrchestratorConfig,
        registry_config: RegistryConfig,
        runtime_service: RuntimeService | None = None,
        capability_repository: CapabilityRepository | None = None,
    ) -> None:
        self._session = session
        self._tasks = task_repository
        self._agents = agent_repository
        self._publisher = publisher
        self._strategy = strategy
        self._config = config
        self._registry_config = registry_config
        self._runtime_service = runtime_service
        self._capabilities = capability_repository

    async def dispatch(self, task: Task, *, trace_id: str) -> TaskExecution:
        required_permissions = set(task.required_permissions)
        required_capabilities = set(task.required_capabilities)
        candidate_agent_ids = None
        if required_capabilities:
            if self._capabilities is None:
                raise RuntimeError("CapabilityRepository is required for capability-based dispatch")
            candidate_agent_ids = await self._capabilities.list_agent_ids_for_capabilities(
                required_capabilities
            )
        eligible_statuses = {
            status.value for status in self._config.dispatcher.eligible_agent_statuses
        }
        candidates = await self._agents.list_eligible(
            required_permissions,
            eligible_statuses=eligible_statuses,
            heartbeat_stale_after_seconds=(self._registry_config.heartbeat.stale_after_seconds),
            target_agent_id=task.target_agent_id,
            candidate_agent_ids=candidate_agent_ids,
        )
        agent = self._strategy.select(task, candidates)
        if agent is None:
            targeted = task.target_agent_id is not None
            event_type = EventType.PERMISSION_REJECTED if targeted else EventType.DISPATCH_FAILED
            error = (
                "Target Agent is unavailable or lacks required permissions"
                if targeted
                else "No eligible Agent found for task"
            )
            await self._publisher.publish(
                PlatformEvent(
                    type=event_type,
                    trace_id=trace_id,
                    aggregate_id=task.id,
                    actor="orchestrator",
                    resource=f"task:{task.id}",
                    task_id=task.id,
                    agent_id=task.target_agent_id,
                    payload={
                        "required_permissions": sorted(required_permissions),
                        "required_capabilities": sorted(required_capabilities),
                    },
                    error=error,
                )
            )
            await self._session.commit()
            if targeted:
                raise PermissionDenied(error)
            raise RegistryError(error)

        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        execution = await self._tasks.add_execution(
            TaskExecution(
                task=task,
                agent_id=agent.id,
                status=TaskStatus.QUEUED.value,
                trace_id=trace_id,
            )
        )
        await self._tasks.add_log(
            TaskLog(
                task_id=task.id,
                level="INFO",
                message="Task dispatched",
                trace_id=trace_id,
            )
        )
        await self._tasks.add_execution_log(
            ExecutionLog(
                execution_id=execution.id,
                level="INFO",
                message=f"Queued for Agent {agent.id}",
            )
        )
        await self._publish_state_change(
            task,
            trace_id=trace_id,
            agent_id=agent.id,
            execution_id=execution.id,
            previous=TaskStatus.CREATED,
        )
        return execution

    async def execute(
        self, execution: TaskExecution, task: Task, *, trace_id: str
    ) -> TaskExecution:
        """Run a queued Task only through the injected Runtime boundary."""

        if self._runtime_service is None:
            raise RuntimeError("RuntimeService is required for Agent execution")
        await self.mark_running(execution, trace_id=trace_id)
        agent = await self._agents.get(execution.agent_id)
        if agent is None:
            raise RegistryError(f"Assigned Agent {execution.agent_id} no longer exists")
        result = await self._runtime_service.execute(agent, task, trace_id=trace_id)
        return await self.mark_finished(
            execution,
            success=bool(result.get("success")),
            result=result,
            trace_id=trace_id,
        )

    async def mark_running(self, execution: TaskExecution, *, trace_id: str) -> TaskExecution:
        TaskStateMachine.transition(execution.task, TaskStatus.RUNNING)
        TaskStateMachine.transition(execution, TaskStatus.RUNNING)
        execution.start_time = datetime.now(UTC)
        await self._publish_state_change(
            execution.task,
            trace_id=trace_id,
            agent_id=execution.agent_id,
            execution_id=execution.id,
            previous=TaskStatus.QUEUED,
        )
        await self._session.commit()
        return execution

    async def mark_finished(
        self,
        execution: TaskExecution,
        *,
        success: bool,
        result: dict[str, object],
        trace_id: str,
    ) -> TaskExecution:
        target = TaskStatus.SUCCESS if success else TaskStatus.FAILED
        TaskStateMachine.transition(execution.task, target)
        TaskStateMachine.transition(execution, target)
        execution.end_time = datetime.now(UTC)
        execution.result = result
        await self._publish_state_change(
            execution.task,
            trace_id=trace_id,
            agent_id=execution.agent_id,
            execution_id=execution.id,
            previous=TaskStatus.RUNNING,
        )
        await self._session.commit()
        return execution

    async def _publish_state_change(
        self,
        task: Task,
        *,
        trace_id: str,
        agent_id: object,
        execution_id: object,
        previous: TaskStatus,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.TASK_STATE_CHANGED,
                trace_id=trace_id,
                aggregate_id=task.id,
                actor="orchestrator",
                resource=f"task:{task.id}",
                task_id=task.id,
                agent_id=agent_id,
                payload={
                    "previous_status": previous.value,
                    "status": task.status,
                    "execution_id": str(execution_id),
                    "timeout_seconds": self._config.dispatcher.task_timeout_seconds,
                },
                result={"status": task.status},
            )
        )
