"""Add Acquisition durable-execution tables (Phase 28.1/28.2/28.3).

Revision ID: 20260812_0021
Revises: 20260808_0020
Create Date: 2026-08-12 06:00:00

Creates the seven acquisition ORM tables (app/acquisition/models_db.py):

    acquisition_runs
    acquisition_plans
    acquisition_steps
    acquisition_artifacts
    extracted_documents
    completeness_reports
    public_endpoint_candidates

Columns, FKs, unique constraints and indexes match the ORM models exactly so
``alembic upgrade head`` on a fresh PostgreSQL database yields the full
acquisition schema WITHOUT relying on Base.metadata.create_all. The existing
Phase 16/17 worker/sandbox tables are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0021"
down_revision: str | None = "20260808_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def _index(table: str, columns: tuple[str, ...], *, unique: bool = False) -> None:
    for column in columns:
        name = f"ix_{table}_{column}"
        if unique:
            op.create_index(name, table, [column], unique=True)
        else:
            op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "acquisition_runs",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("target_asset", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=128), nullable=False),
        sa.Column("blocked_reason", sa.String(length=64), nullable=False),
        sa.Column("blocked_detail", sa.Text(), nullable=True),
        sa.Column("replans", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("total_requests", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("strategy_history", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("sandbox_execution_id", sa.Uuid(), nullable=True),
        sa.Column("worker_execution_id", sa.Uuid(), nullable=True),
        sa.Column("claim_token_hash", sa.String(length=64), nullable=True),
        sa.Column("claim_attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_result_rejected", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_acquisition_runs"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_acquisition_runs_task_id_tasks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_acquisition_runs_agent_id_agents",
            ondelete="RESTRICT",
        ),
    )
    _index("acquisition_runs", ("task_id", "agent_id", "trace_id", "status"))
    # unique + indexed idempotency key (matches idempotency_key unique=True, index=True)
    op.create_index(
        "ix_acquisition_runs_idempotency_key",
        "acquisition_runs",
        ["idempotency_key"],
        unique=True,
    )

    op.create_table(
        "acquisition_plans",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=128), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("completeness_conditions", sa.JSON(), nullable=False),
        sa.Column("budgets", sa.JSON(), nullable=False),
        sa.Column("fallback_strategy", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_acquisition_plans"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_acquisition_plans_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
    )
    _index("acquisition_plans", ("run_id",))

    op.create_table(
        "acquisition_steps",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_acquisition_steps"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_acquisition_steps_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "acquisition_artifacts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("duplicate_of", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_acquisition_artifacts"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_acquisition_artifacts_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_acquisition_artifacts_evidence_id_evidence",
            ondelete="SET NULL",
        ),
    )
    _index("acquisition_artifacts", ("object_key", "sha256", "evidence_id"))

    op.create_table(
        "extracted_documents",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("extraction_backend", sa.String(length=64), nullable=False),
        sa.Column("text_length", sa.Integer(), nullable=False),
        sa.Column("doc_metadata", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_extracted_documents"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_extracted_documents_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name="fk_extracted_documents_evidence_id_evidence",
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "completeness_reports",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("coverage_score", sa.Float(), nullable=False),
        sa.Column("field_completeness", sa.Float(), nullable=False),
        sa.Column("time_coverage", sa.Float(), nullable=False),
        sa.Column("pagination_complete", sa.Boolean(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_completeness_reports"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_completeness_reports_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", name="uq_completeness_reports_run_id"),
    )

    op.create_table(
        "public_endpoint_candidates",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("observed_from", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_public_endpoint_candidates"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["acquisition_runs.id"],
            name="fk_public_endpoint_candidates_run_id_acquisition_runs",
            ondelete="CASCADE",
        ),
    )
    _index("public_endpoint_candidates", ("state",))


def downgrade() -> None:
    # reverse creation order (respects FK dependencies)
    op.drop_table("public_endpoint_candidates")
    op.drop_table("completeness_reports")
    op.drop_table("extracted_documents")
    op.drop_table("acquisition_artifacts")
    op.drop_table("acquisition_steps")
    op.drop_table("acquisition_plans")
    op.drop_table("acquisition_runs")
