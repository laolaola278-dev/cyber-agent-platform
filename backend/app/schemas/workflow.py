"""Workflow definition, planning, and execution API schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import WorkflowNodeType, WorkflowStatus, WorkflowStepStatus


class RetryPolicy(BaseModel):
    """Bounded retry policy for one workflow node."""

    max_attempts: int = Field(default=1, ge=1, le=10)
    delay_seconds: float = Field(default=0, ge=0, le=300)


class WorkflowNodeDefinition(BaseModel):
    """Declarative node compiled from YAML."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    type: WorkflowNodeType
    capability: str | None = Field(default=None, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = Field(default=None, max_length=512)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @model_validator(mode="after")
    def validate_node_contract(self) -> "WorkflowNodeDefinition":
        if self.type == WorkflowNodeType.AGENT and not self.capability:
            raise ValueError("AgentNode requires capability")
        if self.type == WorkflowNodeType.CONDITION and not self.condition:
            raise ValueError("ConditionNode requires condition")
        if self.type != WorkflowNodeType.AGENT and self.capability is not None:
            raise ValueError("Only AgentNode may declare capability")
        return self


class WorkflowEdgeDefinition(BaseModel):
    """Directed edge with an optional condition result selector."""

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    when: Literal["true", "false"] | None = None


class WorkflowDocument(BaseModel):
    """Versioned DAG document accepted from YAML or JSON API input."""

    name: str = Field(min_length=1, max_length=256)
    version: str = Field(default="1.0.0", min_length=1, max_length=64)
    description: str | None = None
    nodes: list[WorkflowNodeDefinition] = Field(min_length=2)
    edges: list[WorkflowEdgeDefinition] = Field(min_length=1)


class WorkflowDefinitionCreate(BaseModel):
    """Workflow creation payload supporting YAML as the canonical definition."""

    yaml: str = Field(min_length=1)


class WorkflowDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    definition: dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WorkflowRunCreate(BaseModel):
    workflow_id: UUID
    asset_id: UUID | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    execute: bool = True


class WorkflowPlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=512)


class CapabilityPlan(BaseModel):
    goal: str
    capabilities: list[str]
    workflow: WorkflowDocument


class WorkflowStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_id: str
    node_type: str
    capability: str | None
    status: WorkflowStepStatus
    attempt: int
    max_attempts: int
    timeout_seconds: int
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowInstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    definition_id: UUID
    asset_id: UUID | None
    status: WorkflowStatus
    input: dict[str, Any]
    context: dict[str, Any]
    current_node: str | None
    trace_id: str
    cancel_requested: bool
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[WorkflowStepRead] = Field(default_factory=list)
