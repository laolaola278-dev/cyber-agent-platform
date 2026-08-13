"""Agentic engine persistence models (v2.0 / Phase 25).

New tables for Agent runs, plans, observations, decisions, handoffs,
investigation sessions and model invocations. Existing domain models
(Asset, Evidence, Finding, SecurityEvent, Incident, Response) are untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One agent run (top-level telemetry record)."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'LIMIT_REACHED')",
            name="ck_agent_runs_status",
        ),
    )

    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[InvestigationSession | None] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    plans: Mapped[list[AgentPlan]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    observations: Mapped[list[AgentObservation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[AgentDecision]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    handoffs: Mapped[list[AgentHandoff]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    invocations: Mapped[list[ModelInvocation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class InvestigationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single investigation (scoped memory container)."""

    __tablename__ = "investigation_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ABANDONED')",
            name="ck_investigation_sessions_status",
        ),
    )

    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", index=True, nullable=False
    )
    conclusion: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    conclusion_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped[AgentRun | None] = relationship(back_populates="session")


class AgentPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A persisted investigation plan and its validation state."""

    __tablename__ = "agent_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'VALIDATED', 'WAITING_APPROVAL', 'EXECUTED', 'REJECTED')",
            name="ck_agent_plans_status",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), default="LOW", nullable=False)
    requires_approval: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="plans")


class AgentObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One structured observation produced by the agent."""

    __tablename__ = "agent_observations"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_agent_observations_confidence",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[AgentRun] = relationship(back_populates="observations")


class AgentDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A decision recorded by the agent loop."""

    __tablename__ = "agent_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_type IN "
            "('CAPABILITY_REJECTED', 'APPROVAL_REQUESTED', 'LOOP_FINISHED', 'REPLAN')",
            name="ck_agent_decisions_type",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str | None] = mapped_column(String(128), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="decisions")


class AgentHandoff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An explicit handoff contract between agents."""

    __tablename__ = "agent_handoffs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED', 'ACCEPTED', 'DECLINED')",
            name="ck_agent_handoffs_status",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_agent: Mapped[str] = mapped_column(String(128), nullable=False)
    target_agent: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    context_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="handoffs")


class ModelInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One LLM invocation (telemetry; never stores secret material)."""

    __tablename__ = "model_invocations"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_policy: Mapped[str] = mapped_column(String(128), default="phase26-v1", nullable=False)
    redaction_summary: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    structured_output_valid: Mapped[bool] = mapped_column(default=True, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    guardrail_verdict: Mapped[str] = mapped_column(String(16), default="ALLOWED", nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="invocations")


class InvestigationHypothesisRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted investigation hypotheses (hypotheses are never evidence)."""

    __tablename__ = "investigation_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PROPOSED', 'SUPPORTED', 'CONTRADICTED', 'INCONCLUSIVE', 'REJECTED')",
            name="ck_investigation_hypotheses_state",
        ),
    )

    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="PROPOSED", nullable=False)
    supporting_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    contradicting_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    insufficient_evidence: Mapped[bool] = mapped_column(default=False, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="agent", nullable=False)
