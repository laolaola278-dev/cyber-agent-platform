"""Narrow execution context supplied to Agent implementations."""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.events import EventPublisher
from app.models import Task
from app.runtime.services import ServiceProvider


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """All platform services an Agent may use during one execution.

    Database sessions, repositories, and the Dispatcher are intentionally absent.
    """

    task: Task
    trace_id: str
    logger: logging.Logger
    configuration: dict[str, Any]
    publisher: EventPublisher
    services: ServiceProvider
    agent_id: UUID
