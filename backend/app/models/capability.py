"""Capability Registry persistence models."""

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Capability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable platform capability used for Agent discovery and governance."""

    __tablename__ = "capabilities"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(32), default="LOW", nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    agent_links: Mapped[list["AgentCapability"]] = relationship(
        back_populates="capability", cascade="all, delete-orphan"
    )


class AgentCapability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Version-independent declaration that an Agent provides one capability."""

    __tablename__ = "agent_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "capability_id", name="uq_agent_capabilities_agent_capability"
        ),
    )

    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capabilities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    agent: Mapped["Agent"] = relationship(back_populates="capability_links")
    capability: Mapped[Capability] = relationship(back_populates="agent_links")


from app.models.agent import Agent  # noqa: E402
