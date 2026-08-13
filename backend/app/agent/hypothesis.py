"""Investigation hypothesis model (v2.0 / Phase 26).

A hypothesis is NEVER evidence. Every hypothesis must reference supporting
evidence or explicitly declare ``insufficient_evidence=True``. Models cannot
emit source-less asserted facts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HypothesisState(StrEnum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"


class InvestigationHypothesis(BaseModel):
    """A falsifiable statement grounded in evidence or explicitly ungrounded."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str = Field(default_factory=lambda: str(uuid4()))
    statement: str = Field(min_length=1, max_length=2048)
    state: HypothesisState = HypothesisState.PROPOSED
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = "agent"

    @model_validator(mode="after")
    def _evidence_grounding(self) -> InvestigationHypothesis:
        """Models cannot emit source-less asserted facts."""
        if not self.supporting_evidence and not self.insufficient_evidence:
            raise ValueError(
                "Hypothesis must reference supporting evidence or set insufficient_evidence=True"
            )
        return self

    def transition(
        self, state: HypothesisState, *, evidence: list[str] | None = None
    ) -> InvestigationHypothesis:
        """Return a new hypothesis in the target state (immutable model)."""
        return InvestigationHypothesis(
            hypothesis_id=self.hypothesis_id,
            statement=self.statement,
            state=state,
            supporting_evidence=self.supporting_evidence,
            contradicting_evidence=self.contradicting_evidence,
            insufficient_evidence=self.insufficient_evidence,
            confidence=self.confidence,
            created_at=self.created_at,
            source=self.source,
        )

    def redacted_snapshot(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "state": self.state.value,
            "supporting_evidence": self.supporting_evidence,
            "contradicting_evidence": self.contradicting_evidence,
            "insufficient_evidence": self.insufficient_evidence,
            "confidence": self.confidence,
        }


class AttackChainStage(BaseModel):
    """One ordered stage of a hypothesized attack chain."""

    model_config = ConfigDict(frozen=True)

    order: int = Field(ge=0)
    tactic: str = ""
    technique_id: str = ""
    technique_name: str = ""
    entities: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)


class AttackChainHypothesis(BaseModel):
    """A hypothesized multi-stage attack chain (a hypothesis, not evidence)."""

    model_config = ConfigDict(frozen=True)

    chain_id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str
    ordered_stages: list[AttackChainStage] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.3)
    gaps: list[str] = Field(default_factory=list)
    alternative_hypotheses: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False

    def as_hypothesis(self) -> InvestigationHypothesis:
        """Expose the chain as a grounded hypothesis for the hypothesis registry."""
        return InvestigationHypothesis(
            statement=self.summary,
            supporting_evidence=self.supporting_evidence,
            contradicting_evidence=self.contradicting_evidence,
            insufficient_evidence=self.insufficient_evidence,
            confidence=self.confidence,
            source="attack-chain",
        )
