"""Agentic engine application service (v2.0 / Phase 25).

Orchestrates investigations, persists runs/sessions/telemetry through the
platform repository layer, and exposes the evaluation harness. The LLM
provider is injected; nothing here ever grants the model direct access to
shell, secrets, network, plugins or workers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import InvestigationAgent
from app.agent.contracts import InvestigationSessionMemory
from app.agent.evaluation import AgentEvaluationHarness, build_scenarios
from app.agent.executor import ReadOnlyCapabilityExecutor
from app.agent.llm import FakeLLMProvider
from app.agent.observability import AgentObservability
from app.agent.planner import AgenticPlanner
from app.exceptions import AgentError
from app.models import (
    AgentDecision,
    AgentHandoff,
    AgentObservation,
    AgentPlan,
    AgentRun,
    Capability,
    InvestigationSession,
    ModelInvocation,
)
from app.repositories.agent_engine import AgentEngineRepository


class AgentEngineService:
    """Application service for the Agentic engine."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AgentEngineRepository | None = None,
        *,
        provider: Any | None = None,
        observability: AgentObservability | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or AgentEngineRepository(session)
        self._provider = provider or FakeLLMProvider()
        self._observability = observability or AgentObservability()

    # -- registry ----------------------------------------------------------

    async def registry_names(self) -> set[str]:
        """Names of enabled capabilities in the platform registry."""
        statement = select(Capability.name).where(Capability.enabled.is_(True))
        names = set((await self._session.scalars(statement)).all())
        names.update(
            {
                "knowledge.read",
                "asset.read",
                "finding.read",
                "security_event.read",
                "incident.read",
                "evidence.read",
            }
        )
        return names

    # -- investigations -----------------------------------------------------

    async def create_investigation(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        data_blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        registry = await self.registry_names()
        agent = self._build_agent(registry)
        result = await agent.investigate(
            goal=goal,
            context=context,
            registry=registry,
            data_blocks=data_blocks or [],
        )
        session_record = await self._persist(agent, result.session, result.run.run_id, goal)
        return await self._read_investigation(session_record)

    async def continue_investigation(
        self,
        session_id: UUID | str,
        *,
        goal: str | None,
        context: dict[str, Any] | None,
        data_blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session_id = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        existing = await self._repository.get_session(session_id)
        if existing is None:
            raise AgentError(f"Investigation session not found: {session_id}")
        if existing.status not in {"ACTIVE", "COMPLETED"}:
            raise AgentError(f"Investigation session is not continuable: {existing.status}")
        registry = await self.registry_names()
        agent = self._build_agent(registry)
        result = await agent.investigate(
            goal=goal or existing.goal,
            context=context or {},
            registry=registry,
            data_blocks=data_blocks or [],
        )
        session_record = await self._persist(
            agent, result.session, result.run.run_id, goal or existing.goal
        )
        return await self._read_investigation(session_record)

    async def get_investigation(self, session_id: UUID | str) -> dict[str, Any]:
        session_id = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        session_record = await self._repository.get_session(session_id)
        if session_record is None:
            raise AgentError(f"Investigation session not found: {session_id}")
        return await self._read_investigation(session_record)

    async def get_run(self, run_id: UUID | str) -> dict[str, Any]:
        run_id = UUID(str(run_id)) if not isinstance(run_id, UUID) else run_id
        run = await self._repository.get_run(run_id)
        if run is None:
            raise AgentError(f"Agent run not found: {run_id}")
        return {
            "id": str(run.id),
            "trace_id": run.trace_id,
            "agent_name": run.agent_name,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "status": run.status,
            "goal": run.goal,
            "latency_ms": run.latency_ms,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.total_tokens,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "observations": [
                {
                    "capability": observation.capability,
                    "summary": observation.summary,
                    "evidence_refs": observation.evidence_refs,
                    "confidence": observation.confidence,
                }
                for observation in await self._repository.observations_for_run(run_id)
            ],
        }

    async def list_investigations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        statement = (
            select(InvestigationSession)
            .order_by(InvestigationSession.created_at.desc())
            .limit(limit)
        )
        sessions = (await self._session.scalars(statement)).all()
        return [await self._read_investigation(session) for session in sessions]

    async def run_evaluations(self) -> dict[str, Any]:
        harness = AgentEvaluationHarness()
        report = await harness.run(build_scenarios())
        return {
            "overall_score": report.overall_score,
            "metrics": [
                {
                    "name": metric.name,
                    "passed": metric.passed,
                    "total": metric.total,
                    "rate": metric.rate,
                }
                for metric in report.metrics
            ],
            "total_scenarios": len(report.results),
        }

    # -- internals ----------------------------------------------------------

    def _build_agent(self, registry: set[str]) -> InvestigationAgent:
        executor = ReadOnlyCapabilityExecutor(self._session, registry)
        planner = AgenticPlanner(self._provider)
        return InvestigationAgent(
            planner,
            executor,
            observability=self._observability,
        )

    async def _persist(
        self,
        agent: InvestigationAgent,
        memory: InvestigationSessionMemory,
        run_id: str,
        goal: str,
    ) -> InvestigationSession:
        run_record = self._observability.get(run_id)
        if run_record is None:
            raise AgentError("Agent run telemetry missing after investigation")
        run = await self._repository.add_run(
            AgentRun(
                trace_id=run_record.trace_id,
                agent_name=run_record.agent_name,
                model=run_record.model,
                prompt_version=run_record.prompt_version,
                status="SUCCEEDED" if memory.conclusion else "FAILED",
                goal=goal,
                latency_ms=run_record.latency_ms,
                prompt_tokens=run_record.token_usage.prompt_tokens,
                completion_tokens=run_record.token_usage.completion_tokens,
                total_tokens=run_record.token_usage.total_tokens,
                started_at=run_record.started_at,
                finished_at=run_record.finished_at or datetime.now(UTC),
            )
        )
        plan = memory.plan
        if plan is not None:
            await self._repository.add_plan(
                AgentPlan(
                    run_id=run.id,
                    goal=plan.goal,
                    reasoning_summary=plan.reasoning_summary,
                    steps=[step.model_dump() for step in plan.steps],
                    risk_level=plan.risk_level,
                    requires_approval=plan.requires_approval,
                    status="WAITING_APPROVAL" if plan.requires_approval else "EXECUTED",
                )
            )
        for observation in memory.observations:
            await self._repository.add_observation(
                AgentObservation(
                    run_id=run.id,
                    capability=observation.capability,
                    summary=observation.summary,
                    evidence_refs=observation.evidence_refs,
                    confidence=observation.confidence,
                    observed_at=observation.timestamp,
                )
            )
        for decision in memory.decisions:
            await self._repository.add_decision(
                AgentDecision(
                    run_id=run.id,
                    decision_type=decision.decision_type,
                    rationale=decision.rationale,
                    capability=decision.capability,
                )
            )
        for handoff in memory.handoffs:
            await self._repository.add_handoff(
                AgentHandoff(
                    run_id=run.id,
                    source_agent=handoff.source_agent,
                    target_agent=handoff.target_agent,
                    reason=handoff.reason,
                    context_refs=handoff.context_refs,
                    allowed_capabilities=handoff.allowed_capabilities,
                    status=handoff.status,
                )
            )
        verdict = "ALLOWED"
        guardrail_decisions = run_record.guardrail_decisions
        if any(not decision.allowed for decision in guardrail_decisions):
            verdict = "REJECTED"
        await self._repository.add_invocation(
            ModelInvocation(
                run_id=run.id,
                model=run_record.model,
                prompt_version=run_record.prompt_version,
                latency_ms=run_record.latency_ms,
                prompt_tokens=run_record.token_usage.prompt_tokens,
                completion_tokens=run_record.token_usage.completion_tokens,
                total_tokens=run_record.token_usage.total_tokens,
                guardrail_verdict=verdict,
            )
        )
        session_record = await self._repository.add_session(
            InvestigationSession(
                run_id=run.id,
                goal=goal,
                status="COMPLETED" if memory.conclusion else "ACTIVE",
                conclusion=memory.conclusion.model_dump(mode="json") if memory.conclusion else None,
                conclusion_confidence=memory.conclusion.confidence if memory.conclusion else None,
            )
        )
        await self._session.commit()
        return session_record

    async def _read_investigation(self, session_record: InvestigationSession) -> dict[str, Any]:
        session_id = session_record.id
        run_id = session_record.run_id
        plan = None
        observations: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
        if run_id is not None:
            plans = await self._repository.plans_for_run(run_id)
            if plans:
                first = plans[0]
                plan = {
                    "goal": first.goal,
                    "reasoning_summary": first.reasoning_summary,
                    "steps": first.steps,
                    "requires_approval": first.requires_approval,
                    "risk_level": first.risk_level,
                    "status": first.status,
                }
            observations = [
                {
                    "capability": observation.capability,
                    "summary": observation.summary,
                    "evidence_refs": observation.evidence_refs,
                    "confidence": observation.confidence,
                }
                for observation in await self._repository.observations_for_run(run_id)
            ]
            decisions = [
                {
                    "decision_type": decision.decision_type,
                    "rationale": decision.rationale,
                    "capability": decision.capability,
                }
                for decision in await self._repository.decisions_for_run(run_id)
            ]
            handoffs = [
                {
                    "source_agent": handoff.source_agent,
                    "target_agent": handoff.target_agent,
                    "reason": handoff.reason,
                    "status": handoff.status,
                    "allowed_capabilities": handoff.allowed_capabilities,
                }
                for handoff in await self._repository.handoffs_for_run(run_id)
            ]
        return {
            "id": str(session_id),
            "goal": session_record.goal,
            "status": session_record.status,
            "conclusion": session_record.conclusion,
            "conclusion_confidence": session_record.conclusion_confidence,
            "created_at": session_record.created_at.isoformat(),
            "updated_at": session_record.updated_at.isoformat(),
            "run_id": str(run_id) if run_id else None,
            "plan": plan,
            "observations": observations,
            "decisions": decisions,
            "handoffs": handoffs,
        }
