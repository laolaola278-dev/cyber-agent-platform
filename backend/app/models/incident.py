"""Incident and Investigation Case persistence models."""

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


class Incident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Platform-owned incident aggregate; source facts stay in their bounded contexts."""

    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_incidents_severity",
        ),
        CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_incidents_confidence",
        ),
        CheckConstraint(
            "priority IN ('P1', 'P2', 'P3', 'P4')",
            name="ck_incidents_priority",
        ),
        CheckConstraint(
            "status IN ('NEW', 'TRIAGED', 'INVESTIGATING', 'CONTAINED', "
            "'RESOLVED', 'CLOSED', 'REOPENED')",
            name="ck_incidents_status",
        ),
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    assignee: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    queue: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    classification: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    risk: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    correlation_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sla_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    duplicate_of: Mapped["Incident | None"] = relationship(remote_side="Incident.id", lazy="joined")
    timelines: Mapped[list["IncidentTimeline"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="IncidentTimeline.created_at",
    )
    artifacts: Mapped[list["IncidentArtifact"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="IncidentArtifact.created_at",
    )
    cases: Mapped[list["InvestigationCase"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InvestigationCase.created_at",
    )
    findings: Mapped[list["IncidentFinding"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", lazy="selectin"
    )
    knowledge: Mapped[list["IncidentKnowledge"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", lazy="selectin"
    )
    assets: Mapped[list["IncidentAsset"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", lazy="selectin"
    )


class IncidentTimeline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only incident activity and state history."""

    __tablename__ = "incident_timelines"

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="timelines")


class IncidentArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Typed link to a platform object or bounded external indicator value."""

    __tablename__ = "incident_artifacts"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('ASSET', 'EVIDENCE', 'FINDING', 'SECURITY_EVENT', "
            "'KNOWLEDGE', 'REPORT', 'URL', 'HASH', 'IP', 'DOMAIN')",
            name="ck_incident_artifacts_type",
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reference_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="artifacts")


class IncidentFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit Finding-to-Incident association; Finding lifecycle remains independent."""

    __tablename__ = "incident_findings"
    __table_args__ = (
        UniqueConstraint("incident_id", "finding_id", name="uq_incident_findings_pair"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("findings.id", ondelete="RESTRICT"), index=True
    )
    relation: Mapped[str] = mapped_column(String(64), default="source", nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="findings")


class IncidentEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit SecurityEvent-to-Incident association."""

    __tablename__ = "incident_events"
    __table_args__ = (UniqueConstraint("incident_id", "event_id", name="uq_incident_events_pair"),)

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security_events.id", ondelete="RESTRICT"), index=True
    )
    relation: Mapped[str] = mapped_column(String(64), default="correlated", nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="events")


class IncidentKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Version-aware knowledge context associated with an Incident."""

    __tablename__ = "incident_knowledge"
    __table_args__ = (
        UniqueConstraint("incident_id", "knowledge_id", name="uq_incident_knowledge_pair"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), index=True
    )

    incident: Mapped[Incident] = relationship(back_populates="knowledge")


class IncidentAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit Asset-to-Incident association."""

    __tablename__ = "incident_assets"
    __table_args__ = (UniqueConstraint("incident_id", "asset_id", name="uq_incident_assets_pair"),)

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )

    incident: Mapped[Incident] = relationship(back_populates="assets")


class InvestigationCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Independent investigation workspace; one Incident may have multiple Cases."""

    __tablename__ = "investigation_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CLOSED')",
            name="ck_investigation_cases_status",
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False, index=True)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    assignee: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    queue: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="cases")
    comments: Mapped[list["CaseComment"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CaseComment.created_at",
    )


class CaseComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable collaboration note within an InvestigationCase."""

    __tablename__ = "case_comments"

    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[InvestigationCase] = relationship(back_populates="comments")
