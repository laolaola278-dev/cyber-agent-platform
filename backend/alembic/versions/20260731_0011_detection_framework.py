"""Add Detection Framework and unified SecurityEvent.

Revision ID: 20260731_0011
Revises: 20260731_0010
Create Date: 2026-07-31 15:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0011"
down_revision: str | None = "20260731_0010"
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
        "detection_plugins",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_detection_plugins_name_version"),
    )
    _index("detection_plugins", ("name", "enabled"))
    op.create_table(
        "detection_capabilities",
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plugin_id"], ["detection_plugins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_id", "capability_id", name="uq_detection_capabilities_plugin_capability"
        ),
    )
    _index("detection_capabilities", ("plugin_id", "capability_id"))
    op.create_table(
        "detection_tasks",
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
            name="ck_detection_tasks_status",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["detection_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_detection_tasks_task_id"),
    )
    _index("detection_tasks", ("task_id", "plugin_id", "status"))
    op.create_table(
        "security_events",
        sa.Column("detection_task_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plugin", sa.String(length=128), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=True),
        sa.Column("rule", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_security_events_severity",
        ),
        sa.CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_security_events_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('NEW', 'CORRELATED', 'TRIAGED', 'IGNORED', 'ARCHIVED')",
            name="ck_security_events_status",
        ),
        sa.ForeignKeyConstraint(["detection_task_id"], ["detection_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "security_events",
        (
            "detection_task_id",
            "fingerprint",
            "event_type",
            "source",
            "severity",
            "confidence",
            "timestamp",
            "plugin",
            "tool",
            "rule",
            "status",
        ),
    )
    _link_tables()


def _link_tables() -> None:
    _simple_link("event_references", "url", sa.Text(), None, "uq_event_references_pair")
    _simple_link("event_evidence", "evidence_id", sa.Uuid(), "evidence", "uq_event_evidence_pair")
    _simple_link("event_assets", "asset_id", sa.Uuid(), "assets", "uq_event_assets_pair")
    op.create_table(
        "event_knowledge",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"], ["knowledge_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "knowledge_id", name="uq_event_knowledge_pair"),
    )
    _index("event_knowledge", ("event_id", "knowledge_id", "knowledge_version_id"))


def _simple_link(
    table: str,
    value_column: str,
    value_type: sa.types.TypeEngine[object],
    target_table: str | None,
    unique_name: str,
) -> None:
    columns: list[object] = [
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(value_column, value_type, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"], ondelete="CASCADE"),
    ]
    if target_table:
        columns.append(
            sa.ForeignKeyConstraint([value_column], [f"{target_table}.id"], ondelete="RESTRICT")
        )
    columns.extend(
        [
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", value_column, name=unique_name),
        ]
    )
    op.create_table(table, *columns)
    _index(table, ("event_id", value_column))


def downgrade() -> None:
    op.drop_table("event_knowledge")
    op.drop_table("event_assets")
    op.drop_table("event_evidence")
    op.drop_table("event_references")
    op.drop_table("security_events")
    op.drop_table("detection_tasks")
    op.drop_table("detection_capabilities")
    op.drop_table("detection_plugins")
