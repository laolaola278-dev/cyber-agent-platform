"""Registry application services."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities import CapabilityRegistryService
from app.config import RegistryConfig
from app.core.enums import AgentStatus, HealthStatus, ToolStatus
from app.core.state_machine import AgentStateMachine
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import AgentNotFound, RegistryError, ToolNotFound
from app.models import Agent, AgentHeartbeat, AgentVersion, Tool, ToolVersion
from app.repositories import (
    AgentRepository,
    CapabilityRepository,
    PageResult,
    ToolRepository,
)
from app.schemas.registry import (
    AgentRegister,
    AgentUpdate,
    HeartbeatRequest,
    ToolRegister,
)


class AgentRegistryService:
    """Application layer for Agent registration, state, and heartbeats."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AgentRepository,
        publisher: EventPublisher,
        config: RegistryConfig,
        capability_service: CapabilityRegistryService | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._publisher = publisher
        self._config = config
        self._capabilities = capability_service or CapabilityRegistryService(
            session, CapabilityRepository(session)
        )

    async def register(self, payload: AgentRegister, *, trace_id: str) -> Agent:
        existing = await self._repository.get_by_name(payload.name)
        if existing is not None:
            if existing.version == payload.version:
                raise RegistryError(f"Agent {payload.name}:{payload.version} already exists")
            existing.version = payload.version
            self._apply_manifest(existing, payload)
            await self._repository.add_version(
                AgentVersion(
                    agent_id=existing.id,
                    version=payload.version,
                    manifest=payload.model_dump(),
                    created_at=datetime.now(UTC),
                )
            )
            await self._capabilities.sync_agent_capabilities(existing.id, payload.capabilities)
            await self._publisher.publish(
                self._event(
                    EventType.AGENT_UPDATED,
                    existing,
                    trace_id=trace_id,
                    actor=payload.author,
                    payload={"version": payload.version},
                )
            )
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        agent = Agent(
            name=payload.name,
            version=payload.version,
            status=self._config.registration.default_agent_status.value,
            health_status=self._config.heartbeat.default_health_status.value,
        )
        self._apply_manifest(agent, payload)
        await self._repository.add(agent)
        await self._repository.add_version(
            AgentVersion(
                agent_id=agent.id,
                version=payload.version,
                manifest=payload.model_dump(),
                created_at=datetime.now(UTC),
            )
        )
        await self._capabilities.sync_agent_capabilities(agent.id, payload.capabilities)
        await self._publisher.publish(
            self._event(
                EventType.AGENT_REGISTERED,
                agent,
                trace_id=trace_id,
                actor=payload.author,
                payload={"name": agent.name, "version": payload.version},
                result={"status": agent.status},
            )
        )
        await self._session.commit()
        return agent

    async def list(self, *, page: int, page_size: int) -> PageResult[Agent]:
        return await self._repository.list_page(page=page, page_size=page_size)

    async def get(self, agent_id: UUID) -> Agent:
        agent = await self._repository.get(agent_id)
        if agent is None:
            raise AgentNotFound(f"Agent {agent_id} not found")
        return agent

    async def list_versions(
        self, agent_id: UUID, *, page: int, page_size: int
    ) -> PageResult[AgentVersion]:
        await self.get(agent_id)
        return await self._repository.list_versions(agent_id, page=page, page_size=page_size)

    async def update(
        self,
        agent_id: UUID,
        payload: AgentUpdate,
        *,
        trace_id: str,
        actor: str = "api-user",
    ) -> Agent:
        agent = await self.get(agent_id)
        changes = payload.model_dump(exclude_unset=True)
        requested_status = changes.pop("status", None)
        for field, value in changes.items():
            setattr(agent, field, value)
        if changes.get("capabilities") is not None:
            await self._capabilities.sync_agent_capabilities(agent.id, changes["capabilities"])
        if requested_status is not None:
            AgentStateMachine.transition(agent, requested_status)
        await self._publisher.publish(
            self._event(
                EventType.AGENT_UPDATED,
                agent,
                trace_id=trace_id,
                actor=actor,
                payload={"changes": list(payload.model_fields_set)},
                result={"status": agent.status},
            )
        )
        await self._session.commit()
        await self._session.refresh(agent)
        return agent

    async def delete(self, agent_id: UUID, *, trace_id: str, actor: str = "api-user") -> None:
        agent = await self.get(agent_id)
        await self._repository.delete(agent)
        await self._publisher.publish(
            self._event(
                EventType.AGENT_DELETED,
                agent,
                trace_id=trace_id,
                actor=actor,
                payload={"name": agent.name},
            )
        )
        await self._session.commit()

    async def heartbeat(self, payload: HeartbeatRequest, *, trace_id: str) -> Agent:
        agent = await self.get(payload.agent_id)
        timestamp = datetime.now(UTC)
        previous_status = agent.status
        agent.health_status = payload.health_status.value
        agent.heartbeat_time = timestamp
        if payload.health_status == HealthStatus.HEALTHY:
            if agent.status == AgentStatus.OFFLINE:
                AgentStateMachine.transition(agent, AgentStatus.STARTING)
            if agent.status == AgentStatus.STARTING:
                AgentStateMachine.transition(agent, AgentStatus.ONLINE)
        elif agent.status in {
            AgentStatus.STARTING,
            AgentStatus.ONLINE,
            AgentStatus.STOPPING,
        }:
            AgentStateMachine.transition(agent, AgentStatus.ERROR)
        await self._repository.add_heartbeat(
            AgentHeartbeat(
                agent_id=agent.id,
                health_status=payload.health_status.value,
                details=payload.details,
                timestamp=timestamp,
            )
        )
        await self._publisher.publish(
            self._event(
                EventType.AGENT_HEARTBEAT,
                agent,
                trace_id=trace_id,
                actor="agent-runtime",
                payload={
                    "health_status": payload.health_status.value,
                    **payload.details,
                },
                result={"previous_status": previous_status, "status": agent.status},
            )
        )
        await self._session.commit()
        await self._session.refresh(agent)
        return agent

    @staticmethod
    def _apply_manifest(agent: Agent, payload: AgentRegister) -> None:
        agent.description = payload.description
        agent.author = payload.author
        agent.permissions = payload.permissions
        agent.capabilities = payload.capabilities
        agent.tools = payload.tools
        agent.minimum_runtime_version = payload.minimum_runtime_version
        agent.platform_version = payload.platform_version
        agent.sdk_version = payload.sdk_version
        agent.runtime = payload.runtime
        agent.network_policy = payload.network_policy
        agent.resource_limit = payload.resource_limit
        agent.approval_policy = payload.approval_policy

    @staticmethod
    def _event(
        event_type: EventType,
        agent: Agent,
        *,
        trace_id: str,
        actor: str,
        payload: dict[str, object],
        result: dict[str, object] | None = None,
    ) -> PlatformEvent:
        return PlatformEvent(
            type=event_type,
            trace_id=trace_id,
            aggregate_id=agent.id,
            actor=actor,
            resource=f"agent:{agent.id}",
            agent_id=agent.id,
            payload=payload,
            result=result,
        )


