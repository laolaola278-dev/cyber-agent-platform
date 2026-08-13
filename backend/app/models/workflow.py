"""Workflow definition, instance, checkpoint, and execution persistence models."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
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

WORKFLOW_STATES = "'PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED'"
STEP_STATES = "'PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED', 'SKIPPED'"


class WorkflowDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned, declarative DAG definition compiled from trusted YAML input."""

    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_workflow_definitions_name_version"),
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), default="1.0.0", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    instances: Mapped[list["WorkflowInstance"]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class WorkflowInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable state and checkpoint cursor for one workflow run."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({WORKFLOW_STATES})",
            name="workflow_instance_status",
        ),
    )

    definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    current_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    definition: Mapped["WorkflowDefinition"] = relationship(back_populates="instances")
    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )
    executions: Mapped[list["WorkflowExecution"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class WorkflowStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Latest durable checkpoint for one node in a workflow instance."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint(f"status IN ({STEP_STATES})", name="workflow_step_status"),
        UniqueConstraint("instance_id", "node_id", name="uq_workflow_steps_instance_node"),
    )

    instance_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capability: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    instance: Mapped["WorkflowInstance"] = relationship(back_populates="steps")
    executions: Mapped[list["WorkflowExecution"]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )


class WorkflowExecution(UUIDPrimaryKeyMixin, Base):
    """Append-only history for each workflow node execution attempt."""

    __tablename__ = "workflow_executions"
    __table_args__ = (
        CheckConstraint(f"status IN ({STEP_STATES})", name="workflow_execution_status"),
    )

    instance_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    instance: Mapped["WorkflowInstance"] = relationship(back_populates="executions")
    step: Mapped["WorkflowStep"] = relationship(back_populates="executions")


if TYPE_CHECKING:
    pass
