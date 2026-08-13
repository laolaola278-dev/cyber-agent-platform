"""Response, Approval, Rollback and evidence persistence models."""

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


class ResponsePlugin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_plugins"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_response_plugins_name_version"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supports_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_rollback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    sandbox_compatible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    certified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    operational_documentation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ResponsePolicyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_policies"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_response_policies_name_version"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ResponsePlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_plans"
    __table_args__ = (
        CheckConstraint(
            "approval_state IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', "
            "'EXPIRED', 'EXECUTED', 'ROLLED_BACK')",
            name="ck_response_plans_approval_state",
        ),
        CheckConstraint(
            "execution_state IN ('PLANNED', 'BLOCKED', 'READY', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'VERIFIED')",
            name="ck_response_plans_execution_state",
        ),
        CheckConstraint(
            "rollback_state IN ('NOT_SUPPORTED', 'AVAILABLE', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'VERIFIED')",
            name="ck_response_plans_rollback_state",
        ),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_response_plans_risk_level",
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), index=True
    )
    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plugins.id", ondelete="RESTRICT"), index=True
    )
    target_capability: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    execution_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rollback_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rollback_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    supports_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    incident: Mapped["Incident"] = relationship(lazy="joined")
    plugin: Mapped[ResponsePlugin] = relationship(lazy="joined")
    assets: Mapped[list["ResponsePlanAsset"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )
    approvals: Mapped[list["ResponseApproval"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )
    executions: Mapped[list["ResponseExecution"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )
    rollbacks: Mapped[list["ResponseRollback"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence: Mapped[list["ResponseEvidence"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )


class ResponsePlanAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_plan_assets"
    __table_args__ = (UniqueConstraint("plan_id", "asset_id", name="uq_response_plan_assets_pair"),)

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plans.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    plan: Mapped[ResponsePlan] = relationship(back_populates="assets")


class ResponseApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_approvals"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plans.id", ondelete="CASCADE"), index=True
    )
    approver: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    approval_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan: Mapped[ResponsePlan] = relationship(back_populates="approvals")


class ResponseExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_executions"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plans.id", ondelete="CASCADE"), index=True
    )
    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plugins.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rollback_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[ResponsePlan] = relationship(back_populates="executions")


class ResponseRollback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_rollbacks"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plans.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_executions.id", ondelete="RESTRICT"), index=True
    )
    actor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[ResponsePlan] = relationship(back_populates="rollbacks")


class ResponseEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "response_evidence"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("response_plans.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("response_executions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    rollback_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("response_rollbacks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    evidence_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    plan: Mapped[ResponsePlan] = relationship(back_populates="evidence")


from app.models.incident import Incident  # noqa: E402
