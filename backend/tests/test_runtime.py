"""Phase 2 Runtime, manifest, evidence, report, and adapter tests."""

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import InMemoryEventBus
from app.evidence.service import EvidenceService
from app.models import Agent, AgentRuntime, Evidence, Report, Task
from app.report.service import ReportService
from app.runtime.manager import RuntimeManager
from app.runtime.manifest import ManifestLoader
from app.runtime.services import ServiceProvider
from app.tools.playwright.adapter import PlaywrightAdapter
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Provide a direct transactional test session."""

    async with TestSessionFactory() as active_session:
        yield active_session


class FakeTool:
    """No-network adapter used to verify Runtime boundaries deterministically."""

    async def initialize(self, config: dict[str, object]) -> None:
        self.config = config

    async def execute(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            "url": payload["url"],
            "http_status": 200,
            "title": "Example",
            "html": "<html>example</html>",
            "screenshot": b"png",
        }

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_manifest_loader_validates_and_rejects_wrong_filename(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """name: data-acquisition-agent
version: 1.0.0
runtime:
  entrypoint: agent:DataAcquisitionAgent
""",
        encoding="utf-8",
    )
    loader = ManifestLoader()
    manifest = loader.load(manifest_path)
    assert manifest.name == "data-acquisition-agent"
    with pytest.raises(ValueError, match="named manifest.yaml"):
        loader.load(tmp_path / "other.yaml")


@pytest.mark.asyncio
async def test_playwright_adapter_enforces_public_get_policy() -> None:
    adapter = PlaywrightAdapter()
    await adapter.validate({"url": "https://example.com", "method": "GET"})
    with pytest.raises(ValueError, match="only HTTP GET"):
        await adapter.validate({"url": "https://example.com", "method": "POST"})
    with pytest.raises(ValueError, match="injection"):
        await adapter.validate({"url": "https://example.com", "cookies": []})


@pytest.mark.asyncio
async def test_evidence_and_report_are_persisted(session: AsyncSession, tmp_path: Path) -> None:
    bus = InMemoryEventBus()
    agent = Agent(name="runtime-agent", version="1.0.0", status="ONLINE")
    task = Task(name="capture", task_type="data-acquisition", status="RUNNING")
    session.add_all([agent, task])
    await session.flush()
    evidence_service = EvidenceService(session, bus, tmp_path)
    evidence = await evidence_service.save_capture(
        task_id=task.id,
        agent_id=agent.id,
        trace_id="trace-1",
        url="https://example.com",
        http_status=200,
        title="Example",
        html="<html>example</html>",
        screenshot=b"png",
    )
    report = await ReportService(session, bus).generate(
        task=task, agent_id=agent.id, trace_id="trace-1", status="SUCCESS"
    )
    await session.commit()
    assert evidence.screenshot_path is not None
    assert evidence.screenshot_path.endswith(".png")
    assert evidence.evidence_type == "HTML"
    assert evidence.sha256 == evidence.html_hash
    assert evidence.content_type == "text/html; charset=utf-8"
    assert report.json_content["statistics"]["evidence_count"] == 1
    assert "CAP Task Report" in report.markdown_content
    assert "<article>" in report.html_content
    assert await session.scalar(select(Evidence).where(Evidence.id == evidence.id)) is not None
    assert await session.scalar(select(Report).where(Report.id == report.id)) is not None


@pytest.mark.asyncio
async def test_runtime_load_start_execute_stop(session: AsyncSession, tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """name: runtime-agent
version: 1.0.0
runtime:
  entrypoint: runtime_test_agent:RuntimeTestAgent
""",
        encoding="utf-8",
    )
    agent_module = tmp_path / "runtime_test_agent.py"
    agent_module.write_text(
        """from datetime import UTC, datetime
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
""",
        encoding="utf-8",
    )
    bus = InMemoryEventBus()
    agent = Agent(name="runtime-agent", version="1.0.0", status="OFFLINE")
    task = Task(name="runtime", task_type="data-acquisition", status="RUNNING")
    session.add_all([agent, task])
    await session.flush()
    evidence_service = EvidenceService(session, bus, tmp_path / "evidence")
    report_service = ReportService(session, bus)
    services = ServiceProvider()
    services.register(EvidenceService, evidence_service)
    services.register(ReportService, report_service)
    manager = RuntimeManager(
        session,
        bus,
        services,
        report_service,
    )
    runtime = await manager.load(agent, manifest_path, trace_id="trace-2")
    assert runtime.status == "OFFLINE"
    await manager.start(runtime, agent, task, trace_id="trace-2")
    assert runtime.status == "ONLINE"
    result = await manager.execute(runtime, agent, task, trace_id="trace-2")
    assert result["success"] is True
    await manager.stop(runtime, agent, trace_id="trace-2")
    assert runtime.status == "OFFLINE"
    await manager.reload(runtime, agent, manifest_path, trace_id="trace-2")
    health = await manager.health(runtime, agent, trace_id="trace-2")
    assert health["status"] == "HEALTHY"
    await manager.destroy(runtime, agent, trace_id="trace-2")
    await session.flush()
    assert await session.get(AgentRuntime, runtime.id) is None
