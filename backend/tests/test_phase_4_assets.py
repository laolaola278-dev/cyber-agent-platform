"""Phase 4 unified Asset and Inventory Center tests."""

import importlib.util
import logging
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets import AssetRegistry, AssetResolver, AssetService
from app.core.enums import AssetRelationType, AssetType, WorkflowStatus
from app.database import Base
from app.events import InMemoryEventBus
from app.events.audit import AuditSubscriber
from app.evidence.service import EvidenceService
from app.exceptions import AssetConflict, AssetNotFound, AssetResolutionError
from app.models import Agent, Asset, AssetEvidence, AssetReport, AuditLog, Evidence, Report, Task
from app.report.service import ReportService
from app.repositories import (
    AssetRepository,
    AuditRepository,
    WorkflowDefinitionRepository,
    WorkflowInstanceRepository,
)
from app.runtime.context import RuntimeContext
from app.runtime.services import ServiceProvider
from app.schemas import (
    AssetCreate,
    AssetDiscoveryRequest,
    AssetRelationCreate,
    AssetUpdate,
    WorkflowDefinitionCreate,
)
from app.sdk.contracts import AgentContext, TaskRequest
from app.sdk.tool_adapter import BaseToolAdapter
from app.services.audit import AuditService
from app.tool_manager import ToolManager
from app.workflow import WorkflowRuntime, WorkflowService
from tests.conftest import TestSessionFactory


class FakeDNSResolver:
    def __init__(self, addresses: list[str] | None = None, error: OSError | None = None) -> None:
        self.addresses = addresses or []
        self.error = error
        self.hostnames: list[str] = []

    async def resolve(self, hostname: str) -> list[str]:
        self.hostnames.append(hostname)
        if self.error is not None:
            raise self.error
        return self.addresses


class RecordingToolAdapter(BaseToolAdapter):
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def initialize(self, config: dict[str, Any]) -> None:
        return None

    async def validate(self, payload: dict[str, Any]) -> None:
        return None

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "url": payload["url"],
            "http_status": 200,
            "title": "Asset-aware capture",
            "html": "<html>asset</html>",
            "screenshot": None,
        }

    async def shutdown(self) -> None:
        return None


class RecordingToolManager(ToolManager):
    def __init__(self, adapter: RecordingToolAdapter) -> None:
        self.adapter = adapter

    async def load(self, name: str, *, trace_id: str | None = None) -> BaseToolAdapter:
        assert name == "playwright"
        assert trace_id == "agent-asset-trace"
        return self.adapter


class AssetAwareExecutor:
    def __init__(self) -> None:
        self.asset_ids: list[UUID | None] = []

    async def execute_capability(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        asset_id: UUID | None = None,
    ) -> dict[str, Any]:
        self.asset_ids.append(asset_id)
        return {
            "success": True,
            "capability": capability,
            "trace_id": trace_id,
            "asset_id": str(asset_id) if asset_id else None,
        }


