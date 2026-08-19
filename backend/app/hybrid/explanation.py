"""Phase 27 -- explanation layer.

The LLM's primary job becomes explaining WHY. Explanations must reference
CVSS / KEV / asset criticality / evidence. Hidden chain-of-thought is never
exposed; only decision rationale with citations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Explanation:
    statement: str
    evidence_refs: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    model_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence_refs": self.evidence_refs,
            "knowledge_refs": self.knowledge_refs,
            "factors": self.factors,
            "model_generated": self.model_generated,
        }

    def coverage(self) -> float:
        """Fraction of the explanation that cites evidence or knowledge."""
        referenced = bool(self.evidence_refs or self.knowledge_refs or self.factors)
        return 1.0 if referenced else 0.0


class ExplanationBuilder:
    """Builds deterministic explanations from scored components.

    Optionally lets an LLM reword the rationale -- but every explanation must
    keep the deterministic factor list (the user always sees the basis).
    """

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def build(
        self,
        *,
        statement: str,
        factors: list[str],
        evidence_refs: list[str] | None = None,
        knowledge_refs: list[str] | None = None,
    ) -> Explanation:
        base = Explanation(
            statement=statement,
            evidence_refs=evidence_refs or [],
            knowledge_refs=knowledge_refs or [],
            factors=list(factors),
            model_generated=False,
        )
        if self._llm is None:
            return base
        try:
            reworded = await self._llm.explain(
                statement=statement,
                factors=list(factors),
                evidence_refs=list(base.evidence_refs),
            )
            if reworded and reworded.strip():
                base.statement = reworded.strip()
                base.model_generated = True
        except Exception:  # noqa: BLE001 -- explanation failure is non-fatal
            pass
        return base


def evaluate_explanations(
    explanations: list[Explanation],
    *,
    required_factors: set[str],
) -> dict[str, float]:
    """Explainability metrics: coverage / correctness / unsupported rate."""
    total = len(explanations)
    if total == 0:
        return {
            "evidence_coverage": 0.0,
            "factor_coverage": 0.0,
            "correctness": 0.0,
            "unsupported_rate": 0.0,
        }
    evidence_coverage = sum(1 for e in explanations if e.evidence_refs) / total
    factor_coverage = sum(1 for e in explanations if set(e.factors) & required_factors) / total
    correct = (
        sum(1 for e in explanations if e.evidence_refs and (set(e.factors) & required_factors))
        / total
    )
    unsupported = (
        sum(1 for e in explanations if not e.evidence_refs and not e.knowledge_refs) / total
    )
    return {
        "evidence_coverage": round(evidence_coverage, 4),
        "factor_coverage": round(factor_coverage, 4),
        "correctness": round(correct, 4),
        "unsupported_rate": round(unsupported, 4),
    }
