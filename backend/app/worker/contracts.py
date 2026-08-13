"""Worker identity, heartbeat, lease and execution contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.sandbox.profile import SandboxProfile


class WorkerStatus(StrEnum):
    REGISTERED = "REGISTERED"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"
    UNHEALTHY = "UNHEALTHY"
    DEAD = "DEAD"


class LeaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class SandboxExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    RECOVERED = "RECOVERED"


class WorkerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=128)
    runtime_version: str = Field(min_length=1, max_length=64)
    capabilities: frozenset[str] = frozenset()
    status: WorkerStatus = WorkerStatus.REGISTERED
    max_concurrency: int = Field(default=1, ge=1, le=1024)
    active_executions: int = Field(default=0, ge=0)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")
    state_version: int = Field(default=1, ge=1)


class WorkerHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worker_id: UUID
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: WorkerStatus
    active_executions: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    worker_id: UUID
    execution_id: UUID
    owner: str = Field(min_length=1, max_length=256)
    status: LeaseStatus = LeaseStatus.ACTIVE
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    renewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    version: int = Field(default=1, ge=1)
    fencing_token: UUID = Field(default_factory=uuid4)

    @classmethod
    def acquire(
        cls,
        *,
        worker_id: UUID,
        execution_id: UUID,
        owner: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        acquired = now or datetime.now(UTC)
        return cls(
            worker_id=worker_id,
            execution_id=execution_id,
            owner=owner,
            acquired_at=acquired,
            renewed_at=acquired,
            expires_at=acquired + timedelta(seconds=ttl_seconds),
        )


class PluginExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_name: str = Field(min_length=1, max_length=128)
    plugin_version: str = Field(min_length=1, max_length=64)
    capability: str = Field(min_length=1, max_length=128)
    operation: str = Field(default="execute", min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    sandbox_profile: SandboxProfile
    secret_references: tuple[str, ...] = ()
    retry_limit: int = Field(default=0, ge=0, le=10)


class WorkerExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    worker_id: UUID
    sandbox_execution_id: UUID
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)
    timed_out: bool = False
    attempts: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
