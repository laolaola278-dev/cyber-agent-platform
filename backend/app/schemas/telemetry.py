"""Telemetry and broker-neutral stream API contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BackpressureAction(StrEnum):
    DROP = "DROP"
    RETRY = "RETRY"
    PAUSE = "PAUSE"
    REJECT = "REJECT"


class TelemetryTaskStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TelemetryPolicy(BaseModel):
    """Fail-closed limits for source-neutral telemetry ingestion."""

    allowed_plugins: list[str] = Field(default_factory=lambda: ["synthetic-telemetry"])
    allowed_streams: list[str] = Field(default_factory=lambda: ["synthetic"])
    timeout_seconds: int = Field(default=30, ge=1, le=86_400)
    max_records: int = Field(default=1_000, ge=1, le=100_000)
    max_record_size_bytes: int = Field(default=262_144, ge=1, le=10_000_000)
    batch_size: int = Field(default=100, ge=1, le=10_000)
    window_seconds: int = Field(default=60, ge=1, le=86_400)
    queue_capacity: int = Field(default=1_000, ge=1, le=100_000)
    backpressure_action: BackpressureAction = BackpressureAction.REJECT
    retry_attempts: int = Field(default=3, ge=0, le=100)
    pause_seconds: float = Field(default=0.05, ge=0, le=60)

    @field_validator("allowed_plugins", "allowed_streams")
    @classmethod
    def normalize_identities(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip().casefold() for item in value if item.strip()))

    @model_validator(mode="after")
    def require_allowlists(self) -> "TelemetryPolicy":
        if not self.allowed_plugins or not self.allowed_streams:
            raise ValueError("Telemetry plugin and stream allowlists must not be empty")
        return self


class TelemetryRecord(BaseModel):
    """Stable transport record; it is not a SecurityEvent."""

    id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=256)
    timestamp: datetime
    stream: str = Field(min_length=1, max_length=256)
    offset: int = Field(ge=0)
    sequence: int = Field(ge=0)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source", "stream")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Telemetry source and stream must not be blank")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TelemetryTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    plugin_name: str = Field(default="synthetic-telemetry", min_length=1, max_length=128)
    stream: str = Field(default="synthetic", min_length=1, max_length=256)
    partition: str = Field(default="0", min_length=1, max_length=128)
    consumer: str = Field(default="cap-default", min_length=1, max_length=128)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    policy: TelemetryPolicy | None = None
    execute: bool = True

    @field_validator("plugin_name", "stream", "partition", "consumer")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Telemetry identity must not be blank")
        return normalized


class TelemetryPlan(BaseModel):
    plugin_name: str
    stream: str
    partition: str
    consumer: str
    steps: list[str]
    limits: dict[str, int | float | str]


class TelemetryExecutionResult(BaseModel):
    plugin_name: str
    plugin_version: str
    records: list[TelemetryRecord]
    received_count: int = Field(ge=0)
    published_count: int = Field(ge=0)
    dropped_count: int = Field(default=0, ge=0)


class TelemetryTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    pipeline_id: UUID
    plugin_name: str
    status: TelemetryTaskStatus
    stream: str
    partition: str
    consumer: str
    policy: dict[str, Any]
    plan: dict[str, Any]
    result_summary: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class TelemetryCheckpointRead(BaseModel):
    """Provider-neutral checkpoint view without persistence-specific fields."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    stream: str
    partition: str
    consumer: str
    offset: int
    sequence: int
    checksum: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    committed_at: datetime


class TelemetryRuntimeStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    worker_id: str
    pipeline_id: UUID | None
    status: str
    stream: str | None
    partition: str | None
    consumer: str | None
    current_offset: int | None
    lag: int
    queue_depth: int
    backpressure_action: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    heartbeat_at: datetime
    created_at: datetime
    updated_at: datetime


class TelemetryRuntimeRead(BaseModel):
    workers: list[TelemetryRuntimeStateRead]
    queue_capacity: int
    checkpoint_provider: str
    plugin_count: int
    capabilities: list[str]


class TelemetryReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: str = Field(min_length=1, max_length=256)
    partition: str = Field(default="0", min_length=1, max_length=128)
    consumer: str = Field(default="cap-default", min_length=1, max_length=128)
    from_offset: int | None = Field(default=None, ge=0)
    to_offset: int | None = Field(default=None, ge=0)
    window_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @field_validator("stream", "partition", "consumer")
    @classmethod
    def normalize_replay_identity(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Replay identity must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_range(self) -> "TelemetryReplayRequest":
        if self.to_offset is not None and self.from_offset is not None:
            if self.to_offset < self.from_offset:
                raise ValueError("Replay to_offset must be greater than or equal to from_offset")
        return self


class TelemetryReplayRead(BaseModel):
    stream: str
    partition: str
    consumer: str
    from_offset: int
    to_offset: int | None
    window_seconds: int | None
    records: list[TelemetryRecord]
    checkpoint_unchanged: bool = True
