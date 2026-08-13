"""Runtime and first-agent API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl


class RuntimeStartRequest(BaseModel):
    agent_id: UUID
    task_id: UUID


class RuntimeRestartRequest(BaseModel):
    task_id: UUID


class RuntimeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    status: str
    manifest_path: str
    entrypoint: str
    loaded_at: datetime | None
    started_at: datetime | None
    stopped_at: datetime | None
    last_health: dict[str, Any]
    last_error: str | None


class DataAcquisitionRequest(BaseModel):
    url: HttpUrl
