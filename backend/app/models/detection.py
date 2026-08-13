"""Detection Framework persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DetectionPlugin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "detection_plugins"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_detection_plugins_name_version"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    capabilities: Mapped[list["DetectionCapability"]] = relationship(
        back_populates="plugin", cascade="all, delete-orphan", lazy="selectin"
    )


class DetectionCapability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "detection_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "plugin_id", "capability_id", name="uq_detection_capabilities_plugin_capability"
        ),
    )

    plugin_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_plugins.id", ondelete="CASCADE"), index=True
    )
    capability_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("capabilities.id", ondelete="RESTRICT"), index=True
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    plugin: Mapped[DetectionPlugin] = relationship(back_populates="capabilities")
    capability: Mapped["Capability"] = relationship(lazy="joined")


class DetectionTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "detection_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_detection_tasks_status",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    plugin_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("detection_plugins.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    requested_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["Task"] = relationship(lazy="joined")
    plugin: Mapped[DetectionPlugin | None] = relationship(lazy="joined")
    events: Mapped[list["SecurityEvent"]] = relationship(
        back_populates="detection_task", cascade="all, delete-orphan", lazy="selectin"
    )


class SecurityEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized time-bound security fact, deliberately distinct from Finding."""

    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_security_events_severity",
        ),
        CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_security_events_confidence",
        ),
        CheckConstraint(
            "status IN ('NEW', 'CORRELATED', 'TRIAGED', 'IGNORED', 'ARCHIVED')",
            name="ck_security_events_status",
        ),
    )

    detection_task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("detection_tasks.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    plugin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    detection_task: Mapped[DetectionTask] = relationship(back_populates="events")
    references: Mapped[list["EventReference"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    knowledge_links: Mapped[list["EventKnowledge"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence_links: Mapped[list["EventEvidence"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    asset_links: Mapped[list["EventAsset"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )


class EventReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_references"
    __table_args__ = (UniqueConstraint("event_id", "url", name="uq_event_references_pair"),)

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security_events.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[SecurityEvent] = relationship(back_populates="references")


class EventKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_knowledge"
    __table_args__ = (UniqueConstraint("event_id", "knowledge_id", name="uq_event_knowledge_pair"),)

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security_events.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), index=True
    )
    event: Mapped[SecurityEvent] = relationship(back_populates="knowledge_links")
    knowledge: Mapped["Knowledge"] = relationship(lazy="joined")
    knowledge_version: Mapped["KnowledgeVersion"] = relationship(lazy="joined")


class EventEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_evidence"
    __table_args__ = (UniqueConstraint("event_id", "evidence_id", name="uq_event_evidence_pair"),)

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security_events.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    event: Mapped[SecurityEvent] = relationship(back_populates="evidence_links")
    evidence: Mapped["Evidence"] = relationship(lazy="joined")


class EventAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_assets"
    __table_args__ = (UniqueConstraint("event_id", "asset_id", name="uq_event_assets_pair"),)

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security_events.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    event: Mapped[SecurityEvent] = relationship(back_populates="asset_links")
    asset: Mapped["Asset"] = relationship(lazy="joined")


from app.models.asset import Asset  # noqa: E402
from app.models.capability import Capability  # noqa: E402
from app.models.knowledge import Knowledge, KnowledgeVersion  # noqa: E402
from app.models.runtime import Evidence  # noqa: E402
from app.models.task import Task  # noqa: E402
