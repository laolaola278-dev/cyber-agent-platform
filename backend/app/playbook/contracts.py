"""Strict Playbook DSL and API contracts for Phase 20."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlaybookTriggerType(StrEnum):
    MANUAL = "manual"
    INCIDENT_CREATED = "incident.created"
    SCHEDULE = "schedule"
    FINDING_CREATED = "finding.created"
    SECURITY_EVENT_CREATED = "security_event.created"
    APPROVAL_GRANTED = "approval.granted"
    RESPONSE_COMPLETED = "response.completed"
    NOTIFICATION_FAILED = "notification.failed"


class PlaybookNodeType(StrEnum):
    CONDITION = "condition"
    APPROVAL = "approval"
    ASSESSMENT = "assessment"
    DETECTION = "detection"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    TICKET = "ticket"
    DELAY = "delay"
    PARALLEL = "parallel"


class PlaybookStepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    TIMED_OUT = "TIMED_OUT"


class PlaybookExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


IMPLEMENTED_TRIGGERS = frozenset({PlaybookTriggerType.MANUAL, PlaybookTriggerType.INCIDENT_CREATED})
RESERVED_TRIGGERS = frozenset(set(PlaybookTriggerType) - IMPLEMENTED_TRIGGERS)
IMPLEMENTED_NODES = frozenset(
    {
        PlaybookNodeType.CONDITION,
        PlaybookNodeType.APPROVAL,
        PlaybookNodeType.ASSESSMENT,
        PlaybookNodeType.DETECTION,
        PlaybookNodeType.RESPONSE,
        PlaybookNodeType.NOTIFICATION,
        PlaybookNodeType.TICKET,
    }
)
RESERVED_NODES = frozenset({PlaybookNodeType.DELAY, PlaybookNodeType.PARALLEL})


class PlaybookRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=5)
    delay_seconds: float = Field(default=0, ge=0, le=300)


class PlaybookCompensation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: PlaybookNodeType
    capability: str | None = Field(default=None, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_compensation(self) -> "PlaybookCompensation":
        if self.type not in IMPLEMENTED_NODES - {
            PlaybookNodeType.CONDITION,
            PlaybookNodeType.APPROVAL,
        }:
            raise ValueError("Compensation must call an implemented capability node")
        if self.type not in {
            PlaybookNodeType.RESPONSE,
            PlaybookNodeType.NOTIFICATION,
            PlaybookNodeType.TICKET,
        }:
            raise ValueError("Only response, notification, or ticket compensation is supported")
        if not self.capability:
            raise ValueError("Compensation requires capability")
        return self


class PlaybookStepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    type: PlaybookNodeType
    capability: str | None = Field(default=None, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = Field(default=None, max_length=512)
    retry: PlaybookRetryPolicy = Field(default_factory=PlaybookRetryPolicy)
    timeout_seconds: int = Field(default=60, ge=1, le=3_600)
    compensation: PlaybookCompensation | None = None

    @model_validator(mode="after")
    def validate_step(self) -> "PlaybookStepDefinition":
        if self.type == PlaybookNodeType.CONDITION and not self.condition:
            raise ValueError("Condition step requires condition")
        if (
            self.type
            in {
                PlaybookNodeType.ASSESSMENT,
                PlaybookNodeType.DETECTION,
                PlaybookNodeType.RESPONSE,
                PlaybookNodeType.NOTIFICATION,
                PlaybookNodeType.TICKET,
            }
            and not self.capability
        ):
            raise ValueError(f"{self.type.value} step requires capability")
        if self.type in RESERVED_NODES:
            raise ValueError(f"{self.type.value} step is reserved and not executable")
        if self.type in {PlaybookNodeType.CONDITION, PlaybookNodeType.APPROVAL} and self.capability:
            raise ValueError("Condition and approval steps cannot declare capability")
        return self


class PlaybookTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: PlaybookTriggerType
    filters: dict[str, Any] = Field(default_factory=dict)


class PlaybookDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dsl_version: str = Field(default="v1", pattern=r"^v1$")
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    trigger: PlaybookTrigger
    steps: list[PlaybookStepDefinition] = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=3_600, ge=1, le=86_400)
    max_parallel: int = Field(default=1, ge=1, le=1)
    allowed_plugins: list[str] = Field(default_factory=list, max_length=50)
    allowed_capabilities: list[str] = Field(default_factory=list, max_length=100)
    allowed_runners: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_graph(self) -> "PlaybookDocument":
        if self.trigger.type not in IMPLEMENTED_TRIGGERS:
            raise ValueError(f"Trigger {self.trigger.type.value} is reserved and not executable")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Playbook step ids must be unique")
        declared = {step.capability for step in self.steps if step.capability}
        if self.allowed_capabilities and not declared <= set(self.allowed_capabilities):
            raise ValueError("Step capability is not in playbook allowlist")
        if self.allowed_plugins and any(not plugin.strip() for plugin in self.allowed_plugins):
            raise ValueError("Playbook plugin allowlist cannot contain blank values")
        return self


class PlaybookDSL:
    """Safe YAML loader: only the typed document is accepted; no code is evaluated."""

    @staticmethod
    def load(source_yaml: str) -> PlaybookDocument:
        raw = yaml.safe_load(source_yaml)
        if not isinstance(raw, dict):
            raise ValueError("Playbook YAML must contain a mapping")
        return PlaybookDocument.model_validate(raw)


class PlaybookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml: str = Field(min_length=1, max_length=500_000)
    enabled: bool = True


class PlaybookApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approver: str = Field(min_length=1, max_length=256)
    comment: str = Field(default="", max_length=4_000)


class PlaybookRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(min_length=1, max_length=256)
    approvals: dict[str, PlaybookApproval] = Field(default_factory=dict, max_length=100)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)


class PlaybookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    description: str | None
    enabled: bool
    document: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PlaybookStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    step_id: str
    node_type: str
    capability: str | None
    status: PlaybookStepStatus
    attempt: int
    max_attempts: int
    output: dict[str, Any] | None
    error: str | None
    compensation_status: str | None
    started_at: datetime | None
    completed_at: datetime | None


class PlaybookExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    playbook_id: UUID
    trigger_type: PlaybookTriggerType
    status: PlaybookExecutionStatus
    actor: str
    input: dict[str, Any]
    context: dict[str, Any]
    current_step: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[PlaybookStepRead] = Field(default_factory=list)


class PlaybookRunResult(BaseModel):
    execution_id: UUID
    status: PlaybookExecutionStatus
    step_results: list[dict[str, Any]] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC)
