"""Knowledge Center API and provider-neutral schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import KnowledgeStatus, KnowledgeType


class KnowledgeRelationInput(BaseModel):
    target_type: str = Field(min_length=1, max_length=64)
    target_external_id: str = Field(min_length=1, max_length=256)
    relation_type: str = Field(min_length=1, max_length=64)
    target_source: str | None = Field(default=None, max_length=128)
    properties: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRecord(BaseModel):
    """Normalized record emitted by a Provider and consumed by the Importer."""

    knowledge_type: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    description: str = ""
    references: list[str] = Field(default_factory=list)
    status: KnowledgeStatus = KnowledgeStatus.ACTIVE
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_updated_at: datetime | None = None
    relations: list[KnowledgeRelationInput] = Field(default_factory=list)

    @field_validator("knowledge_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("external_id", "source", "version", "title")
    @classmethod
    def strip_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("references")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})


class KnowledgeSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider_type: str
    base_url: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: str
    content_hash: str
    source_updated_at: datetime | None
    imported_at: datetime


class KnowledgeRead(BaseModel):
    id: UUID
    knowledge_type: str
    external_id: str
    source: str
    version: str
    title: str
    description: str
    references: list[str]
    status: str
    attributes: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class KnowledgeRelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_knowledge_id: UUID
    target_knowledge_id: UUID
    relation_type: str
    source_name: str
    properties: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class KnowledgeImportRequest(BaseModel):
    source: str = Field(min_length=1, max_length=128)
    provider: str = Field(default="json", min_length=1, max_length=128)
    format: str = Field(default="json", pattern="^[a-z0-9_-]+$")
    payload: dict[str, Any] | list[dict[str, Any]]


class KnowledgeImportRead(BaseModel):
    source: str
    imported: int
    unchanged: int
    relations: int
    knowledge_ids: list[UUID]


BUILTIN_KNOWLEDGE_TYPES = tuple(item.value for item in KnowledgeType)
