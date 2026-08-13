"""Zeek Detection Plugin API contracts."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.detection import DetectionTaskRead


class ZeekDetectionCreate(BaseModel):
    """Select a platform-configured Zeek source; arbitrary paths are forbidden."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Zeek JSONL ingestion", min_length=1, max_length=256)
    asset_id: UUID
    data_source_id: str = Field(min_length=1, max_length=128)
    execute: bool = True

    @field_validator("data_source_id")
    @classmethod
    def normalize_source_id(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Zeek data_source_id must not be blank")
        return normalized


class ZeekDetectionRead(DetectionTaskRead):
    pass


class ZeekSourceStatus(BaseModel):
    source_id: str
    available: bool
    fixture: bool


class ZeekStatusRead(BaseModel):
    healthy: bool
    tool: str
    version: str
    input_format: str
    supported_logs: list[str]
    tsv_reserved: bool
    sources: list[ZeekSourceStatus]
    sandbox: dict[str, Any]
