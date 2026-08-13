"""Unified Knowledge Center persistence and provenance models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configured provenance source owned by one provider implementation."""

    __tablename__ = "knowledge_sources"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Knowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable source-scoped knowledge identity and latest materialized projection."""

    __tablename__ = "knowledge"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "knowledge_type",
            "external_id",
            name="uq_knowledge_source_type_external_id",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="RESTRICT"), index=True
    )
    knowledge_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    current_version: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    current_content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    references: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source: Mapped[KnowledgeSource] = relationship(lazy="joined")
    versions: Mapped[list["KnowledgeVersion"]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan", lazy="selectin"
    )


class KnowledgeVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable normalized snapshot retained for every imported source revision."""

    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_id", "version", "content_hash", name="uq_knowledge_version_snapshot"
        ),
    )

    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    knowledge: Mapped[Knowledge] = relationship(back_populates="versions")


class KnowledgeRelation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Directed typed edge between stable knowledge identities."""

    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_knowledge_id",
            "target_knowledge_id",
            "relation_type",
            name="uq_knowledge_relations_source_target_type",
        ),
    )

    source_knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    target_knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AssetKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "asset_knowledge"
    __table_args__ = (
        UniqueConstraint("asset_id", "knowledge_id", name="uq_asset_knowledge_asset_knowledge"),
    )

    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), index=True
    )


class EvidenceKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_knowledge"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id", "knowledge_id", name="uq_evidence_knowledge_evidence_knowledge"
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), index=True
    )


class ReportKnowledge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_knowledge"
    __table_args__ = (
        UniqueConstraint("report_id", "knowledge_id", name="uq_report_knowledge_report_knowledge"),
    )

    report_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge.id", ondelete="RESTRICT"), index=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), index=True
    )
