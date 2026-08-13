"""Phase 5 unified Knowledge Center tests."""

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import KnowledgeType
from app.database import Base
from app.events import InMemoryEventBus
from app.events.audit import AuditSubscriber
from app.exceptions import KnowledgeNotFound, KnowledgeValidationError
from app.knowledge import (
    JSONKnowledgeImporter,
    KnowledgeImporter,
    KnowledgeRegistry,
    KnowledgeResolver,
)
from app.knowledge.service import KnowledgeService
from app.models import (
    Agent,
    Asset,
    AssetKnowledge,
    AuditLog,
    Evidence,
    EvidenceKnowledge,
    KnowledgeRelation,
    KnowledgeSource,
    KnowledgeVersion,
    ReportKnowledge,
    Task,
)
from app.report.service import ReportService
from app.repositories import (
    AuditRepository,
    KnowledgeRepository,
    KnowledgeSourceRepository,
)
from app.services.audit import AuditService
from tests.conftest import TestSessionFactory


def _service(session: AsyncSession) -> KnowledgeService:
    bus = InMemoryEventBus()
    AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
    registry = KnowledgeRegistry()
    registry.register_importer(JSONKnowledgeImporter())
    repository = KnowledgeRepository(session)
    importer = KnowledgeImporter(
        session,
        repository,
        KnowledgeSourceRepository(session),
        bus,
        registry,
        KnowledgeResolver(registry),
    )
    return KnowledgeService(session, repository, importer, bus)


def _records(
    version: str = "2026.1", title: str = "Example vulnerability"
) -> list[dict[str, object]]:
    return [
        {
            "knowledge_type": "CWE",
            "external_id": "CWE-79",
            "version": "4.18",
            "title": "Improper Neutralization of Input",
            "description": "Cross-site scripting weakness class",
            "references": ["https://cwe.mitre.org/data/definitions/79.html"],
        },
        {
            "knowledge_type": "CPE",
            "external_id": "cpe:2.3:a:example:server:1.0:*:*:*:*:*:*:*",
            "version": "2.3",
            "title": "Example Server 1.0",
        },
        {
            "knowledge_type": "CVE",
            "external_id": "cve-2026-1234",
            "version": version,
            "title": title,
            "description": "A public test vulnerability used by CAP tests",
            "references": [
                "https://example.com/advisory",
                "https://example.com/advisory",
            ],
            "attributes": {"cvss": 9.8, "vendor": "Example"},
            "relations": [
                {
                    "target_type": "CWE",
                    "target_external_id": "CWE-79",
                    "relation_type": "maps_to",
                },
                {
                    "target_type": "CPE",
                    "target_external_id": "cpe:2.3:a:example:server:1.0:*:*:*:*:*:*:*",
                    "relation_type": "affects",
                },
            ],
        },
    ]


async def test_json_import_versions_relations_search_and_audit() -> None:
    async with TestSessionFactory() as session:
        service = _service(session)
        first = await service.import_payload(
            source="cvelistV5",
            provider="cve",
            format_name="json",
            payload={"records": _records()},
            trace_id="knowledge-import-1",
        )
        assert first.imported == 3
        assert first.unchanged == 0
        assert first.relations == 2

        unchanged = await service.import_payload(
            source="cvelistV5",
            provider="cve",
            format_name="json",
            payload=_records(),
            trace_id="knowledge-import-2",
        )
        assert unchanged.imported == 0
        assert unchanged.unchanged == 3
        assert unchanged.relations == 0

        updated = await service.import_payload(
            source="cvelistV5",
            provider="cve",
            format_name="json",
            payload=_records(version="2026.2", title="Updated vulnerability"),
            trace_id="knowledge-import-3",
        )
        assert updated.imported == 1
        assert updated.unchanged == 2

        cve = await service.get_by_external_id("CVE", "cve-2026-1234")
        assert cve.external_id == "CVE-2026-1234"
        assert cve.current_version == "2026.2"
        assert cve.title == "Updated vulnerability"
        assert cve.references == ["https://example.com/advisory"]
        assert len(cve.versions) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeVersion)
                .where(KnowledgeVersion.knowledge_id == cve.id)
            )
            == 2
        )
        assert await session.scalar(select(func.count()).select_from(KnowledgeRelation)) == 2
        assert (await service.search(query="public test", knowledge_type="CVE")).total == 1
        assert (await service.search(query="CVE-2026-1234")).total == 1
        assert (await service.search(source="CVELISTV5", status="ACTIVE")).total == 3

        actions = set(
            await session.scalars(
                select(AuditLog.action).where(
                    AuditLog.trace_id.in_(
                        ["knowledge-import-1", "knowledge-import-2", "knowledge-import-3"]
                    )
                )
            )
        )
        assert {
            "KnowledgeImported",
            "KnowledgeVersionCreated",
            "KnowledgeRelationCreated",
        } <= actions


