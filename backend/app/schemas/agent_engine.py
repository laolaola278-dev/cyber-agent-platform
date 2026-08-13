"""API schemas for the Agentic engine (v2.0 / Phase 25)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InvestigationCreate(BaseModel):
    """Request to start a new investigation."""

    model_config = ConfigDict(extra="allow")

    goal: str = Field(min_length=1, max_length=4096)
    context: dict[str, Any] = Field(default_factory=dict)
    data_blocks: list[dict[str, Any]] = Field(default_factory=list)


class InvestigationContinue(BaseModel):
    """Request to continue an existing investigation."""

    goal: str | None = Field(default=None, max_length=4096)
    context: dict[str, Any] = Field(default_factory=dict)
    data_blocks: list[dict[str, Any]] = Field(default_factory=list)


class PlanStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capability: str
    purpose: str
    risk: str
    required_approval: bool


class InvestigationPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    goal: str
    reasoning_summary: str
    steps: list[PlanStepRead]
    requires_approval: bool
    risk_level: str


class ObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capability: str
    summary: str
    evidence_refs: list[str]
    confidence: float


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_type: str
    rationale: str
    capability: str | None


class HandoffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_agent: str
    target_agent: str
    reason: str
    status: str
    allowed_capabilities: list[str]


class InvestigationRead(BaseModel):
    """Public view of an investigation (never exposes secrets or CoT)."""

    id: UUID
    goal: str
    status: str
    conclusion: dict[str, Any] | None
    conclusion_confidence: float | None
    created_at: datetime
    updated_at: datetime
    plan: InvestigationPlanRead | None = None
    observations: list[ObservationRead] = Field(default_factory=list)
    decisions: list[DecisionRead] = Field(default_factory=list)
    handoffs: list[HandoffRead] = Field(default_factory=list)
    run_id: UUID | None = None


class RunRead(BaseModel):
    """Redacted telemetry view of an agent run."""

    id: UUID
    trace_id: str
    agent_name: str
    model: str
    prompt_version: str
    status: str
    goal: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    started_at: datetime
    finished_at: datetime | None
    observations: list[ObservationRead] = Field(default_factory=list)


class EvaluationMetricRead(BaseModel):
    name: str
    passed: int
    total: int
    rate: float


class EvaluationReportRead(BaseModel):
    overall_score: float
    metrics: list[EvaluationMetricRead]
    total_scenarios: int
