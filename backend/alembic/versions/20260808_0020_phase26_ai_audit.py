"""Extend ModelInvocation telemetry and add InvestigationHypothesis (v2.0 / Phase 26).

Revision ID: 20260808_0020
Revises: 20260808_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260808_0020"
down_revision: str | None = "20260808_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_invocations",
    sa.Column("provider",
    sa.String(length=64), nullable=False, server_default="unknown"))
    op.add_column("model_invocations",
    sa.Column("input_policy",
    sa.String(length=128), nullable=False, server_default="phase26-v1"))
    op.add_column("model_invocations",
    sa.Column("redaction_summary",
    sa.String(length=512), nullable=False, server_default=""))
    op.add_column("model_invocations",
    sa.Column("structured_output_valid",
    sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_table(
        "investigation_hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False),
        sa.Column("contradicting_evidence", sa.JSON(), nullable=False),
        sa.Column("insufficient_evidence", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "state IN ('PROPOSED', 'SUPPORTED', 'CONTRADICTED', 'INCONCLUSIVE', 'REJECTED')",
            name="ck_investigation_hypotheses_state",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
            name="fk_investigation_hypotheses_run_id_agent_runs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigation_hypotheses"),
    )
    op.create_index(
        "ix_investigation_hypotheses_run_id", "investigation_hypotheses", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_investigation_hypotheses_run_id", table_name="investigation_hypotheses")
    op.drop_table("investigation_hypotheses")
    op.drop_column("model_invocations", "structured_output_valid")
    op.drop_column("model_invocations", "redaction_summary")
    op.drop_column("model_invocations", "input_policy")
    op.drop_column("model_invocations", "provider")
