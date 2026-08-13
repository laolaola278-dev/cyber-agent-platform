"""Agent, Tool, heartbeat, and Registry API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AgentStatus, HealthStatus, ToolStatus


class AgentRegister(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    description: str | None = None
    author: str = Field(default="system", min_length=1, max_length=256)
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    minimum_runtime_version: str = "1.0.0"
    platform_version: str = "0.2.1"
    sdk_version: str = "1.0.0"
    runtime: dict[str, Any] = Field(default_factory=dict)
    network_policy: dict[str, Any] = Field(default_factory=dict)
    resource_limit: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    description: str | None = None
    author: str | None = Field(default=None, max_length=256)
    permissions: list[str] | None = None
    capabilities: list[str] | None = None
    tools: list[str] | None = None
    runtime: dict[str, Any] | None = None
    network_policy: dict[str, Any] | None = None
    resource_limit: dict[str, Any] | None = None
    approval_policy: dict[str, Any] | None = None
    status: AgentStatus | None = None


class AgentRegistryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    author: str
    permissions: list[str]
    capabilities: list[str]
    tools: list[str]
    minimum_runtime_version: str
    platform_version: str
    sdk_version: str
    runtime: dict[str, Any]
    network_policy: dict[str, Any]
    resource_limit: dict[str, Any]
    approval_policy: dict[str, Any]
    status: AgentStatus
    health_status: HealthStatus
    heartbeat_time: datetime | None
    created_at: datetime
    updated_at: datetime


class ToolRegister(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    tool_type: str = Field(min_length=1, max_length=64)
    description: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    runtime_requirements: dict[str, Any] = Field(default_factory=dict)


class ToolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    tool_type: str
    description: str | None
    required_permissions: list[str]
    config_schema: dict[str, Any]
    runtime_requirements: dict[str, Any]
    status: ToolStatus
    created_at: datetime
    updated_at: datetime


class HeartbeatRequest(BaseModel):
    agent_id: UUID
    health_status: HealthStatus
    details: dict[str, Any] = Field(default_factory=dict)


class AgentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    version: str
    manifest: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ToolVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tool_id: UUID
    version: str
    manifest: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RegistryStatus(BaseModel):
    agents_total: int
    agents_online: int
    tools_enabled: int
