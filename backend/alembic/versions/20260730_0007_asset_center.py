"""Add unified Asset and Inventory Center.

Revision ID: 20260730_0007
Revises: 20260730_0006
Create Date: 2026-07-30 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260730_0006"
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
    op.create_table(
        "assets",
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("canonical_value", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=True),
        sa.Column("business_unit", sa.String(length=256), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("criticality", sa.String(length=32), nullable=True),
        sa.Column("risk", sa.String(length=32), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=256), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", name="uq_assets_agent_id"),
        sa.UniqueConstraint("asset_type", "canonical_value", name="uq_assets_type_canonical_value"),
    )
    for column in (
        "asset_type",
        "name",
        "owner",
        "business_unit",
        "environment",
        "criticality",
        "risk",
        "agent_id",
        "deleted_at",
    ):
        op.create_index(f"ix_assets_{column}", "assets", [column])

    op.create_table(
        "asset_relations",
        sa.Column("source_asset_id", sa.Uuid(), nullable=False),
        sa.Column("target_asset_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_asset_id",
            "target_asset_id",
            "relation_type",
            name="uq_asset_relations_source_target_type",
        ),
    )
    for column in ("source_asset_id", "target_asset_id", "relation_type"):
        op.create_index(f"ix_asset_relations_{column}", "asset_relations", [column])

    op.create_table(
        "asset_tags",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "name", name="uq_asset_tags_asset_name"),
    )
    op.create_index("ix_asset_tags_asset_id", "asset_tags", ["asset_id"])
    op.create_index("ix_asset_tags_name", "asset_tags", ["name"])

    op.create_table(
        "asset_evidence",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "evidence_id", name="uq_asset_evidence_asset_evidence"),
    )
    op.create_index("ix_asset_evidence_asset_id", "asset_evidence", ["asset_id"])
    op.create_index("ix_asset_evidence_evidence_id", "asset_evidence", ["evidence_id"])

    op.create_table(
        "asset_reports",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "report_id", name="uq_asset_reports_asset_report"),
    )
    op.create_index("ix_asset_reports_asset_id", "asset_reports", ["asset_id"])
    op.create_index("ix_asset_reports_report_id", "asset_reports", ["report_id"])

    op.add_column("tasks", sa.Column("asset_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_asset_id_assets",
        "tasks",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_tasks_asset_id", "tasks", ["asset_id"])
    op.add_column("workflow_instances", sa.Column("asset_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_workflow_instances_asset_id_assets",
        "workflow_instances",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_workflow_instances_asset_id", "workflow_instances", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_asset_id", table_name="workflow_instances")
    op.drop_constraint(
        "fk_workflow_instances_asset_id_assets",
        "workflow_instances",
        type_="foreignkey",
    )
    op.drop_column("workflow_instances", "asset_id")
    op.drop_index("ix_tasks_asset_id", table_name="tasks")
    op.drop_constraint("fk_tasks_asset_id_assets", "tasks", type_="foreignkey")
    op.drop_column("tasks", "asset_id")
    op.drop_table("asset_reports")
    op.drop_table("asset_evidence")
    op.drop_table("asset_tags")
    op.drop_table("asset_relations")
    op.drop_table("assets")
