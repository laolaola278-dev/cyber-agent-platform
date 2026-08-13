"""Phase 2.1 Runtime decoupling and Capability Registry tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.capabilities import CapabilityRegistryService
from app.config import ConfigurationProvider
from app.events import EventType, InMemoryEventBus
from app.exceptions import CapabilityNotFound
from app.models import Agent, Task, Tool, ToolVersion
from app.orchestrator import FirstAvailableStrategy, TaskDispatcher
from app.report.templates import ReportTemplateRegistry
from app.repositories import (
    AgentRepository,
    CapabilityRepository,
    TaskRepository,
    ToolRepository,
)
from app.runtime.services import ServiceProvider
from app.sdk.tool_adapter import BaseToolAdapter
from app.tool_manager import (
    ToolFactory,
    ToolManager,
    ToolManifest,
    ToolManifestLoader,
)
from app.tools.playwright.browser import BrowserManager
from tests.conftest import TestSessionFactory

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


async def _capture(events: list[object], event: object) -> None:
    events.append(event)


class FakeAdapter(BaseToolAdapter):
    """In-memory Tool Adapter for lifecycle verification."""

    def __init__(self) -> None:
        self.initialized_with: dict[str, Any] | None = None
        self.stopped = False

    async def initialize(self, config: dict[str, Any]) -> None:
        self.initialized_with = config

    async def validate(self, payload: dict[str, Any]) -> None:
        return None

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    async def shutdown(self) -> None:
        self.stopped = True


def _configuration() -> ConfigurationProvider:
    configuration = ConfigurationProvider(CONFIG_DIR)
    configuration.load()
    return configuration


def test_service_provider_is_typed_and_rejects_missing_services() -> None:
    provider = ServiceProvider()
    service = ReportTemplateRegistry.with_platform_defaults()
    provider.register(ReportTemplateRegistry, service)
    assert provider.resolve(ReportTemplateRegistry) is service
    with pytest.raises(LookupError, match="is not registered"):
        provider.resolve(ToolManager)
    with pytest.raises(TypeError, match="must implement"):
        provider.register(ReportTemplateRegistry, object())  # type: ignore[arg-type]


def test_tool_manifest_loader_reads_trusted_yaml() -> None:
    path = Path(__file__).resolve().parents[2] / "tools" / "playwright" / "manifest.yaml"
    manifest = ToolManifestLoader().load(path)
    assert manifest.name == "playwright"
    assert manifest.as_registration().runtime_requirements["adapter"] == "playwright"


def test_tool_manifest_loader_rejects_invalid_files(tmp_path: Path) -> None:
    wrong_name = tmp_path / "tool.yaml"
    wrong_name.write_text("name: fake", encoding="utf-8")
    with pytest.raises(ValueError, match="named manifest.yaml"):
        ToolManifestLoader().load(wrong_name)

    invalid = tmp_path / "manifest.yaml"
    invalid.write_text("- not-a-mapping", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        ToolManifestLoader().load(invalid)


def test_tool_factory_rejects_unknown_adapter() -> None:
    with pytest.raises(LookupError, match="Unsupported Tool Adapter"):
        ToolFactory().create(
            ToolManifest(
                name="unknown",
                version="1",
                adapter="missing",
                capabilities=[],
                config={},
            )
        )


async def test_tool_manager_loads_caches_and_unloads_registry_adapter() -> None:
    async with TestSessionFactory() as session:
        tool = Tool(
            name="fake",
            version="1",
            tool_type="fake",
            status="ENABLED",
        )
        session.add(tool)
        await session.flush()
        session.add(
            ToolVersion(
                tool_id=tool.id,
                version="1",
                manifest={
                    "runtime_requirements": {
                        "adapter": "fake",
                        "capabilities": ["test.execute"],
                        "config": {"isolated": True},
                    }
                },
            )
        )
        await session.flush()
        adapter = FakeAdapter()
        factory = ToolFactory()
        factory.register("fake", lambda manifest: adapter)
        events = []
        bus = InMemoryEventBus()
        bus.subscribe(EventType.TOOL_LOADED, lambda event: _capture(events, event))
        bus.subscribe(EventType.TOOL_UNLOADED, lambda event: _capture(events, event))
        manager = ToolManager(ToolRepository(session), factory, bus)

        loaded = await manager.load("fake")
        assert loaded is adapter
        assert await manager.load("fake") is adapter
        assert manager.get("fake") is adapter
        assert manager.is_loaded("fake") is True
        assert await manager.is_registered("fake") is True
        assert adapter.initialized_with == {"isolated": True}
        await manager.shutdown_all()
        assert adapter.stopped is True
        assert manager.is_loaded("fake") is False
        assert [event.type for event in events] == [
            EventType.TOOL_LOADED,
            EventType.TOOL_UNLOADED,
        ]
        with pytest.raises(LookupError, match="is not loaded"):
            manager.get("fake")
        with pytest.raises(LookupError, match="is not registered"):
            await manager.load("missing")


async def test_tool_manager_rejects_missing_active_version() -> None:
    async with TestSessionFactory() as session:
        session.add(
            Tool(
                name="manifestless",
                version="1",
                tool_type="fake",
                status="ENABLED",
            )
        )
        await session.flush()
        manager = ToolManager(ToolRepository(session), ToolFactory())
        with pytest.raises(LookupError, match="Active manifest"):
            await manager.load("manifestless")


async def test_browser_manager_requires_start_and_is_idempotent() -> None:
    manager = BrowserManager()
    with pytest.raises(RuntimeError, match="not started"):
        await manager.new_context()

    class FakeContext:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class FakeBrowser:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class FakePlaywright:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    browser = FakeBrowser()
    playwright = FakePlaywright()
    context = FakeContext()
    manager._browser = browser
    manager._playwright = playwright
    manager._contexts.add(context)
    await manager.start()
    await manager.stop()
    assert context.closed is True
    assert browser.closed is True
    assert playwright.stopped is True


async def test_capability_registry_syncs_and_resolves_all_required_names() -> None:
    async with TestSessionFactory() as session:
        first = Agent(name="first-capability-agent", version="1")
        second = Agent(name="second-capability-agent", version="1")
        session.add_all([first, second])
        await session.flush()
        service = CapabilityRegistryService(session, CapabilityRepository(session))
        await service.sync_agent_capabilities(first.id, ["crawl.html", "browser.render"])
        await service.sync_agent_capabilities(second.id, ["crawl.html"])

        resolved = await service.resolve_agents({"crawl.html", "browser.render"})
        assert [agent.id for agent in resolved] == [first.id]
        assert (await service.list(page=1, page_size=10)).total == 2
        assert (await service.get("crawl.html")).enabled is True
        with pytest.raises(CapabilityNotFound, match="not found"):
            await service.get("missing")


async def test_dispatcher_filters_by_capability_before_permissions() -> None:
    async with TestSessionFactory() as session:
        capable = Agent(
            name="capable-agent",
            version="1",
            status="ONLINE",
            permissions=["task:execute"],
            heartbeat_time=datetime.now(UTC),
        )
        permission_only = Agent(
            name="permission-only-agent",
            version="1",
            status="ONLINE",
            permissions=["task:execute"],
            heartbeat_time=datetime.now(UTC),
        )
        task = Task(
            name="capability task",
            task_type="test",
            required_permissions=["task:execute"],
            required_capabilities=["browser.render"],
            status="CREATED",
        )
        session.add_all([capable, permission_only, task])
        await session.flush()
        capability_repository = CapabilityRepository(session)
        capability_service = CapabilityRegistryService(session, capability_repository)
        await capability_service.sync_agent_capabilities(capable.id, ["browser.render"])
        configuration = _configuration()
        dispatcher = TaskDispatcher(
            session,
            TaskRepository(session),
            AgentRepository(session),
            InMemoryEventBus(),
            FirstAvailableStrategy(),
            configuration.orchestrator,
            configuration.registry,
            capability_repository=capability_repository,
        )

        execution = await dispatcher.dispatch(task, trace_id="capability-dispatch")
        assert execution.agent_id == capable.id


async def test_capability_api_lists_registered_capabilities(client: AsyncClient) -> None:
    created = await client.post(
        "/registry/agents",
        json={
            "name": "api-capability-agent",
            "version": "1",
            "capabilities": ["evidence.generate"],
        },
    )
    assert created.status_code == 201
    listed = await client.get("/capabilities")
    fetched = await client.get("/capabilities/evidence.generate")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "evidence.generate"
    assert fetched.status_code == 200


@pytest.mark.parametrize("name", ["json", "markdown", "html"])
def test_default_report_templates_render(name: str) -> None:
    payload = {
        "task": {"id": "task-id", "name": "<capture>", "type": "test"},
        "agent_id": "agent-id",
        "trace_id": "trace-id",
        "status": "FAILED",
        "evidence": [
            {
                "http_status": 200,
                "title": None,
                "url": "https://example.com/?a=1&b=2",
            }
        ],
        "statistics": {"evidence_count": 1},
        "error": "failed <safely>",
    }
    rendered = ReportTemplateRegistry.with_platform_defaults().render(name, payload)
    assert rendered
    if name == "html":
        assert "&lt;capture&gt;" in rendered
        assert "failed &lt;safely&gt;" in rendered


def test_report_template_registry_rejects_unknown_name() -> None:
    with pytest.raises(LookupError, match="not registered"):
        ReportTemplateRegistry().render("missing", {})
