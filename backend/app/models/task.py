"""Task lifecycle persistence model."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform request dispatched to one eligible registered Agent."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_tasks_status",
        ),
    )

    name: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    required_permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    target_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    asset_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    executions: Mapped[list["TaskExecution"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    logs: Mapped[list["TaskLog"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskLog(UUIDPrimaryKeyMixin, Base):
    """Append-only task lifecycle log."""

    __tablename__ = "task_logs"

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    task: Mapped["Task"] = relationship(back_populates="logs")


if TYPE_CHECKING:
    from app.models.task_execution import TaskExecution
