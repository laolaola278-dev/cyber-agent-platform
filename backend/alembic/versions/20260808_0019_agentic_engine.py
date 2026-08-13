"""Create Agentic engine tables (v2.0 / Phase 25).

Revision ID: 20260808_0019
Revises: 20260803_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260808_0019"
down_revision: str | None = "20260803_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'LIMIT_REACHED')",
            name="ck_agent_runs_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint("trace_id", name="uq_agent_runs_trace_id"),
    )
    op.create_index("ix_agent_runs_agent_name", "agent_runs", ["agent_name"], unique=False)
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"], unique=False)

    op.create_table(
        "investigation_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sa.JSON(), nullable=True),
        sa.Column("conclusion_confidence", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ABANDONED')",
            name="ck_investigation_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_investigation_sessions_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigation_sessions"),
    )
    op.create_index(
        "ix_investigation_sessions_run_id", "investigation_sessions", ["run_id"], unique=False
    )
    op.create_index(
        "ix_investigation_sessions_status", "investigation_sessions", ["status"], unique=False
    )

    op.create_table(
        "agent_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'VALIDATED', 'WAITING_APPROVAL', 'EXECUTED', 'REJECTED')",
            name="ck_agent_plans_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_agent_plans_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_plans"),
    )
    op.create_index("ix_agent_plans_run_id", "agent_plans", ["run_id"], unique=False)

    op.create_table(
        "agent_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_agent_observations_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_agent_observations_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_observations"),
    )
    op.create_index(
        "ix_agent_observations_run_id", "agent_observations", ["run_id"], unique=False
    )

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "decision_type IN ('CAPABILITY_REJECTED', 'APPROVAL_REQUESTED', 'LOOP_FINISHED', 'REPLAN')",
            name="ck_agent_decisions_type",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_agent_decisions_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_decisions"),
    )
    op.create_index("ix_agent_decisions_run_id", "agent_decisions", ["run_id"], unique=False)

    op.create_table(
        "agent_handoffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_agent", sa.String(length=128), nullable=False),
        sa.Column("target_agent", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context_refs", sa.JSON(), nullable=False),
        sa.Column("allowed_capabilities", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'ACCEPTED', 'DECLINED')",
            name="ck_agent_handoffs_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_agent_handoffs_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_handoffs"),
    )
    op.create_index("ix_agent_handoffs_run_id", "agent_handoffs", ["run_id"], unique=False)

    op.create_table(
        "model_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("guardrail_verdict", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE", name="fk_model_invocations_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_invocations"),
    )
    op.create_index("ix_model_invocations_run_id", "model_invocations", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_model_invocations_run_id", table_name="model_invocations")
    op.drop_table("model_invocations")
    op.drop_index("ix_agent_handoffs_run_id", table_name="agent_handoffs")
    op.drop_table("agent_handoffs")
    op.drop_index("ix_agent_decisions_run_id", table_name="agent_decisions")
    op.drop_table("agent_decisions")
    op.drop_index("ix_agent_observations_run_id", table_name="agent_observations")
    op.drop_table("agent_observations")
    op.drop_index("ix_agent_plans_run_id", table_name="agent_plans")
    op.drop_table("agent_plans")
    op.drop_index("ix_investigation_sessions_run_id", table_name="investigation_sessions")
    op.drop_index("ix_investigation_sessions_status", table_name="investigation_sessions")
    op.drop_table("investigation_sessions")
    op.drop_index("ix_agent_runs_trace_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent_name", table_name="agent_runs")
    op.drop_table("agent_runs")
