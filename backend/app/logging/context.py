"""Structured logging context and adapter."""

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

trace_id_context: ContextVar[str] = ContextVar("trace_id", default="-")
task_id_context: ContextVar[str] = ContextVar("task_id", default="-")
agent_id_context: ContextVar[str] = ContextVar("agent_id", default="-")


@dataclass(frozen=True, slots=True)
class LogContext:
    trace_id: str
    task_id: UUID | None = None
    agent_id: UUID | None = None


class CorrelationFieldsFilter(logging.Filter):
    """Supply safe correlation defaults for framework and third-party logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        defaults = {
            "trace_id": trace_id_context.get(),
            "task_id": task_id_context.get(),
            "agent_id": agent_id_context.get(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        for name, value in defaults.items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return True


class ContextLogger:
    """Logger facade that always emits required CAP correlation fields."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def log(self, level: int, message: str, *, context: LogContext) -> None:
        self._logger.log(
            level,
            message,
            extra={
                "trace_id": context.trace_id,
                "task_id": str(context.task_id or "-"),
                "agent_id": str(context.agent_id or "-"),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
