"""Security Assessment Framework API and plugin-neutral contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import FindingConfidence, FindingSeverity, FindingStatus, RiskLevel

ASSESSMENT_CAPABILITIES = frozenset(
    {
        "web.scan",
        "web.dast",
        "web.spider",
        "web.passive_scan",
        "web.active_scan",
        "port.scan",
        "template.scan",
        "host.scan",
        "container.scan",
        "dependency.scan",
        "ssl.scan",
        "header.scan",
        "dns.scan",
    }
)
DEFAULT_ASSESSMENT_CAPABILITIES = ASSESSMENT_CAPABILITIES - {"web.active_scan"}


class AssessmentPolicy(BaseModel):
    """Fail-closed policy enforced by planner and runtime before plugin execution."""

    max_concurrency: int = Field(default=1, ge=1, le=64)
    max_requests: int = Field(default=100, ge=1, le=100_000)
    rate_limit_per_second: float = Field(default=1.0, gt=0, le=1_000)
    scan_depth: int = Field(default=1, ge=0, le=20)
    timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    asset_allowlist: list[UUID] = Field(default_factory=list)
    asset_denylist: list[UUID] = Field(default_factory=list)
    capability_allowlist: list[str] = Field(
        default_factory=lambda: sorted(DEFAULT_ASSESSMENT_CAPABILITIES)
    )

    @field_validator("capability_allowlist")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))
        unsupported = set(normalized) - ASSESSMENT_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported assessment capabilities: {sorted(unsupported)}")
        return normalized

    @model_validator(mode="after")
    def validate_asset_lists(self) -> "AssessmentPolicy":
        overlap = set(self.asset_allowlist) & set(self.asset_denylist)
        if overlap:
            raise ValueError("Asset allowlist and denylist must not overlap")
        return self


class AssessmentTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    asset_id: UUID
    capabilities: list[str] = Field(min_length=1)
    plugin_name: str | None = Field(default=None, min_length=1, max_length=128)
    policy: AssessmentPolicy | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False

    @field_validator("capabilities")
    @classmethod
    def validate_requested_capabilities(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))
        unsupported = set(normalized) - ASSESSMENT_CAPABILITIES
        if unsupported:
            raise ValueError(f"Unsupported assessment capabilities: {sorted(unsupported)}")
        if not normalized:
            raise ValueError("At least one assessment capability is required")
        return normalized


class AssessmentPlan(BaseModel):
    asset_id: UUID
    capabilities: list[str]
    plugin_name: str
    steps: list[str]
    limits: dict[str, int | float]


class AssessmentTaskRead(BaseModel):
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


class RawFinding(BaseModel):
    """Plugin-neutral candidate finding carried only inside AssessmentResult."""

    title: str = Field(min_length=1, max_length=512)
    severity: FindingSeverity
    confidence: FindingConfidence = FindingConfidence.MEDIUM
    description: str = ""
    affected_asset: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    knowledge_ids: list[UUID] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    tool: str | None = Field(default=None, max_length=128)
    rule: str | None = Field(default=None, max_length=256)
    unique_id_from_tool: str | None = Field(default=None, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("references")
    @classmethod
    def normalize_references(cls, value: list[str]) -> list[str]:
        return sorted(dict.fromkeys(item.strip() for item in value if item.strip()))


class AssessmentResult(BaseModel):
    """Only value an AssessmentPlugin may return to the platform runtime."""

    success: bool
    plugin_name: str
    plugin_version: str
    findings: list[RawFinding] = Field(default_factory=list)
    requests_made: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assessment_task_id: UUID
    duplicate_of_id: UUID | None
    fingerprint: str
    title: str
    severity: FindingSeverity
    confidence: FindingConfidence
    description: str
    affected_asset: str
    plugin: str
    tool: str | None
    rule: str | None
    risk_level: RiskLevel
    risk_score: float
    status: FindingStatus
    attributes: dict[str, Any]
    references: list[str] = Field(default_factory=list)
    evidence: list[UUID] = Field(default_factory=list)
    knowledge: list[UUID] = Field(default_factory=list)
    assets: list[UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AssessmentPluginRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    enabled: bool
    permissions: list[str]
    capabilities: list[str] = Field(default_factory=list)


class AssessmentCapabilityRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    risk_level: str
    enabled: bool
    plugin: str


class NucleiAssessmentCreate(BaseModel):
    asset_id: UUID
    templates: list[str] = Field(min_length=1, max_length=100)
    policy: AssessmentPolicy | None = None
    execute: bool = True

    @field_validator("templates")
    @classmethod
    def normalize_templates(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("At least one approved Nuclei template is required")
        return normalized


class ZapPolicy(AssessmentPolicy):
    """ZAP-specific policy; passive-only unless Active Scan is explicitly enabled."""

    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    passive_scan_enabled: bool = True
    active_scan_enabled: bool = False
    spider_enabled: bool = False
    spider_depth: int = Field(default=1, ge=0, le=20)
    max_urls: int = Field(default=100, ge=1, le=100_000)
    max_scan_time_seconds: int = Field(default=300, ge=1, le=86_400)
    scan_policy: str = Field(default="cap-passive-baseline", min_length=1, max_length=128)
    exclude_regexes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scan_modes(self) -> "ZapPolicy":
        if not self.passive_scan_enabled and not self.active_scan_enabled:
            raise ValueError("At least one ZAP scan mode must be enabled")
        if self.active_scan_enabled and "web.active_scan" not in self.capability_allowlist:
            raise ValueError("Active Scan requires web.active_scan in capability allowlist")
        if self.max_scan_time_seconds > self.timeout_seconds:
            raise ValueError("ZAP maximum scan time cannot exceed assessment timeout")
        return self


class ZapAssessmentCreate(BaseModel):
    """Asset-referenced ZAP request; clients cannot supply an arbitrary target."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    policy: ZapPolicy | None = None
    execute: bool = True


class ZapPolicyRead(BaseModel):
    name: str
    passive_scan_enabled: bool
    active_scan_enabled: bool
    spider_enabled: bool
    description: str


class ZapStatusRead(BaseModel):
    healthy: bool
    version: str | None = None
    error: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)


class FindingTransitionCreate(BaseModel):
    status: FindingStatus
    actor: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=4000)


class FindingTransitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_id: UUID
    from_status: FindingStatus
    to_status: FindingStatus
    actor: str
    reason: str | None
    trace_id: str
    created_at: datetime


class AssessmentReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assessment_task_id: UUID
    plugin_id: UUID
    asset_id: UUID
    trace_id: str
    status: str
    summary: dict[str, Any]
    content: dict[str, Any]
    created_at: datetime
    updated_at: datetime
