"""Agent Registry persistence models."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable Agent identity and its current registry state."""

    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ONLINE', 'OFFLINE', 'STARTING', 'STOPPING', 'ERROR')",
            name="ck_agents_status",
        ),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(256), default="system", nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    minimum_runtime_version: Mapped[str] = mapped_column(
        String(64), default="1.0.0", nullable=False
    )
    platform_version: Mapped[str] = mapped_column(String(64), default="0.2.1", nullable=False)
    sdk_version: Mapped[str] = mapped_column(String(64), default="1.0.0", nullable=False)
    runtime: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    network_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    resource_limit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    approval_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OFFLINE", index=True, nullable=False)
    health_status: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", index=True, nullable=False
    )
    heartbeat_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentVersion.created_at",
    )
    heartbeats: Mapped[list["AgentHeartbeat"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    executions: Mapped[list["TaskExecution"]] = relationship(back_populates="agent")
    capability_links: Mapped[list["AgentCapability"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable version manifest of an Agent definition."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    agent: Mapped["Agent"] = relationship(back_populates="versions")


class AgentHeartbeat(UUIDPrimaryKeyMixin, Base):
    """Append-only health heartbeat reported by an Agent runtime."""

    __tablename__ = "agent_heartbeats"

    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    health_status: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    agent: Mapped["Agent"] = relationship(back_populates="heartbeats")


if TYPE_CHECKING:
    from app.models.capability import AgentCapability
    from app.models.task_execution import TaskExecution
