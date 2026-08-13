"""Version-one CAP Agent SDK data contracts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    trace_id: str
    task_id: UUID
    agent_id: UUID
    actor: str
    approved_actions: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_type: str
    input: dict[str, Any] = Field(default_factory=dict)
    required_permissions: set[str] = Field(default_factory=set)


class AgentResult(BaseModel):
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class TaskResponse(BaseModel):
    task_id: UUID
    accepted: bool
    status: str
    message: str | None = None


class HealthCheck(BaseModel):
    healthy: bool
    status: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)