async def test_import_validation_registry_and_resolver_extension() -> None:
    importer = JSONKnowledgeImporter()
    with pytest.raises(KnowledgeValidationError, match="object or array"):
        importer.parse("invalid", source="source")
    with pytest.raises(KnowledgeValidationError, match="must be an object"):
        importer.parse(["invalid"], source="source")
    with pytest.raises(KnowledgeValidationError, match="must match"):
        importer.parse([{"source": "other", "knowledge_type": "CVE"}], source="source")
    with pytest.raises(KnowledgeValidationError, match="Invalid JSON"):
        importer.parse([{"knowledge_type": "CVE"}], source="source")

    registry = KnowledgeRegistry()
    registry.register_type("CUSTOM_KNOWLEDGE")
    registry.register_relation("custom_relation")
    resolver = KnowledgeResolver(registry)
    assert resolver.canonical_external_id("CUSTOM_KNOWLEDGE", " Mixed-ID ") == "mixed-id"
    assert resolver.relation_type("CUSTOM_RELATION") == "custom_relation"
    assert "CUSTOM_KNOWLEDGE" in registry.knowledge_types
    with pytest.raises(KnowledgeValidationError, match="Unsupported knowledge type"):
        registry.require_type("UNKNOWN")
    with pytest.raises(KnowledgeValidationError, match="Unsupported knowledge relation"):
        registry.require_relation("unknown")
    with pytest.raises(KnowledgeValidationError, match="Unknown knowledge provider"):
        registry.require_provider("missing")
    with pytest.raises(KnowledgeValidationError, match="Unsupported import format"):
        registry.require_importer("yaml")


async def test_source_disable_missing_relation_and_not_found() -> None:
    async with TestSessionFactory() as session:
        service = _service(session)
        with pytest.raises(KnowledgeNotFound):
            await service.get(UUID("00000000-0000-0000-0000-000000000001"))
        with pytest.raises(KnowledgeNotFound):
            await service.get_by_external_id("CVE", "CVE-0-0")
        with pytest.raises(KnowledgeValidationError, match="Relation target not found"):
            await service.import_payload(
                source="vendor",
                provider="vendor",
                format_name="json",
                payload={
                    "knowledge_type": "VENDOR_ADVISORY",
                    "external_id": "ADV-1",
                    "version": "1",
                    "title": "Advisory",
                    "relations": [
                        {
                            "target_type": "CVE",
                            "target_external_id": "CVE-2099-1",
                            "relation_type": "related_to",
                        }
                    ],
                },
                trace_id="missing-target",
            )
        await session.rollback()
        source = KnowledgeSource(
            name="disabled", provider_type="json", enabled=False, configuration={}
        )
        session.add(source)
        await session.commit()
        with pytest.raises(KnowledgeValidationError, match="is disabled"):
            await service.import_payload(
                source="disabled",
                provider="json",
                format_name="json",
                payload=_records()[0],
                trace_id="disabled-source",
            )