def _asset_payload(
    *,
    value: str = "Example.COM.",
    name: str = "Example Domain",
    tags: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> AssetCreate:
    return AssetCreate(
        asset_type=AssetType.DOMAIN,
        name=name,
        value=value,
        owner="security-team",
        business_unit="platform",
        environment="production",
        criticality="high",
        risk="medium",
        tags=tags or ["External", "Production"],
        capabilities=capabilities or ["crawl.html", "dns.resolve"],
    )


def _asset_service(
    session: AsyncSession,
    *,
    resolver: AssetResolver | None = None,
) -> AssetService:
    bus = InMemoryEventBus()
    AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
    return AssetService(
        session,
        AssetRepository(session),
        bus,
        resolver=resolver,
    )


async def test_asset_crud_search_duplicate_and_audit() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        asset = await service.create(_asset_payload(), trace_id="asset-create")
        assert asset.canonical_value == "example.com"
        assert {tag.name for tag in asset.tags} == {"external", "production"}

        with pytest.raises(AssetConflict, match="already exists"):
            await service.create(
                _asset_payload(value="example.com", name="Duplicate"),
                trace_id="asset-duplicate",
            )

        updated = await service.update(
            asset.id,
            AssetUpdate(
                name="Primary Domain",
                tags=["managed"],
                risk="high",
                capabilities=["crawl.html", "assessment.scan"],
            ),
            trace_id="asset-update",
        )
        assert updated.name == "Primary Domain"
        assert [tag.name for tag in updated.tags] == ["managed"]

        filters = (
            {"name": "primary"},
            {"asset_type": AssetType.DOMAIN},
            {"tag": "MANAGED"},
            {"owner": "SECURITY-TEAM"},
            {"risk": "HIGH"},
            {"environment": "PRODUCTION"},
            {"capability": "assessment.scan"},
        )
        for query in filters:
            result = await service.search(**query)
            assert result.total == 1
            assert result.items[0].id == asset.id

        partial_capability = await service.search(capability="assessment")
        assert partial_capability.total == 0
        audit_actions = set(
            await session.scalars(
                select(AuditLog.action).where(
                    AuditLog.trace_id.in_(["asset-create", "asset-update"])
                )
            )
        )
        assert audit_actions == {"AssetCreated", "AssetUpdated"}


async def test_asset_update_rejects_duplicate_identity_and_soft_delete_hides_asset() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        first = await service.create(_asset_payload(), trace_id="first")
        second = await service.create(
            _asset_payload(value="second.example", name="Second"), trace_id="second"
        )
        with pytest.raises(AssetConflict, match="identity"):
            await service.update(
                second.id,
                AssetUpdate(value="EXAMPLE.COM."),
                trace_id="duplicate-update",
            )

        deleted = await service.soft_delete(first.id, trace_id="asset-delete", actor="phase-4-test")
        assert deleted.deleted_at is not None
        assert deleted.deleted_by == "phase-4-test"
        with pytest.raises(AssetNotFound):
            await service.get(first.id)
        assert (await service.search(name="example.com")).total == 0
        persisted = await session.get(Asset, first.id)
        assert persisted is not None
        assert persisted.deleted_at is not None
        audit = await session.scalar(select(AuditLog).where(AuditLog.trace_id == "asset-delete"))
        assert audit is not None
        assert audit.action == "AssetSoftDeleted"


async def test_asset_relations_are_directed_idempotent_and_reject_self_reference() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        domain = await service.create(_asset_payload(), trace_id="domain")
        ip_asset = await service.create(
            AssetCreate(asset_type=AssetType.IP, name="IPv4", value="192.0.2.10"),
            trace_id="ip",
        )
        payload = AssetRelationCreate(
            target_asset_id=ip_asset.id,
            relation_type=AssetRelationType.RESOLVES_TO,
            properties={"source": "test-dns"},
        )
        relation = await service.add_relation(domain.id, payload, trace_id="relation")
        repeated = await service.add_relation(domain.id, payload, trace_id="relation-repeat")
        assert repeated.id == relation.id
        assert await service.list_relations(domain.id) == [relation]
        assert await service.list_relations(ip_asset.id) == [relation]

        with pytest.raises(AssetConflict, match="cannot reference itself"):
            await service.add_relation(
                domain.id,
                AssetRelationCreate(
                    target_asset_id=domain.id,
                    relation_type=AssetRelationType.RELATED_TO,
                ),
                trace_id="self-relation",
            )


async def test_resolver_normalizes_url_and_discovery_restores_soft_deleted_assets() -> None:
    dns = FakeDNSResolver(["2001:0db8::1", "192.0.2.10", "192.0.2.10"])
    resolver = AssetResolver(dns)
    result = await resolver.resolve_url("HTTPS://Example.COM.:443/path?q=1#fragment")
    assert result.website.canonical_value == "https://example.com/path?q=1"
    assert result.domain.canonical_value == "example.com"
    assert [item.canonical_value for item in result.ips] == ["192.0.2.10", "2001:db8::1"]
    assert dns.hostnames == ["example.com"]

    async with TestSessionFactory() as session:
        service = _asset_service(session, resolver=resolver)
        existing = await service.create(_asset_payload(), trace_id="existing-domain")
        await service.soft_delete(existing.id, trace_id="delete-domain", actor="test")
        website, domain, ips, relations = await service.discover(
            AssetDiscoveryRequest(
                url="https://example.com/path?q=1",
                tags=["discovered"],
                environment="test",
            ),
            trace_id="discover",
        )
        assert domain.id == existing.id
        assert domain.deleted_at is None
        assert len(ips) == 2
        assert len(relations) == 3
        assert relations[0].relation_type == AssetRelationType.REFERENCES.value
        assert {item.relation_type for item in relations[1:]} == {
            AssetRelationType.RESOLVES_TO.value
        }
        assert website.canonical_value == "https://example.com/path?q=1"
        assert {tag.name for tag in domain.tags} == {
            "external",
            "production",
            "discovered",
        }
        discovery_actions = list(
            await session.scalars(select(AuditLog.action).where(AuditLog.trace_id == "discover"))
        )
        assert discovery_actions.count("AssetCreated") == 3
        assert discovery_actions.count("AssetUpdated") == 1
        assert discovery_actions.count("AssetRelationCreated") == 3
        assert discovery_actions.count("AssetDiscovered") == 1


async def test_resolver_and_discovery_translate_invalid_input_and_dns_errors() -> None:
    resolver = AssetResolver(FakeDNSResolver())
    with pytest.raises(ValueError, match="absolute HTTP or HTTPS"):
        await resolver.resolve_url("ftp://example.com/file")

    async with TestSessionFactory() as session:
        service = _asset_service(
            session,
            resolver=AssetResolver(FakeDNSResolver(error=OSError("dns unavailable"))),
        )
        with pytest.raises(AssetResolutionError, match="dns unavailable"):
            await service.discover(
                AssetDiscoveryRequest(url="https://example.com"),
                trace_id="dns-error",
            )
        assert (await service.search()).total == 0


async def test_asset_links_evidence_reports_and_orm_relationships() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        asset = await service.create(_asset_payload(), trace_id="linked-asset")
        agent = Agent(name="asset-runtime-agent", version="1.0.0", status="ONLINE")
        task = Task(
            name="asset task",
            task_type="workflow-capability",
            status="SUCCESS",
            asset_id=asset.id,
        )
        session.add_all([agent, task])
        await session.flush()
        evidence = Evidence(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="asset-provenance",
            url="https://example.com",
            evidence_type="HTML",
            sha256="a" * 64,
            html_hash="a" * 64,
            content_hash="a" * 64,
        )
        report = Report(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="asset-provenance",
            status="SUCCESS",
            json_content={},
            markdown_content="# report",
            html_content="<h1>report</h1>",
        )
        session.add_all([evidence, report])
        await session.flush()
        await service.link_evidence(asset.id, evidence.id, trace_id="link-evidence")
        await service.link_evidence(asset.id, evidence.id, trace_id="link-evidence-repeat")
        await service.link_report(asset.id, report.id, trace_id="link-report")
        await service.link_report(asset.id, report.id, trace_id="link-report-repeat")
        await session.commit()

        assert [item.id for item in await service.list_evidence(asset.id)] == [evidence.id]
        assert [item.id for item in await service.list_reports(asset.id)] == [report.id]
        assert await session.scalar(select(func.count()).select_from(AssetEvidence)) == 1
        assert await session.scalar(select(func.count()).select_from(AssetReport)) == 1

    async with TestSessionFactory() as session:
        loaded_evidence = await session.get(Evidence, evidence.id)
        loaded_report = await session.get(Report, report.id)
        assert loaded_evidence is not None
        assert loaded_report is not None
        assert [item.id for item in loaded_evidence.assets] == [asset.id]
        assert [item.id for item in loaded_report.assets] == [asset.id]


async def test_evidence_and_report_services_audit_automatic_asset_links(
    tmp_path: Path,
) -> None:
    async with TestSessionFactory() as session:
        asset = await _asset_service(session).create(
            _asset_payload(), trace_id="automatic-link-asset"
        )
        agent = Agent(name="automatic-link-agent", version="1.0.0", status="ONLINE")
        task = Task(
            name="automatic provenance",
            task_type="data-acquisition",
            status="RUNNING",
            asset_id=asset.id,
        )
        session.add_all([agent, task])
        await session.flush()
        bus = InMemoryEventBus()
        AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
        evidence = await EvidenceService(session, bus, tmp_path).save_capture(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="automatic-provenance",
            url="https://example.com",
            http_status=200,
            title="Example",
            html="<html>example</html>",
            screenshot=None,
            asset_id=asset.id,
        )
        report = await ReportService(session, bus).generate(
            task=task,
            agent_id=agent.id,
            trace_id="automatic-provenance",
            status="SUCCESS",
        )
        assert await session.scalar(
            select(AssetEvidence).where(
                AssetEvidence.asset_id == asset.id,
                AssetEvidence.evidence_id == evidence.id,
            )
        )
        assert await session.scalar(
            select(AssetReport).where(
                AssetReport.asset_id == asset.id,
                AssetReport.report_id == report.id,
            )
        )
        actions = set(
            await session.scalars(
                select(AuditLog.action).where(AuditLog.trace_id == "automatic-provenance")
            )
        )
        assert actions == {
            "AssetEvidenceLinked",
            "AssetReportLinked",
            "EvidenceSaved",
            "ReportGenerated",
        }


async def test_asset_link_rejects_missing_resources() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        asset = await service.create(_asset_payload(), trace_id="asset")
        with pytest.raises(AssetNotFound, match="Evidence"):
            await service.link_evidence(asset.id, uuid4(), trace_id="missing-evidence")
        with pytest.raises(AssetNotFound, match="Report"):
            await service.link_report(asset.id, uuid4(), trace_id="missing-report")


async def test_workflow_propagates_asset_context_and_rejects_deleted_asset() -> None:
    async with TestSessionFactory() as session:
        service = _asset_service(session)
        asset = await service.create(_asset_payload(), trace_id="workflow-asset")
        executor = AssetAwareExecutor()
        instances = WorkflowInstanceRepository(session)
        workflow_service = WorkflowService(
            session,
            WorkflowDefinitionRepository(session),
            instances,
            InMemoryEventBus(),
            WorkflowRuntime(session, instances, InMemoryEventBus(), executor),
        )
        definition = await workflow_service.create_definition(
            WorkflowDefinitionCreate(yaml="""
name: asset-aware-workflow
version: 1.0.0
steps:
  - capability: crawl.html
"""),
            trace_id="workflow-definition",
        )
        run = await workflow_service.create_run(
            definition.id,
            {"url": "https://example.com"},
            asset_id=asset.id,
            trace_id="workflow-run",
        )
        assert run.status == WorkflowStatus.SUCCESS.value
        assert run.asset_id == asset.id
        assert executor.asset_ids == [asset.id]

        await service.soft_delete(asset.id, trace_id="workflow-delete", actor="test")
        with pytest.raises(AssetNotFound):
            await workflow_service.create_run(
                definition.id,
                {},
                asset_id=asset.id,
                trace_id="workflow-deleted-asset",
            )


async def test_asset_api_crud_search_relations_and_soft_delete(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/assets",
        json={
            "asset_type": "DOMAIN",
            "name": "API Domain",
            "value": "API.Example.",
            "owner": "api-team",
            "environment": "staging",
            "risk": "low",
            "tags": ["API"],
            "capabilities": ["crawl.html"],
        },
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]
    assert created.json()["canonical_value"] == "api.example"
    assert created.json()["tags"] == ["api"]

    duplicate = await client.post(
        "/assets",
        json={"asset_type": "DOMAIN", "name": "Duplicate", "value": "api.example"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ASSET_CONFLICT"

    ip_response = await client.post(
        "/assets",
        json={"asset_type": "IP", "name": "API IP", "value": "192.0.2.20"},
    )
    ip_id = ip_response.json()["id"]
    relation = await client.post(
        f"/assets/{asset_id}/relations",
        json={"target_asset_id": ip_id, "relation_type": "resolves_to"},
    )
    assert relation.status_code == 201
    assert relation.json()["target_asset_id"] == ip_id
    relations = await client.get(f"/assets/{ip_id}/relations")
    assert len(relations.json()) == 1

    updated = await client.put(
        f"/assets/{asset_id}",
        json={"risk": "high", "tags": ["managed"]},
    )
    assert updated.status_code == 200
    assert updated.json()["risk"] == "high"
    assert updated.json()["tags"] == ["managed"]
    searched = await client.get("/assets", params={"tag": "managed", "capability": "crawl.html"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 1

    deleted = await client.delete(f"/assets/{asset_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/assets/{asset_id}")
    assert missing.status_code == 404
    listed = await client.get("/assets", params={"name": "API Domain"})
    assert listed.json()["total"] == 0


async def test_data_acquisition_agent_propagates_asset_to_evidence(tmp_path: Path) -> None:
    module_path = Path(__file__).parents[2] / "agents" / "data-acquisition" / "agent.py"
    spec = importlib.util.spec_from_file_location("phase4_data_acquisition_agent", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent_class = module.DataAcquisitionAgent

    async with TestSessionFactory() as session:
        bus = InMemoryEventBus()
        asset_service = _asset_service(session)
        asset = await asset_service.create(_asset_payload(), trace_id="agent-asset")
        agent_model = Agent(name="data-acquisition-agent", version="1.0.0", status="ONLINE")
        task = Task(
            name="asset capture",
            task_type="data-acquisition",
            status="RUNNING",
            input={"url": "https://example.com"},
            asset_id=asset.id,
        )
        session.add_all([agent_model, task])
        await session.flush()

        adapter = RecordingToolAdapter()
        services = ServiceProvider()
        services.register(ToolManager, RecordingToolManager(adapter))
        services.register(EvidenceService, EvidenceService(session, bus, tmp_path))
        AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
        runtime = RuntimeContext(
            task=task,
            trace_id="agent-asset-trace",
            logger=logging.getLogger("phase4-test"),
            configuration={},
            publisher=bus,
            services=services,
            agent_id=agent_model.id,
        )
        context = AgentContext(
            trace_id=runtime.trace_id,
            task_id=task.id,
            agent_id=agent_model.id,
            actor="test",
            metadata={"runtime_context": runtime},
        )
        implementation = agent_class()
        await implementation.initialize(context)
        result = await implementation.execute(
            TaskRequest(task_type="data-acquisition", input=task.input), context
        )
        assert result.success is True
        assert adapter.payloads == [{"url": "https://example.com", "method": "GET"}]
        evidence = await session.scalar(select(Evidence).where(Evidence.task_id == task.id))
        assert evidence is not None
        link = await session.scalar(
            select(AssetEvidence).where(AssetEvidence.evidence_id == evidence.id)
        )
        assert link is not None
        assert link.asset_id == asset.id
        evidence_link_audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.trace_id == "agent-asset-trace",
                AuditLog.action == "AssetEvidenceLinked",
            )
        )
        assert evidence_link_audit is not None
        assert evidence_link_audit.resource == f"asset:{asset.id}"

        invalid = await implementation.execute(
            TaskRequest(task_type="data-acquisition", input={"url": 7}), context
        )
        assert invalid.success is False
        await implementation.shutdown()
        health = await implementation.health_check()
        assert health.healthy is True
        with pytest.raises(TypeError, match="RuntimeContext was not injected"):
            await implementation.initialize(
                AgentContext(
                    trace_id="invalid",
                    task_id=task.id,
                    agent_id=agent_model.id,
                    actor="test",
                )
            )


async def test_asset_registry_and_canonicalization_policy() -> None:
    registry = AssetRegistry()
    assert AssetType.DOMAIN in registry.types
    assert registry.require_type(AssetType.CONTAINER) == AssetType.CONTAINER
    assert registry.require_relation(AssetRelationType.DEPLOYED_IN) == AssetRelationType.DEPLOYED_IN
    with pytest.raises(ValueError, match="Unsupported asset type"):
        registry.require_type(cast(AssetType, "UNSUPPORTED"))
    with pytest.raises(ValueError, match="Unsupported asset relation"):
        registry.require_relation(cast(AssetRelationType, "unsupported"))

    assert AssetService.canonicalize(AssetType.HOST, " Host.Example. ") == "host.example"
    assert AssetService.canonicalize(AssetType.IP, "2001:0db8::1") == "2001:db8::1"
    assert (
        AssetService.canonicalize(AssetType.WEBSITE, "HTTPS://Example.COM.:443/path?q=1#fragment")
        == "https://example.com/path?q=1"
    )
    assert (
        AssetService.canonicalize(AssetType.REPOSITORY, "https://EXAMPLE.com:8443/repo")
        == "https://example.com:8443/repo"
    )
    assert AssetService.canonicalize(AssetType.DOCUMENT, "  Security Guide  ") == "security guide"
    with pytest.raises(AssetResolutionError, match="absolute URL"):
        AssetService.canonicalize(AssetType.WEBSITE, "example.com")


async def test_asset_api_discovery_evidence_and_reports(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def resolve_dns(self: object, hostname: str) -> list[str]:
        assert hostname == "discovery.example"
        return ["192.0.2.30"]

    monkeypatch.setattr("app.assets.resolver.SystemDNSResolver.resolve", resolve_dns)
    discovered = await client.post(
        "/assets/discover",
        json={
            "url": "https://Discovery.Example:443/path#fragment",
            "tags": ["api-discovery"],
            "environment": "test",
        },
    )
    assert discovered.status_code == 200
    body = discovered.json()
    assert body["website"]["canonical_value"] == "https://discovery.example/path"
    assert body["domain"]["canonical_value"] == "discovery.example"
    assert [item["canonical_value"] for item in body["ips"]] == ["192.0.2.30"]
    assert len(body["relations"]) == 2
    asset_id = UUID(body["website"]["id"])

    async with TestSessionFactory() as session:
        service = _asset_service(session)
        agent = Agent(name="api-provenance-agent", version="1.0.0", status="ONLINE")
        task = Task(
            name="api provenance",
            task_type="workflow-capability",
            status="SUCCESS",
            asset_id=asset_id,
        )
        session.add_all([agent, task])
        await session.flush()
        evidence = Evidence(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="api-provenance",
            url="https://discovery.example/path",
            evidence_type="HTML",
            sha256="b" * 64,
            html_hash="b" * 64,
            content_hash="b" * 64,
        )
        report = Report(
            task_id=task.id,
            agent_id=agent.id,
            trace_id="api-provenance",
            status="SUCCESS",
            json_content={},
            markdown_content="# API report",
            html_content="<h1>API report</h1>",
        )
        session.add_all([evidence, report])
        await session.flush()
        await service.link_evidence(asset_id, evidence.id, trace_id="api-link-evidence")
        await service.link_report(asset_id, report.id, trace_id="api-link-report")
        await session.commit()

    evidence_response = await client.get(f"/assets/{asset_id}/evidence")
    report_response = await client.get(f"/assets/{asset_id}/reports")
    assert evidence_response.status_code == 200
    assert report_response.status_code == 200
    assert evidence_response.json()[0]["trace_id"] == "api-provenance"
    assert report_response.json()[0]["trace_id"] == "api-provenance"


def test_phase_4_metadata_and_migration_contract() -> None:
    required_tables = {
        "assets",
        "asset_relations",
        "asset_tags",
        "asset_evidence",
        "asset_reports",
    }
    assert required_tables <= set(Base.metadata.tables)
    assert {
        foreign_key.target_fullname for foreign_key in Base.metadata.tables["tasks"].foreign_keys
    } >= {"assets.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in Base.metadata.tables["workflow_instances"].foreign_keys
    } >= {"assets.id"}

    module_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260730_0007_asset_center.py"
    )
    spec = importlib.util.spec_from_file_location("phase4_asset_migration", module_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260730_0007"
    assert migration.down_revision == "20260730_0006"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


async def test_agent_asset_requires_valid_reference_contract(client: AsyncClient) -> None:
    missing_reference = await client.post(
        "/assets",
        json={"asset_type": "AGENT", "name": "Agent Asset", "value": "agent-a"},
    )
    assert missing_reference.status_code == 422

    forbidden_reference = await client.post(
        "/assets",
        json={
            "asset_type": "DOMAIN",
            "name": "Domain",
            "value": "agent.example",
            "agent_id": str(uuid4()),
        },
    )
    assert forbidden_reference.status_code == 422

    agent_response = await client.post(
        "/registry/agents",
        json={"name": "referenced-agent", "version": "1.0.0", "author": "test"},
    )
    agent_id = agent_response.json()["id"]
    created = await client.post(
        "/assets",
        json={
            "asset_type": "AGENT",
            "name": "Referenced Agent",
            "value": "referenced-agent",
            "agent_id": agent_id,
        },
    )
    assert created.status_code == 201
    assert created.json()["agent_id"] == agent_id
