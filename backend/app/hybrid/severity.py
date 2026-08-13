"""Phase 27 -- deterministic severity engine.

Severity is computed from platform signals (finding severity, CVSS, EPSS,
KEV, asset criticality, exposure, evidence/detection confidence). The LLM can
only explain the result -- it can never override the deterministic severity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITY_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
SEVERITY_RANK = {value: key for key, value in SEVERITY_ORDER.items()}

_CRITICALITY_WEIGHT = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass
class SeverityFactor:
    name: str
    value: Any
    contribution: float  # 0..1 direction toward higher severity


@dataclass
class SeverityAssessment:
    severity: str
    score: float  # 0..1 continuous score
    confidence: float
    factors: list[SeverityFactor] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "factors": [
                {"name": f.name, "value": str(f.value), "contribution": round(f.contribution, 3)}
                for f in self.factors
            ],
            "explanation": self.explanation,
        }


def _severity_score(level: str) -> float:
    return SEVERITY_ORDER.get(str(level).upper(), 0) / 4.0


def _cvss_to_score(cvss: float | None) -> float | None:
    if cvss is None:
        return None
    return max(0.0, min(1.0, cvss / 10.0))


def _epss_to_score(epss: float | None) -> float | None:
    if epss is None:
        return None
    return max(0.0, min(1.0, epss))


class DeterministicSeverityEngine:
    """Pure deterministic severity computation. No LLM inside."""

    def __init__(
        self,
        *,
        weights: dict[str, float] | None = None,
        kev_bonus: float = 0.15,
    ) -> None:
        self._weights = weights or {
            "finding": 0.30,
            "cvss": 0.25,
            "epss": 0.10,
            "kev": 0.10,
            "criticality": 0.15,
            "evidence": 0.05,
            "detection": 0.05,
        }
        self._kev_bonus = kev_bonus

    def assess(
        self,
        *,
        finding_severity: str | None = None,
        cvss: float | None = None,
        epss: float | None = None,
        in_kev: bool = False,
        asset_criticality: str | None = None,
        exposed: bool = False,
        evidence_confidence: float | None = None,
        detection_confidence: str | None = None,
    ) -> SeverityAssessment:
        factors: list[SeverityFactor] = []

        # Base rank anchored on the most authoritative signal: the finding's
        # own severity (already assigned by the domain service). When absent we
        # derive it from CVSS/EPSS/criticality evidence.
        anchor_rank = SEVERITY_ORDER.get(str(finding_severity or "").upper(), 0)
        if anchor_rank:
            factors.append(SeverityFactor("finding_severity", finding_severity, anchor_rank / 4))
        elif cvss is not None:
            anchor_rank = SEVERITY_ORDER.get(_cvss_severity(cvss), 1)
            factors.append(SeverityFactor("cvss", cvss, anchor_rank / 4))
        elif asset_criticality:
            anchor_rank = SEVERITY_ORDER.get(str(asset_criticality).upper(), 1)
            factors.append(SeverityFactor("asset_criticality", asset_criticality, anchor_rank / 4))

        rank = anchor_rank
        score_components: list[tuple[str, float]] = []

        # Escalation signals (each can raise the rank at most one level overall)
        if cvss is not None and _cvss_severity(cvss) in ("HIGH", "CRITICAL"):
            factors.append(SeverityFactor("cvss", cvss, 1.0))
            rank = max(rank, 3)  # HIGH floor from severe CVSS
            score_components.append(("cvss", _cvss_to_score(cvss) or 0.0))
        if epss is not None and (epss or 0) >= 0.5:
            factors.append(SeverityFactor("epss", epss, 1.0))
            score_components.append(("epss", _epss_to_score(epss) or 0.0))
        if in_kev:
            factors.append(SeverityFactor("kev", "known exploited", 1.0))
            rank = min(4, rank + 1)
            score_components.append(("kev", 1.0))
        if str(asset_criticality or "").upper() in ("HIGH", "CRITICAL") and exposed:
            factors.append(SeverityFactor("exposure", "critical + exposed", 1.0))
            rank = min(4, rank + 1)
        if detection_confidence and str(detection_confidence).upper() == "LOW":
            factors.append(SeverityFactor("detection_confidence", detection_confidence, -1.0))
            rank = max(1, rank - 1)
        if evidence_confidence is not None and evidence_confidence < 0.5:
            factors.append(
                SeverityFactor("evidence_confidence", round(evidence_confidence, 2), -0.5)
            )
            rank = max(1, rank - 1)

        # Continuous score: anchor rank weighted, plus positive signal mean.
        rank = max(1, min(4, rank))
        continuous = rank / 4.0
        if score_components:
            continuous = 0.7 * continuous + 0.3 * (
                sum(v for _, v in score_components) / len(score_components)
            )
        continuous = max(0.0, min(1.0, continuous))
        severity = SEVERITY_RANK[rank]
        confidence = self._confidence(continuous, len(factors))
        return SeverityAssessment(
            severity=severity,
            score=continuous,
            confidence=confidence,
            factors=factors,
        )

    @staticmethod
    def _level(score: float) -> str:
        if score >= 0.75:
            return "CRITICAL"
        if score >= 0.5:
            return "HIGH"
        if score >= 0.25:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _confidence(score: float, factor_count: int) -> float:
        base = 0.5 + 0.08 * factor_count
        # extreme scores are more confident than borderline
        distance = abs(score - 0.5) * 2.0
        return max(0.3, min(0.98, base + distance * 0.2))


def _cvss_severity(cvss: float) -> str:
    if cvss >= 9.0:
        return "CRITICAL"
    if cvss >= 7.0:
        return "HIGH"
    if cvss >= 4.0:
        return "MEDIUM"
    if cvss > 0.0:
        return "LOW"
    return "NONE"
