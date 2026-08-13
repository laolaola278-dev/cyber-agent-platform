"""Add unified Notification and Ticket Framework tables.

Revision ID: 20260801_0015
Revises: 20260801_0014
Create Date: 2026-08-01 22:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260801_0015"
down_revision: str | None = "20260801_0014"
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
        "notification_plugins",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("supports_verification", sa.Boolean(), nullable=False),
        sa.Column("health_status", sa.String(32), nullable=False),
        sa.Column("sandbox_compatible", sa.Boolean(), nullable=False),
        sa.Column("certified", sa.Boolean(), nullable=False),
        sa.Column("operational_documentation", sa.Text(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_notification_plugins_name_version"),
    )
    _index("notification_plugins", ("name", "enabled", "certified"))
    op.create_table(
        "notification_templates",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "format IN ('MARKDOWN', 'HTML', 'JSON', 'TEXT')",
            name="ck_notification_templates_format",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_notification_templates_name_version"),
    )
    _index("notification_templates", ("name", "format", "enabled"))
    op.create_table(
        "notification_plans",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("response_plan_id", sa.Uuid(), nullable=True),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("recipient_group", sa.String(128), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(256), nullable=False),
        sa.Column("deduplication_key", sa.String(256), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("suppression_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'SUPPRESSED', 'RUNNING', 'SENT', 'VERIFIED', 'FAILED')",
            name="ck_notification_plans_status",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_notification_plans_severity",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_notification_plans_priority",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["response_plan_id"], ["response_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plugin_id"], ["notification_plugins.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["template_id"], ["notification_templates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "notification_plans",
        (
            "incident_id",
            "response_plan_id",
            "plugin_id",
            "template_id",
            "capability",
            "recipient_group",
            "severity",
            "priority",
            "status",
            "requested_by",
            "deduplication_key",
        ),
    )
    op.create_table(
        "notification_executions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_status", sa.String(32), nullable=False),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["notification_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["notification_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "notification_executions",
        ("plan_id", "plugin_id", "status", "verification_status"),
    )
    op.create_table(
        "notification_evidence",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["notification_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["notification_executions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "notification_evidence",
        ("plan_id", "execution_id", "evidence_type", "sha256"),
    )
    op.create_table(
        "tickets",
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_tickets_priority",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
            name="ck_tickets_status",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "tickets",
        ("incident_id", "priority", "status", "external_reference", "created_by"),
    )


def downgrade() -> None:
    op.drop_table("tickets")
    op.drop_table("notification_evidence")
    op.drop_table("notification_executions")
    op.drop_table("notification_plans")
    op.drop_table("notification_templates")
    op.drop_table("notification_plugins")
