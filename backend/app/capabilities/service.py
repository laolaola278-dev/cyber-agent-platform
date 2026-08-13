"""Capability Registry application service."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import CapabilityNotFound
from app.models import Agent, Capability
from app.repositories.capability import CapabilityRepository
from app.repositories.pagination import PageResult


class CapabilityRegistryService:
    """Register capabilities and maintain Agent-to-capability declarations."""

    def __init__(self, session: AsyncSession, repository: CapabilityRepository) -> None:
        self._session = session
        self._repository = repository

    async def register(
        self,
        name: str,
        *,
        description: str | None = None,
        risk_level: str = "LOW",
    ) -> Capability:
        capability = await self._repository.get_by_name(name)
        if capability is None:
            capability = await self._repository.add(
                Capability(
                    name=name,
                    description=description,
                    risk_level=risk_level,
                    enabled=True,
                )
            )
        else:
            capability.description = description or capability.description
            capability.risk_level = risk_level
            capability.enabled = True
        await self._session.flush()
        return capability

    async def list(self, *, page: int, page_size: int) -> PageResult[Capability]:
        return await self._repository.list_enabled(page=page, page_size=page_size)

    async def get(self, name: str) -> Capability:
        capability = await self._repository.get_by_name(name)
        if capability is None or not capability.enabled:
            raise CapabilityNotFound(f"Capability {name} not found")
        return capability

    async def sync_agent_capabilities(self, agent_id: UUID, names: Sequence[str]) -> None:
        capabilities = [await self.register(name) for name in dict.fromkeys(names)]
        await self._repository.replace_agent_capabilities(agent_id, capabilities)

    async def resolve_agents(self, names: set[str]) -> Sequence[Agent]:
        return await self._repository.list_agents_for_capabilities(names)
