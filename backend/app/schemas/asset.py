"""Asset Center API schemas and search contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import AssetRelationType, AssetType


class AssetCreate(BaseModel):
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=2048)
    owner: str | None = Field(default=None, max_length=256)
    business_unit: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=64)
    criticality: str | None = Field(default=None, max_length=32)
    risk: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    agent_id: UUID | None = None

    @model_validator(mode="after")
    def validate_agent_reference(self) -> "AssetCreate":
        if (self.asset_type == AssetType.AGENT) != (self.agent_id is not None):
            raise ValueError("AGENT assets require agent_id and other asset types forbid it")
        return self


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    value: str | None = Field(default=None, min_length=1, max_length=2048)
    owner: str | None = Field(default=None, max_length=256)
    business_unit: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=64)
    criticality: str | None = Field(default=None, max_length=32)
    risk: str | None = Field(default=None, max_length=32)
    tags: list[str] | None = Field(default=None, max_length=100)
    capabilities: list[str] | None = Field(default=None, max_length=100)
    properties: dict[str, Any] | None = None


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_type: AssetType
    name: str
    value: str
    canonical_value: str
    owner: str | None
    business_unit: str | None
    environment: str | None
    criticality: str | None
    risk: str | None
    tags: list[str]
    capabilities: list[str]
    properties: dict[str, Any]
    agent_id: UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def serialize_tags(cls, value: object) -> object:
        if isinstance(value, list) and value and not isinstance(value[0], str):
            return [item.name for item in value]
        return value


class AssetRelationCreate(BaseModel):
    target_asset_id: UUID
    relation_type: AssetRelationType
    properties: dict[str, Any] = Field(default_factory=dict)


class AssetRelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_asset_id: UUID
    target_asset_id: UUID
    relation_type: AssetRelationType
    properties: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AssetDiscoveryRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    owner: str | None = Field(default=None, max_length=256)
    business_unit: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=64)
    criticality: str | None = Field(default=None, max_length=32)
    risk: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=100)


class AssetEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    agent_id: UUID
    trace_id: str
    url: str
    evidence_type: str
    sha256: str
    captured_at: datetime


class AssetReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    agent_id: UUID
    trace_id: str
    status: str
    created_at: datetime


class AssetDiscoveryRead(BaseModel):
    website: AssetRead
    domain: AssetRead
    ips: list[AssetRead]
    relations: list[AssetRelationRead]