class ToolRegistryService:
    """Application layer for versioned Tool Adapter definitions."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ToolRepository,
        publisher: EventPublisher,
        config: RegistryConfig,
    ) -> None:
        self._session = session
        self._repository = repository
        self._publisher = publisher
        self._config = config

    async def register(
        self, payload: ToolRegister, *, trace_id: str, actor: str = "api-user"
    ) -> Tool:
        existing = await self._repository.get_by_name(payload.name)
        if existing is not None:
            if existing.version == payload.version:
                raise RegistryError(f"Tool {payload.name}:{payload.version} already exists")
            existing.version = payload.version
            self._apply_manifest(existing, payload)
            await self._repository.add_version(
                ToolVersion(
                    tool_id=existing.id,
                    version=payload.version,
                    manifest=payload.model_dump(),
                    created_at=datetime.now(UTC),
                )
            )
            tool = existing
        else:
            tool = Tool(
                name=payload.name,
                version=payload.version,
                tool_type=payload.tool_type,
                status=self._config.registration.default_tool_status.value,
            )
            self._apply_manifest(tool, payload)
            await self._repository.add(tool)
            await self._repository.add_version(
                ToolVersion(
                    tool_id=tool.id,
                    version=payload.version,
                    manifest=payload.model_dump(),
                    created_at=datetime.now(UTC),
                )
            )
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.TOOL_REGISTERED,
                trace_id=trace_id,
                aggregate_id=tool.id,
                actor=actor,
                resource=f"tool:{tool.id}",
                tool_id=tool.id,
                payload={"name": tool.name, "version": payload.version},
                result={"status": tool.status},
            )
        )
        await self._session.commit()
        await self._session.refresh(tool)
        return tool

    async def list(self, *, page: int, page_size: int) -> PageResult[Tool]:
        return await self._repository.list_page(page=page, page_size=page_size)

    async def get(self, tool_id: UUID) -> Tool:
        tool = await self._repository.get(tool_id)
        if tool is None:
            raise ToolNotFound(f"Tool {tool_id} not found")
        return tool

    async def list_versions(
        self, tool_id: UUID, *, page: int, page_size: int
    ) -> PageResult[ToolVersion]:
        await self.get(tool_id)
        return await self._repository.list_versions(tool_id, page=page, page_size=page_size)

    async def disable(self, tool_id: UUID, *, trace_id: str, actor: str = "api-user") -> Tool:
        tool = await self.get(tool_id)
        tool.status = ToolStatus.DISABLED.value
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.TOOL_DISABLED,
                trace_id=trace_id,
                aggregate_id=tool.id,
                actor=actor,
                resource=f"tool:{tool.id}",
                tool_id=tool.id,
                result={"status": tool.status},
            )
        )
        await self._session.commit()
        await self._session.refresh(tool)
        return tool

    @staticmethod
    def _apply_manifest(tool: Tool, payload: ToolRegister) -> None:
        tool.tool_type = payload.tool_type
        tool.description = payload.description
        tool.required_permissions = payload.required_permissions
        tool.config_schema = payload.config_schema
        tool.runtime_requirements = payload.runtime_requirements
