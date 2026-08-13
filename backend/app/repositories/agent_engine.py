"""Repository for the Agentic engine persistence models."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentDecision,
    AgentHandoff,
    AgentObservation,
    AgentPlan,
    AgentRun,
    InvestigationSession,
    ModelInvocation,
)


class AgentEngineRepository:
    """Persistence access for agent runs, sessions and telemetry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_run(self, run: AgentRun) -> AgentRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: UUID) -> AgentRun | None:
        return await self._session.get(AgentRun, run_id)

    async def list_runs(self, *, limit: int = 50) -> Sequence[AgentRun]:
        statement = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
        return (await self._session.scalars(statement)).all()

    async def add_session(self, session_record: InvestigationSession) -> InvestigationSession:
        self._session.add(session_record)
        await self._session.flush()
        return session_record

    async def get_session(self, session_id: UUID) -> InvestigationSession | None:
        return await self._session.get(InvestigationSession, session_id)

    async def add_plan(self, plan: AgentPlan) -> AgentPlan:
        self._session.add(plan)
        await self._session.flush()
        return plan

    async def add_observation(self, observation: AgentObservation) -> AgentObservation:
        self._session.add(observation)
        await self._session.flush()
        return observation

    async def add_decision(self, decision: AgentDecision) -> AgentDecision:
        self._session.add(decision)
        await self._session.flush()
        return decision

    async def add_handoff(self, handoff: AgentHandoff) -> AgentHandoff:
        self._session.add(handoff)
        await self._session.flush()
        return handoff

    async def add_invocation(self, invocation: ModelInvocation) -> ModelInvocation:
        self._session.add(invocation)
        await self._session.flush()
        return invocation

    async def observations_for_run(self, run_id: UUID) -> Sequence[AgentObservation]:
        statement = select(AgentObservation).where(AgentObservation.run_id == run_id)
        return (await self._session.scalars(statement)).all()

    async def plans_for_run(self, run_id: UUID) -> Sequence[AgentPlan]:
        statement = select(AgentPlan).where(AgentPlan.run_id == run_id)
        return (await self._session.scalars(statement)).all()

    async def decisions_for_run(self, run_id: UUID) -> Sequence[AgentDecision]:
        statement = select(AgentDecision).where(AgentDecision.run_id == run_id)
        return (await self._session.scalars(statement)).all()

    async def handoffs_for_run(self, run_id: UUID) -> Sequence[AgentHandoff]:
        statement = select(AgentHandoff).where(AgentHandoff.run_id == run_id)
        return (await self._session.scalars(statement)).all()
