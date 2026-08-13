"""Knowledge enrichment candidates (v2.0 / Phase 26).

The platform reuses the existing Knowledge Center. Agents may reference
CVE / CWE / CAPEC / ATT&CK / KEV / IOC knowledge and stage
``KnowledgeCandidate`` objects; they may never directly create confirmed
Knowledge records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

KNOWLEDGE_VOCABULARY: tuple[str, ...] = ("CVE", "CWE", "CAPEC", "ATT&CK", "KEV", "IOC")

REFERENCE_PATTERN = re.compile(r"^(CVE|CWE|CAPEC|ATT&CK|KEV|IOC)[-:]([A-Za-z0-9._-]+)$")


class KnowledgeCandidate(BaseModel):
    """A *proposal* to enrich the Knowledge Center. Never a confirmed record."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    content: str
    content_type: str = "text"
    vocabulary: str = Field(default="CVE")
    reference_id: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0, default=0.4)
    source_refs: list[str] = Field(default_factory=list)
    status: str = "PENDING_VALIDATION"

    def validate_vocabulary(self) -> bool:
        return self.vocabulary.upper() in KNOWLEDGE_VOCABULARY

    def validate_reference(self) -> bool:
        return REFERENCE_PATTERN.match(f"{self.vocabulary.upper()}-{self.reference_id}") is not None


@dataclass(slots=True)
class KnowledgeEnrichmentReport:
    """Aggregate of staged knowledge candidates."""

    candidates: list[KnowledgeCandidate] = field(default_factory=list)

    def add(self, candidate: KnowledgeCandidate) -> None:
        if not candidate.validate_vocabulary():
            raise ValueError(f"Unknown knowledge vocabulary: {candidate.vocabulary}")
        if not candidate.validate_reference():
            raise ValueError(f"Invalid reference id: {candidate.reference_id}")
        self.candidates.append(candidate)

    def snapshot(self) -> list[dict[str, Any]]:
        return [candidate.model_dump() for candidate in self.candidates]

    @property
    def pending_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.status == "PENDING_VALIDATION")
