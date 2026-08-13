"""Notification and Ticket Framework contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import FindingSeverity

NOTIFICATION_CAPABILITIES = frozenset(
    {
        "notification.email",
        "notification.webhook",
        "notification.chat",
        "notification.ticket",
        "notification.sms",
        "notification.custom",
    }
)


class NotificationStatus(StrEnum):
    PLANNED = "PLANNED"
    SUPPRESSED = "SUPPRESSED"
    RUNNING = "RUNNING"
    SENT = "SENT"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class TemplateFormat(StrEnum):
    MARKDOWN = "MARKDOWN"
    HTML = "HTML"
    JSON = "JSON"
    TEXT = "TEXT"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RecipientGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    recipients: list[str] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("recipients")
    @classmethod
    def normalize_recipients(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))
        if not normalized:
            raise ValueError("Recipient group cannot be empty")
        return normalized


class NotificationRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    capability: str
    recipient_group: str = Field(min_length=1, max_length=128)
    template_name: str = Field(min_length=1, max_length=128)
    severities: list[FindingSeverity] = Field(default_factory=lambda: list(FindingSeverity))
    priorities: list[TicketPriority] = Field(default_factory=lambda: list(TicketPriority))

    @field_validator("capability", "recipient_group", "template_name", "name")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        if value not in NOTIFICATION_CAPABILITIES:
            raise ValueError(f"Unsupported notification capability: {value}")
        return value


class SilenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID | None = None
    recipient_group: str | None = None
    capability: str | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_window(self) -> "SilenceRule":
        if self.ends_at <= self.starts_at:
            raise ValueError("Silence end must be after start")
        return self


class EscalationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_severity: FindingSeverity = FindingSeverity.HIGH
    from_group: str = Field(min_length=1, max_length=128)
    to_group: str = Field(min_length=1, max_length=128)


class NotificationPolicy(BaseModel):
    """Fail-closed policy applied before any plugin receives recipients or content."""

    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(default="default-notification-policy", min_length=1, max_length=128)
    enabled: bool = True
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: sorted(NOTIFICATION_CAPABILITIES)
    )
    allowed_severities: list[FindingSeverity] = Field(default_factory=lambda: list(FindingSeverity))
    allowed_priorities: list[TicketPriority] = Field(default_factory=lambda: list(TicketPriority))
    business_hours_start: int = Field(default=0, ge=0, le=23)
    business_hours_end: int = Field(default=23, ge=0, le=23)
    defer_outside_business_hours: bool = False
    recipient_allowlist: list[str] = Field(default_factory=lambda: ["soc@example.test"])
    recipient_groups: list[RecipientGroup] = Field(
        default_factory=lambda: [RecipientGroup(name="soc", recipients=["soc@example.test"])]
    )
    routes: list[NotificationRoute] = Field(
        default_factory=lambda: [
            NotificationRoute(
                name="default-synthetic",
                capability="notification.custom",
                recipient_group="soc",
                template_name="default-text",
            )
        ]
    )
    rate_limit_count: int = Field(default=100, ge=1, le=100_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    deduplication_window_seconds: int = Field(default=300, ge=0, le=604_800)
    silence_rules: list[SilenceRule] = Field(default_factory=list)
    escalation_rules: list[EscalationRule] = Field(default_factory=list)
    execution_timeout_seconds: int = Field(default=30, ge=1, le=3_600)
    max_evidence_items: int = Field(default=20, ge=1, le=1_000)
    max_result_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)

    @field_validator("allowed_capabilities")
    @classmethod
    def normalize_capabilities(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().casefold() for item in value if item.strip()})
        unsupported = set(normalized) - NOTIFICATION_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported notification capabilities: {sorted(unsupported)}")
        return normalized

    @field_validator("recipient_allowlist")
    @classmethod
    def normalize_allowlist(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().casefold() for item in value if item.strip()})

    @model_validator(mode="after")
    def validate_boundaries(self) -> "NotificationPolicy":
        if not self.enabled or not self.allowed_capabilities:
            return self
        groups = {group.name: set(group.recipients) for group in self.recipient_groups}
        if len(groups) != len(self.recipient_groups):
            raise ValueError("Recipient group names must be unique")
        allowlist = set(self.recipient_allowlist)
        if not allowlist or any(not recipients <= allowlist for recipients in groups.values()):
            raise ValueError("Every recipient group member must be explicitly allowlisted")
        if any(route.recipient_group not in groups for route in self.routes):
            raise ValueError("Every route must reference a configured recipient group")
        if any(route.capability not in self.allowed_capabilities for route in self.routes):
            raise ValueError("Every route capability must be allowed")
        return self


class NotificationTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    format: TemplateFormat
    subject: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=100_000)
    variables: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().casefold()


class NotificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    response_plan_id: UUID | None = None
    capability: str = "notification.custom"
    plugin_name: str | None = None
    recipient_group: str | None = None
    template_name: str | None = None
    severity: FindingSeverity
    priority: TicketPriority
    requested_by: str = Field(min_length=1, max_length=256)
    variables: dict[str, Any] = Field(default_factory=dict)
    deduplication_key: str | None = Field(default=None, max_length=256)

    @field_validator("capability", "plugin_name", "recipient_group", "template_name")
    @classmethod
    def normalize_identity(cls, value: str | None) -> str | None:
        return value.strip().casefold() if value is not None else None

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        if value not in NOTIFICATION_CAPABILITIES:
            raise ValueError(f"Unsupported notification capability: {value}")
        return value


class NotificationPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    response_plan_id: UUID | None
    capability: str
    plugin_name: str
    recipient_group: str
    recipients: list[str]
    template_name: str
    template_format: TemplateFormat
    template_subject: str
    template_body: str
    variables: dict[str, Any]
    severity: FindingSeverity
    priority: TicketPriority
    policy_name: str
    deduplication_key: str
    steps: list[str]


class RenderedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", max_length=500)
    body: str = Field(max_length=200_000)
    format: TemplateFormat
    content_type: str = Field(min_length=1, max_length=128)


class NotificationVerification(BaseModel):
    verified: bool
    status: str = Field(min_length=1, max_length=64)
    external_reference: str | None = Field(default=None, max_length=2_048)
    details: dict[str, Any] = Field(default_factory=dict)


class NotificationEvidenceItem(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: str = Field(min_length=1, max_length=2_048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationResult(BaseModel):
    """Only value a NotificationPlugin may return to NotificationRuntime."""

    success: bool
    plugin_name: str
    plugin_version: str
    capability: str
    status: str = Field(min_length=1, max_length=64)
    recipients: list[str]
    verification: NotificationVerification
    evidence: list[NotificationEvidenceItem] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    message: str = Field(max_length=4_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    plugin_id: UUID
    status: str
    verification_status: str
    external_reference: str | None
    result: dict[str, Any]
    duration_ms: int
    message: str
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class NotificationEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    execution_id: UUID | None
    evidence_type: str
    sha256: str
    reference: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    response_plan_id: UUID | None
    plugin_id: UUID
    template_id: UUID
    capability: str
    recipient_group: str
    recipients: list[str]
    severity: FindingSeverity
    priority: TicketPriority
    status: NotificationStatus
    requested_by: str
    deduplication_key: str
    suppression_reason: str | None
    policy_snapshot: dict[str, Any]
    plan: dict[str, Any]
    executions: list[NotificationExecutionRead] = Field(default_factory=list)
    evidence: list[NotificationEvidenceRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class NotificationPluginRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    enabled: bool
    permissions: list[str]
    capabilities: list[str]
    health_status: str
    sandbox_compatible: bool
    certified: bool
    supports_verification: bool


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID | None = None
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=20_000)
    priority: TicketPriority
    status: TicketStatus = TicketStatus.OPEN
    external_reference: str | None = Field(default=None, max_length=2_048)
    labels: list[str] = Field(default_factory=list, max_length=100)
    created_by: str = Field(min_length=1, max_length=256)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().casefold() for item in value if item.strip()})


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID | None
    title: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    external_reference: str | None
    labels: list[str]
    created_by: str
    created_at: datetime
    updated_at: datetime
