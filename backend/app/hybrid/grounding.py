"""Phase 27 -- evidence grounding engine.

Every LLM claim must resolve to evidence references that are then validated
against the platform evidence set. Claims without support are UNSUPPORTED and
must never be reported as fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GROUNDING_STATUS = ("SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED", "CONTRADICTED")


@dataclass
class GroundedClaim:
    claim: str
    status: str
    evidence_refs: list[str] = field(default_factory=list)
    matched_refs: list[str] = field(default_factory=list)
    unmatched_refs: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "status": self.status,
            "evidence_refs": self.evidence_refs,
            "matched_refs": self.matched_refs,
            "unmatched_refs": self.unmatched_refs,
            "reason": self.reason,
        }


class EvidenceGroundingEngine:
    """Validates claims against a known evidence reference set."""

    def __init__(self, known_evidence: set[str] | None = None) -> None:
        self._known = known_evidence or set()

    def set_known_evidence(self, refs: set[str]) -> None:
        self._known = refs

    def ground(
        self,
        claim: str,
        evidence_refs: list[str] | None,
    ) -> GroundedClaim:
        refs = evidence_refs or []
        matched = [ref for ref in refs if ref in self._known]
        unmatched = [ref for ref in refs if ref not in self._known]

        if not refs:
            return GroundedClaim(
                claim=claim,
                status="UNSUPPORTED",
                evidence_refs=[],
                reason="No evidence reference provided; claim is unsupported.",
            )
        if len(matched) == len(refs):
            return GroundedClaim(
                claim=claim,
                status="SUPPORTED",
                evidence_refs=refs,
                matched_refs=matched,
                reason="All referenced evidence is known.",
            )
        if matched:
            return GroundedClaim(
                claim=claim,
                status="PARTIALLY_SUPPORTED",
                evidence_refs=refs,
                matched_refs=matched,
                unmatched_refs=unmatched,
                reason=f"{len(matched)}/{len(refs)} references matched.",
            )
        return GroundedClaim(
            claim=claim,
            status="UNSUPPORTED",
            evidence_refs=refs,
            unmatched_refs=unmatched,
            reason="No referenced evidence matched the known evidence set.",
        )

    def ground_claims(
        self, claims: list[tuple[str, list[str]]]
    ) -> list[GroundedClaim]:
        return [self.ground(claim, refs) for claim, refs in claims]

    @staticmethod
    def aggregate(claims: list[GroundedClaim]) -> dict[str, float]:
        """Aggregate grounding stats over a batch of claims.

        Keys are lower-case metric names (supported / partially_supported /
        unsupported / contradicted) for consistent reporting.
        """
        total = len(claims)
        result: dict[str, float] = {
            "supported": 0.0,
            "partially_supported": 0.0,
            "unsupported": 0.0,
            "contradicted": 0.0,
        }
        if total == 0:
            return result
        counts = {status: 0 for status in GROUNDING_STATUS}
        for claim in claims:
            counts[claim.status] = counts.get(claim.status, 0) + 1
        mapping = {
            "SUPPORTED": "supported",
            "PARTIALLY_SUPPORTED": "partially_supported",
            "UNSUPPORTED": "unsupported",
            "CONTRADICTED": "contradicted",
        }
        for status, count in counts.items():
            result[mapping.get(status, "unsupported")] = count / total
        return result
