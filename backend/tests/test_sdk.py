"""Agent SDK and Tool Adapter contract tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.sdk import (
    AgentContext,
    AgentResult,
    BaseAgent,
    BaseToolAdapter,
    HealthCheck,
    TaskRequest,
)


class ExampleAgent(BaseAgent):
    async def initialize(self, context: AgentContext) -> None:
        self.context = context

    async def execute(self, request: TaskRequest, context: AgentContext) -> AgentResult:
        now = datetime.now(UTC)
        return AgentResult(success=True, started_at=now, finished_at=now)

    async def health_check(self) -> HealthCheck:
        return HealthCheck(healthy=True, status="HEALTHY")

    async def shutdown(self) -> None:
        return None


class ExampleAdapter(BaseToolAdapter):
    async def initialize(self, config: dict[str, object]) -> None:
        self.config = config

    async def validate(self, payload: dict[str, object]) -> None:
        if "value" not in payload:
            raise ValueError("value is required")

    async def execute(self, payload: dict[str, object]) -> dict[str, object]:
        return payload

    async def shutdown(self) -> None:
        return None


async def test_base_agent_contract() -> None:
    agent = ExampleAgent()
    context = AgentContext(trace_id="test", task_id=uuid4(), agent_id=uuid4(), actor="test")
    await agent.initialize(context)
    result = await agent.execute(TaskRequest(task_type="example"), context)
    assert result.success is True
    assert (await agent.health_check()).healthy is True


async def test_tool_adapter_contract() -> None:
    adapter = ExampleAdapter()
    await adapter.initialize({})
    await adapter.validate({"value": "ok"})
    assert await adapter.execute({"value": "ok"}) == {"value": "ok"}
    with pytest.raises(ValueError):
        await adapter.validate({})
