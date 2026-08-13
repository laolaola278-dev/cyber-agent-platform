"""Agentic engine domain contracts (v2.0 / Phase 25).

All Agent-facing data models are defined here as Pydantic contracts.
They are intentionally free of ORM / repository / database imports so the
LLM layer and planner can never touch platform persistence.

Security rule: the LLM is a *decision layer only*. Every contract that could
carry an executable instruction (shell, python, sql, raw tool command) is
rejected by guardrails before it can reach any executor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# LLM Provider contracts
# ---------------------------------------------------------------------------


class ModelCapability(StrEnum):
    """Capabilities a provider may advertise."""

    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    FUNCTION_CALLING = "function_calling"


class TokenUsage(BaseModel):
    """Token accounting for one model invocation."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelRequest(BaseModel):
    """A provider-neutral completion request.

    ``data`` carries *untrusted* evidence/log/web content. Providers must
    treat it strictly as data and never as instructions. ``extra`` carries
    structured hints (goal, available capabilities) that a real provider
    would otherwise receive inside the prompt text.
    """

    model_config = ConfigDict(extra="allow")

    system_prompt: str = ""
    user_prompt: str = ""
    data: list[dict[str, Any]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    required_capability: ModelCapability = ModelCapability.TEXT
    temperature: float = 0.0
    max_tokens: int = 2048
    prompt_version: str = "phase25-v1"


class ModelResponse(BaseModel):
    """A provider-neutral completion result."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    structured: dict[str, Any] | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider: str = "unknown"
    latency_ms: int = 0
    finish_reason: str = "stop"


class LLMProvider:
    """Provider-neutral interface. Every real provider must implement this.

    The provider receives plain text (system + user + data) and returns text
    or structured JSON. It never receives database credentials, secrets,
    network access, shell access or plugin instances.
    """

    name: str = "base"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def supports(self, capability: ModelCapability) -> bool:
        return capability == ModelCapability.TEXT

    def health_check(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    """Immutable description of one Agent role in the platform.

    ``capabilities`` MUST reference the platform Capability Registry names.
    There is deliberately no second capability vocabulary in the Agent layer.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str = "1.0.0"
    role: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    risk_level: str = "LOW"
    planning_permission: bool = True
    execution_permission: bool = True
    handoff_targets: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Investigation plan and steps
# ---------------------------------------------------------------------------


class PlanStep(BaseModel):
    """One step of a constrained investigation plan."""

    model_config = ConfigDict(frozen=True)

    capability: str = Field(min_length=1)
    purpose: str
    risk: str = "LOW"
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_approval: bool = False


class InvestigationPlan(BaseModel):
    """Strictly structured planner output.

    The model may only reference capabilities that exist and are allowed in
    the platform Capability Registry. Any unknown capability fails closed.
    """

    model_config = ConfigDict(frozen=True)

    goal: str
    reasoning_summary: str
    steps: list[PlanStep] = Field(default_factory=list)
    requires_approval: bool = False
    risk_level: str = "LOW"


# ---------------------------------------------------------------------------
# Observation / evidence / conclusion
# ---------------------------------------------------------------------------


class AgentObservation(BaseModel):
    """A structured observation produced by the Agent after a capability call.

    An observation is *not* Evidence: Evidence is verifiable raw platform
    evidence; an observation is the Agent's structured reading of a result.
    """

    model_config = ConfigDict(frozen=True)

    source: str = "agent"
    capability: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Hypothesis(BaseModel):
    """A candidate explanation with its supporting evidence."""

    model_config = ConfigDict(frozen=True)

    statement: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class RecommendedAction(BaseModel):
    """A *suggestion* only. High-risk actions are never auto-executed."""

    model_config = ConfigDict(frozen=True)

    capability: str
    action: str
    risk: str = "LOW"
    requires_approval: bool = False


class InvestigationConclusion(BaseModel):
    """Final structured conclusion of an investigation."""

    model_config = ConfigDict(frozen=True)

    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class KnowledgeCandidate(BaseModel):
    """A proposal that some content should enter the Knowledge Center.

    Candidates are never written directly to Knowledge Center by the model;
    they await platform validation or human approval.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    content_type: str = "text"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source_refs: list[str] = Field(default_factory=list)
    status: str = "PENDING_VALIDATION"


# ---------------------------------------------------------------------------
# Handoff contract
# ---------------------------------------------------------------------------


class HandoffContract(BaseModel):
    """Explicit record of an Agent-to-Agent handoff.

    Handoffs never carry secrets. Allowed capabilities must be a subset of
    the platform registry.
    """

    model_config = ConfigDict(frozen=True)

    handoff_id: str = Field(default_factory=lambda: str(uuid4()))
    source_agent: str
    target_agent: str
    reason: str
    context_refs: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    status: str = "PROPOSED"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Loop control
# ---------------------------------------------------------------------------


class AgentLoopBudget(BaseModel):
    """Hard limits preventing runaway or infinite agent loops."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = 8
    max_duration_seconds: int = 300
    token_budget: int = 100_000
    capability_budget: int = 12
    retry_limit: int = 2


# ---------------------------------------------------------------------------
# Session memory
# ---------------------------------------------------------------------------


class AgentDecision(BaseModel):
    """A decision recorded in session memory."""

    model_config = ConfigDict(frozen=True)

    decision_type: str
    rationale: str
    capability: str | None = None
    observation_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InvestigationSessionMemory:
    """Per-investigation session memory (never cross-user permanent chat).

    Supports Plan / Observation / Decision / Handoff / Conclusion and
    KnowledgeCandidate staging. Persistence is owned by the application
    service, not by the model.
    """

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id: str = session_id or str(uuid4())
        self.plan: InvestigationPlan | None = None
        self.observations: list[AgentObservation] = []
        self.decisions: list[AgentDecision] = []
        self.handoffs: list[HandoffContract] = []
        self.conclusion: InvestigationConclusion | None = None
        self.knowledge_candidates: list[KnowledgeCandidate] = []

    def set_plan(self, plan: InvestigationPlan) -> None:
        self.plan = plan

    def add_observation(self, observation: AgentObservation) -> None:
        self.observations.append(observation)

    def add_decision(self, decision: AgentDecision) -> None:
        self.decisions.append(decision)

    def add_handoff(self, handoff: HandoffContract) -> None:
        self.handoffs.append(handoff)

    def set_conclusion(self, conclusion: InvestigationConclusion) -> None:
        self.conclusion = conclusion

    def stage_knowledge_candidate(self, candidate: KnowledgeCandidate) -> None:
        self.knowledge_candidates.append(candidate)
