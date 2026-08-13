"""Phase 27 -- confidence calibration.

Final confidence is NEVER taken from the model's self-report. It is computed
from: evidence quality, deterministic score strength, knowledge match, and
model agreement (when a model is present).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConfidenceInputs:
    evidence_quality: float = 0.5  # 0..1 derived from evidence presence/type
    deterministic_score: float = 0.5  # 0..1 strength of deterministic scoring
    knowledge_match: float = 0.0  # 0..1 knowledge retrieval hit strength
    model_agreement: float | None = None  # 0..1 agreement across model runs


@dataclass
class CalibratedConfidence:
    confidence: float
    components: dict[str, float] = field(default_factory=dict)
    basis: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "basis": self.basis,
        }


class ConfidenceCalibrator:
    """Weighted calibration that ignores model self-reported confidence."""

    def __init__(
        self,
        *,
        weights: dict[str, float] | None = None,
        min_confidence: float = 0.2,
        max_confidence: float = 0.97,
    ) -> None:
        self._weights = weights or {
            "evidence": 0.4,
            "deterministic": 0.3,
            "knowledge": 0.2,
            "model": 0.1,
        }
        self._min = min_confidence
        self._max = max_confidence

    def calibrate(self, inputs: ConfidenceInputs) -> CalibratedConfidence:
        components: dict[str, float] = {
            "evidence": _clip(inputs.evidence_quality),
            "deterministic": _clip(inputs.deterministic_score),
            "knowledge": _clip(inputs.knowledge_match),
        }
        if inputs.model_agreement is not None:
            components["model"] = _clip(inputs.model_agreement)

        total_weight = 0.0
        weighted = 0.0
        for name, weight in self._weights.items():
            if name in components:
                weighted += weight * components[name]
                total_weight += weight
        if total_weight == 0:
            return CalibratedConfidence(
                confidence=self._min,
                components=components,
                basis="no-signal",
            )

        confidence = weighted / total_weight
        # penalize over-confidence when evidence is thin
        if inputs.evidence_quality < 0.3:
            confidence *= 0.7
        confidence = max(self._min, min(self._max, confidence))
        basis = "model+deterministic" if inputs.model_agreement is not None else "deterministic"
        return CalibratedConfidence(
            confidence=confidence,
            components=components,
            basis=basis,
        )


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))
