"""Agent loop (v2.0 / Phase 25).

Plan -> Validate -> Execute Capability -> Observe -> Evaluate -> Replan ->
Finish, bounded by hard budgets (max_steps, max_duration, token_budget,
capability_budget, retry_limit) so a loop can never run away.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.agent.contracts import (
    AgentDecision,
    AgentLoopBudget,
    AgentObservation,
    AgentProfile,
    InvestigationPlan,
)
from app.agent.exceptions import AgentLoopLimit
from app.agent.executor import ReadOnlyCapabilityExecutor
from app.agent.guardrails import CapabilityGuardrail
from app.agent.observability import AgentObservability


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    """Outcome of running an agent loop over a plan."""

    status: str  # SUCCEEDED | SUFFICIENT_EVIDENCE | LIMIT_REACHED | FAILED
    reason: str
    observations: tuple[AgentObservation, ...] = ()
    approval_requests: tuple[str, ...] = ()
    guardrail_rejections: int = 0


class AgentLoop:
    """Executes a validated plan under hard budgets, step by step."""

    def __init__(
        self,
        executor: ReadOnlyCapabilityExecutor,
        budget: AgentLoopBudget | None = None,
    ) -> None:
        self._executor = executor
        self._budget = budget or AgentLoopBudget()
        self._capability_guardrail = CapabilityGuardrail()

    async def run(
        self,
        *,
        plan: InvestigationPlan,
        profile: AgentProfile,
        registry: set[str],
        memory: Any,  # InvestigationSessionMemory
        observability: AgentObservability,
        run_id: str,
    ) -> AgentLoopResult:
        started = monotonic()
        steps_run = 0
        capability_calls = 0
        successful_calls = 0
        retries_used = 0
        rejections = 0
        approval_requests: list[str] = []

        for step in plan.steps:
            if steps_run >= self._budget.max_steps:
                return AgentLoopResult(
                    status="LIMIT_REACHED",
                    reason=f"Reached max_steps={self._budget.max_steps}",
                )
            if monotonic() - started > self._budget.max_duration_seconds:
                return AgentLoopResult(status="LIMIT_REACHED", reason="Reached max_duration")
            if capability_calls >= self._budget.capability_budget:
                return AgentLoopResult(status="LIMIT_REACHED", reason="Reached capability_budget")

            steps_run += 1
            if step.required_approval:
                # Human-in-the-loop: convert to an approval request, never
                # execute. Checked before the capability guardrail because an
                # approval-flagged step is a *proposal*, not an execution.
                approval_requests.append(step.capability)
                memory.add_decision(
                    AgentDecision(
                        decision_type="APPROVAL_REQUESTED",
                        rationale="High-risk capability converted to approval request",
                        capability=step.capability,
                    )
                )
                continue

            decision = self._capability_guardrail.check(
                step.capability,
                registry=registry,
                profile=profile,
                risk_level=step.risk,
            )
            observability.record_guardrail(run_id, decision)
            if not decision.allowed:
                rejections += 1
                memory.add_decision(
                    AgentDecision(
                        decision_type="CAPABILITY_REJECTED",
                        rationale=decision.reason,
                        capability=step.capability,
                    )
                )
                continue

            observation = await self._execute_with_retry(
                step.capability,
                step.parameters,
                profile=profile,
                run_id=run_id,
                observability=observability,
                allowed_capabilities=set(profile.capabilities),
            )
            capability_calls += 1
            if observation.evidence_refs or observation.confidence >= 0.5:
                successful_calls += 1
            memory.add_observation(observation)

            if self._evidence_sufficient(memory):
                return AgentLoopResult(
                    status="SUFFICIENT_EVIDENCE",
                    reason="Collected enough evidence to conclude",
                    observations=tuple(memory.observations),
                    approval_requests=tuple(approval_requests),
                    guardrail_rejections=rejections,
                )

        status = "SUCCEEDED" if successful_calls else "FAILED"
        if retries_used > self._budget.retry_limit:
            status = "LIMIT_REACHED"
        return AgentLoopResult(
            status=status,
            reason="Finished all plan steps",
            observations=tuple(memory.observations),
            approval_requests=tuple(approval_requests),
            guardrail_rejections=rejections,
        )

    async def _execute_with_retry(
        self,
        capability: str,
        parameters: dict[str, Any],
        *,
        profile: AgentProfile,
        run_id: str,
        observability: AgentObservability,
        allowed_capabilities: set[str],
    ) -> AgentObservation:
        last_error: str | None = None
        for _attempt in range(self._budget.retry_limit + 1):
            started = monotonic()
            result = await self._executor.execute(
                capability,
                parameters,
                allowed_capabilities=allowed_capabilities,
            )
            latency = int((monotonic() - started) * 1000)
            if result.error is None:
                observability.record_capability_call(
                    run_id,
                    capability=capability,
                    status="SUCCEEDED",
                    latency_ms=latency,
                )
                return result.to_observation()
            last_error = result.error
            observability.record_capability_call(
                run_id,
                capability=capability,
                status="FAILED",
                latency_ms=latency,
            )
        return AgentObservation(
            capability=capability,
            summary=f"Capability failed after retries: {last_error}",
            evidence_refs=[],
            confidence=0.1,
        )

    @staticmethod
    def _evidence_sufficient(memory: Any) -> bool:
        refs: set[str] = set()
        for observation in memory.observations:
            refs.update(observation.evidence_refs)
        return bool(refs) and len(memory.observations) >= 2


def enforce_loop_budget(budget: AgentLoopBudget, *, steps: int, tokens: int) -> None:
    """Explicit budget guard used by callers that bypass the loop runner."""
    if steps > budget.max_steps:
        raise AgentLoopLimit(f"max_steps exceeded: {steps} > {budget.max_steps}")
    if tokens > budget.token_budget:
        raise AgentLoopLimit(f"token_budget exceeded: {tokens} > {budget.token_budget}")
