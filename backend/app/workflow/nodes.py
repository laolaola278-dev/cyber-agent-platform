"""Extensible workflow node handler contracts and platform defaults."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.core.enums import WorkflowNodeType, WorkflowStepStatus
from app.schemas.workflow import WorkflowNodeDefinition


@dataclass(frozen=True, slots=True)
class NodeResult:
    """Normalized node output consumed by the workflow runtime."""

    status: WorkflowStepStatus
    output: dict[str, Any]
    condition: bool | None = None


class AgentNodeExecutor(Protocol):
    async def execute_capability(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        asset_id: UUID | None = None,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class NodeContext:
    trace_id: str
    workflow_input: dict[str, Any]
    state: dict[str, Any]
    agent_executor: AgentNodeExecutor
    asset_id: UUID | None = None


class NodeHandler(Protocol):
    node_type: WorkflowNodeType

    async def execute(
        self, definition: WorkflowNodeDefinition, context: NodeContext
    ) -> NodeResult: ...


class StartNodeHandler:
    node_type = WorkflowNodeType.START

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        return NodeResult(WorkflowStepStatus.SUCCESS, {"started": True})


class EndNodeHandler:
    node_type = WorkflowNodeType.END

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        return NodeResult(WorkflowStepStatus.SUCCESS, {"completed": True})


class ApprovalNodeHandler:
    """Phase 3 placeholder: persist a wait checkpoint without approval business logic."""

    node_type = WorkflowNodeType.APPROVAL

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        return NodeResult(
            WorkflowStepStatus.WAITING,
            {"reason": "Approval provider is not implemented in Phase 3"},
        )


class ConditionNodeHandler:
    """Evaluate a deliberately small and auditable equality condition language."""

    node_type = WorkflowNodeType.CONDITION

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        expression = definition.condition or ""
        if "==" not in expression:
            raise ValueError("Condition must use path == literal syntax")
        path, expected = (part.strip() for part in expression.split("==", maxsplit=1))
        value: object = context.state
        for segment in path.split("."):
            if not isinstance(value, dict) or segment not in value:
                value = None
                break
            value = value[segment]
        expected_value: object
        if expected.casefold() in {"true", "false"}:
            expected_value = expected.casefold() == "true"
        else:
            expected_value = expected.strip("\"'")
        matched = value == expected_value
        return NodeResult(
            WorkflowStepStatus.SUCCESS,
            {"condition": matched, "actual": value},
            condition=matched,
        )


class AgentNodeHandler:
    node_type = WorkflowNodeType.AGENT

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        if definition.capability is None:
            raise ValueError("AgentNode capability is required")
        payload = {**context.workflow_input, **definition.input}
        if context.asset_id is None:
            result = await context.agent_executor.execute_capability(
                definition.capability,
                payload,
                trace_id=context.trace_id,
            )
        else:
            result = await context.agent_executor.execute_capability(
                definition.capability,
                payload,
                trace_id=context.trace_id,
                asset_id=context.asset_id,
            )
        success = bool(result.get("success"))
        return NodeResult(
            WorkflowStepStatus.SUCCESS if success else WorkflowStepStatus.FAILED,
            result,
        )


class NodeRegistry:
    """Plugin registry mapping stable node types to injected handlers."""

    def __init__(self) -> None:
        self._handlers: dict[WorkflowNodeType, NodeHandler] = {}

    def register(self, handler: NodeHandler) -> None:
        self._handlers[handler.node_type] = handler

    def resolve(self, node_type: WorkflowNodeType) -> NodeHandler:
        try:
            return self._handlers[node_type]
        except KeyError as error:
            raise LookupError(f"No workflow handler registered for {node_type}") from error

    @classmethod
    def with_platform_defaults(cls) -> "NodeRegistry":
        registry = cls()
        for handler in (
            StartNodeHandler(),
            AgentNodeHandler(),
            ConditionNodeHandler(),
            ApprovalNodeHandler(),
            EndNodeHandler(),
        ):
            registry.register(handler)
        return registry
