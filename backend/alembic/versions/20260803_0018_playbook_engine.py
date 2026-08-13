"""Create SOAR Playbook definitions and durable execution history.

Revision ID: 20260803_0018
Revises: 20260802_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0018"
down_revision: str | None = "20260802_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EXECUTION_STATES = (
    "'PENDING', 'RUNNING', 'WAITING_APPROVAL', 'SUCCEEDED', 'FAILED', "
    "'COMPENSATING', 'COMPENSATED', 'COMPENSATION_FAILED', 'TIMED_OUT', 'CANCELLED'"
)
STEP_STATES = (
    "'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'COMPENSATING', "
    "'COMPENSATED', 'COMPENSATION_FAILED', 'TIMED_OUT'"
)


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_playbooks"),
        sa.UniqueConstraint("name", name="uq_playbooks_name"),
    )
    op.create_index("ix_playbooks_name", "playbooks", ["name"], unique=False)
    op.create_index("ix_playbooks_enabled", "playbooks", ["enabled"], unique=False)

    op.create_table(
        "playbook_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("playbook_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("dsl_version", sa.String(length=16), nullable=False),
        sa.Column("source_yaml", sa.Text(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            ondelete="CASCADE",
            name="fk_playbook_versions_playbook_id_playbooks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_versions"),
        sa.UniqueConstraint("playbook_id", "version", name="uq_playbook_versions_playbook_version"),
    )
    op.create_index(
        "ix_playbook_versions_playbook_id", "playbook_versions", ["playbook_id"], unique=False
    )

    op.create_table(
        "playbook_triggers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("playbook_version_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["playbook_version_id"],
            ["playbook_versions.id"],
            ondelete="CASCADE",
            name="fk_playbook_triggers_playbook_version_id_playbook_versions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_triggers"),
        sa.UniqueConstraint(
            "playbook_version_id", "trigger_type", name="uq_playbook_triggers_version_type"
        ),
    )
    op.create_index(
        "ix_playbook_triggers_playbook_version_id",
        "playbook_triggers",
        ["playbook_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_playbook_triggers_trigger_type", "playbook_triggers", ["trigger_type"], unique=False
    )
    op.create_index("ix_playbook_triggers_enabled", "playbook_triggers", ["enabled"], unique=False)

    op.create_table(
        "playbook_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("playbook_id", sa.Uuid(), nullable=False),
        sa.Column("playbook_version_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            ondelete="RESTRICT",
            name="fk_playbook_executions_playbook_id_playbooks",
        ),
        sa.ForeignKeyConstraint(
            ["playbook_version_id"],
            ["playbook_versions.id"],
            ondelete="RESTRICT",
            name="fk_playbook_executions_playbook_version_id_playbook_versions",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["playbook_triggers.id"],
            ondelete="SET NULL",
            name="fk_playbook_executions_trigger_id_playbook_triggers",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_executions"),
        sa.CheckConstraint(f"status IN ({EXECUTION_STATES})", name="playbook_execution_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_playbook_executions_idempotency_key"),
    )
    for name, column in (
        ("playbook_id", "playbook_id"),
        ("playbook_version_id", "playbook_version_id"),
        ("trigger_id", "trigger_id"),
        ("trigger_type", "trigger_type"),
        ("status", "status"),
        ("trace_id", "trace_id"),
    ):
        op.create_index(
            f"ix_playbook_executions_{name}", "playbook_executions", [column], unique=False
        )

    op.create_table(
        "playbook_step_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("compensation_status", sa.String(length=32), nullable=True),
        sa.Column("compensation_output", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["playbook_executions.id"],
            ondelete="CASCADE",
            name="fk_playbook_step_executions_execution_id_playbook_executions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_step_executions"),
        sa.CheckConstraint(f"status IN ({STEP_STATES})", name="playbook_step_execution_status"),
        sa.UniqueConstraint("execution_id", "step_id", name="uq_playbook_step_execution_step"),
    )
    for name, column in (
        ("execution_id", "execution_id"),
        ("node_type", "node_type"),
        ("capability", "capability"),
        ("status", "status"),
    ):
        op.create_index(
            f"ix_playbook_step_executions_{name}",
            "playbook_step_executions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for name in ("capability", "status", "node_type", "execution_id"):
        op.drop_index(f"ix_playbook_step_executions_{name}", table_name="playbook_step_executions")
    op.drop_table("playbook_step_executions")
    for name in (
        "trace_id",
        "status",
        "trigger_type",
        "trigger_id",
        "playbook_version_id",
        "playbook_id",
    ):
        op.drop_index(f"ix_playbook_executions_{name}", table_name="playbook_executions")
    op.drop_table("playbook_executions")
    op.drop_index("ix_playbook_triggers_enabled", table_name="playbook_triggers")
    op.drop_index("ix_playbook_triggers_trigger_type", table_name="playbook_triggers")
    op.drop_index("ix_playbook_triggers_playbook_version_id", table_name="playbook_triggers")
    op.drop_table("playbook_triggers")
    op.drop_index("ix_playbook_versions_playbook_id", table_name="playbook_versions")
    op.drop_table("playbook_versions")
    op.drop_index("ix_playbooks_enabled", table_name="playbooks")
    op.drop_index("ix_playbooks_name", table_name="playbooks")
    op.drop_table("playbooks")
