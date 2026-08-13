"""Phase 27 -- false positive scoring.

Deterministic estimate of the probability that a detection is a false
positive, based on rule/event/asset/history signals. The LLM only provides
analysis rationale; the final FP flag stays advisory (a domain service or
human makes the actual state change).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Well-known benign indicators that reduce FP likelihood.
_KNOWN_BENIGN_KEYWORDS = (
    "scanner",
    "scan",
    "scheduled",
    "maintenance",
    "pentest",
    "penetration",
    "baseline",
    "test",
    "monitoring",
    "healthcheck",
    "cve scan",
    "nessus",
    "qualys",
    "acunetix",
    "backup",
    "deployment",
    "deploy",
    "ci runner",
    "pipeline",
    "developer",
    "debug",
    "staging",
    "red-team",
    "red team",
    "nightly",
    "backup server",
    "backup vlan",
    "orchestrator",
    "sensor",
    "whitelisted",
    "approved",
    "maintenance window",
)

# High-frequency noisy event types commonly benign at scale.
_NOISY_EVENT_TYPES = (
    "dns_request",
    "http_request",
    "connection",
    "flow",
    "login_attempt",
    "port_scan",
)


@dataclass
class FPFactor:
    name: str
    value: Any
    direction: str  # "raises_fp" | "lowers_fp"


@dataclass
class FalsePositiveAssessment:
    false_positive_probability: float  # 0..1
    confidence: float
    likely_false_positive: bool  # advisory only
    factors: list[FPFactor] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "false_positive_probability": round(self.false_positive_probability, 4),
            "confidence": round(self.confidence, 4),
            "likely_false_positive": self.likely_false_positive,
            "factors": [
                {"name": f.name, "value": str(f.value), "direction": f.direction}
                for f in self.factors
            ],
            "rationale": self.rationale,
        }


class FalsePositiveScorer:
    """Deterministic FP probability estimator."""

    def __init__(
        self,
        *,
        threshold: float = 0.7,
        history_window_days: int = 30,
    ) -> None:
        self._threshold = threshold
        self._history_window_days = history_window_days

    def score(
        self,
        *,
        rule: str | None = None,
        event_type: str | None = None,
        frequency_30d: int = 0,
        asset_criticality: str | None = None,
        historical_fp_rate: float | None = None,
        evidence_quality: float | None = None,
        detection_confidence: str | None = None,
        known_benign_match: bool | None = None,
    ) -> FalsePositiveAssessment:
        """Compute FP probability. All inputs are platform signals."""
        score = 0.0
        factors: list[FPFactor] = []

        # 1. Rule/event signals
        text = f"{rule or ''} {event_type or ''}".lower()
        if any(kw in text for kw in _KNOWN_BENIGN_KEYWORDS):
            score += 0.25
            factors.append(FPFactor("known_benign_signal", "matched", "raises_fp"))
        if any(et in text for et in _NOISY_EVENT_TYPES):
            score += 0.2
            factors.append(FPFactor("noisy_event_type", event_type, "raises_fp"))

        # 2. Frequency: repeated events at an asset raise FP odds
        if frequency_30d >= 50:
            score += 0.25
            factors.append(FPFactor("event_frequency", frequency_30d, "raises_fp"))
        elif frequency_30d >= 10:
            score += 0.15
            factors.append(FPFactor("event_frequency", frequency_30d, "raises_fp"))

        # 3. Asset criticality lowers FP odds (care more about critical assets)
        if asset_criticality:
            if str(asset_criticality).upper() in ("HIGH", "CRITICAL"):
                score -= 0.15
                factors.append(FPFactor("asset_criticality", asset_criticality, "lowers_fp"))

        # 4. Historical FP rate
        if historical_fp_rate is not None:
            score += 0.4 * max(0.0, min(1.0, historical_fp_rate))
            factors.append(
                FPFactor("historical_fp_rate", round(historical_fp_rate, 2), "raises_fp")
            )

        # 5. Evidence quality lowers FP
        if evidence_quality is not None:
            score -= 0.2 * max(0.0, min(1.0, evidence_quality))
            factors.append(FPFactor("evidence_quality", round(evidence_quality, 2), "lowers_fp"))

        # 6. Detection confidence
        if detection_confidence:
            if str(detection_confidence).upper() == "HIGH":
                score -= 0.1
                factors.append(FPFactor("detection_confidence", detection_confidence, "lowers_fp"))
            elif str(detection_confidence).upper() == "LOW":
                score += 0.1
                factors.append(FPFactor("detection_confidence", detection_confidence, "raises_fp"))

        # 7. Explicit benign indicator flag (from knowledge: warning list match)
        if known_benign_match:
            score += 0.45
            factors.append(FPFactor("known_benign_ioc", True, "raises_fp"))

        probability = max(0.0, min(1.0, score))
        # Explicit platform FP hint is authoritative: mark as likely FP
        # regardless of other signals.
        if known_benign_match:
            probability = max(probability, 0.75)
            factors.append(FPFactor("platform_fp_hint", True, "raises_fp"))
        confidence = max(0.3, min(0.9, 0.5 + 0.05 * len(factors)))
        return FalsePositiveAssessment(
            false_positive_probability=probability,
            confidence=confidence,
            likely_false_positive=probability >= self._threshold,
            factors=factors,
        )
