"""Investigation Agent (v2.0 / Phase 25).

The first real platform Agent. It understands an investigation goal, reads
platform context, plans, executes low-risk read-only capabilities, checks
observations, decides whether evidence is sufficient, and produces an
``InvestigationConclusion``. High-risk actions are converted to approval
requests and are never auto-executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.contracts import (
    AgentDecision,
    AgentLoopBudget,
    AgentObservation,
    AgentProfile,
    Hypothesis,
    InvestigationConclusion,
    InvestigationSessionMemory,
    KnowledgeCandidate,
    RecommendedAction,
    TokenUsage,
)
from app.agent.exceptions import AgentGuardrailViolation
from app.agent.executor import ReadOnlyCapabilityExecutor
from app.agent.guardrails import InputGuardrail, OutputGuardrail
from app.agent.handoff import HandoffManager
from app.agent.loop import AgentLoop, AgentLoopResult
from app.agent.observability import AgentObservability, AgentRunRecord
from app.agent.planner import AgenticPlanner

INVESTIGATION_AGENT_PROFILE = AgentProfile(
    name="investigation",
    version="1.0.0",
    role="Security Investigation Analyst",
    description=(
        "Reads assets, findings, security events, incidents, evidence and "
        "knowledge to produce a governed investigation conclusion."
    ),
    capabilities=[
        "knowledge.read",
        "asset.read",
        "finding.read",
        "security_event.read",
        "incident.read",
        "evidence.read",
    ],
    required_context=["goal", "scope"],
    allowed_tools=[],
    allowed_domains=["asset", "finding", "security_event", "incident", "evidence", "knowledge"],
    risk_level="LOW",
    planning_permission=True,
    execution_permission=True,
    handoff_targets=["assessment", "detection", "response-advisor", "knowledge"],
)


@dataclass(slots=True)
class InvestigationResult:
    """Outcome of one investigation."""

    session: InvestigationSessionMemory
    conclusion: InvestigationConclusion
    run: AgentRunRecord
    loop_result: AgentLoopResult | None = None


class InvestigationAgent:
    """Governed investigation agent: plan -> validate -> execute -> conclude."""

    def __init__(
        self,
        planner: AgenticPlanner,
        executor: ReadOnlyCapabilityExecutor,
        *,
        observability: AgentObservability | None = None,
        handoff_manager: HandoffManager | None = None,
        budget: AgentLoopBudget | None = None,
        profile: AgentProfile = INVESTIGATION_AGENT_PROFILE,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._observability = observability or AgentObservability()
        self._handoffs = handoff_manager or HandoffManager()
        self._loop = AgentLoop(executor, budget=budget)
        self._budget = budget or AgentLoopBudget()
        self._profile = profile
        self._input_guardrail = InputGuardrail()
        self._output_guardrail = OutputGuardrail()

    async def investigate(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        registry: set[str],
        data_blocks: list[dict[str, Any]] | None = None,
        prompt_version: str = "phase25-v1",
    ) -> InvestigationResult:
        """Run a full investigation and return the structured conclusion."""
        memory = InvestigationSessionMemory()
        run = self._observability.begin(
            agent_name=self._profile.name,
            model="fake-llm",
            prompt_version=prompt_version,
        )
        try:
            decision = self._input_guardrail.check(
                content=goal + " " + str(context),
                source="user-goal",
                authorized_targets=None,
            )
            self._observability.record_guardrail(run.run_id, decision)
            if not decision.allowed:
                raise AgentGuardrailViolation(decision.reason)

            plan, isolation = await self._planner.create_plan(
                goal=goal,
                context=context,
                available_capabilities=set(self._profile.capabilities),
                registry=registry,
                profile=self._profile,
                data_blocks=data_blocks,
                prompt_version=prompt_version,
            )
            memory.set_plan(plan)
            self._observability.record_plan(run.run_id, plan)

            loop_result = await self._loop.run(
                plan=plan,
                profile=self._profile,
                registry=registry,
                memory=memory,
                observability=self._observability,
                run_id=run.run_id,
            )
            memory.add_decision(
                AgentDecision(
                    decision_type="LOOP_FINISHED",
                    rationale=loop_result.reason,
                )
            )

            conclusion = self._build_conclusion(plan.goal, memory)
            self._output_guardrail.check(
                content=conclusion.summary + " " + " ".join(conclusion.evidence_refs),
                evidence_refs=conclusion.evidence_refs,
                known_evidence=(
                    set(memory.observations[0].evidence_refs) if memory.observations else set()
                ),
            )
            memory.set_conclusion(conclusion)
            self._observability.record_conclusion(run.run_id, conclusion)
            self._observability.finish(
                run.run_id,
                status="SUCCEEDED",
                latency_ms=0,
                usage=TokenUsage(prompt_tokens=128, completion_tokens=64, total_tokens=192),
            )
            return InvestigationResult(
                session=memory,
                conclusion=conclusion,
                run=run,
                loop_result=loop_result,
            )
        except Exception:
            self._observability.finish(run.run_id, status="FAILED", latency_ms=0)
            raise

    def request_handoff(
        self,
        *,
        target_agent: str,
        reason: str,
        context_refs: list[str],
        allowed_capabilities: list[str],
        registry: set[str],
    ) -> Any:
        """Create a synthetic handoff contract (recorded, not executed)."""
        return self._handoffs.propose(
            source_agent=self._profile.name,
            target_agent=target_agent,
            reason=reason,
            context_refs=context_refs,
            allowed_capabilities=allowed_capabilities,
            registry=registry,
        )

    def _build_conclusion(
        self,
        goal: str,
        memory: InvestigationSessionMemory,
    ) -> InvestigationConclusion:
        observations = memory.observations
        evidence_refs: list[str] = []
        for observation in observations:
            for ref in observation.evidence_refs:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)

        summary = self._summarize(goal, observations)
        hypotheses = self._build_hypotheses(observations)
        actions = self._build_recommended_actions(observations)
        if memory.plan is not None and memory.plan.requires_approval:
            # Human-in-the-loop: a high-risk follow-up was proposed during
            # planning. Surface it as a recommendation that requires approval;
            # it is never executed by this agent.
            actions.append(
                RecommendedAction(
                    capability="response.waf",
                    action="Proposed containment follow-up (requires human approval)",
                    risk="HIGH",
                    requires_approval=True,
                )
            )
            memory.add_decision(
                AgentDecision(
                    decision_type="APPROVAL_REQUESTED",
                    rationale="High-risk follow-up converted to approval request",
                    capability="response.waf",
                )
            )
        confidence = min(1.0, 0.35 + 0.1 * len(evidence_refs))
        unresolved = (
            [
                f"Step produced no evidence: {observation.capability}"
                for observation in observations
                if not observation.evidence_refs
            ]
            if observations
            else ["No capability could be executed; conclusion is low confidence"]
        )
        return InvestigationConclusion(
            summary=summary,
            confidence=confidence,
            timeline=[
                {"at": obs.timestamp.isoformat(), "capability": obs.capability}
                for obs in observations
            ],
            observations=[obs.summary for obs in observations],
            evidence_refs=evidence_refs,
            hypotheses=hypotheses,
            recommended_actions=actions,
            unresolved_questions=unresolved,
        )

    @staticmethod
    def _summarize(goal: str, observations: list[AgentObservation]) -> str:
        if not observations:
            return f"No evidence collected for goal: {goal}"
        detail = "; ".join(obs.summary for obs in observations[:3])
        return f"Investigation of '{goal}': {detail}"

    @staticmethod
    def _build_hypotheses(observations: list[AgentObservation]) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for observation in observations:
            if observation.evidence_refs:
                hypotheses.append(
                    Hypothesis(
                        statement=f"{observation.capability} indicates relevant activity",
                        evidence_refs=observation.evidence_refs,
                        confidence=observation.confidence,
                    )
                )
        return hypotheses

    @staticmethod
    def _build_recommended_actions(
        observations: list[AgentObservation],
    ) -> list[RecommendedAction]:
        """Recommendations only. High-risk actions are flagged, never executed."""
        actions: list[RecommendedAction] = []
        for observation in observations:
            if observation.evidence_refs:
                actions.append(
                    RecommendedAction(
                        capability=observation.capability,
                        action=f"Review {observation.capability} evidence for follow-up",
                        risk="LOW",
                        requires_approval=False,
                    )
                )
        return actions

    def stage_knowledge(
        self, memory: InvestigationSessionMemory, *, title: str, content: str
    ) -> None:
        """Stage a KnowledgeCandidate for platform validation (never direct write)."""
        memory.stage_knowledge_candidate(
            KnowledgeCandidate(
                title=title,
                content=content,
                source_refs=[
                    obs.evidence_refs[0] for obs in memory.observations if obs.evidence_refs
                ],
            )
        )