async def test_asset_evidence_report_links_snapshot_current_version(tmp_path: Path) -> None:
    async with TestSessionFactory() as session:
        service = _service(session)
        result = await service.import_payload(
            source="cvelistV5",
            provider="cve",
            format_name="json",
            payload=_records(),
            trace_id="link-import",
        )
        cve = await service.get_by_external_id("CVE", "CVE-2026-1234")
        agent = Agent(name="knowledge-agent", version="1.0.0", status="ONLINE")
        asset = Asset(
            asset_type="APPLICATION",
            name="Example App",
            value="Example App",
            canonical_value="example app",
            capabilities=[],
            properties={},
        )
        task = Task(name="assessment", task_type="assessment", status="RUNNING", asset_id=None)
        session.add_all([agent, asset, task])
        await session.flush()
        await service.link_asset(asset.id, cve.id, trace_id="link-asset")
        evidence = Evidence(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="link-evidence",
            url="https://example.com",
            evidence_type="JSON",
            sha256="a" * 64,
            content_type="application/json",
            html_hash="b" * 64,
            content_hash="c" * 64,
        )
        session.add(evidence)
        await session.flush()
        await service.link_evidence(evidence.id, cve.id, trace_id="link-evidence")
        report = await ReportService(session, service._publisher).generate(
            task=task,
            agent_id=agent.id,
            trace_id="link-report",
            status="SUCCESS",
        )
        await session.commit()

        assert result.knowledge_ids
        asset_link = await session.scalar(select(AssetKnowledge))
        evidence_link = await session.scalar(select(EvidenceKnowledge))
        report_link = await session.scalar(select(ReportKnowledge))
        assert asset_link is not None
        assert evidence_link is not None
        assert report_link is not None
        assert asset_link.knowledge_version_id == evidence_link.knowledge_version_id
        assert report_link.knowledge_version_id == evidence_link.knowledge_version_id
        assert report.json_content["knowledge"][0]["external_id"] == "CVE-2026-1234"
        actions = set(
            await session.scalars(
                select(AuditLog.action).where(
                    AuditLog.trace_id.in_(["link-asset", "link-evidence", "link-report"])
                )
            )
        )
        assert {
            "AssetKnowledgeLinked",
            "EvidenceKnowledgeLinked",
            "ReportKnowledgeLinked",
        } <= actions


async def test_knowledge_api_import_list_search_and_typed_lookup(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/knowledge/import",
        json={
            "source": "cvelistV5",
            "provider": "cve",
            "format": "json",
            "payload": _records(),
        },
    )
    assert response.status_code == 201
    knowledge_id = response.json()["knowledge_ids"][2]
    assert response.json()["imported"] == 3

    listing = await client.get("/knowledge", params={"knowledge_type": "CVE"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["source"] == "cvelistV5"

    search = await client.get("/knowledge/search", params={"q": "test vulnerability"})
    assert search.status_code == 200
    assert search.json()["total"] == 1
    detail = await client.get(f"/knowledge/{knowledge_id}")
    assert detail.status_code == 200
    assert detail.json()["external_id"] == "CVE-2026-1234"
    for path in (
        "/knowledge/cve/cve-2026-1234",
        "/knowledge/cwe/CWE-79",
    ):
        typed = await client.get(path)
        assert typed.status_code == 200
    missing_attack = await client.get("/knowledge/attack/T9999")
    assert missing_attack.status_code == 404


async def test_knowledge_model_and_migration_contract() -> None:
    tables = Base.metadata.tables
    for table in (
        "knowledge",
        "knowledge_relations",
        "knowledge_sources",
        "knowledge_versions",
        "asset_knowledge",
        "evidence_knowledge",
        "report_knowledge",
    ):
        assert table in tables
    assert len(KnowledgeType) >= 10
    assert {"id", "source_id", "title", "description", "references", "status"} <= set(
        tables["knowledge"].columns.keys()
    )
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260730_0008_knowledge_center.py"
    )
    spec = importlib.util.spec_from_file_location("phase5_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260730_0008"
    assert module.down_revision == "20260730_0007"
