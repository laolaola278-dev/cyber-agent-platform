"""Add Nuclei Assessment Plugin governance models.

Revision ID: 20260731_0010
Revises: 20260731_0009
Create Date: 2026-07-31 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0010"
down_revision: str | None = "20260731_0009"
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


def upgrade() -> None:
    op.drop_constraint("ck_findings_status", "findings", type_="check")
    op.execute("UPDATE findings SET status = 'NEW' WHERE status = 'OPEN'")
    op.execute("UPDATE findings SET status = 'FIXED' WHERE status = 'MITIGATED'")
    op.execute("UPDATE findings SET status = 'ACCEPTED_RISK' WHERE status = 'ACCEPTED'")
    op.create_check_constraint(
        "ck_findings_status",
        "findings",
        "status IN ('NEW', 'TRIAGED', 'CONFIRMED', 'FALSE_POSITIVE', "
        "'ACCEPTED_RISK', 'FIXED', 'REOPENED')",
    )
    op.create_table(
        "finding_history",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "finding_comments",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "finding_transitions",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "assessment_reports",
        sa.Column("assessment_task_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["assessment_task_id"], ["assessment_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["plugin_id"], ["assessment_plugins.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_task_id", name="uq_assessment_reports_assessment_task_id"),
    )
    for table, columns in {
        "finding_history": ("finding_id", "actor", "action"),
        "finding_comments": ("finding_id", "author"),
        "finding_transitions": (
            "finding_id",
            "from_status",
            "to_status",
            "actor",
            "trace_id",
        ),
        "assessment_reports": (
            "assessment_task_id",
            "plugin_id",
            "asset_id",
            "trace_id",
            "status",
        ),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("assessment_reports")
    op.drop_table("finding_transitions")
    op.drop_table("finding_comments")
    op.drop_table("finding_history")
    op.drop_constraint("ck_findings_status", "findings", type_="check")
    op.execute(
        "UPDATE findings SET status = 'OPEN' " "WHERE status IN ('NEW', 'TRIAGED', 'REOPENED')"
    )
    op.execute("UPDATE findings SET status = 'MITIGATED' WHERE status = 'FIXED'")
    op.execute("UPDATE findings SET status = 'ACCEPTED' WHERE status = 'ACCEPTED_RISK'")
    op.create_check_constraint(
        "ck_findings_status",
        "findings",
        "status IN ('OPEN', 'CONFIRMED', 'FALSE_POSITIVE', 'MITIGATED', 'ACCEPTED')",
    )
