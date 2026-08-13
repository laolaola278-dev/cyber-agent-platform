"""SOAR Playbook definitions, versions, triggers, executions, and step history."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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

PLAYBOOK_EXECUTION_STATES = (
    "'PENDING', 'RUNNING', 'WAITING_APPROVAL', 'SUCCEEDED', 'FAILED', "
    "'COMPENSATING', 'COMPENSATED', 'COMPENSATION_FAILED', 'TIMED_OUT', 'CANCELLED'"
)
PLAYBOOK_STEP_STATES = (
    "'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'COMPENSATING', "
    "'COMPENSATED', 'COMPENSATION_FAILED', 'TIMED_OUT'"
)


class Playbook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playbooks"

    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    versions: Mapped[list["PlaybookVersion"]] = relationship(
        back_populates="playbook", cascade="all, delete-orphan"
    )
    executions: Mapped[list["PlaybookExecution"]] = relationship(back_populates="playbook")


class PlaybookVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playbook_versions"
    __table_args__ = (
        UniqueConstraint("playbook_id", "version", name="uq_playbook_versions_playbook_version"),
    )

    playbook_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    dsl_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    playbook: Mapped[Playbook] = relationship(back_populates="versions")
    triggers: Mapped[list["PlaybookTrigger"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    executions: Mapped[list["PlaybookExecution"]] = relationship(back_populates="version")


class PlaybookTrigger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playbook_triggers"
    __table_args__ = (
        UniqueConstraint(
            "playbook_version_id", "trigger_type", name="uq_playbook_triggers_version_type"
        ),
    )

    playbook_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbook_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    version: Mapped[PlaybookVersion] = relationship(back_populates="triggers")
    executions: Mapped[list["PlaybookExecution"]] = relationship(back_populates="trigger")


class PlaybookExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playbook_executions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({PLAYBOOK_EXECUTION_STATES})", name="playbook_execution_status"
        ),
    )

    playbook_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    playbook_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbook_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trigger_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbook_triggers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, unique=True, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    playbook: Mapped[Playbook] = relationship(back_populates="executions")
    version: Mapped[PlaybookVersion] = relationship(back_populates="executions")
    trigger: Mapped[PlaybookTrigger | None] = relationship(back_populates="executions")
    steps: Mapped[list["PlaybookStepExecution"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )


class PlaybookStepExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playbook_step_executions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({PLAYBOOK_STEP_STATES})", name="playbook_step_execution_status"
        ),
        UniqueConstraint("execution_id", "step_id", name="uq_playbook_step_execution_step"),
    )

    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbook_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capability: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    compensation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    compensation_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[PlaybookExecution] = relationship(back_populates="steps")
