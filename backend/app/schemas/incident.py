"""Incident and Investigation Case contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import (
    FindingConfidence,
    FindingSeverity,
    IncidentArtifactType,
    IncidentPriority,
    IncidentStatus,
    InvestigationStatus,
)


class IncidentPolicy(BaseModel):
    """Configuration-first policy for platform-owned Incident governance."""

    automatic_creation_enabled: bool = True
    automatic_escalation_enabled: bool = True
    duplicate_merge_enabled: bool = True
    automatic_close_enabled: bool = False
    reopen_enabled: bool = True
    allowed_sources: list[str] = Field(
        default_factory=lambda: ["MANUAL", "ASSESSMENT", "DETECTION"]
    )
    minimum_severity: FindingSeverity = FindingSeverity.HIGH
    minimum_confidence: FindingConfidence = FindingConfidence.MEDIUM
    event_threshold: int = Field(default=2, ge=2, le=10_000)
    correlation_window_seconds: int = Field(default=300, ge=1, le=604_800)
    duplicate_window_seconds: int = Field(default=86_400, ge=1, le=31_536_000)
    default_priority: IncidentPriority = IncidentPriority.P3
    default_queue: str = Field(default="security-operations", min_length=1, max_length=128)
    max_artifacts: int = Field(default=1_000, ge=1, le=100_000)
    sla_targets_minutes: dict[IncidentPriority, int] = Field(
        default_factory=lambda: {
            IncidentPriority.P1: 15,
            IncidentPriority.P2: 60,
            IncidentPriority.P3: 240,
            IncidentPriority.P4: 1_440,
        }
    )

    @field_validator("allowed_sources")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().upper() for item in value if item.strip()})
        if not normalized:
            raise ValueError("At least one Incident source is required")
        unsupported = set(normalized) - {"MANUAL", "ASSESSMENT", "DETECTION"}
        if unsupported:
            raise ValueError(f"Unsupported Incident sources: {sorted(unsupported)}")
        return normalized

    @model_validator(mode="after")
    def validate_sla_targets(self) -> "IncidentPolicy":
        if set(self.sla_targets_minutes) != set(IncidentPriority):
            raise ValueError("SLA targets must be defined for every Incident priority")
        if any(value <= 0 for value in self.sla_targets_minutes.values()):
            raise ValueError("SLA targets must be positive")
        return self


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=20_000)
    severity: FindingSeverity
    priority: IncidentPriority | None = None
    confidence: FindingConfidence = FindingConfidence.MEDIUM
    source: str = Field(default="MANUAL", min_length=1, max_length=64)
    owner: str | None = Field(default=None, max_length=256)
    assignee: str | None = Field(default=None, max_length=256)
    queue: str | None = Field(default=None, max_length=128)
    classification: str | None = Field(default=None, max_length=128)
    risk: str | None = Field(default=None, max_length=64)
    attributes: dict[str, Any] = Field(default_factory=dict)
    finding_ids: list[UUID] = Field(default_factory=list)
    event_ids: list[UUID] = Field(default_factory=list)
    knowledge_ids: list[UUID] = Field(default_factory=list)
    asset_ids: list[UUID] = Field(default_factory=list)
    create_case: bool = True

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.strip().upper()


class IncidentTransitionCreate(BaseModel):
    status: IncidentStatus
    actor: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=4_000)


class IncidentAssignmentCreate(BaseModel):
    actor: str = Field(min_length=1, max_length=256)
    owner: str | None = Field(default=None, max_length=256)
    assignee: str | None = Field(default=None, max_length=256)
    queue: str | None = Field(default=None, max_length=128)
    priority: IncidentPriority | None = None
    reason: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def require_change(self) -> "IncidentAssignmentCreate":
        if all(value is None for value in (self.owner, self.assignee, self.queue, self.priority)):
            raise ValueError("At least one assignment field is required")
        return self


class IncidentArtifactCreate(BaseModel):
    artifact_type: IncidentArtifactType
    reference_id: UUID | None = None
    value: str | None = Field(default=None, max_length=4_096)
    label: str | None = Field(default=None, max_length=256)
    attributes: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="system", min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_reference(self) -> "IncidentArtifactCreate":
        platform_types = {
            IncidentArtifactType.ASSET,
            IncidentArtifactType.EVIDENCE,
            IncidentArtifactType.FINDING,
            IncidentArtifactType.SECURITY_EVENT,
            IncidentArtifactType.KNOWLEDGE,
            IncidentArtifactType.REPORT,
        }
        if self.artifact_type in platform_types and self.reference_id is None:
            raise ValueError("Platform artifact types require reference_id")
        if self.artifact_type not in platform_types and not self.value:
            raise ValueError("Value artifact types require value")
        return self


class InvestigationCaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    owner: str | None = Field(default=None, max_length=256)
    assignee: str | None = Field(default=None, max_length=256)
    queue: str | None = Field(default=None, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(min_length=1, max_length=256)


class CaseCommentCreate(BaseModel):
    author: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=20_000)


class IncidentTimelineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    actor: str
    description: str
    from_status: str | None
    to_status: str | None
    details: dict[str, Any]
    created_at: datetime


class IncidentArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artifact_type: str
    reference_id: UUID | None
    value: str | None
    label: str | None
    attributes: dict[str, Any]
    created_at: datetime


class CaseCommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author: str
    body: str
    created_at: datetime


class InvestigationCaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    title: str
    status: InvestigationStatus
    owner: str | None
    assignee: str | None
    queue: str | None
    started_at: datetime | None
    completed_at: datetime | None
    attributes: dict[str, Any]
    comments: list[CaseCommentRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str
    severity: FindingSeverity
    priority: IncidentPriority
    status: IncidentStatus
    confidence: FindingConfidence
    source: str
    owner: str | None
    assignee: str | None
    queue: str | None
    classification: str | None
    risk: str | None
    attributes: dict[str, Any]
    duplicate_of_id: UUID | None
    sla_due_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    timelines: list[IncidentTimelineRead] = Field(default_factory=list)
    artifacts: list[IncidentArtifactRead] = Field(default_factory=list)
    cases: list[InvestigationCaseRead] = Field(default_factory=list)
    finding_ids: list[UUID] = Field(default_factory=list)
    event_ids: list[UUID] = Field(default_factory=list)
    knowledge_ids: list[UUID] = Field(default_factory=list)
    asset_ids: list[UUID] = Field(default_factory=list)


class IncidentPlan(BaseModel):
    source: str
    correlation_key: str
    priority: IncidentPriority
    queue: str
    sla_minutes: int
    finding_ids: list[UUID] = Field(default_factory=list)
    event_ids: list[UUID] = Field(default_factory=list)
    steps: list[str] = Field(
        default_factory=lambda: ["validate", "correlate", "create", "link", "audit"]
    )


class IncidentCandidate(BaseModel):
    title: str
    description: str = ""
    severity: FindingSeverity
    confidence: FindingConfidence
    source: str
    correlation_key: str
    finding_ids: list[UUID] = Field(default_factory=list)
    event_ids: list[UUID] = Field(default_factory=list)
    asset_ids: list[UUID] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
