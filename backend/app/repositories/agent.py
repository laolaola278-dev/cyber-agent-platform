"""Agent Registry persistence operations."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.models import Agent, AgentHeartbeat, AgentVersion
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class AgentRepository(SQLAlchemyRepository[Agent]):
    """Persistence operations for stable Agent identities."""

    model = Agent

    async def get_by_name(self, name: str) -> Agent | None:
        return await self.session.scalar(select(Agent).where(Agent.name == name))

    async def list_eligible(
        self,
        required_permissions: set[str],
        *,
        eligible_statuses: set[str],
        heartbeat_stale_after_seconds: int,
        target_agent_id: UUID | None = None,
        candidate_agent_ids: set[UUID] | None = None,
    ) -> Sequence[Agent]:
        heartbeat_cutoff = datetime.now(UTC) - timedelta(seconds=heartbeat_stale_after_seconds)
        statement = (
            select(Agent)
            .where(
                Agent.status.in_(eligible_statuses),
                Agent.heartbeat_time.is_not(None),
                Agent.heartbeat_time >= heartbeat_cutoff,
            )
            .order_by(Agent.created_at)
        )
        if target_agent_id is not None:
            statement = statement.where(Agent.id == target_agent_id)
        if candidate_agent_ids is not None:
            if not candidate_agent_ids:
                return []
            statement = statement.where(Agent.id.in_(candidate_agent_ids))
        candidates = (await self.session.scalars(statement)).all()
        return [
            agent for agent in candidates if required_permissions.issubset(set(agent.permissions))
        ]

    async def add_version(self, version: AgentVersion) -> AgentVersion:
        self.session.add(version)
        await self.session.flush()
        return version

    async def list_versions(
        self, agent_id: UUID, *, page: int, page_size: int
    ) -> PageResult[AgentVersion]:
        return await self._list_related_versions(
            AgentVersion,
            AgentVersion.agent_id == agent_id,
            page=page,
            page_size=page_size,
        )

    async def _list_related_versions[
        VersionT: AgentVersion
    ](self, model: type[VersionT], criterion: object, *, page: int, page_size: int) -> PageResult[
        VersionT
    ]:
        from sqlalchemy import func

        total = await self.session.scalar(select(func.count()).select_from(model).where(criterion))
        statement = (
            select(model)
            .where(criterion)
            .order_by(model.created_at.desc(), model.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.session.scalars(statement)).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def add_heartbeat(self, heartbeat: AgentHeartbeat) -> AgentHeartbeat:
        self.session.add(heartbeat)
        await self.session.flush()
        return heartbeat

    async def delete(self, agent: Agent) -> None:
        await self.session.delete(agent)
        await self.session.flush()
