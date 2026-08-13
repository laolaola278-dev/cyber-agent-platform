"""Add Telemetry and Stream Framework control-plane tables.

Revision ID: 20260801_0013
Revises: 20260731_0012
Create Date: 2026-08-01 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260801_0013"
down_revision: str | None = "20260731_0012"
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
        "telemetry_pipelines",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("receivers", sa.JSON(), nullable=False),
        sa.Column("processors", sa.JSON(), nullable=False),
        sa.Column("exporters", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_telemetry_pipelines_name_version"),
    )
    _index("telemetry_pipelines", ("name", "enabled"))
    op.create_table(
        "telemetry_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stream", sa.String(256), nullable=False),
        sa.Column("partition", sa.String(128), nullable=False),
        sa.Column("consumer", sa.String(128), nullable=False),
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
            name="ck_telemetry_tasks_status",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["telemetry_pipelines.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_telemetry_tasks_task_id"),
    )
    _index(
        "telemetry_tasks",
        ("task_id", "pipeline_id", "plugin_name", "status", "stream", "partition", "consumer"),
    )
    op.create_table(
        "telemetry_checkpoints",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("stream", sa.String(256), nullable=False),
        sa.Column("partition", sa.String(128), nullable=False),
        sa.Column("consumer", sa.String(128), nullable=False),
        sa.Column(sa.quoted_name("offset", True), sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('"offset" >= 0', name="ck_telemetry_checkpoints_offset"),
        sa.CheckConstraint("sequence >= 0", name="ck_telemetry_checkpoints_sequence"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stream", "partition", "consumer", name="uq_telemetry_checkpoint_cursor"
        ),
    )
    _index("telemetry_checkpoints", ("provider", "stream", "partition", "consumer", "committed_at"))
    op.create_table(
        "telemetry_runtime_states",
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stream", sa.String(256), nullable=True),
        sa.Column("partition", sa.String(128), nullable=True),
        sa.Column("consumer", sa.String(128), nullable=True),
        sa.Column("current_offset", sa.Integer(), nullable=True),
        sa.Column("lag", sa.Integer(), nullable=False),
        sa.Column("queue_depth", sa.Integer(), nullable=False),
        sa.Column("backpressure_action", sa.String(32), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('IDLE', 'RUNNING', 'PAUSED', 'FAILED', 'STOPPED')",
            name="ck_telemetry_runtime_states_status",
        ),
        sa.CheckConstraint("lag >= 0", name="ck_telemetry_runtime_states_lag"),
        sa.CheckConstraint("queue_depth >= 0", name="ck_telemetry_runtime_states_queue_depth"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["telemetry_pipelines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_id", name="uq_telemetry_runtime_states_worker_id"),
    )
    _index(
        "telemetry_runtime_states", ("worker_id", "pipeline_id", "status", "stream", "heartbeat_at")
    )


def downgrade() -> None:
    op.drop_table("telemetry_runtime_states")
    op.drop_table("telemetry_checkpoints")
    op.drop_table("telemetry_tasks")
    op.drop_table("telemetry_pipelines")
