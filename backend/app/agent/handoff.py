"""Multi-Agent handoff contract (v2.0 / Phase 25).

Only synthetic/fake handoffs are implemented in this phase. Every handoff is
explicitly recorded with source/target/reason/context/allowed capabilities
and never carries secrets.
"""

from __future__ import annotations

from app.agent.contracts import HandoffContract
from app.agent.exceptions import AgentError

KNOWN_AGENTS: frozenset[str] = frozenset(
    {
        "investigation",
        "assessment",
        "detection",
        "response-advisor",
        "knowledge",
    }
)


class HandoffManager:
    """Validates and records explicit agent handoffs."""

    def propose(
        self,
        *,
        source_agent: str,
        target_agent: str,
        reason: str,
        context_refs: list[str],
        allowed_capabilities: list[str],
        registry: set[str],
    ) -> HandoffContract:
        if source_agent not in KNOWN_AGENTS or target_agent not in KNOWN_AGENTS:
            raise AgentError(f"Unknown agent in handoff: {source_agent} -> {target_agent}")
        if target_agent == source_agent:
            raise AgentError("Handoff target cannot equal the source agent")
        unknown = [cap for cap in allowed_capabilities if cap not in registry]
        if unknown:
            raise AgentError(f"Handoff references unknown capabilities: {unknown}")
        return HandoffContract(
            source_agent=source_agent,
            target_agent=target_agent,
            reason=reason,
            context_refs=context_refs,
            allowed_capabilities=sorted(allowed_capabilities),
            status="PROPOSED",
        )

    def finalize(self, contract: HandoffContract, *, decision: str) -> HandoffContract:
        if decision not in {"ACCEPTED", "DECLINED"}:
            raise AgentError(f"Invalid handoff decision: {decision}")
        return HandoffContract(
            handoff_id=contract.handoff_id,
            source_agent=contract.source_agent,
            target_agent=contract.target_agent,
            reason=contract.reason,
            context_refs=contract.context_refs,
            allowed_capabilities=contract.allowed_capabilities,
            status=decision,
            created_at=contract.created_at,
        )
