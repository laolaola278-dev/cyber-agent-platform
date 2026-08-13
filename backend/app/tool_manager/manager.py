"""Tool lifecycle manager between Runtime and Tool Registry definitions."""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.events import EventPublisher, EventType, PlatformEvent
from app.models import ToolVersion
from app.repositories import ToolRepository
from app.sdk.tool_adapter import BaseToolAdapter
from app.tool_manager.factory import ToolFactory
from app.tool_manager.manifest import ToolManifest


class ToolManager:
    """Resolve Registry manifests, cache adapters, and own Tool lifecycles."""

    def __init__(
        self,
        repository: ToolRepository,
        factory: ToolFactory,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._repository = repository
        self._factory = factory
        self._publisher = publisher
        self._instances: dict[str, BaseToolAdapter] = {}

    async def load(self, name: str, *, trace_id: str | None = None) -> BaseToolAdapter:
        existing = self._instances.get(name)
        if existing is not None:
            return existing
        tool = await self._repository.get_by_name(name)
        if tool is None or tool.status != "ENABLED":
            raise LookupError(f"Enabled Tool {name} is not registered")
        version = await self._repository.session.scalar(
            select(ToolVersion).where(
                ToolVersion.tool_id == tool.id,
                ToolVersion.version == tool.version,
                ToolVersion.is_active.is_(True),
            )
        )
        if version is None:
            raise LookupError(f"Active manifest for Tool {name}:{tool.version} not found")
        raw = dict(version.manifest)
        requirements = dict(raw.get("runtime_requirements", {}))
        manifest = ToolManifest(
            name=tool.name,
            version=tool.version,
            adapter=str(requirements.get("adapter", tool.tool_type)),
            capabilities=list(requirements.get("capabilities", [])),
            config=dict(requirements.get("config", {})),
        )
        adapter = self._factory.create(manifest)
        await adapter.initialize(manifest.config)
        self._instances[name] = adapter
        await self._publish(
            EventType.TOOL_LOADED,
            name,
            tool.id,
            {"version": tool.version, "adapter": manifest.adapter},
            trace_id=trace_id,
        )
        return adapter

    def get(self, name: str) -> BaseToolAdapter:
        try:
            return self._instances[name]
        except KeyError as error:
            raise LookupError(f"Tool {name} is not loaded") from error

    async def unload(self, name: str, *, trace_id: str | None = None) -> None:
        adapter = self._instances.pop(name, None)
        if adapter is not None:
            await adapter.shutdown()
            tool = await self._repository.get_by_name(name)
            await self._publish(
                EventType.TOOL_UNLOADED,
                name,
                tool.id if tool is not None else None,
                {},
                trace_id=trace_id,
            )

    async def shutdown_all(self) -> None:
        for name in list(self._instances):
            await self.unload(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._instances

    async def is_registered(self, name: str) -> bool:
        """Return whether a Tool definition exists in the Registry."""

        return await self._repository.get_by_name(name) is not None

    async def _publish(
        self,
        event_type: EventType,
        name: str,
        tool_id: UUID | None,
        payload: dict[str, object],
        *,
        trace_id: str | None = None,
    ) -> None:
        if self._publisher is None:
            return
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=trace_id or str(uuid4()),
                aggregate_id=tool_id,
                actor="tool-manager",
                resource=f"tool:{name}",
                tool_id=tool_id,
                payload=payload,
            )
        )
