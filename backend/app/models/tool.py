"""Tool Registry persistence models."""

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Tool(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable Tool identity and current enabled state."""

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    config_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    runtime_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ENABLED", index=True, nullable=False)

    versions: Mapped[list["ToolVersion"]] = relationship(
        back_populates="tool",
        cascade="all, delete-orphan",
        order_by="ToolVersion.created_at",
    )


class ToolVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable version manifest for a Tool Adapter definition."""

    __tablename__ = "tool_versions"
    __table_args__ = (UniqueConstraint("tool_id", "version", name="uq_tool_versions_tool_version"),)

    tool_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    tool: Mapped["Tool"] = relationship(back_populates="versions")
