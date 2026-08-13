"""Phase 28 -- Acquisition persistence models.

New tables only (spec 31). These do NOT create a second Asset / Evidence /
Knowledge / SecurityFact -- they reference the existing platform tables.
Raw payloads live in the EvidenceObjectStore; DB rows are small metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AcquisitionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One acquisition run (lifecycle: PENDING/RUNNING/COMPLETE/PARTIAL/BLOCKED/FAILED)."""

    __tablename__ = "acquisition_runs"

    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    target_asset: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    strategy: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    blocked_reason: Mapped[str] = mapped_column(String(64), default="NONE", nullable=False)
    blocked_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    replans: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    strategy_history: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- Phase 28.1 production path -----------------------------------------
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True, unique=True
    )
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    worker_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sandbox_execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    worker_execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # -- Phase 28.2 durable claim / fencing / observability ------------------
    # claim_token_hash: sha256 of the fencing token -- the raw fencing token is
    # NEVER stored (plaintext tokens are forbidden by the durability spec).
    claim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_result_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plan: Mapped[AcquisitionPlanRecord] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )


class AcquisitionPlanRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Snapshot of the AcquisitionPlan at run time."""

    __tablename__ = "acquisition_plans"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("acquisition_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False)
    steps: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    expected_outputs: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    completeness_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    budgets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    fallback_strategy: Mapped[str] = mapped_column(Text, default="", nullable=False)

    run: Mapped[AcquisitionRun] = relationship(back_populates="plan")


class AcquisitionStepRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Execution record of one plan step."""

    __tablename__ = "acquisition_steps"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("acquisition_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)


class AcquisitionArtifactRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Lineage record: object_key + evidence reference + capture metadata."""

    __tablename__ = "acquisition_artifacts"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("acquisition_runs.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(16), default="GET", nullable=False)
    tool: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    evidence_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    duplicate_of: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractedDocumentRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized extracted content metadata (full text optional by size)."""

    __tablename__ = "extracted_documents"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("acquisition_runs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True
    )
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_backend: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    text_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class CompletenessReportRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stored CompletenessReport per run."""

    __tablename__ = "completeness_reports"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("acquisition_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    field_completeness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    time_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pagination_complete: Mapped[bool] = mapped_column(default=False, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gaps: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), default="PARTIAL", nullable=False)


class PublicEndpointCandidateRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Observed public endpoints (OBSERVED/VALIDATED/REJECTED)."""

    __tablename__ = "public_endpoint_candidates"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("acquisition_runs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(16), default="GET", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="OBSERVED", nullable=False, index=True)
    observed_from: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[int | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
