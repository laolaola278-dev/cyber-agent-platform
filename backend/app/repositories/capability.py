"""Capability Registry persistence operations."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select

from app.models import Agent, AgentCapability, Capability
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class CapabilityRepository(SQLAlchemyRepository[Capability]):
    """Persist capabilities and resolve Agents providing all requested names."""

    model = Capability

    async def get_by_name(self, name: str) -> Capability | None:
        return await self.session.scalar(select(Capability).where(Capability.name == name))

    async def list_enabled(self, *, page: int, page_size: int) -> PageResult[Capability]:
        criterion = Capability.enabled.is_(True)
        total = await self.session.scalar(
            select(func.count()).select_from(Capability).where(criterion)
        )
        statement = (
            select(Capability)
            .where(criterion)
            .order_by(Capability.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.session.scalars(statement)).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def replace_agent_capabilities(
        self, agent_id: UUID, capabilities: Sequence[Capability]
    ) -> None:
        await self.session.execute(
            delete(AgentCapability).where(AgentCapability.agent_id == agent_id)
        )
        self.session.add_all(
            AgentCapability(agent_id=agent_id, capability_id=item.id) for item in capabilities
        )
        await self.session.flush()

    async def list_agent_ids_for_capabilities(self, names: set[str]) -> set[UUID]:
        if not names:
            return set()
        statement = (
            select(AgentCapability.agent_id)
            .join(Capability, Capability.id == AgentCapability.capability_id)
            .where(Capability.name.in_(names), Capability.enabled.is_(True))
            .group_by(AgentCapability.agent_id)
            .having(func.count(func.distinct(Capability.name)) == len(names))
        )
        return set((await self.session.scalars(statement)).all())

    async def list_agents_for_capabilities(self, names: set[str]) -> Sequence[Agent]:
        ids = await self.list_agent_ids_for_capabilities(names)
        if not ids:
            return []
        return (await self.session.scalars(select(Agent).where(Agent.id.in_(ids)))).all()
