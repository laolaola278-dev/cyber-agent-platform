"""Provider-neutral LLM abstraction (v2.0 / Phase 25).

Only ``LLMProvider`` (interface) and ``FakeLLMProvider`` (deterministic test
implementation) are shipped in this phase. The provider boundary guarantees:

- the LLM never receives database credentials, secrets, network, shell or
  plugin access;
- the LLM is called with plain text and returns text or structured JSON;
- real providers (OpenAI / Azure OpenAI / Anthropic / Gemini / local /
  OpenAI-compatible endpoints) plug in behind the same interface later.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.contracts import (
    LLMProvider,
    ModelCapability,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

# Capability names that are considered read-only investigation primitives.
# The planner may only emit steps that exist in the platform registry; this
# ordered list is the fake provider's deterministic "planning vocabulary".
READ_ONLY_INVESTIGATION_CAPABILITIES: tuple[str, ...] = (
    "knowledge.read",
    "asset.read",
    "finding.read",
    "security_event.read",
    "incident.read",
    "evidence.read",
)

HIGH_RISK_PREFIXES: tuple[str, ...] = (
    "response.waf",
    "response.firewall",
    "response.edr",
    "host.isolate",
    "response.block",
    "response.delete",
)


class FakeLLMProvider(LLMProvider):
    """Deterministic, offline LLM stand-in for tests and evaluation.

    It synthesizes a structured JSON plan from the request metadata. Unlike a
    real model it never guesses unknown capabilities and never emits raw
    commands; this makes guardrail behavior fully testable.
    """

    name = "fake-llm"

    def __init__(self, *, plan_override: dict[str, Any] | None = None) -> None:
        self._plan_override = plan_override

    def supports(self, capability: ModelCapability) -> bool:
        return capability in {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
        }

    def health_check(self) -> bool:
        return True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return a deterministic completion.

        ``request.extra`` may carry structured hints (goal, context,
        available_capabilities) that a real provider would receive inside the
        prompt text. ``data`` blocks are treated strictly as untrusted data.
        """
        hints: dict[str, Any] = request.extra if isinstance(request.extra, dict) else {}
        goal: str = str(hints.get("goal", request.user_prompt or "investigate"))
        available: list[str] = list(hints.get("available_capabilities", []))
        requires_high_risk: bool = bool(hints.get("requires_high_risk", False))
        injection_observed: bool = bool(hints.get("injection_observed", False))
        task_type: str = str(hints.get("task_type", "plan"))

        if self._plan_override is not None:
            structured = dict(self._plan_override)
        elif task_type == "triage":
            structured = self._build_triage(hints)
        elif task_type == "attack_chain":
            structured = self._build_attack_chain(hints)
        else:
            structured = self._build_plan(
                goal=goal,
                available=available,
                requires_high_risk=requires_high_risk,
                injection_observed=injection_observed,
            )

        reasoning = "Deterministic fake provider: selected read-only capabilities from the "
        reasoning += "platform registry that are relevant to the goal."
        if injection_observed:
            reasoning += " Untrusted content was treated as data and did not influence the plan."
        structured["reasoning_summary"] = reasoning

        content = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
        return ModelResponse(
            content=content,
            structured=structured,
            provider=self.name,
            usage=TokenUsage(
                prompt_tokens=64,
                completion_tokens=len(content) // 4,
                total_tokens=80,
            ),
            finish_reason="stop",
        )

    def _build_plan(
        self,
        *,
        goal: str,
        available: list[str],
        requires_high_risk: bool,
        injection_observed: bool,
    ) -> dict[str, Any]:
        """Deterministically pick read-only steps in registry order."""
        registry = set(available)
        steps: list[dict[str, Any]] = []
        for name in READ_ONLY_INVESTIGATION_CAPABILITIES:
            if name in registry:
                steps.append(
                    {
                        "capability": name,
                        "purpose": f"Gather {name.split('.')[0]} context for the investigation",
                        "risk": "LOW",
                    }
                )
        plan: dict[str, Any] = {
            "goal": goal,
            "steps": steps,
            "requires_approval": False,
            "risk_level": "LOW",
        }
        if requires_high_risk and not injection_observed:
            # The model *proposes* a high-risk follow-up. The proposed
            # capability is NOT part of the executable steps (the agent is not
            # granted response.* privileges); the platform marks the plan as
            # requiring approval and the agent surfaces it as a recommendation
            # only. High-risk actions are never auto-executed.
            plan["requires_approval"] = True
            plan["risk_level"] = "HIGH"
        return plan

    @staticmethod
    def _build_triage(hints: dict[str, Any]) -> dict[str, Any]:
        """Deterministic triage output used by Fake-vs-Real comparison."""
        severity = str(hints.get("severity", "MEDIUM")).upper()
        false_positive_hint = bool(hints.get("false_positive_hint", False))
        techniques = list(hints.get("expected_techniques", []))
        classification = "BENIGN" if false_positive_hint else (
            "MALICIOUS" if severity in {"HIGH", "CRITICAL"} else "SUSPICIOUS"
        )
        return {
            "classification": classification,
            "severity_assessment": severity,
            "confidence": 0.8,
            "likely_false_positive": false_positive_hint,
            "related_entities": list(hints.get("related_entities", [])),
            "techniques": techniques,
            "recommended_investigation": [
                "correlate_events",
                "check_asset_exposure",
            ],
            "escalation_recommended": severity in {"HIGH", "CRITICAL"},
            "evidence_refs": list(hints.get("evidence_refs", [])),
            "uncertainties": [] if severity not in {"UNKNOWN", ""} else ["severity unknown"],
        }

    @staticmethod
    def _build_attack_chain(hints: dict[str, Any]) -> dict[str, Any]:
        """Deterministic attack-chain output used by Fake-vs-Real comparison."""
        techniques = list(hints.get("expected_techniques", []))
        stages = [
            {
                "order": index,
                "tactic": "initial-access" if index == 0 else "unknown",
                "technique_id": technique,
                "technique_name": technique,
                "entities": list(hints.get("entities", [])),
                "supporting_evidence": list(hints.get("evidence_refs", [])),
            }
            for index, technique in enumerate(techniques)
        ]
        return {
            "summary": "Deterministic multi-stage attack chain hypothesis",
            "ordered_stages": stages,
            "entities": list(hints.get("entities", [])),
            "techniques": techniques,
            "supporting_evidence": list(hints.get("evidence_refs", [])),
            "contradicting_evidence": [],
            "confidence": 0.6,
            "gaps": [],
            "alternative_hypotheses": [],
        }
