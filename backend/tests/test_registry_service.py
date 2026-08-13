"""Direct Registry service branch and event tests."""

import pytest

from app.config import ConfigurationProvider
from app.core.enums import AgentStatus
from app.events import EventType, InMemoryEventBus
from app.exceptions import AgentNotFound, RegistryError, ToolNotFound
from app.repositories import AgentRepository, ToolRepository
from app.schemas.registry import (
    AgentRegister,
    AgentUpdate,
    HeartbeatRequest,
    ToolRegister,
)
from app.services.registry import AgentRegistryService, ToolRegistryService
from tests.conftest import TestSessionFactory

CONFIG_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "config"


def _config():
    provider = ConfigurationProvider(CONFIG_DIR)
    provider.load()
    return provider


async def _capture(events: list[object], event: object) -> None:
    events.append(event)


async def test_agent_service_version_update_list_and_error_heartbeat() -> None:
    async with TestSessionFactory() as session:
        events = []
        bus = InMemoryEventBus()
        for event_type in (
            EventType.AGENT_REGISTERED,
            EventType.AGENT_UPDATED,
            EventType.AGENT_HEARTBEAT,
            EventType.AGENT_DELETED,
        ):
            bus.subscribe(event_type, lambda event: _capture(events, event))
        service = AgentRegistryService(session, AgentRepository(session), bus, _config().registry)
        first = await service.register(
            AgentRegister(name="service-agent", version="1", author="test"),
            trace_id="trace-agent",
        )
        updated = await service.register(
            AgentRegister(name="service-agent", version="2", author="test", description="v2"),
            trace_id="trace-agent",
        )
        assert updated.id == first.id
        assert (await service.list(page=1, page_size=10)).total == 1
        assert (await service.list_versions(first.id, page=1, page_size=10)).total == 2

        await service.update(
            first.id,
            AgentUpdate(status=AgentStatus.STARTING),
            trace_id="trace-agent",
        )
        unhealthy = await service.heartbeat(
            HeartbeatRequest(agent_id=first.id, health_status="UNHEALTHY"),
            trace_id="trace-agent",
        )
        assert unhealthy.status == AgentStatus.ERROR
        await service.update(
            first.id,
            AgentUpdate(status=AgentStatus.OFFLINE),
            trace_id="trace-agent",
        )
        await service.delete(first.id, trace_id="trace-agent")
        assert {event.type for event in events}.issuperset(
            {
                EventType.AGENT_REGISTERED,
                EventType.AGENT_UPDATED,
                EventType.AGENT_HEARTBEAT,
                EventType.AGENT_DELETED,
            }
        )
        with pytest.raises(AgentNotFound):
            await service.get(first.id)


async def test_agent_service_rejects_duplicate_version() -> None:
    async with TestSessionFactory() as session:
        service = AgentRegistryService(
            session, AgentRepository(session), InMemoryEventBus(), _config().registry
        )
        payload = AgentRegister(name="duplicate-service", version="1", author="test")
        await service.register(payload, trace_id="trace-duplicate")
        with pytest.raises(RegistryError):
            await service.register(payload, trace_id="trace-duplicate")


async def test_tool_service_version_history_disable_and_not_found() -> None:
    async with TestSessionFactory() as session:
        events = []
        bus = InMemoryEventBus()
        bus.subscribe(EventType.TOOL_REGISTERED, lambda event: _capture(events, event))
        bus.subscribe(EventType.TOOL_DISABLED, lambda event: _capture(events, event))
        service = ToolRegistryService(session, ToolRepository(session), bus, _config().registry)
        first = await service.register(
            ToolRegister(name="service-tool", version="1", tool_type="adapter"),
            trace_id="trace-tool",
        )
        await service.register(
            ToolRegister(name="service-tool", version="2", tool_type="adapter"),
            trace_id="trace-tool",
        )
        assert (await service.list(page=1, page_size=10)).total == 1
        assert (await service.list_versions(first.id, page=1, page_size=10)).total == 2
        assert (await service.disable(first.id, trace_id="trace-tool")).status == "DISABLED"
        assert [event.type for event in events].count(EventType.TOOL_REGISTERED) == 2
        with pytest.raises(ToolNotFound):
            await service.get(__import__("uuid").uuid4())
