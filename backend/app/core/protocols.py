"""Interface-first protocols for platform extension points."""

from typing import Protocol
from uuid import UUID

from app.sdk.contracts import AgentContext, AgentResult, HealthCheck, TaskRequest


class AgentRuntime(Protocol):
    """Runtime contract that every executable Agent implementation satisfies."""

    async def execute(self, request: TaskRequest, context: AgentContext) -> AgentResult: ...

    async def health_check(self) -> HealthCheck: ...


class WorkflowExecutor(Protocol):
    """Reserved workflow execution contract for a later platform phase."""

    async def dispatch(self, task_id: UUID) -> None: ...


class MemoryProvider(Protocol):
    """Reserved state and memory provider contract."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class ApprovalProvider(Protocol):
    """Reserved approval boundary; high-impact actions remain denied by default."""

    async def is_approved(self, task_id: UUID, action: str) -> bool: ...
