"""Unified Asset Center persistence models and explicit relationships."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Canonical platform asset and source-of-truth governance metadata."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("asset_type", "canonical_value", name="uq_assets_type_canonical_value"),
    )

    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_value: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    business_unit: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    criticality: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    tags: Mapped[list["AssetTag"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", lazy="selectin"
    )
    knowledge: Mapped[list["Knowledge"]] = relationship(
        secondary="asset_knowledge", viewonly=True, lazy="selectin"
    )


class AssetRelation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Directed, typed relationship between two canonical assets."""

    __tablename__ = "asset_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_asset_id",
            "target_asset_id",
            "relation_type",
            name="uq_asset_relations_source_target_type",
        ),
    )

    source_asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    target_asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AssetTag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized tag attached to one asset."""

    __tablename__ = "asset_tags"
    __table_args__ = (UniqueConstraint("asset_id", "name", name="uq_asset_tags_asset_name"),)

    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    asset: Mapped[Asset] = relationship(back_populates="tags")


class AssetEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit provenance link from immutable Evidence to an Asset."""

    __tablename__ = "asset_evidence"
    __table_args__ = (
        UniqueConstraint("asset_id", "evidence_id", name="uq_asset_evidence_asset_evidence"),
    )

    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )


class AssetReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit provenance link from a generated Report to an Asset."""

    __tablename__ = "asset_reports"
    __table_args__ = (
        UniqueConstraint("asset_id", "report_id", name="uq_asset_reports_asset_report"),
    )

    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    report_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )


if TYPE_CHECKING:
    from app.models.knowledge import Knowledge
