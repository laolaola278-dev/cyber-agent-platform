"""First CAP runtime validation Agent: public web data acquisition only."""

from datetime import UTC, datetime
from typing import cast

from app.evidence.service import EvidenceService
from app.runtime.context import RuntimeContext
from app.sdk.base_agent import BaseAgent
from app.sdk.contracts import AgentContext, AgentResult, HealthCheck, TaskRequest
from app.sdk.tool_adapter import BaseToolAdapter
from app.tool_manager import ToolManager


class DataAcquisitionAgent(BaseAgent):
    """Capture a single public web page through the injected tool capability."""

    def __init__(self) -> None:
        self._tool_adapter: BaseToolAdapter | None = None

    async def initialize(self, context: AgentContext) -> None:
        runtime = self._runtime_context(context)
        self._tool_adapter = await runtime.services.resolve(ToolManager).load(
            "playwright", trace_id=runtime.trace_id
        )

    async def execute(self, request: TaskRequest, context: AgentContext) -> AgentResult:
        started_at = datetime.now(UTC)
        runtime = self._runtime_context(context)
        url = request.input.get("url")
        if not isinstance(url, str):
            return AgentResult(
                success=False,
                error="data-acquisition requires a url string",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        try:
            runtime.logger.info("Tool Invoke", extra={"trace_id": runtime.trace_id})
            if self._tool_adapter is None:
                raise RuntimeError("Playwright tool adapter was not initialized")
            capture = await self._tool_adapter.execute({"url": url, "method": "GET"})
            evidence_service = runtime.services.resolve(EvidenceService)
            evidence = await evidence_service.save_capture(
                task_id=runtime.task.id,
                agent_id=runtime.agent_id,
                trace_id=runtime.trace_id,
                url=capture["url"],
                http_status=capture["http_status"],
                title=capture["title"],
                html=capture["html"],
                screenshot=capture["screenshot"],
                asset_id=runtime.task.asset_id,
            )
            return AgentResult(
                success=True,
                output={"evidence_id": str(evidence.id), "url": evidence.url},
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except (RuntimeError, ValueError, OSError) as error:
            return AgentResult(
                success=False,
                error=str(error),
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )

    async def health_check(self) -> HealthCheck:
        return HealthCheck(healthy=True, status="HEALTHY", details={"capability": "public-web-get"})

    async def shutdown(self) -> None:
        self._tool_adapter = None

    @staticmethod
    def _runtime_context(context: AgentContext) -> RuntimeContext:
        candidate = context.metadata.get("runtime_context")
        if not isinstance(candidate, RuntimeContext):
            raise TypeError("RuntimeContext was not injected")
        return cast(RuntimeContext, candidate)
