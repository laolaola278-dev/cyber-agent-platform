"""Telemetry and stream control-plane persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TelemetryPipeline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telemetry_pipelines"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_telemetry_pipelines_name_version"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    receivers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    processors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    exporters: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TelemetryTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telemetry_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_telemetry_tasks_status",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    pipeline_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("telemetry_pipelines.id", ondelete="RESTRICT"), index=True
    )
    plugin_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    partition: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    consumer: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["Task"] = relationship(lazy="joined")
    pipeline: Mapped[TelemetryPipeline] = relationship(lazy="joined")


class TelemetryCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telemetry_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "provider", "stream", "partition", "consumer", name="uq_telemetry_checkpoint_cursor"
        ),
        CheckConstraint('"offset" >= 0', name="ck_telemetry_checkpoints_offset"),
        CheckConstraint("sequence >= 0", name="ck_telemetry_checkpoints_sequence"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    partition: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    consumer: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    offset: Mapped[int] = mapped_column(sa.quoted_name("offset", True), Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class TelemetryRuntimeState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telemetry_runtime_states"
    __table_args__ = (
        UniqueConstraint("worker_id", name="uq_telemetry_runtime_states_worker_id"),
        CheckConstraint(
            "status IN ('IDLE', 'RUNNING', 'PAUSED', 'FAILED', 'STOPPED')",
            name="ck_telemetry_runtime_states_status",
        ),
        CheckConstraint("lag >= 0", name="ck_telemetry_runtime_states_lag"),
        CheckConstraint("queue_depth >= 0", name="ck_telemetry_runtime_states_queue_depth"),
    )

    worker_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pipeline_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_pipelines.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="IDLE", nullable=False, index=True)
    stream: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    partition: Mapped[str | None] = mapped_column(String(128), nullable=True)
    consumer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lag: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queue_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    backpressure_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


from app.models.task import Task  # noqa: E402
