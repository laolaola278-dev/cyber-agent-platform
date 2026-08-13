"""Guardrails for the Agentic engine (v2.0 / Phase 25).

Four guardrail layers, all fail-closed:

- InputGuardrail:     prompt injection, scope expansion, unauthorized targets
- PlanGuardrail:      unknown capability, high-risk action, tool command injection
- CapabilityGuardrail: capability existence / permission / risk before execution
- OutputGuardrail:    secret exposure, sensitive evidence leakage, hallucination

A guardrail that cannot positively verify safety returns ``allowed=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.contracts import AgentProfile, InvestigationPlan
from app.agent.injection import (
    analyze_dangerous_commands,
    analyze_injection,
    analyze_secret_exposure,
)

HIGH_RISK_LEVELS: frozenset[str] = frozenset({"HIGH", "CRITICAL"})
HIGH_RISK_CAPABILITY_PREFIXES: tuple[str, ...] = (
    "response.",
    "host.",
    "isolation.",
    "block.",
    "delete.",
    "quarantine.",
)
APPROVAL_REQUIRED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "response.waf",
        "response.firewall",
        "response.edr",
        "host.isolate",
        "response.block",
        "response.delete",
    }
)


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """One guardrail verdict. Allowed only when positively verified."""

    guardrail: str
    allowed: bool
    reason: str = ""
    severity: str = "LOW"
    details: tuple[str, ...] = field(default_factory=tuple)


class InputGuardrail:
    """Reject untrusted content that tries to become instructions."""

    name = "input"

    def check(
        self,
        *,
        content: str,
        source: str = "untrusted",
        authorized_targets: set[str] | None = None,
        target: str | None = None,
    ) -> GuardrailDecision:
        analysis = analyze_injection(content, source=source)
        if analysis.risk == "HIGH":
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Prompt injection detected in untrusted content",
                severity="HIGH",
                details=analysis.matched_patterns,
            )
        if (
            authorized_targets is not None
            and target is not None
            and target not in authorized_targets
        ):
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Target is outside the agent's authorized scope",
                severity="HIGH",
                details=(target,),
            )
        return GuardrailDecision(guardrail=self.name, allowed=True, severity="LOW")


class PlanGuardrail:
    """Validate a planner-emitted plan before any execution."""

    name = "plan"

    def check(
        self,
        plan: InvestigationPlan,
        *,
        registry: set[str],
        profile: AgentProfile,
    ) -> GuardrailDecision:
        allowed = set(profile.capabilities)
        for step in plan.steps:
            if step.capability not in registry:
                return GuardrailDecision(
                    guardrail=self.name,
                    allowed=False,
                    reason="Plan references an unknown capability",
                    severity="HIGH",
                    details=(step.capability,),
                )
            if step.capability not in allowed:
                return GuardrailDecision(
                    guardrail=self.name,
                    allowed=False,
                    reason="Capability not granted to this agent profile",
                    severity="HIGH",
                    details=(step.capability,),
                )
            commands = analyze_dangerous_commands(step.capability + " " + step.purpose)
            if commands:
                return GuardrailDecision(
                    guardrail=self.name,
                    allowed=False,
                    reason="Plan step embeds a dangerous command",
                    severity="HIGH",
                    details=commands,
                )
            if step.risk in HIGH_RISK_LEVELS or step.capability in APPROVAL_REQUIRED_CAPABILITIES:
                if not step.required_approval:
                    return GuardrailDecision(
                        guardrail=self.name,
                        allowed=False,
                        reason="High-risk step missing required approval flag",
                        severity="HIGH",
                        details=(step.capability,),
                    )
        if plan.risk_level in HIGH_RISK_LEVELS and not plan.requires_approval:
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="High-risk plan missing approval requirement",
                severity="HIGH",
            )
        return GuardrailDecision(guardrail=self.name, allowed=True, severity="LOW")


class CapabilityGuardrail:
    """Authorize a single capability call immediately before execution."""

    name = "capability"

    def check(
        self,
        capability: str,
        *,
        registry: set[str],
        profile: AgentProfile,
        risk_level: str = "LOW",
        only_read_only: bool = True,
    ) -> GuardrailDecision:
        if capability not in registry:
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Capability does not exist in the platform registry",
                severity="HIGH",
            )
        if capability not in set(profile.capabilities):
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Capability is not allowed for this agent profile",
                severity="HIGH",
            )
        if risk_level in HIGH_RISK_LEVELS or capability in APPROVAL_REQUIRED_CAPABILITIES:
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="High-risk capability must go through human approval",
                severity="HIGH",
                details=(capability,),
            )
        if only_read_only and not capability.endswith(".read"):
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Investigation agent may only execute read-only capabilities",
                severity="HIGH",
                details=(capability,),
            )
        return GuardrailDecision(guardrail=self.name, allowed=True, severity="LOW")


class OutputGuardrail:
    """Sanitize model output before it reaches any consumer."""

    name = "output"

    def check(
        self,
        content: str,
        *,
        evidence_refs: list[str],
        known_evidence: set[str],
    ) -> GuardrailDecision:
        secrets = analyze_secret_exposure(content)
        if secrets:
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Model output contains secret material",
                severity="HIGH",
                details=secrets,
            )
        hallucinations = [ref for ref in evidence_refs if ref not in known_evidence]
        if hallucinations:
            return GuardrailDecision(
                guardrail=self.name,
                allowed=False,
                reason="Output cites unknown evidence references",
                severity="MEDIUM",
                details=hallucinations,
            )
        return GuardrailDecision(guardrail=self.name, allowed=True, severity="LOW")


def run_all_guardrails(
    *,
    plan: InvestigationPlan | None = None,
    registry: set[str] | None = None,
    profile: AgentProfile | None = None,
    content: str = "",
    source: str = "untrusted",
    evidence_refs: list[str] | None = None,
    known_evidence: set[str] | None = None,
) -> list[GuardrailDecision]:
    """Run the applicable guardrail layers and return every decision."""
    decisions: list[GuardrailDecision] = []
    input_guardrail = InputGuardrail()
    decisions.append(input_guardrail.check(content=content, source=source))
    if plan is not None and registry is not None and profile is not None:
        decisions.append(PlanGuardrail().check(plan, registry=registry, profile=profile))
        capability_guardrail = CapabilityGuardrail()
        for step in plan.steps:
            decisions.append(
                capability_guardrail.check(
                    step.capability,
                    registry=registry,
                    profile=profile,
                    risk_level=step.risk,
                )
            )
    if evidence_refs is not None and known_evidence is not None:
        decisions.append(
            OutputGuardrail().check(
                content=content,
                evidence_refs=evidence_refs,
                known_evidence=known_evidence,
            )
        )
    return decisions
