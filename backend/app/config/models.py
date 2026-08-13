"""Strongly typed YAML-backed platform configuration models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import AgentStatus, HealthStatus, ToolStatus
from app.schemas.assessment import AssessmentPolicy
from app.schemas.detection import DetectionPolicy
from app.schemas.incident import IncidentPolicy
from app.schemas.notification import NotificationPolicy
from app.schemas.response import ResponsePolicy


class HeartbeatConfig(BaseModel):
    """Agent heartbeat thresholds and default health state."""

    stale_after_seconds: int = Field(gt=0)
    default_health_status: HealthStatus


class RegistrationConfig(BaseModel):
    """Registry defaults applied to newly registered definitions."""

    default_agent_status: AgentStatus
    default_tool_status: ToolStatus
    require_unique_name_version: bool = True


class RegistryConfig(BaseModel):
    """Registry and heartbeat configuration."""

    heartbeat: HeartbeatConfig
    registration: RegistrationConfig


class DispatcherConfig(BaseModel):
    """Task dispatcher policy selected at runtime through dependency injection."""

    eligible_agent_statuses: list[AgentStatus]
    scheduling_strategy: str = Field(min_length=1)
    task_timeout_seconds: int = Field(gt=0)
    queue_on_dispatch: bool = True
    fail_when_no_agent: bool = True

    @field_validator("eligible_agent_statuses")
    @classmethod
    def ensure_statuses_are_not_empty(cls, value: list[AgentStatus]) -> list[AgentStatus]:
        if not value:
            raise ValueError("At least one eligible Agent status is required")
        return value


class SecurityConfig(BaseModel):
    """Orchestrator security defaults."""

    deny_high_risk_without_approval: bool = True


class OrchestratorConfig(BaseModel):
    """Dispatcher and security configuration."""

    dispatcher: DispatcherConfig
    security: SecurityConfig


class RuntimeConfig(BaseModel):
    """Runtime filesystem and first-Agent execution policy."""

    manifest_directory: str = "../../agents"
    evidence_directory: str = "./data/evidence"
    allowed_task_types: list[str] = Field(default_factory=lambda: ["data-acquisition"])
    playwright: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettings(BaseModel):
    """Top-level runtime YAML document."""

    runtime: RuntimeConfig


class NucleiTemplateConfig(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_requests: int = Field(default=1, ge=1, le=100_000)


class NucleiConfig(BaseModel):
    executable: str = "nuclei"
    template_root: str = "../../tools/nuclei/templates"
    max_output_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    approved_templates: dict[str, NucleiTemplateConfig] = Field(default_factory=dict)


class ZapSandboxConfig(BaseModel):
    cpu_limit: float = Field(default=1.0, gt=0, le=64)
    memory_limit_mb: int = Field(default=1024, ge=128, le=131_072)
    timeout_seconds: int = Field(default=600, ge=1, le=86_400)
    network_policy: str = "asset-scope-only"


class ZapConfig(BaseModel):
    api_url: str = "http://127.0.0.1:8080"
    api_key_secret_reference: str = "zap-api-key"
    allowed_scan_policies: list[str] = Field(
        default_factory=lambda: ["cap-passive-baseline", "cap-active-controlled"]
    )
    sandbox: ZapSandboxConfig = Field(default_factory=ZapSandboxConfig)


class AssessmentSettings(BaseModel):
    """Top-level Assessment policy and governed tool configuration."""

    policy: AssessmentPolicy = Field(default_factory=AssessmentPolicy)
    nuclei: NucleiConfig = Field(default_factory=NucleiConfig)
    zap: ZapConfig = Field(default_factory=ZapConfig)


class SuricataDataSourceConfig(BaseModel):
    path: str = Field(min_length=1)
    fixture: bool = False


class SuricataSandboxConfig(BaseModel):
    cpu_limit: float = Field(default=0.5, gt=0, le=64)
    memory_limit_mb: int = Field(default=256, ge=64, le=131_072)
    timeout_seconds: int = Field(default=30, ge=1, le=86_400)
    max_input_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    max_records: int = Field(default=1_000, ge=1, le=100_000)
    allowed_event_types: list[str] = Field(
        default_factory=lambda: ["alert", "flow", "stats", "dns", "http", "tls", "fileinfo"]
    )
    filesystem_policy: str = "configured-read-only-sources"
    network_policy: str = "none"


class SuricataConfig(BaseModel):
    version: str = "8.0.6"
    data_sources: dict[str, SuricataDataSourceConfig] = Field(default_factory=dict)
    sandbox: SuricataSandboxConfig = Field(default_factory=SuricataSandboxConfig)


class ZeekDataSourceConfig(BaseModel):
    path: str = Field(min_length=1)
    fixture: bool = False


class ZeekSandboxConfig(BaseModel):
    cpu_limit: float = Field(default=0.5, gt=0, le=64)
    memory_limit_mb: int = Field(default=256, ge=64, le=131_072)
    timeout_seconds: int = Field(default=30, ge=1, le=86_400)
    max_input_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    max_records: int = Field(default=1_000, ge=1, le=100_000)
    allowed_logs: list[str] = Field(
        default_factory=lambda: ["conn", "dns", "http", "ssl", "files", "notice"]
    )
    filesystem_policy: str = "configured-read-only-sources"
    network_policy: str = "none"


class ZeekConfig(BaseModel):
    version: str = "7.0.0"
    data_sources: dict[str, ZeekDataSourceConfig] = Field(default_factory=dict)
    sandbox: ZeekSandboxConfig = Field(default_factory=ZeekSandboxConfig)


class DetectionSettings(BaseModel):
    """Top-level Detection policy and governed Suricata/Zeek integrations."""

    policy: DetectionPolicy = Field(default_factory=DetectionPolicy)
    suricata: SuricataConfig = Field(default_factory=SuricataConfig)
    zeek: ZeekConfig = Field(default_factory=ZeekConfig)


class TelemetryConfig(BaseModel):
    allowed_plugins: list[str] = Field(default_factory=lambda: ["synthetic-telemetry"])
    allowed_streams: list[str] = Field(default_factory=lambda: ["synthetic"])
    timeout_seconds: int = Field(default=30, ge=1, le=86_400)
    max_records: int = Field(default=1_000, ge=1, le=100_000)
    max_record_size_bytes: int = Field(default=262_144, ge=1, le=10_000_000)
    batch_size: int = Field(default=100, ge=1, le=10_000)
    window_seconds: int = Field(default=60, ge=1, le=86_400)
    queue_capacity: int = Field(default=1_000, ge=1, le=100_000)
    backpressure_action: str = "REJECT"
    retry_attempts: int = Field(default=3, ge=0, le=100)
    pause_seconds: float = Field(default=0.05, ge=0, le=60)
    checkpoint_provider: str = "memory"


class TelemetrySettings(BaseModel):
    """Top-level source-neutral telemetry policy."""

    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)


class IncidentSettings(BaseModel):
    """Top-level Incident and Case Management policy defaults."""

    policy: IncidentPolicy = Field(default_factory=IncidentPolicy)


class ResponseSettings(BaseModel):
    """Top-level Response, Approval and Rollback policy defaults."""

    policy: ResponsePolicy = Field(default_factory=ResponsePolicy)


class NotificationSettings(BaseModel):
    """Top-level Notification, Routing, Template and Ticket policy defaults."""

    policy: NotificationPolicy = Field(default_factory=NotificationPolicy)


class LoggingConfig(BaseModel):
    """Validated Python logging configuration loaded from YAML."""

    model_config = ConfigDict(extra="allow")

    version: int = Field(ge=1)
    disable_existing_loggers: bool = False
    formatters: dict[str, dict[str, Any]]
    handlers: dict[str, dict[str, Any]]
    root: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary accepted by ``logging.config.dictConfig``."""

        return self.model_dump(mode="python", exclude_none=True)
