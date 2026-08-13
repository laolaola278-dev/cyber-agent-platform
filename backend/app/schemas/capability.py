"""Capability Registry API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CapabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    risk_level: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
