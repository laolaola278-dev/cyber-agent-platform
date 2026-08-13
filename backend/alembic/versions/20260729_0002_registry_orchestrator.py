"""Add Registry, heartbeat, task lifecycle, and execution log primitives.

Revision ID: 20260729_0002
Revises: 20260729_0001
Create Date: 2026-07-29 15:47:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0002"
down_revision: str | None = "20260729_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_agents_name_version", "agents", type_="unique")
    op.create_unique_constraint("uq_agents_name", "agents", ["name"])
    op.drop_constraint("uq_tools_name_version", "tools", type_="unique")
    op.create_unique_constraint("uq_tools_name", "tools", ["name"])

    op.add_column(
        "audit_logs",
        sa.Column("trace_id", sa.String(length=64), server_default="-", nullable=False),
    )
    op.add_column("audit_logs", sa.Column("agent_id", sa.String(length=36), nullable=True))
    op.add_column("audit_logs", sa.Column("task_id", sa.String(length=36), nullable=True))
    op.add_column("audit_logs", sa.Column("tool_id", sa.String(length=36), nullable=True))
    op.add_column("audit_logs", sa.Column("result", sa.JSON(), nullable=True))
    op.add_column("audit_logs", sa.Column("error", sa.String(length=2048), nullable=True))
    op.create_index(op.f("ix_audit_logs_trace_id"), "audit_logs", ["trace_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_agent_id"), "audit_logs", ["agent_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_task_id"), "audit_logs", ["task_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_tool_id"), "audit_logs", ["tool_id"], unique=False)

    op.add_column(
        "agents",
        sa.Column("author", sa.String(length=256), server_default="system", nullable=False),
    )
    op.add_column(
        "agents",
        sa.Column("runtime", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )
    op.add_column(
        "agents",
        sa.Column(
            "network_policy", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "resource_limit", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "approval_policy", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
    )
    op.add_column(
        "agents",
        sa.Column("health_status", sa.String(length=32), server_default="UNKNOWN", nullable=False),
    )
    op.add_column("agents", sa.Column("heartbeat_time", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_agents_health_status"), "agents", ["health_status"], unique=False)

    op.create_table(
        "agent_versions",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index(
        op.f("ix_agent_versions_agent_id"), "agent_versions", ["agent_id"], unique=False
    )

    op.create_table(
        "agent_heartbeats",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_heartbeats_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_heartbeats")),
    )
    op.create_index(
        op.f("ix_agent_heartbeats_agent_id"), "agent_heartbeats", ["agent_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_heartbeats_timestamp"), "agent_heartbeats", ["timestamp"], unique=False
    )

    op.alter_column("tools", "type", new_column_name="tool_type")
    op.alter_column("tools", "config", new_column_name="config_schema")
    op.add_column(
        "tools",
        sa.Column(
            "required_permissions", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )
    op.add_column(
        "tools",
        sa.Column(
            "runtime_requirements", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
    )
    op.add_column(
        "tools",
        sa.Column("status", sa.String(length=32), server_default="ENABLED", nullable=False),
    )
    op.add_column(
        "tools",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "tools",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_tools_status"), "tools", ["status"], unique=False)

    op.create_table(
        "tool_versions",
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tools.id"],
            name=op.f("fk_tool_versions_tool_id_tools"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_versions")),
        sa.UniqueConstraint("tool_id", "version", name="uq_tool_versions_tool_version"),
    )
    op.create_index(op.f("ix_tool_versions_tool_id"), "tool_versions", ["tool_id"], unique=False)

    op.add_column(
        "tasks",
        sa.Column(
            "required_permissions", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )
    op.add_column("tasks", sa.Column("target_agent_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_tasks_target_agent_id"), "tasks", ["target_agent_id"], unique=False)
    op.execute("UPDATE tasks SET status = 'CREATED' WHERE status = 'pending'")
    op.alter_column("tasks", "status", server_default="CREATED")

    op.create_table(
        "task_logs",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_task_logs_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_logs")),
    )
    op.create_index(op.f("ix_task_logs_task_id"), "task_logs", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_logs_trace_id"), "task_logs", ["trace_id"], unique=False)

    op.add_column(
        "task_executions",
        sa.Column("trace_id", sa.String(length=64), server_default="-", nullable=False),
    )
    op.execute("UPDATE task_executions SET status = 'QUEUED' WHERE status = 'queued'")
    op.alter_column("task_executions", "status", server_default="QUEUED")
    op.create_index(
        op.f("ix_task_executions_trace_id"), "task_executions", ["trace_id"], unique=False
    )

    op.create_table(
        "execution_logs",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            name=op.f("fk_execution_logs_execution_id_task_executions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_logs")),
    )
    op.create_index(
        op.f("ix_execution_logs_execution_id"), "execution_logs", ["execution_id"], unique=False
    )
    op.create_index(
        op.f("ix_execution_logs_timestamp"), "execution_logs", ["timestamp"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_execution_logs_timestamp"), table_name="execution_logs")
    op.drop_index(op.f("ix_execution_logs_execution_id"), table_name="execution_logs")
    op.drop_table("execution_logs")
    op.drop_index(op.f("ix_task_executions_trace_id"), table_name="task_executions")
    op.drop_column("task_executions", "trace_id")
    op.drop_index(op.f("ix_task_logs_trace_id"), table_name="task_logs")
    op.drop_index(op.f("ix_task_logs_task_id"), table_name="task_logs")
    op.drop_table("task_logs")
    op.drop_index(op.f("ix_tasks_target_agent_id"), table_name="tasks")
    op.drop_column("tasks", "target_agent_id")
    op.drop_column("tasks", "required_permissions")
    op.drop_index(op.f("ix_tool_versions_tool_id"), table_name="tool_versions")
    op.drop_table("tool_versions")
    op.drop_index(op.f("ix_tools_status"), table_name="tools")
    op.drop_column("tools", "updated_at")
    op.drop_column("tools", "created_at")
    op.drop_column("tools", "status")
    op.drop_column("tools", "runtime_requirements")
    op.drop_column("tools", "required_permissions")
    op.alter_column("tools", "config_schema", new_column_name="config")
    op.alter_column("tools", "tool_type", new_column_name="type")
    op.drop_index(op.f("ix_agent_heartbeats_timestamp"), table_name="agent_heartbeats")
    op.drop_index(op.f("ix_agent_heartbeats_agent_id"), table_name="agent_heartbeats")
    op.drop_table("agent_heartbeats")
    op.drop_index(op.f("ix_agent_versions_agent_id"), table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_index(op.f("ix_agents_health_status"), table_name="agents")
    op.drop_column("agents", "heartbeat_time")
    op.drop_column("agents", "health_status")
    op.drop_column("agents", "approval_policy")
    op.drop_column("agents", "resource_limit")
    op.drop_column("agents", "network_policy")
    op.drop_column("agents", "runtime")
    op.drop_column("agents", "author")
    op.drop_constraint("uq_tools_name", "tools", type_="unique")
    op.create_unique_constraint("uq_tools_name_version", "tools", ["name", "version"])
    op.drop_constraint("uq_agents_name", "agents", type_="unique")
    op.create_unique_constraint("uq_agents_name_version", "agents", ["name", "version"])
    op.drop_index(op.f("ix_audit_logs_tool_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_task_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_agent_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_trace_id"), table_name="audit_logs")
    op.drop_column("audit_logs", "error")
    op.drop_column("audit_logs", "result")
    op.drop_column("audit_logs", "tool_id")
    op.drop_column("audit_logs", "task_id")
    op.drop_column("audit_logs", "agent_id")
    op.drop_column("audit_logs", "trace_id")
