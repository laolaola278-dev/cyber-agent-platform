"""Add unified Knowledge Center.

Revision ID: 20260730_0008
Revises: 20260730_0007
Create Date: 2026-07-30 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0008"
down_revision: str | None = "20260730_0007"
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


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider_type", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_knowledge_sources_name"),
    )
    for column in ("name", "provider_type", "enabled"):
        op.create_index(f"ix_knowledge_sources_{column}", "knowledge_sources", [column])

    op.create_table(
        "knowledge",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("current_version", sa.String(length=256), nullable=False),
        sa.Column("current_content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("references", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "knowledge_type",
            "external_id",
            name="uq_knowledge_source_type_external_id",
        ),
    )
    for column in (
        "source_id",
        "knowledge_type",
        "external_id",
        "current_version",
        "current_content_hash",
        "title",
        "status",
    ):
        op.create_index(f"ix_knowledge_{column}", "knowledge", [column])

    op.create_table(
        "knowledge_versions",
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=256), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_id",
            "version",
            "content_hash",
            name="uq_knowledge_version_snapshot",
        ),
    )
    for column in ("knowledge_id", "version", "content_hash", "imported_at"):
        op.create_index(f"ix_knowledge_versions_{column}", "knowledge_versions", [column])

    op.create_table(
        "knowledge_relations",
        sa.Column("source_knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("target_knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_knowledge_id",
            "target_knowledge_id",
            "relation_type",
            name="uq_knowledge_relations_source_target_type",
        ),
    )
    for column in (
        "source_knowledge_id",
        "target_knowledge_id",
        "relation_type",
        "source_name",
    ):
        op.create_index(f"ix_knowledge_relations_{column}", "knowledge_relations", [column])

    _create_link_table(
        "asset_knowledge", "asset_id", "assets", "uq_asset_knowledge_asset_knowledge"
    )
    _create_link_table(
        "evidence_knowledge",
        "evidence_id",
        "evidence",
        "uq_evidence_knowledge_evidence_knowledge",
    )
    _create_link_table(
        "report_knowledge", "report_id", "reports", "uq_report_knowledge_report_knowledge"
    )


def _create_link_table(
    table_name: str, owner_column: str, owner_table: str, unique_name: str
) -> None:
    op.create_table(
        table_name,
        sa.Column(owner_column, sa.Uuid(), nullable=False),
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"], ["knowledge_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(owner_column, "knowledge_id", name=unique_name),
    )
    for column in (owner_column, "knowledge_id", "knowledge_version_id"):
        op.create_index(f"ix_{table_name}_{column}", table_name, [column])


def downgrade() -> None:
    op.drop_table("report_knowledge")
    op.drop_table("evidence_knowledge")
    op.drop_table("asset_knowledge")
    op.drop_table("knowledge_relations")
    op.drop_table("knowledge_versions")
    op.drop_table("knowledge")
    op.drop_table("knowledge_sources")
