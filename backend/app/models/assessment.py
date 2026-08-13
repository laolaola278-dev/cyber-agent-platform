"""Security Assessment Framework persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AssessmentPlugin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registered assessment plugin definition, never the executable instance itself."""

    __tablename__ = "assessment_plugins"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_assessment_plugins_name_version"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    capabilities: Mapped[list["AssessmentCapability"]] = relationship(
        back_populates="plugin", cascade="all, delete-orphan", lazy="selectin"
    )


class AssessmentCapability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Assessment-domain projection of one platform capability supplied by a plugin."""

    __tablename__ = "assessment_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "plugin_id",
            "capability_id",
            name="uq_assessment_capabilities_plugin_capability",
        ),
    )

    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assessment_plugins.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capabilities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    plugin: Mapped[AssessmentPlugin] = relationship(back_populates="capabilities")
    capability: Mapped["Capability"] = relationship(lazy="joined")


class AssessmentTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Assessment-specific extension of the platform Task lifecycle."""

    __tablename__ = "assessment_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_assessment_tasks_status",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    plugin_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assessment_plugins.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", nullable=False, index=True)
    requested_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["Task"] = relationship(lazy="joined")
    plugin: Mapped[AssessmentPlugin | None] = relationship(lazy="joined")
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="assessment_task", cascade="all, delete-orphan", lazy="selectin"
    )


class Finding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized, tool-neutral security finding with a stable deduplication fingerprint."""

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_findings_severity",
        ),
        CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_findings_confidence",
        ),
        CheckConstraint(
            "status IN ('NEW', 'TRIAGED', 'CONFIRMED', 'FALSE_POSITIVE', "
            "'ACCEPTED_RISK', 'FIXED', 'REOPENED')",
            name="ck_findings_status",
        ),
    )

    assessment_task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assessment_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("findings.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    affected_asset: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    plugin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    assessment_task: Mapped[AssessmentTask] = relationship(back_populates="findings")
    references: Mapped[list["FindingReference"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence_links: Mapped[list["FindingEvidence"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    knowledge_links: Mapped[list["FindingKnowledge"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    asset_links: Mapped[list["FindingAsset"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    history: Mapped[list["FindingHistory"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    comments: Mapped[list["FindingComment"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )
    transitions: Mapped[list["FindingTransition"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )


class FindingHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only snapshot of meaningful Finding changes."""

    __tablename__ = "finding_history"

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="history")


class FindingComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reserved human collaboration record for Finding triage."""

    __tablename__ = "finding_comments"

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="comments")


class FindingTransition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable accepted state transition with optional reason."""

    __tablename__ = "finding_transitions"

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    finding: Mapped[Finding] = relationship(back_populates="transitions")


class AssessmentReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable platform-owned assessment aggregation, never written by a Plugin."""

    __tablename__ = "assessment_reports"

    assessment_task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assessment_tasks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assessment_plugins.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    assessment_task: Mapped[AssessmentTask] = relationship(lazy="joined")
    plugin: Mapped[AssessmentPlugin] = relationship(lazy="joined")
    asset: Mapped["Asset"] = relationship(lazy="joined")


class FindingReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "finding_references"
    __table_args__ = (
        UniqueConstraint("finding_id", "url", name="uq_finding_references_finding_url"),
    )

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    finding: Mapped[Finding] = relationship(back_populates="references")


class FindingEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "finding_evidence"
    __table_args__ = (
        UniqueConstraint("finding_id", "evidence_id", name="uq_finding_evidence_pair"),
    )

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    finding: Mapped[Finding] = relationship(back_populates="evidence_links")
    evidence: Mapped["Evidence"] = relationship(lazy="joined")


class FindingKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "finding_knowledge"
    __table_args__ = (
        UniqueConstraint("finding_id", "knowledge_id", name="uq_finding_knowledge_pair"),
    )

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    finding: Mapped[Finding] = relationship(back_populates="knowledge_links")
    knowledge: Mapped["Knowledge"] = relationship(lazy="joined")
    knowledge_version: Mapped["KnowledgeVersion"] = relationship(lazy="joined")


class FindingAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "finding_assets"
    __table_args__ = (UniqueConstraint("finding_id", "asset_id", name="uq_finding_assets_pair"),)

    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    finding: Mapped[Finding] = relationship(back_populates="asset_links")
    asset: Mapped["Asset"] = relationship(lazy="joined")


from app.models.asset import Asset  # noqa: E402
from app.models.capability import Capability  # noqa: E402
from app.models.knowledge import Knowledge, KnowledgeVersion  # noqa: E402
from app.models.runtime import Evidence  # noqa: E402
from app.models.task import Task  # noqa: E402
