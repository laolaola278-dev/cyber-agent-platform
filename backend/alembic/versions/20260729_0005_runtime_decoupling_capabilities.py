"""Add capability dispatch and extensible evidence/report metadata.

Revision ID: 20260729_0005
Revises: 20260729_0004
Create Date: 2026-07-29 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0005"
down_revision: str | None = "20260729_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("capabilities", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "agents",
        sa.Column(
            "minimum_runtime_version",
            sa.String(length=64),
            server_default="1.0.0",
            nullable=False,
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "platform_version",
            sa.String(length=64),
            server_default="0.2.1",
            nullable=False,
        ),
    )
    op.add_column(
        "agents",
        sa.Column("sdk_version", sa.String(length=64), server_default="1.0.0", nullable=False),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "required_capabilities",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )

    op.create_table(
        "capabilities",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_capabilities_name", "capabilities", ["name"], unique=True)
    op.create_index("ix_capabilities_risk_level", "capabilities", ["risk_level"])

    op.create_table(
        "agent_capabilities",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "capability_id",
            name="uq_agent_capabilities_agent_capability",
        ),
    )
    op.create_index("ix_agent_capabilities_agent_id", "agent_capabilities", ["agent_id"])
    op.create_index(
        "ix_agent_capabilities_capability_id",
        "agent_capabilities",
        ["capability_id"],
    )

    op.add_column(
        "evidence",
        sa.Column("evidence_type", sa.String(length=32), server_default="HTML", nullable=False),
    )
    op.add_column("evidence", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.execute("UPDATE evidence SET sha256 = html_hash")
    op.alter_column("evidence", "sha256", nullable=False)
    op.add_column(
        "evidence",
        sa.Column(
            "content_type",
            sa.String(length=255),
            server_default="text/html; charset=utf-8",
            nullable=False,
        ),
    )
    op.add_column(
        "evidence", sa.Column("object_storage_path", sa.String(length=1024), nullable=True)
    )
    op.create_index("ix_evidence_evidence_type", "evidence", ["evidence_type"])
    op.add_column(
        "reports", sa.Column("html_content", sa.Text(), server_default="", nullable=False)
    )

    for table_name, column_name in (
        ("agents", "capabilities"),
        ("agents", "minimum_runtime_version"),
        ("agents", "platform_version"),
        ("agents", "sdk_version"),
        ("tasks", "required_capabilities"),
        ("evidence", "evidence_type"),
        ("evidence", "content_type"),
        ("reports", "html_content"),
    ):
        op.alter_column(table_name, column_name, server_default=None)


def downgrade() -> None:
    op.drop_column("reports", "html_content")
    op.drop_index("ix_evidence_evidence_type", table_name="evidence")
    op.drop_column("evidence", "object_storage_path")
    op.drop_column("evidence", "content_type")
    op.drop_column("evidence", "sha256")
    op.drop_column("evidence", "evidence_type")
    op.drop_table("agent_capabilities")
    op.drop_table("capabilities")
    op.drop_column("tasks", "required_capabilities")
    op.drop_column("agents", "sdk_version")
    op.drop_column("agents", "platform_version")
    op.drop_column("agents", "minimum_runtime_version")
    op.drop_column("agents", "capabilities")
