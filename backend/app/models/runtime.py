"""Runtime, evidence, and report persistence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentRuntime(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One managed in-process runtime for a registered Agent."""

    __tablename__ = "agent_runtimes"

    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="OFFLINE", nullable=False, index=True)
    manifest_path: Mapped[str] = mapped_column(String(512), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent: Mapped["Agent"] = relationship()


class Evidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable capture metadata produced during an Agent task."""

    __tablename__ = "evidence"

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str] = mapped_column(
        String(32), default="HTML", nullable=False, index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(255), default="text/html; charset=utf-8", nullable=False
    )
    object_storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    html_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    assets: Mapped[list["Asset"]] = relationship(
        secondary="asset_evidence", viewonly=True, lazy="selectin"
    )
    knowledge: Mapped[list["Knowledge"]] = relationship(
        secondary="evidence_knowledge", viewonly=True, lazy="selectin"
    )


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized JSON and Markdown report for one completed task."""

    __tablename__ = "reports"

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    json_content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    assets: Mapped[list["Asset"]] = relationship(
        secondary="asset_reports", viewonly=True, lazy="selectin"
    )
    knowledge: Mapped[list["Knowledge"]] = relationship(
        secondary="report_knowledge", viewonly=True, lazy="selectin"
    )


from app.models.agent import Agent  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.models.knowledge import Knowledge  # noqa: E402
