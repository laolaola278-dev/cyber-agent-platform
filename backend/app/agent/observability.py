"""AI observability for the Agentic engine (v2.0 / Phase 25).

Records agent runs, models, prompt versions, token usage, latency, plans,
capability calls, guardrail decisions, handoffs and conclusions. Secrets are
never recorded. Trace IDs link to the platform OpenTelemetry context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.agent.contracts import (
    AgentObservation,
    HandoffContract,
    InvestigationConclusion,
    InvestigationPlan,
    TokenUsage,
)
from app.agent.guardrails import GuardrailDecision


@dataclass(slots=True)
class AgentRunRecord:
    """One agent run's telemetry record (persisted via the application service)."""

    run_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    agent_name: str = "investigation"
    model: str = "fake-llm"
    prompt_version: str = "phase25-v1"
    status: str = "RUNNING"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    latency_ms: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    plan: InvestigationPlan | None = None
    capability_calls: list[dict[str, Any]] = field(default_factory=list)
    guardrail_decisions: list[GuardrailDecision] = field(default_factory=list)
    handoffs: list[HandoffContract] = field(default_factory=list)
    observations: list[AgentObservation] = field(default_factory=list)
    conclusion: InvestigationConclusion | None = None

    def finish(self, *, status: str, latency_ms: int) -> None:
        self.status = status
        self.finished_at = datetime.now(UTC)
        self.latency_ms = latency_ms

    def redacted_snapshot(self) -> dict[str, Any]:
        """Public-safe snapshot: never contains secret material."""
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "agent": self.agent_name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "latency_ms": self.latency_ms,
            "token_usage": {
                "prompt_tokens": self.token_usage.prompt_tokens,
                "completion_tokens": self.token_usage.completion_tokens,
                "total_tokens": self.token_usage.total_tokens,
            },
            "steps_planned": len(self.plan.steps) if self.plan else 0,
            "capability_calls": len(self.capability_calls),
            "guardrail_decisions": len(self.guardrail_decisions),
            "handoffs": len(self.handoffs),
            "observations": len(self.observations),
            "conclusion": self.conclusion.summary if self.conclusion else None,
            "conclusion_confidence": self.conclusion.confidence if self.conclusion else None,
        }


class AgentObservability:
    """Collects run telemetry and bridges to OpenTelemetry spans."""

    def __init__(self) -> None:
        self._active: dict[str, AgentRunRecord] = {}
        self._completed: list[AgentRunRecord] = []

    def begin(
        self,
        *,
        agent_name: str = "investigation",
        model: str = "fake-llm",
        prompt_version: str = "phase25-v1",
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            agent_name=agent_name,
            model=model,
            prompt_version=prompt_version,
        )
        self._active[record.run_id] = record
        return record

    def record_plan(self, run_id: str, plan: InvestigationPlan) -> None:
        record = self._require(run_id)
        record.plan = plan

    def record_capability_call(
        self,
        run_id: str,
        *,
        capability: str,
        status: str,
        latency_ms: int,
    ) -> None:
        record = self._require(run_id)
        record.capability_calls.append(
            {
                "capability": capability,
                "status": status,
                "latency_ms": latency_ms,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    def record_guardrail(self, run_id: str, decision: GuardrailDecision) -> None:
        self._require(run_id).guardrail_decisions.append(decision)

    def record_observation(self, run_id: str, observation: AgentObservation) -> None:
        self._require(run_id).observations.append(observation)

    def record_handoff(self, run_id: str, handoff: HandoffContract) -> None:
        self._require(run_id).handoffs.append(handoff)

    def record_conclusion(self, run_id: str, conclusion: InvestigationConclusion) -> None:
        self._require(run_id).conclusion = conclusion

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        latency_ms: int,
        usage: TokenUsage | None = None,
    ) -> AgentRunRecord:
        record = self._require(run_id)
        record.finish(status=status, latency_ms=latency_ms)
        if usage is not None:
            record.token_usage = usage
        self._active.pop(run_id, None)
        self._completed.append(record)
        return record

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self._active.get(run_id) or next(
            (record for record in self._completed if record.run_id == run_id), None
        )

    def list_records(self) -> list[AgentRunRecord]:
        return self._completed

    def _require(self, run_id: str) -> AgentRunRecord:
        record = self.get(run_id)
        if record is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        return record
