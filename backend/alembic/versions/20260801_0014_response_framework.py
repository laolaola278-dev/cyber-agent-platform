"""Add unified Response, Approval and Rollback Framework tables.

Revision ID: 20260801_0014
Revises: 20260801_0013
Create Date: 2026-08-01 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260801_0014"
down_revision: str | None = "20260801_0013"
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


def _index(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "response_plugins",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("supports_approval", sa.Boolean(), nullable=False),
        sa.Column("supports_rollback", sa.Boolean(), nullable=False),
        sa.Column("health_status", sa.String(32), nullable=False),
        sa.Column("sandbox_compatible", sa.Boolean(), nullable=False),
        sa.Column("certified", sa.Boolean(), nullable=False),
        sa.Column("operational_documentation", sa.Text(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_response_plugins_name_version"),
    )
    _index("response_plugins", ("name", "enabled", "certified"))
    op.create_table(
        "response_policies",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_response_policies_name_version"),
    )
    _index("response_policies", ("name", "enabled"))
    op.create_table(
        "response_plans",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("target_capability", sa.String(128), nullable=False),
        sa.Column("requested_by", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("approval_state", sa.String(32), nullable=False),
        sa.Column("execution_state", sa.String(32), nullable=False),
        sa.Column("rollback_state", sa.String(32), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("rollback_parameters", sa.JSON(), nullable=False),
        sa.Column("supports_rollback", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "approval_state IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', "
            "'EXPIRED', 'EXECUTED', 'ROLLED_BACK')",
            name="ck_response_plans_approval_state",
        ),
        sa.CheckConstraint(
            "execution_state IN ('PLANNED', 'BLOCKED', 'READY', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'VERIFIED')",
            name="ck_response_plans_execution_state",
        ),
        sa.CheckConstraint(
            "rollback_state IN ('NOT_SUPPORTED', 'AVAILABLE', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'VERIFIED')",
            name="ck_response_plans_rollback_state",
        ),
        sa.CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_response_plans_risk_level",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plugin_id"], ["response_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "response_plans",
        (
            "incident_id",
            "plugin_id",
            "target_capability",
            "requested_by",
            "risk_level",
            "approval_state",
            "execution_state",
            "rollback_state",
            "expires_at",
        ),
    )
    op.create_table(
        "response_plan_assets",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["response_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "asset_id", name="uq_response_plan_assets_pair"),
    )
    _index("response_plan_assets", ("plan_id", "asset_id"))
    op.create_table(
        "response_approvals",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("approver", sa.String(256), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("approval_level", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["response_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("response_approvals", ("plan_id", "approver", "decision"))
    op.create_table(
        "response_executions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("rollback_token", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["response_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["response_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("response_executions", ("plan_id", "plugin_id", "status", "verification_status"))
    op.create_table(
        "response_rollbacks",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["response_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["response_executions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "response_rollbacks",
        ("plan_id", "execution_id", "actor", "status", "verification_status"),
    )
    op.create_table(
        "response_evidence",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["response_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["response_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rollback_id"], ["response_rollbacks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "response_evidence",
        ("plan_id", "execution_id", "rollback_id", "evidence_id", "evidence_type", "sha256"),
    )


def downgrade() -> None:
    op.drop_table("response_evidence")
    op.drop_table("response_rollbacks")
    op.drop_table("response_executions")
    op.drop_table("response_approvals")
    op.drop_table("response_plan_assets")
    op.drop_table("response_plans")
    op.drop_table("response_policies")
    op.drop_table("response_plugins")
