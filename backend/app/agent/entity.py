"""Entity resolution candidates (v2.0 / Phase 26).

The platform reuses the existing Asset Center. Models may propose
``EntityLinkCandidate`` objects; the platform must validate a candidate
before it becomes a formal ``AssetRelation``. There is no second asset
registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ENTITY_TYPES: tuple[str, ...] = (
    "IP",
    "DOMAIN",
    "HOST",
    "USER",
    "URL",
    "HASH",
    "APPLICATION",
)


class LinkKind(StrEnum):
    RESOLVES_TO = "RESOLVES_TO"
    CONNECTS_TO = "CONNECTS_TO"
    USED_BY = "USED_BY"
    RELATED_TO = "RELATED_TO"
    RAN_ON = "RAN_ON"


class EntityLinkCandidate(BaseModel):
    """A *proposal* that two entities are related. Not a formal relation."""

    model_config = ConfigDict(frozen=True)

    source_entity: str
    source_type: str = Field(default="IP")
    target_entity: str
    target_type: str = Field(default="HOST")
    link_kind: LinkKind = LinkKind.RELATED_TO
    confidence: float = Field(ge=0.0, le=1.0, default=0.4)
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    status: str = "PENDING_VALIDATION"

    def validate_types(self) -> bool:
        """Entity types must come from the supported vocabulary."""
        return self.source_type.upper() in ENTITY_TYPES and self.target_type.upper() in ENTITY_TYPES


@dataclass(slots=True)
class EntityResolutionReport:
    """Aggregate of proposed entity links."""

    candidates: list[EntityLinkCandidate] = field(default_factory=list)

    def add(self, candidate: EntityLinkCandidate) -> None:
        if not candidate.validate_types():
            raise ValueError(
                f"Unknown entity type: {candidate.source_type}/{candidate.target_type}"
            )
        if candidate.source_entity == candidate.target_entity:
            raise ValueError("Entity link source and target must differ")
        self.candidates.append(candidate)

    def snapshot(self) -> list[dict[str, Any]]:
        return [candidate.model_dump() for candidate in self.candidates]

    @property
    def pending_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.status == "PENDING_VALIDATION")
