"""Unified Response Framework API and plugin-neutral contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import AssetType, FindingSeverity, RiskLevel

RESPONSE_CAPABILITIES = frozenset(
    {
        "response.notify",
        "response.ticket",
        "response.block",
        "response.isolate",
        "response.rollback",
        "response.waf",
        "response.firewall",
        "response.edr",
        "response.custom",
    }
)


class ApprovalState(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"
    ROLLED_BACK = "ROLLED_BACK"


class ResponseExecutionState(StrEnum):
    PLANNED = "PLANNED"
    BLOCKED = "BLOCKED"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"


class RollbackState(StrEnum):
    NOT_SUPPORTED = "NOT_SUPPORTED"
    AVAILABLE = "AVAILABLE"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"


class ResponsePolicy(BaseModel):
    """Configuration-first, fail-closed response authorization policy."""

    policy_name: str = Field(default="default-response-policy", min_length=1, max_length=128)
    enabled: bool = True
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: ["response.notify", "response.ticket"]
    )
    denied_capabilities: list[str] = Field(
        default_factory=lambda: sorted(
            RESPONSE_CAPABILITIES - {"response.notify", "response.ticket"}
        )
    )
    allowed_incident_types: list[str] = Field(default_factory=lambda: ["*"])
    allowed_asset_types: list[AssetType] = Field(default_factory=lambda: list(AssetType))
    approval_required_capabilities: list[str] = Field(default_factory=list)
    automatic_execution_max_risk: RiskLevel = RiskLevel.LOW
    automatic_execution_max_incident_severity: FindingSeverity = FindingSeverity.LOW
    business_hours_start: int = Field(default=0, ge=0, le=23)
    business_hours_end: int = Field(default=23, ge=0, le=23)
    maintenance_windows: list[str] = Field(default_factory=lambda: ["*"])
    approval_ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)
    execution_timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    max_evidence_items: int = Field(default=100, ge=1, le=10_000)
    require_distinct_approver: bool = True
    required_approval_levels: int = Field(default=1, ge=1, le=10)

    @field_validator(
        "allowed_capabilities",
        "denied_capabilities",
        "approval_required_capabilities",
    )
    @classmethod
    def normalize_capabilities(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().casefold() for item in value if item.strip()})
        unsupported = set(normalized) - RESPONSE_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported response capabilities: {sorted(unsupported)}")
        return normalized

    @field_validator("allowed_incident_types", "maintenance_windows")
    @classmethod
    def normalize_text_lists(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().casefold() for item in value if item.strip()})

    @model_validator(mode="after")
    def validate_boundaries(self) -> "ResponsePolicy":
        if not self.allowed_capabilities:
            raise ValueError("At least one response capability must be allowed")
        overlap = set(self.allowed_capabilities) & set(self.denied_capabilities)
        if overlap:
            raise ValueError(f"Capabilities cannot be both allowed and denied: {sorted(overlap)}")
        if not set(self.approval_required_capabilities) <= set(self.allowed_capabilities):
            raise ValueError("Approval-required capabilities must also be allowed")
        if not self.allowed_incident_types or not self.allowed_asset_types:
            raise ValueError("Incident and Asset policy allowlists cannot be empty")
        if not self.maintenance_windows:
            raise ValueError("At least one maintenance window must be configured")
        return self


class ResponsePlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    asset_ids: list[UUID] = Field(min_length=1, max_length=100)
    target_capability: str = Field(min_length=1, max_length=128)
    plugin_name: str | None = Field(default=None, min_length=1, max_length=128)
    requested_by: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4_000)
    risk_level: RiskLevel
    parameters: dict[str, Any] = Field(default_factory=dict)
    rollback_parameters: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @field_validator("target_capability", "plugin_name")
    @classmethod
    def normalize_identity(cls, value: str | None) -> str | None:
        return value.strip().casefold() if value is not None else None

    @field_validator("target_capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        if value not in RESPONSE_CAPABILITIES:
            raise ValueError(f"Unsupported response capability: {value}")
        return value

    @field_validator("asset_ids")
    @classmethod
    def deduplicate_assets(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ResponseApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approver: str = Field(min_length=1, max_length=256)
    comment: str = Field(default="", max_length=4_000)
    level: int = Field(default=1, ge=1, le=10)


class ResponseRejectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approver: str = Field(min_length=1, max_length=256)
    comment: str = Field(min_length=1, max_length=4_000)


class ResponseExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=256)


class ResponseRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4_000)


class ResponsePlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    asset_ids: list[UUID]
    target_capability: str
    plugin_name: str
    parameters: dict[str, Any]
    rollback_parameters: dict[str, Any]
    risk_level: RiskLevel
    approval_required: bool
    supports_rollback: bool
    policy_name: str
    steps: list[str]


class ResponseVerification(BaseModel):
    verified: bool
    status: str = Field(min_length=1, max_length=64)
    details: dict[str, Any] = Field(default_factory=dict)


class ResponseEvidenceItem(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: str = Field(min_length=1, max_length=2_048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResponseResult(BaseModel):
    """Only value a ResponsePlugin may return to ResponseRuntime."""

    success: bool
    plugin_name: str
    plugin_version: str
    capability: str
    execution_status: str = Field(min_length=1, max_length=64)
    verification: ResponseVerification
    evidence: list[ResponseEvidenceItem] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    message: str = Field(max_length=4_000)
    rollback_supported: bool
    rollback_token: str | None = Field(default=None, max_length=2_048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResponseApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    approver: str
    decision: str
    comment: str
    approval_level: int
    decided_at: datetime
    expires_at: datetime
    created_at: datetime


class ResponseExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    plugin_id: UUID
    status: str
    verification_status: str
    result: dict[str, Any]
    duration_ms: int
    message: str
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class ResponseRollbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    execution_id: UUID
    actor: str
    reason: str
    status: str
    verification_status: str
    result: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class ResponseEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    execution_id: UUID | None
    rollback_id: UUID | None
    evidence_id: UUID | None
    evidence_type: str
    sha256: str
    reference: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime


class ResponsePlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    plugin_id: UUID
    target_capability: str
    requested_by: str
    reason: str
    risk_level: RiskLevel
    approval_state: ApprovalState
    execution_state: ResponseExecutionState
    rollback_state: RollbackState
    policy_snapshot: dict[str, Any]
    plan: dict[str, Any]
    parameters: dict[str, Any]
    rollback_parameters: dict[str, Any]
    supports_rollback: bool
    expires_at: datetime
    asset_ids: list[UUID] = Field(default_factory=list)
    approvals: list[ResponseApprovalRead] = Field(default_factory=list)
    executions: list[ResponseExecutionRead] = Field(default_factory=list)
    rollbacks: list[ResponseRollbackRead] = Field(default_factory=list)
    evidence: list[ResponseEvidenceRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ResponsePluginRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    enabled: bool
    permissions: list[str]
    capabilities: list[str]
    supports_approval: bool
    supports_rollback: bool
    health_status: str
    sandbox_compatible: bool
    certified: bool
