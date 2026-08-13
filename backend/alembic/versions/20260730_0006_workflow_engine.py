"""Add durable DAG Workflow Engine persistence.

Revision ID: 20260730_0006
Revises: 20260729_0005
Create Date: 2026-07-30 19:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0006"
down_revision: str | None = "20260729_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKFLOW_STATES = "'PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED'"
STEP_STATES = "'PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED', 'SKIPPED'"


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
        "workflow_definitions",
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_yaml", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_workflow_definitions_name_version"),
    )
    op.create_index("ix_workflow_definitions_name", "workflow_definitions", ["name"])
    op.create_index("ix_workflow_definitions_enabled", "workflow_definitions", ["enabled"])

    op.create_table(
        "workflow_instances",
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("current_node", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"status IN ({WORKFLOW_STATES})", name="ck_workflow_instances_workflow_instance_status"
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["workflow_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("definition_id", "status", "trace_id"):
        op.create_index(f"ix_workflow_instances_{column}", "workflow_instances", [column])

    op.create_table(
        "workflow_steps",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"status IN ({STEP_STATES})", name="ck_workflow_steps_workflow_step_status"
        ),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "node_id", name="uq_workflow_steps_instance_node"),
    )
    for column in ("instance_id", "node_type", "capability", "status"):
        op.create_index(f"ix_workflow_steps_{column}", "workflow_steps", [column])

    op.create_table(
        "workflow_executions",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            f"status IN ({STEP_STATES})", name="ck_workflow_executions_workflow_execution_status"
        ),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("instance_id", "step_id", "status"):
        op.create_index(f"ix_workflow_executions_{column}", "workflow_executions", [column])


def downgrade() -> None:
    op.drop_table("workflow_executions")
    op.drop_table("workflow_steps")
    op.drop_table("workflow_instances")
    op.drop_table("workflow_definitions")
