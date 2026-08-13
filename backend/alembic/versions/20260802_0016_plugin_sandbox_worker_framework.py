"""Add Plugin Sandbox and Worker Framework control-plane tables.

Revision ID: 20260802_0016
Revises: 20260801_0015
Create Date: 2026-08-02 05:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_0016"
down_revision: str | None = "20260801_0015"
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
        "workers",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("runtime_version", sa.String(64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("active_executions", sa.Integer(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_workers_name"),
    )
    _index("workers", ("name", "runtime_version", "status", "last_heartbeat_at"))

    op.create_table(
        "sandbox_profiles",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("policy_checksum", sa.String(64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_sandbox_profiles_name_version"),
    )
    _index("sandbox_profiles", ("name", "provider", "enabled", "policy_checksum"))

    op.create_table(
        "secret_references",
        sa.Column("reference", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_secret_references_reference"),
    )
    _index("secret_references", ("reference", "provider", "enabled"))

    op.create_table(
        "worker_leases",
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("owner", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_worker_leases_execution_id"),
    )
    _index("worker_leases", ("worker_id", "execution_id", "owner", "status", "expires_at"))

    op.create_table(
        "sandbox_executions",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("plugin_name", sa.String(128), nullable=False),
        sa.Column("plugin_version", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_metadata", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.Column("terminated", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_id"], ["sandbox_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_sandbox_executions_execution_id"),
    )
    _index(
        "sandbox_executions",
        (
            "execution_id",
            "worker_id",
            "profile_id",
            "plugin_name",
            "operation",
            "provider",
            "status",
        ),
    )


def downgrade() -> None:
    op.drop_table("sandbox_executions")
    op.drop_table("worker_leases")
    op.drop_table("secret_references")
    op.drop_table("sandbox_profiles")
    op.drop_table("workers")
