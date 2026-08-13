"""Replaceable rule-based assessment risk engine."""

from dataclasses import dataclass
from typing import Protocol

from app.core.enums import FindingSeverity, RiskLevel
from app.models import Asset, Knowledge
from app.schemas.assessment import RawFinding


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    score: float
    reasons: tuple[str, ...]


class RiskEngine(Protocol):
    def assess(
        self, finding: RawFinding, knowledge: list[Knowledge], asset: Asset
    ) -> RiskAssessment: ...


class RuleBasedRiskEngine:
    """Combine tool severity, asset criticality and trusted knowledge attributes."""

    _severity_score = {
        FindingSeverity.INFO: 0.5,
        FindingSeverity.LOW: 2.0,
        FindingSeverity.MEDIUM: 5.0,
        FindingSeverity.HIGH: 8.0,
        FindingSeverity.CRITICAL: 9.5,
    }
    _criticality_bonus = {"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.0, "CRITICAL": 1.5}

    def assess(
        self, finding: RawFinding, knowledge: list[Knowledge], asset: Asset
    ) -> RiskAssessment:
        score = self._severity_score[finding.severity]
        reasons = [f"finding severity {finding.severity.value}"]
        criticality = (asset.criticality or "MEDIUM").upper()
        bonus = self._criticality_bonus.get(criticality, 0.5)
        score += bonus
        reasons.append(f"asset criticality {criticality}")
        if any(
            item.knowledge_type == "CISA_KEV" or bool(item.attributes.get("known_exploited"))
            for item in knowledge
        ):
            score += 1.5
            reasons.append("known exploitation evidence")
        cvss_values = [
            float(item.attributes["cvss"])
            for item in knowledge
            if isinstance(item.attributes.get("cvss"), int | float)
        ]
        if cvss_values:
            score = max(score, max(cvss_values))
            reasons.append("knowledge CVSS floor")
        score = min(round(score, 1), 10.0)
        if score >= 9.0:
            level = RiskLevel.CRITICAL
        elif score >= 7.0:
            level = RiskLevel.HIGH
        elif score >= 4.0:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return RiskAssessment(level=level, score=score, reasons=tuple(reasons))
