"""Task API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import TaskStatus


class TaskCreate(BaseModel):
    """Payload accepted when creating a platform task."""

    name: str = Field(min_length=1, max_length=256)
    task_type: str = Field(min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    target_agent_id: UUID | None = None
    asset_id: UUID | None = None


class TaskRead(BaseModel):
    """Serialized task returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    task_type: str
    status: TaskStatus
    input: dict[str, Any]
    required_permissions: list[str]
    required_capabilities: list[str]
    target_agent_id: UUID | None
    asset_id: UUID | None
    created_at: datetime
    updated_at: datetime
