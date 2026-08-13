from datetime import UTC, datetime
from app.sdk.base_agent import BaseAgent
from app.sdk.contracts import AgentResult, HealthCheck
class RuntimeTestAgent(BaseAgent):
    async def initialize(self, context): pass
    async def execute(self, request, context):
        timestamp = datetime.now(UTC)
        return AgentResult(
            success=True,
            output={"ok": True},
            started_at=timestamp,
            finished_at=timestamp,
        )
    async def health_check(self): return HealthCheck(healthy=True, status="HEALTHY")
    async def shutdown(self): pass
