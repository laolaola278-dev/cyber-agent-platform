"""Enforce Worker state versions, lease fencing and execution history.

Revision ID: 20260802_0017
Revises: 20260802_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_0017"
down_revision: str | None = "20260802_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "worker_leases",
        sa.Column("fencing_token", sa.Uuid(), nullable=True),
    )
    op.execute(sa.text("UPDATE worker_leases SET fencing_token = id WHERE fencing_token IS NULL"))
    op.alter_column("worker_leases", "fencing_token", nullable=False)
    op.create_index(
        "ix_worker_leases_fencing_token", "worker_leases", ["fencing_token"], unique=True
    )
    op.add_column("sandbox_executions", sa.Column("lease_id", sa.Uuid(), nullable=True))
    op.add_column("sandbox_executions", sa.Column("lease_version", sa.Integer(), nullable=True))
    op.add_column(
        "sandbox_executions",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "sandbox_executions", sa.Column("recovery_of_execution_id", sa.Uuid(), nullable=True)
    )
    op.create_index("ix_sandbox_executions_lease_id", "sandbox_executions", ["lease_id"])
    op.create_index(
        "ix_sandbox_executions_recovery_of_execution_id",
        "sandbox_executions",
        ["recovery_of_execution_id"],
    )
    op.create_foreign_key(
        "fk_sandbox_executions_lease_id",
        "sandbox_executions",
        "worker_leases",
        ["lease_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sandbox_executions_lease_id", "sandbox_executions", type_="foreignkey")
    op.drop_index("ix_sandbox_executions_recovery_of_execution_id", table_name="sandbox_executions")
    op.drop_index("ix_sandbox_executions_lease_id", table_name="sandbox_executions")
    op.drop_column("sandbox_executions", "recovery_of_execution_id")
    op.drop_column("sandbox_executions", "attempt")
    op.drop_column("sandbox_executions", "lease_version")
    op.drop_column("sandbox_executions", "lease_id")
    op.drop_index("ix_worker_leases_fencing_token", table_name="worker_leases")
    op.drop_column("worker_leases", "fencing_token")
    op.drop_column("workers", "state_version")
