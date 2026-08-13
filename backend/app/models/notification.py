"""Notification, Template, Ticket, Execution and Evidence persistence models."""

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


class NotificationPlugin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_plugins"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_notification_plugins_name_version"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supports_verification: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    sandbox_compatible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    certified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    operational_documentation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NotificationTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_notification_templates_name_version"),
        CheckConstraint(
            "format IN ('MARKDOWN', 'HTML', 'JSON', 'TEXT')",
            name="ck_notification_templates_format",
        ),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class NotificationPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'SUPPRESSED', 'RUNNING', 'SENT', 'VERIFIED', 'FAILED')",
            name="ck_notification_plans_status",
        ),
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_notification_plans_severity",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_notification_plans_priority",
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), index=True
    )
    response_plan_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("response_plans.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_plugins.id", ondelete="RESTRICT"),
        index=True,
    )
    template_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    recipient_group: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    recipients: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    deduplication_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    plugin: Mapped[NotificationPlugin] = relationship(lazy="joined")
    template: Mapped[NotificationTemplate] = relationship(lazy="joined")
    executions: Mapped[list["NotificationExecution"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence: Mapped[list["NotificationEvidence"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )


class NotificationExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_executions"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notification_plans.id", ondelete="CASCADE"), index=True
    )
    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_plugins.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[NotificationPlan] = relationship(back_populates="executions")


class NotificationEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_evidence"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notification_plans.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_executions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    plan: Mapped[NotificationPlan] = relationship(back_populates="evidence")


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_tickets_priority",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
            name="ck_tickets_status",
        ),
    )

    incident_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_reference: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
