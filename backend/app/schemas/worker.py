"""Worker, Sandbox and aggregate health API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    runtime_version: str
    capabilities: list[str]
    status: str
    max_concurrency: int
    active_executions: int
    registered_at: datetime
    last_heartbeat_at: datetime


class SandboxExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_id: UUID
    worker_id: UUID
    profile_id: UUID | None
    plugin_name: str
    plugin_version: str
    operation: str
    provider: str
    status: str
    result_metadata: dict[str, Any]
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    timed_out: bool
    terminated: bool


class WorkerHealthRead(BaseModel):
    status: str
    workers_total: int = Field(ge=0)
    workers_healthy: int = Field(ge=0)
    workers_stale: int = Field(ge=0)
    leases_expired: int = Field(ge=0)
    sandbox_healthy: bool
    plugin_health: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime
