"""Detection Framework API and plugin-neutral contracts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import FindingConfidence, FindingSeverity, SecurityEventStatus

DETECTION_CAPABILITIES = frozenset(
    {
        "network.detect",
        "host.detect",
        "log.detect",
        "ids.detect",
        "traffic.detect",
        "event.detect",
        "ioc.detect",
        "rule.detect",
    }
)


class DetectionPolicy(BaseModel):
    """Fail-closed ingestion, parsing, execution and retention limits."""

    capability_allowlist: list[str] = Field(default_factory=lambda: sorted(DETECTION_CAPABILITIES))
    allowed_log_sources: list[str] = Field(default_factory=lambda: ["synthetic"])
    allowed_plugins: list[str] = Field(default_factory=lambda: ["fake-detection"])
    allowed_parsers: list[str] = Field(default_factory=lambda: ["structured-json"])
    sampling_rate: float = Field(default=1.0, gt=0, le=1.0)
    max_event_size_bytes: int = Field(default=262_144, ge=1, le=10_000_000)
    rate_limit_per_second: float = Field(default=100.0, gt=0, le=100_000)
    retention_days: int = Field(default=30, ge=1, le=3650)
    timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    max_events: int = Field(default=1000, ge=1, le=100_000)
    correlation_window_seconds: int = Field(default=300, ge=1, le=86_400)

    @field_validator(
        "capability_allowlist",
        "allowed_log_sources",
        "allowed_plugins",
        "allowed_parsers",
    )
    @classmethod
    def normalize_lists(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))

    @model_validator(mode="after")
    def validate_capabilities(self) -> "DetectionPolicy":
        unsupported = set(self.capability_allowlist) - DETECTION_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported detection capabilities: {sorted(unsupported)}")
        if not self.capability_allowlist:
            raise ValueError("At least one detection capability must be allowed")
        if not self.allowed_log_sources:
            raise ValueError("At least one detection log source must be allowed")
        if not self.allowed_plugins:
            raise ValueError("At least one detection plugin must be allowed")
        if not self.allowed_parsers:
            raise ValueError("At least one detection parser must be allowed")
        return self


class DetectionTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    asset_id: UUID
    capabilities: list[str] = Field(min_length=1)
    log_source: str = Field(min_length=1, max_length=128)
    parser: str = Field(min_length=1, max_length=128)
    plugin_name: str | None = Field(default=None, min_length=1, max_length=128)
    policy: DetectionPolicy | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))
        unsupported = set(normalized) - DETECTION_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported detection capabilities: {sorted(unsupported)}")
        if not normalized:
            raise ValueError("At least one detection capability is required")
        return normalized

    @field_validator("log_source", "parser", "plugin_name")
    @classmethod
    def normalize_identity(cls, value: str | None) -> str | None:
        return value.strip().casefold() if value is not None else None


class DetectionPlan(BaseModel):
    asset_id: UUID
    capabilities: list[str]
    plugin_name: str
    log_source: str
    parser: str
    steps: list[str]
    limits: dict[str, int | float]


class RawSecurityEvent(BaseModel):
    """Plugin-neutral candidate event carried only inside DetectionResult."""

    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=256)
    severity: FindingSeverity
    confidence: FindingConfidence = FindingConfidence.MEDIUM
    timestamp: datetime
    asset_ids: list[UUID] = Field(default_factory=list)
    knowledge_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    tool: str | None = Field(default=None, max_length=128)
    rule: str | None = Field(default=None, max_length=256)
    iocs: list[str] = Field(default_factory=list, max_length=100)
    unique_id_from_tool: str | None = Field(default=None, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", "source", "tool", "rule", "unique_id_from_tool")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("references", "iocs")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        return sorted(dict.fromkeys(item.strip() for item in value if item.strip()))


class DetectionResult(BaseModel):
    """Only value a DetectionPlugin may return to the platform runtime."""

    success: bool
    plugin_name: str
    plugin_version: str
    events: list[RawSecurityEvent] = Field(default_factory=list)
    records_collected: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DetectionTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    plugin_id: UUID | None
    status: str
    requested_capabilities: list[str]
    policy: dict[str, Any]
    plan: dict[str, Any]
    result_summary: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class SecurityEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    detection_task_id: UUID
    fingerprint: str
    event_type: str
    source: str
    severity: FindingSeverity
    confidence: FindingConfidence
    timestamp: datetime
    plugin: str
    tool: str | None
    rule: str | None
    status: SecurityEventStatus
    attributes: dict[str, Any]
    references: list[str] = Field(default_factory=list)
    evidence: list[UUID] = Field(default_factory=list)
    knowledge: list[UUID] = Field(default_factory=list)
    assets: list[UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DetectionPluginRead(BaseModel):
    id: UUID
    name: str
    version: str
    description: str | None
    enabled: bool
    permissions: list[str]
    capabilities: list[str] = Field(default_factory=list)


class DetectionCapabilityRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    risk_level: str
    enabled: bool
    plugin: str


class CorrelationGroup(BaseModel):
    key_type: str
    key_value: str
    event_ids: list[UUID]
    first_seen: datetime
    last_seen: datetime
    count: int = Field(ge=2)
