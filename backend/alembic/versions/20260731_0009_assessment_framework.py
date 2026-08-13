"""Add Security Assessment Framework.

Revision ID: 20260731_0009
Revises: 20260730_0008
Create Date: 2026-07-31 04:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0009"
down_revision: str | None = "20260730_0008"
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
        "assessment_plugins",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_assessment_plugins_name_version"),
    )
    for column in ("name", "enabled"):
        op.create_index(f"ix_assessment_plugins_{column}", "assessment_plugins", [column])

    op.create_table(
        "assessment_capabilities",
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plugin_id"], ["assessment_plugins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_id",
            "capability_id",
            name="uq_assessment_capabilities_plugin_capability",
        ),
    )
    for column in ("plugin_id", "capability_id"):
        op.create_index(f"ix_assessment_capabilities_{column}", "assessment_capabilities", [column])

    op.create_table(
        "assessment_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_capabilities", sa.JSON(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_assessment_tasks_status",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["assessment_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_assessment_tasks_task_id"),
    )
    for column in ("task_id", "plugin_id", "status"):
        op.create_index(f"ix_assessment_tasks_{column}", "assessment_tasks", [column])

    op.create_table(
        "findings",
        sa.Column("assessment_task_id", sa.Uuid(), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("affected_asset", sa.Text(), nullable=False),
        sa.Column("plugin", sa.String(length=128), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=True),
        sa.Column("rule", sa.String(length=256), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_findings_severity",
        ),
        sa.CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_findings_confidence"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CONFIRMED', 'FALSE_POSITIVE', 'MITIGATED', 'ACCEPTED')",
            name="ck_findings_status",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_task_id"], ["assessment_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["findings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "assessment_task_id",
        "duplicate_of_id",
        "fingerprint",
        "title",
        "severity",
        "confidence",
        "affected_asset",
        "plugin",
        "tool",
        "rule",
        "risk_level",
        "status",
    ):
        op.create_index(f"ix_findings_{column}", "findings", [column])

    _create_simple_link(
        "finding_references",
        "url",
        sa.Text(),
        None,
        "uq_finding_references_finding_url",
    )
    _create_simple_link(
        "finding_evidence",
        "evidence_id",
        sa.Uuid(),
        ("evidence", "RESTRICT"),
        "uq_finding_evidence_pair",
    )
    _create_simple_link(
        "finding_assets",
        "asset_id",
        sa.Uuid(),
        ("assets", "RESTRICT"),
        "uq_finding_assets_pair",
    )
    op.create_table(
        "finding_knowledge",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"], ["knowledge_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_id", "knowledge_id", name="uq_finding_knowledge_pair"),
    )
    for column in ("finding_id", "knowledge_id", "knowledge_version_id"):
        op.create_index(f"ix_finding_knowledge_{column}", "finding_knowledge", [column])


def _create_simple_link(
    table: str,
    value_column: str,
    value_type: sa.types.TypeEngine[object],
    reference: tuple[str, str] | None,
    unique_name: str,
) -> None:
    columns: list[object] = [
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column(value_column, value_type, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
    ]
    if reference is not None:
        columns.append(
            sa.ForeignKeyConstraint([value_column], [f"{reference[0]}.id"], ondelete=reference[1])
        )
    columns.extend(
        [
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("finding_id", value_column, name=unique_name),
        ]
    )
    op.create_table(table, *columns)
    for column in ("finding_id", value_column):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("finding_knowledge")
    op.drop_table("finding_assets")
    op.drop_table("finding_evidence")
    op.drop_table("finding_references")
    op.drop_table("findings")
    op.drop_table("assessment_tasks")
    op.drop_table("assessment_capabilities")
    op.drop_table("assessment_plugins")
