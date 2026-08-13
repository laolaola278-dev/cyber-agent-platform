"""Phase 27 -- SecurityFact model and FactCandidate.

Core principle: facts are VERIFIED only when they come from platform
sources (Evidence / SecurityEvent / Finding / Asset / Knowledge). An LLM
can only propose a ``FactCandidate``; promotion to a verified fact requires
deterministic validation through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FACT_STATUS = Literal["VERIFIED", "PROPOSED", "REJECTED"]
FACT_SOURCE_KINDS = ("evidence", "security_event", "finding", "asset", "knowledge")

FACT_TYPES = (
    "observed_indicator",  # an IOC observed in evidence/event
    "entity_identity",  # asset/identity involved
    "vulnerability",  # CVE/CWE/CAPEC reference
    "technique",  # ATT&CK technique
    "temporal",  # timing/ordering fact
    "network_flow",  # connection / flow fact
    "asset_property",  # criticality / exposure
    "knowledge_match",  # knowledge center hit
    "rule_metadata",  # detection rule info
    "user_behavior",  # login/command behavior
)


class SecurityFact(BaseModel):
    """A platform-verified atomic security fact.

    ``confidence`` here is a *deterministic* evidence quality estimate, not a
    model self-report (calibration happens in ConfidenceCalibrator).
    """

    model_config = ConfigDict(extra="forbid")

    fact_type: str
    value: str
    source_kind: str = Field(description="one of evidence/security_event/finding/asset/knowledge")
    source_id: str
    evidence_ref: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = Field(default_factory=dict)

    def validate_fact(self) -> bool:
        """Deterministic sanity check: source kind known + confidence bounded."""
        if self.source_kind not in FACT_SOURCE_KINDS:
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        return bool(self.value and self.source_id)


class FactCandidate(BaseModel):
    """An LLM-proposed fact that still needs deterministic validation."""

    model_config = ConfigDict(extra="forbid")

    fact_type: str
    value: str
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    def promote(self, *, source_kind: str, source_id: str) -> SecurityFact | None:
        """Promote to a SecurityFact ONLY when backed by a platform source."""
        if not self.evidence_refs and source_kind not in ("knowledge", "security_event"):
            return None
        ref = self.evidence_refs[0] if self.evidence_refs else None
        return SecurityFact(
            fact_type=self.fact_type,
            value=self.value,
            source_kind=source_kind,
            source_id=source_id,
            evidence_ref=ref,
            confidence=self.confidence,
        )


@dataclass
class FactExtractionResult:
    """Deterministic fact extraction from one platform source."""

    facts: list[SecurityFact] = field(default_factory=list)
    rejected_candidates: list[FactCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
