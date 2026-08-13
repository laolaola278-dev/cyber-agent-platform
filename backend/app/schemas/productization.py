"""Read-only Phase 21 productization API contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DashboardCounts(BaseModel):
    assets: int
    incidents: int
    security_events: int
    findings: int


class DashboardExecutionSummary(BaseModel):
    total: int
    succeeded: int
    failed: int
    waiting_approval: int = 0
    success_rate: float


class WorkerSummary(BaseModel):
    total: int
    healthy: int
    active_executions: int
    capacity: int
    utilization: float


class PluginSummary(BaseModel):
    total: int
    healthy: int
    enabled: int


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    counts: DashboardCounts
    playbooks: DashboardExecutionSummary
    workers: WorkerSummary
    plugins: PluginSummary
    responses: DashboardExecutionSummary
    notifications: DashboardExecutionSummary


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    operator: str
    action: str
    resource: str
    details: dict[str, Any]
    trace_id: str
    result: dict[str, Any] | None
    error: str | None
    timestamp: datetime


class AuditPageRead(BaseModel):
    items: list[AuditEventRead]
    page: int
    page_size: int
    total: int


class PluginInventoryItem(BaseModel):
    id: UUID
    domain: str
    name: str
    version: str
    enabled: bool
    health_status: str
    capabilities: list[str]
    certified: bool
    sandbox_compatible: bool


class ApprovalCenterItem(BaseModel):
    plan_id: UUID
    incident_id: UUID
    capability: str
    requested_by: str
    risk_level: str
    approval_state: str
    execution_state: str
    rollback_state: str
    expires_at: datetime
    approver: str | None
    decision: str | None
    comment: str | None
    decided_at: datetime | None


class SettingsRead(BaseModel):
    app_name: str
    app_version: str
    api_prefix: str
    debug: bool
    log_level: str
    cors_origins: list[str]
    database_driver: str
    redis_configured: bool
    rbac_enabled: bool
    identity_header: str
    trusted_proxy_header: str
    metrics_enabled: bool
    tracing_enabled: bool
    otel_service_name: str
    otel_exporter_endpoint_configured: bool
