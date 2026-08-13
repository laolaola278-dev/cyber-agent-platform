"""Durable DAG workflow runtime with retries, timeouts, cancellation, and resume."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import WorkflowStatus, WorkflowStepStatus
from app.core.state_machine import WorkflowStateMachine, WorkflowStepStateMachine
from app.events import EventPublisher, EventType, PlatformEvent
from app.models import WorkflowExecution, WorkflowInstance, WorkflowStep
from app.repositories import WorkflowInstanceRepository
from app.schemas.workflow import WorkflowDocument, WorkflowNodeDefinition
from app.workflow.nodes import AgentNodeExecutor, NodeContext, NodeRegistry, NodeResult

TERMINAL_STEP_STATES = {
    WorkflowStepStatus.SUCCESS.value,
    WorkflowStepStatus.FAILED.value,
    WorkflowStepStatus.CANCELLED.value,
    WorkflowStepStatus.SKIPPED.value,
}


class WorkflowRuntime:
    """Execute compiled DAG nodes and persist a checkpoint after every transition."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WorkflowInstanceRepository,
        publisher: EventPublisher,
        agent_executor: AgentNodeExecutor,
        nodes: NodeRegistry | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._publisher = publisher
        self._agent_executor = agent_executor
        self._nodes = nodes or NodeRegistry.with_platform_defaults()

    async def execute(self, instance: WorkflowInstance) -> WorkflowInstance:
        document = WorkflowDocument.model_validate(instance.definition.definition)
        if instance.status in {
            WorkflowStatus.SUCCESS.value,
            WorkflowStatus.CANCELLED.value,
        }:
            return instance
        persisted_steps = await self._repository.list_steps(instance.id)
        if instance.status == WorkflowStatus.FAILED.value:
            WorkflowStateMachine.transition(instance, WorkflowStatus.RUNNING)
            for step in persisted_steps:
                if step.status == WorkflowStepStatus.FAILED.value:
                    step.status = WorkflowStepStatus.PENDING.value
                    step.attempt = 0
                    step.error = None
        elif instance.status == WorkflowStatus.WAITING.value:
            WorkflowStateMachine.transition(instance, WorkflowStatus.RUNNING)
            for step in persisted_steps:
                if step.status == WorkflowStepStatus.WAITING.value:
                    step.status = WorkflowStepStatus.PENDING.value
                    step.attempt = 0
        elif instance.status == WorkflowStatus.PENDING.value:
            WorkflowStateMachine.transition(instance, WorkflowStatus.RUNNING)
        instance.started_at = instance.started_at or datetime.now(UTC)
        instance.error = None
        await self._checkpoint(instance, EventType.WORKFLOW_STARTED)

        while instance.status == WorkflowStatus.RUNNING.value:
            await self._session.refresh(instance)
            if instance.cancel_requested:
                await self._cancel(instance)
                break
            persisted_steps = await self._repository.list_steps(instance.id)
            steps = {step.node_id: step for step in persisted_steps}
            node = self._next_node(document, steps)
            if node is None:
                if all(step.status in TERMINAL_STEP_STATES for step in steps.values()):
                    WorkflowStateMachine.transition(instance, WorkflowStatus.SUCCESS)
                    instance.completed_at = datetime.now(UTC)
                    instance.current_node = None
                    await self._checkpoint(instance, EventType.WORKFLOW_STATE_CHANGED)
                break
            instance.current_node = node.id
            step = steps.get(node.id)
            if step is None:
                step = await self._repository.add_step(
                    WorkflowStep(
                        instance_id=instance.id,
                        node_id=node.id,
                        node_type=node.type.value,
                        capability=node.capability,
                        max_attempts=node.retry.max_attempts,
                        timeout_seconds=node.timeout_seconds,
                        input=node.input,
                    )
                )
                steps[node.id] = step
            if self._should_skip(document, node.id, steps, instance.context):
                WorkflowStepStateMachine.transition(step, WorkflowStepStatus.SKIPPED)
                step.completed_at = datetime.now(UTC)
                await self._checkpoint(instance, EventType.WORKFLOW_STEP_FINISHED, step)
                continue
            result = await self._execute_with_retry(instance, step, node)
            if result.status == WorkflowStepStatus.WAITING:
                WorkflowStateMachine.transition(instance, WorkflowStatus.WAITING)
                await self._checkpoint(instance, EventType.WORKFLOW_STATE_CHANGED, step)
                break
            if result.status == WorkflowStepStatus.FAILED:
                WorkflowStateMachine.transition(instance, WorkflowStatus.FAILED)
                instance.error = step.error
                instance.completed_at = datetime.now(UTC)
                await self._checkpoint(instance, EventType.WORKFLOW_STATE_CHANGED, step)
                break
        return instance

    async def cancel(self, instance: WorkflowInstance) -> WorkflowInstance:
        instance.cancel_requested = True
        if instance.status in {
            WorkflowStatus.PENDING.value,
            WorkflowStatus.WAITING.value,
            WorkflowStatus.FAILED.value,
        }:
            await self._cancel(instance)
        else:
            await self._session.commit()
        return instance

    async def _execute_with_retry(
        self,
        instance: WorkflowInstance,
        step: WorkflowStep,
        node: WorkflowNodeDefinition,
    ) -> NodeResult:
        while step.attempt < step.max_attempts:
            step.attempt += 1
            step.status = WorkflowStepStatus.RUNNING.value
            step.started_at = datetime.now(UTC)
            step.error = None
            execution = await self._repository.add_execution(
                WorkflowExecution(
                    instance_id=instance.id,
                    step_id=step.id,
                    attempt=step.attempt,
                    status=WorkflowStepStatus.RUNNING.value,
                    started_at=step.started_at,
                )
            )
            await self._checkpoint(instance, EventType.WORKFLOW_STEP_STARTED, step)
            started = monotonic()
            try:
                result = await asyncio.wait_for(
                    self._nodes.resolve(node.type).execute(
                        node,
                        NodeContext(
                            trace_id=instance.trace_id,
                            workflow_input=instance.input,
                            state=instance.context,
                            agent_executor=self._agent_executor,
                            asset_id=instance.asset_id,
                        ),
                    ),
                    timeout=node.timeout_seconds,
                )
                if result.status == WorkflowStepStatus.FAILED:
                    raise RuntimeError(str(result.output.get("error", "Node execution failed")))
                step.output = result.output
                step.status = result.status.value
                step.completed_at = datetime.now(UTC)
                instance.context = {
                    **instance.context,
                    node.id: result.output,
                    "conditions": {
                        **dict(instance.context.get("conditions", {})),
                        **({node.id: result.condition} if result.condition is not None else {}),
                    },
                }
                execution.status = result.status.value
                execution.output = result.output
                execution.completed_at = step.completed_at
                execution.duration_ms = int((monotonic() - started) * 1000)
                await self._checkpoint(instance, EventType.WORKFLOW_STEP_FINISHED, step)
                return result
            except TimeoutError:
                message = f"Node {node.id} timed out after {node.timeout_seconds} seconds"
            except (LookupError, RuntimeError, ValueError, OSError) as error:
                message = str(error)
            execution.status = WorkflowStepStatus.FAILED.value
            execution.error = message
            execution.completed_at = datetime.now(UTC)
            execution.duration_ms = int((monotonic() - started) * 1000)
            step.error = message
            step.status = WorkflowStepStatus.FAILED.value
            await self._checkpoint(instance, EventType.WORKFLOW_STEP_FINISHED, step)
            if step.attempt < step.max_attempts:
                if node.retry.delay_seconds:
                    await asyncio.sleep(node.retry.delay_seconds)
                step.status = WorkflowStepStatus.RUNNING.value
                continue
            step.completed_at = datetime.now(UTC)
            return NodeResult(WorkflowStepStatus.FAILED, {"error": message})
        return NodeResult(WorkflowStepStatus.FAILED, {"error": step.error or "Retry exhausted"})

    def _next_node(
        self, document: WorkflowDocument, steps: dict[str, WorkflowStep]
    ) -> WorkflowNodeDefinition | None:
        for node in document.nodes:
            step = steps.get(node.id)
            if step and step.status in TERMINAL_STEP_STATES:
                continue
            incoming = [edge for edge in document.edges if edge.target == node.id]
            if not incoming:
                return node
            source_steps = [steps.get(edge.source) for edge in incoming]
            if all(source and source.status in TERMINAL_STEP_STATES for source in source_steps):
                return node
        return None

    def _should_skip(
        self,
        document: WorkflowDocument,
        node_id: str,
        steps: dict[str, WorkflowStep],
        context: dict[str, object],
    ) -> bool:
        incoming = [edge for edge in document.edges if edge.target == node_id]
        if not incoming:
            return False
        conditions = dict(context.get("conditions", {}))
        active = False
        for edge in incoming:
            source = steps.get(edge.source)
            if source is None or source.status != WorkflowStepStatus.SUCCESS.value:
                continue
            if edge.when is None or conditions.get(edge.source) is (edge.when == "true"):
                active = True
        return not active

    async def _cancel(self, instance: WorkflowInstance) -> None:
        WorkflowStateMachine.transition(instance, WorkflowStatus.CANCELLED)
        instance.completed_at = datetime.now(UTC)
        for step in await self._repository.list_steps(instance.id):
            if step.status in {
                WorkflowStepStatus.PENDING.value,
                WorkflowStepStatus.RUNNING.value,
                WorkflowStepStatus.WAITING.value,
            }:
                step.status = WorkflowStepStatus.CANCELLED.value
                step.completed_at = datetime.now(UTC)
        await self._checkpoint(instance, EventType.WORKFLOW_CANCELLED)

    async def _checkpoint(
        self,
        instance: WorkflowInstance,
        event_type: EventType,
        step: WorkflowStep | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=instance.trace_id,
                aggregate_id=instance.id,
                actor="workflow-runtime",
                resource=f"workflow-instance:{instance.id}",
                task_id=None,
                payload={
                    "status": instance.status,
                    "current_node": instance.current_node,
                    "step_id": str(step.id) if step else None,
                    "step_status": step.status if step else None,
                },
            )
        )
        await self._session.commit()
