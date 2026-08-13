"""Abstract Agent SDK interface."""

from abc import ABC, abstractmethod

from app.sdk.contracts import AgentContext, AgentResult, HealthCheck, TaskRequest


class BaseAgent(ABC):
    """Required base class for all dynamically loaded Agent implementations."""

    @abstractmethod
    async def initialize(self, context: AgentContext) -> None:
        """Initialize resources without executing a task."""

    @abstractmethod
    async def execute(self, request: TaskRequest, context: AgentContext) -> AgentResult:
        """Execute one normalized task request."""

    @abstractmethod
    async def health_check(self) -> HealthCheck:
        """Return current runtime health."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release all runtime resources."""
