"""Worker, lease, sandbox and opaque secret-reference persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Worker(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workers"
    __table_args__ = (UniqueConstraint("name", name="uq_workers_name"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    runtime_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    active_executions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    leases: Mapped[list["WorkerLease"]] = relationship(back_populates="worker")
    executions: Mapped[list["SandboxExecution"]] = relationship(back_populates="worker")


class WorkerLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_leases"
    __table_args__ = (UniqueConstraint("execution_id", name="uq_worker_leases_execution_id"),)

    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fencing_token: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, unique=True, index=True
    )

    worker: Mapped[Worker] = relationship(back_populates="leases")


class SandboxProfileRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sandbox_profiles"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_sandbox_profiles_name_version"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    executions: Mapped[list["SandboxExecution"]] = relationship(back_populates="profile")


class SandboxExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sandbox_executions"
    __table_args__ = (UniqueConstraint("execution_id", name="uq_sandbox_executions_execution_id"),)

    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="RESTRICT"), index=True
    )
    profile_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sandbox_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    plugin_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plugin_version: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    terminated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("worker_leases.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    lease_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recovery_of_execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )

    worker: Mapped[Worker] = relationship(back_populates="executions")
    profile: Mapped[SandboxProfileRecord | None] = relationship(back_populates="executions")


class SecretReferenceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persist only opaque lookup metadata; secret values are prohibited."""

    __tablename__ = "secret_references"
    __table_args__ = (UniqueConstraint("reference", name="uq_secret_references_reference"),)

    reference: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    last_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
