"""Add Incident and Investigation Case Management Framework.

Revision ID: 20260731_0012
Revises: 20260731_0011
Create Date: 2026-07-31 19:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0012"
down_revision: str | None = "20260731_0011"
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
    _create_incidents()
    _create_incident_children()
    _create_investigation_cases()
    _create_cross_domain_links()


def _create_incidents() -> None:
    op.create_table(
        "incidents",
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=True),
        sa.Column("assignee", sa.String(length=256), nullable=True),
        sa.Column("queue", sa.String(length=128), nullable=True),
        sa.Column("classification", sa.String(length=128), nullable=True),
        sa.Column("risk", sa.String(length=64), nullable=True),
        sa.Column("correlation_key", sa.String(length=256), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_incidents_severity",
        ),
        sa.CheckConstraint(
            "confidence IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_incidents_confidence"
        ),
        sa.CheckConstraint("priority IN ('P1', 'P2', 'P3', 'P4')", name="ck_incidents_priority"),
        sa.CheckConstraint(
            "status IN ('NEW', 'TRIAGED', 'INVESTIGATING', 'CONTAINED', "
            "'RESOLVED', 'CLOSED', 'REOPENED')",
            name="ck_incidents_status",
        ),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index(
        "incidents",
        (
            "title",
            "severity",
            "priority",
            "status",
            "confidence",
            "source",
            "owner",
            "assignee",
            "queue",
            "classification",
            "risk",
            "correlation_key",
            "duplicate_of_id",
            "sla_due_at",
        ),
    )


def _create_incident_children() -> None:
    op.create_table(
        "incident_timelines",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("incident_timelines", ("incident_id", "event_type", "actor"))
    op.create_table(
        "incident_artifacts",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "artifact_type IN ('ASSET', 'EVIDENCE', 'FINDING', 'SECURITY_EVENT', "
            "'KNOWLEDGE', 'REPORT', 'URL', 'HASH', 'IP', 'DOMAIN')",
            name="ck_incident_artifacts_type",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("incident_artifacts", ("incident_id", "artifact_type", "reference_id"))


def _create_investigation_cases() -> None:
    op.create_table(
        "investigation_cases",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=True),
        sa.Column("assignee", sa.String(length=256), nullable=True),
        sa.Column("queue", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CLOSED')",
            name="ck_investigation_cases_status",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("investigation_cases", ("incident_id", "status", "owner", "assignee", "queue"))
    op.create_table(
        "case_comments",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index("case_comments", ("case_id", "author"))


def _create_cross_domain_links() -> None:
    _link_table("incident_findings", "finding_id", "findings", "uq_incident_findings_pair")
    _link_table("incident_events", "event_id", "security_events", "uq_incident_events_pair")
    _link_table("incident_assets", "asset_id", "assets", "uq_incident_assets_pair")
    op.create_table(
        "incident_knowledge",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"], ["knowledge_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "knowledge_id", name="uq_incident_knowledge_pair"),
    )
    _index("incident_knowledge", ("incident_id", "knowledge_id", "knowledge_version_id"))


def _link_table(table: str, value_column: str, target: str, unique_name: str) -> None:
    columns: list[object] = [
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(value_column, sa.Uuid(), nullable=False),
    ]
    if table in {"incident_findings", "incident_events"}:
        columns.append(sa.Column("relation", sa.String(length=64), nullable=False))
    columns.extend(
        [
            sa.Column("id", sa.Uuid(), nullable=False),
            *_timestamps(),
            sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint([value_column], [f"{target}.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("incident_id", value_column, name=unique_name),
        ]
    )
    op.create_table(table, *columns)
    _index(table, ("incident_id", value_column))


def downgrade() -> None:
    op.drop_table("incident_knowledge")
    op.drop_table("incident_assets")
    op.drop_table("incident_events")
    op.drop_table("incident_findings")
    op.drop_table("case_comments")
    op.drop_table("investigation_cases")
    op.drop_table("incident_artifacts")
    op.drop_table("incident_timelines")
    op.drop_table("incidents")
