"""Application service exposing managed Agent Runtime operations."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ConfigurationProvider
from app.models import Agent, AgentRuntime, Task
from app.runtime.manager import RuntimeManager
from app.runtime.manifest import AgentManifest, ManifestLoader
from app.services.registry import AgentRegistryService, ToolRegistryService
from app.tool_manager import ToolManager, ToolManifestLoader


class RuntimeService:
    """Resolve trusted manifest paths and coordinate RuntimeManager lifecycle calls."""

    def __init__(
        self,
        session: AsyncSession,
        configuration: ConfigurationProvider,
        manager: RuntimeManager,
        registry: AgentRegistryService,
        tool_registry: ToolRegistryService,
        tool_manager: ToolManager,
    ) -> None:
        self._session = session
        self._configuration = configuration
        self._manager = manager
        self._registry = registry
        self._tool_registry = tool_registry
        self._tool_manager = tool_manager
        self._loader = ManifestLoader()
        self._tool_loader = ToolManifestLoader()

    async def ensure_data_acquisition_agent(self, *, trace_id: str) -> Agent:
        """Register the only Phase 2 Agent manifest when no identity exists yet."""

        path = self._manifest_path("data-acquisition")
        manifest = self._loader.load(path)
        await self._ensure_manifest_tools(manifest, trace_id=trace_id)
        agent = await self._session.scalar(select(Agent).where(Agent.name == manifest.name))
        if agent is None:
            agent = await self._loader.register(manifest, self._registry, trace_id=trace_id)
        elif agent.version != manifest.version:
            agent = await self._loader.register(manifest, self._registry, trace_id=trace_id)
        return agent

    async def start(self, agent_id: UUID, task: Task, *, trace_id: str) -> AgentRuntime:
        agent = await self._agent(agent_id)
        runtime = await self._get_or_load(agent, trace_id=trace_id)
        await self._manager.start(runtime, agent, task, trace_id=trace_id)
        await self._session.commit()
        return runtime

    async def stop(self, runtime_id: UUID, *, trace_id: str) -> AgentRuntime:
        runtime = await self.get(runtime_id)
        agent = await self._agent(runtime.agent_id)
        await self._manager.stop(runtime, agent, trace_id=trace_id)
        await self._session.commit()
        return runtime

    async def restart(self, runtime_id: UUID, task: Task, *, trace_id: str) -> AgentRuntime:
        runtime = await self.get(runtime_id)
        agent = await self._agent(runtime.agent_id)
        await self._manager.reload(
            runtime, agent, self._manifest_path_for_agent(agent), trace_id=trace_id
        )
        await self._manager.start(runtime, agent, task, trace_id=trace_id)
        await self._session.commit()
        return runtime

    async def health(self, runtime_id: UUID, *, trace_id: str) -> dict[str, object]:
        runtime = await self.get(runtime_id)
        agent = await self._agent(runtime.agent_id)
        if not self._manager.is_loaded(runtime.agent_id):
            await self._manager.reload(
                runtime, agent, self._manifest_path_for_agent(agent), trace_id=trace_id
            )
        result = await self._manager.health(runtime, agent, trace_id=trace_id)
        await self._session.commit()
        return result

    async def get(self, runtime_id: UUID) -> AgentRuntime:
        runtime = await self._session.get(AgentRuntime, runtime_id)
        if runtime is None:
            raise LookupError(f"Runtime {runtime_id} not found")
        return runtime

    async def execute(self, agent: Agent, task: Task, *, trace_id: str) -> dict[str, object]:
        """Invoke exactly one Agent through RuntimeManager for Dispatcher use."""

        runtime = await self._get_or_load(agent, trace_id=trace_id)
        return await self._manager.execute(runtime, agent, task, trace_id=trace_id)

    async def _get_or_load(self, agent: Agent, *, trace_id: str) -> AgentRuntime:
        runtime = await self._session.scalar(
            select(AgentRuntime).where(AgentRuntime.agent_id == agent.id)
        )
        path = self._manifest_path_for_agent(agent)
        if runtime is None:
            return await self._manager.load(agent, path, trace_id=trace_id)
        if not self._manager.is_loaded(agent.id):
            await self._manager.reload(runtime, agent, path, trace_id=trace_id)
        return runtime

    async def _agent(self, agent_id: UUID) -> Agent:
        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            raise LookupError(f"Agent {agent_id} not found")
        return agent

    async def _ensure_manifest_tools(self, manifest: AgentManifest, *, trace_id: str) -> None:
        """Bootstrap trusted platform Tool definitions declared by an Agent manifest."""

        for name in manifest.tools:
            if await self._tool_manager.is_registered(name):
                continue
            tool_manifest = self._tool_loader.load(self._tool_manifest_path(name))
            if tool_manifest.name != name:
                raise ValueError("Tool manifest identity must match the Agent declaration")
            await self._tool_registry.register(
                tool_manifest.as_registration(),
                trace_id=trace_id,
                actor="runtime-bootstrap",
            )

    def _manifest_path_for_agent(self, agent: Agent) -> Path:
        manifest_root = (
            self._configuration.config_directory
            / self._configuration.runtime.runtime.manifest_directory
        ).resolve()
        for candidate in manifest_root.glob("*/manifest.yaml"):
            manifest = self._loader.load(candidate)
            if manifest.name == agent.name:
                return candidate.resolve()
        raise LookupError(f"No trusted manifest path configured for Agent {agent.name}")

    def _manifest_path(self, agent_directory: str) -> Path:
        configured = self._configuration.runtime.runtime.manifest_directory
        return (
            self._configuration.config_directory / configured / agent_directory / "manifest.yaml"
        ).resolve()

    def _tool_manifest_path(self, name: str) -> Path:
        return (
            self._configuration.config_directory.parents[1] / "tools" / name / "manifest.yaml"
        ).resolve()
