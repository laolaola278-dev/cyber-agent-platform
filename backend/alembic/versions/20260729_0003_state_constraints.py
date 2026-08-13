"""Add database-level Agent and Task status constraints.

Revision ID: 20260729_0003
Revises: 20260729_0002
Create Date: 2026-07-29 18:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260729_0003"
down_revision: str | None = "20260729_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('ONLINE', 'OFFLINE', 'STARTING', 'STOPPING', 'ERROR')",
    )
    op.create_check_constraint(
        "ck_tasks_status",
        "tasks",
        "status IN ('CREATED', 'QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
    )
    op.create_check_constraint(
        "ck_task_executions_status",
        "task_executions",
        "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_task_executions_status", "task_executions", type_="check")
    op.drop_constraint("ck_tasks_status", "tasks", type_="check")
    op.drop_constraint("ck_agents_status", "agents", type_="check")
