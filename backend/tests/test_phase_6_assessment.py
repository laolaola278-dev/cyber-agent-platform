"""Phase 6 Security Assessment Framework tests without real scanner integrations."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.assessment import (
    AssessmentPlanner,
    AssessmentPluginContext,
    AssessmentRegistry,
    AssessmentRuntime,
    FakeAssessmentPlugin,
    ResultNormalizer,
    RuleBasedRiskEngine,
)
from app.core.enums import FindingConfidence, FindingSeverity, RiskLevel
from app.database import Base
from app.events import InMemoryEventBus
from app.exceptions import (
    AssessmentExecutionError,
    AssessmentPolicyViolation,
    AssessmentValidationError,
)
from app.models import (
    Agent,
    AgentRuntime,
    AssessmentCapability,
    AssessmentPlugin,
    AssessmentTask,
    Asset,
    AuditLog,
    Finding,
    FindingAsset,
    FindingEvidence,
    FindingKnowledge,
    FindingReference,
    Knowledge,
    KnowledgeSource,
    KnowledgeVersion,
    Report,
    Task,
)
from app.report.service import ReportService
from app.runtime.service import RuntimeService
from app.schemas.assessment import (
    ASSESSMENT_CAPABILITIES,
    AssessmentPlan,
    AssessmentPolicy,
    AssessmentResult,
    RawFinding,
)
from tests.conftest import TestSessionFactory


class ForbiddenPlugin(FakeAssessmentPlugin):
    name = "forbidden"
    permissions = frozenset({"shell.execute"})


class ExpandingPlugin(FakeAssessmentPlugin):
    name = "expanding"

    async def plan(self, context: AssessmentPluginContext) -> AssessmentPlan:
        return AssessmentPlan(
            asset_id=context.asset_id,
            capabilities=["web.scan", "port.scan"],
            plugin_name=self.name,
            steps=["invalid"],
            limits={},
        )


class ExcessivePlugin(FakeAssessmentPlugin):
    name = "excessive"

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        return AssessmentResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            requests_made=context.policy.max_requests + 1,
        )


class TimedOutPlugin(FakeAssessmentPlugin):
    name = "timed-out"

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        raise TimeoutError("synthetic timeout")


class WrongIdentityPlugin(FakeAssessmentPlugin):
    name = "wrong-identity"

    async def normalize(self, result: AssessmentResult) -> AssessmentResult:
        return result.model_copy(update={"plugin_name": "different-plugin"})


async def _asset(client: AsyncClient, name: str = "Phase 6 App") -> dict[str, object]:
    response = await client.post(
        "/assets",
        json={
            "asset_type": "APPLICATION",
            "name": name,
            "value": name,
            "criticality": "CRITICAL",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_policy_registry_planner_and_security_boundaries() -> None:
    asset_id = uuid4()
    with pytest.raises(ValueError, match="must not overlap"):
        AssessmentPolicy(asset_allowlist=[asset_id], asset_denylist=[asset_id])
    with pytest.raises(ValueError, match="Unsupported assessment capabilities"):
        AssessmentPolicy(capability_allowlist=["shell.execute"])

    registry = AssessmentRegistry()
    invalid_identity = FakeAssessmentPlugin()
    invalid_identity.name = ""
    with pytest.raises(AssessmentValidationError, match="name and version"):
        registry.register(invalid_identity)
    unsupported = FakeAssessmentPlugin()
    unsupported.name = "unsupported"
    unsupported.capabilities = frozenset({"unsupported.scan"})
    with pytest.raises(AssessmentValidationError, match="unsupported"):
        registry.register(unsupported)
    registry.register(FakeAssessmentPlugin())
    assert registry.resolve({"web.scan"}).name == "fake-assessment"
    assert len(registry.plugins) == 1
    with pytest.raises(AssessmentValidationError, match="already registered"):
        registry.register(FakeAssessmentPlugin())
    with pytest.raises(AssessmentValidationError, match="forbidden permissions"):
        registry.register(ForbiddenPlugin())
    with pytest.raises(AssessmentValidationError, match="not registered"):
        registry.require("missing")
    with pytest.raises(AssessmentValidationError, match="provides all requested"):
        registry.resolve({"unknown.scan"})

    planner = AssessmentPlanner(registry)
    policy = AssessmentPolicy(
        asset_allowlist=[asset_id], capability_allowlist=["web.scan"], max_requests=2
    )
    plan, context = planner.plan(
        assessment_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=asset_id,
        trace_id="phase6-plan",
        capabilities=["web.scan"],
        policy=policy,
        input_data={},
    )
    assert plan.plugin_name == "fake-assessment"
    assert context.granted_permissions == frozenset({"assessment.execute", "evidence.write"})
    with pytest.raises(AssessmentPolicyViolation, match="not allowed"):
        planner.plan(
            assessment_task_id=uuid4(),
            task_id=uuid4(),
            asset_id=asset_id,
            trace_id="denied-capability",
            capabilities=["port.scan"],
            policy=policy,
            input_data={},
        )
    denied_asset = uuid4()
    with pytest.raises(AssessmentPolicyViolation, match="explicitly denied"):
        planner.plan(
            assessment_task_id=uuid4(),
            task_id=uuid4(),
            asset_id=denied_asset,
            trace_id="denied-asset",
            capabilities=["web.scan"],
            policy=AssessmentPolicy(asset_denylist=[denied_asset]),
            input_data={},
        )
    with pytest.raises(AssessmentPolicyViolation, match="not present"):
        planner.plan(
            assessment_task_id=uuid4(),
            task_id=uuid4(),
            asset_id=uuid4(),
            trace_id="not-allowlisted",
            capabilities=["web.scan"],
            policy=AssessmentPolicy(asset_allowlist=[asset_id]),
            input_data={},
        )


async def test_runtime_lifecycle_guards_and_scheduler_reservation() -> None:
    asset_id = uuid4()
    policy = AssessmentPolicy(capability_allowlist=["web.scan"], max_requests=1)

    registry = AssessmentRegistry()
    expanding = ExpandingPlugin()
    expanding.capabilities = frozenset({"web.scan", "port.scan"})
    registry.register(expanding)
    planner = AssessmentPlanner(registry)
    plan, context = planner.plan(
        assessment_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=asset_id,
        trace_id="expanding",
        capabilities=["web.scan"],
        policy=policy,
        input_data={},
    )
    with pytest.raises(AssessmentExecutionError, match="expanded"):
        await AssessmentRuntime(registry).execute(plan, context)
    assert not expanding.initialized

    excessive_registry = AssessmentRegistry()
    excessive_registry.register(ExcessivePlugin())
    excessive_planner = AssessmentPlanner(excessive_registry)
    plan, context = excessive_planner.plan(
        assessment_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=asset_id,
        trace_id="excessive",
        capabilities=["web.scan"],
        policy=policy,
        input_data={},
    )
    with pytest.raises(AssessmentPolicyViolation, match="request count"):
        await AssessmentRuntime(excessive_registry).execute(plan, context)

    for plugin, timeout, expected in (
        (TimedOutPlugin(), 60, "timed out"),
        (WrongIdentityPlugin(), 60, "identity does not match"),
    ):
        guarded_registry = AssessmentRegistry()
        guarded_registry.register(plugin)
        guarded_planner = AssessmentPlanner(guarded_registry)
        guarded_policy = AssessmentPolicy(
            capability_allowlist=["web.scan"], timeout_seconds=timeout
        )
        guarded_plan, guarded_context = guarded_planner.plan(
            assessment_task_id=uuid4(),
            task_id=uuid4(),
            asset_id=asset_id,
            trace_id=plugin.name,
            capabilities=["web.scan"],
            policy=guarded_policy,
            input_data={},
        )
        with pytest.raises(AssessmentExecutionError, match=expected):
            await AssessmentRuntime(guarded_registry).execute(guarded_plan, guarded_context)
        assert not plugin.initialized

    from app.assessment.runtime import AssessmentScheduler

    with pytest.raises(NotImplementedError, match="reserved"):
        await AssessmentScheduler().schedule(uuid4())


async def test_normalizer_risk_links_and_validation() -> None:
    async with TestSessionFactory() as session:
        asset = Asset(
            asset_type="APPLICATION",
            name="Critical App",
            value="Critical App",
            canonical_value="critical app",
            criticality="CRITICAL",
            capabilities=[],
            properties={},
        )
        source = KnowledgeSource(name="kev", provider_type="test", configuration={})
        session.add_all([asset, source])
        await session.flush()
        knowledge = Knowledge(
            source_id=source.id,
            knowledge_type="CISA_KEV",
            external_id="CVE-2026-1",
            current_version="1",
            current_content_hash="a" * 64,
            title="Known exploited issue",
            description="",
            references=[],
            status="ACTIVE",
            attributes={"cvss": 9.8, "known_exploited": True},
        )
        session.add(knowledge)
        await session.flush()
        version = KnowledgeVersion(
            knowledge_id=knowledge.id,
            version="1",
            content_hash="a" * 64,
            payload={},
        )
        session.add(version)
        await session.flush()
        raw = RawFinding(
            title="Example finding",
            severity=FindingSeverity.MEDIUM,
            confidence=FindingConfidence.HIGH,
            description="Normalized only",
            affected_asset="critical app",
            knowledge_ids=[knowledge.id],
            references=["https://example.com/ref", "https://example.com/ref"],
            rule="RULE-1",
        )
        result = AssessmentResult(
            success=True,
            plugin_name="fake-assessment",
            plugin_version="1.0.0",
            findings=[raw],
        )
        normalizer = ResultNormalizer(RuleBasedRiskEngine())
        findings = normalizer.normalize(
            assessment_task_id=uuid4(),
            asset=asset,
            result=result,
            evidence={},
            knowledge={knowledge.id: (knowledge, version)},
        )
        assert findings[0].risk_level == RiskLevel.CRITICAL.value
        assert findings[0].risk_score == 9.8
        assert len(findings[0].references) == 1
        assert findings[0].knowledge_links[0].knowledge_version_id == version.id
        assert findings[0].asset_links[0].asset_id == asset.id
        assert len(ResultNormalizer.fingerprint(raw, "fake-assessment", asset.id)) == 64
        low_asset = Asset(
            asset_type="HOST",
            name="Low Risk Host",
            value="low-risk-host",
            canonical_value="low-risk-host",
            criticality="LOW",
            capabilities=[],
            properties={},
        )
        low_risk = RuleBasedRiskEngine().assess(
            raw.model_copy(update={"severity": FindingSeverity.INFO}),
            [],
            low_asset,
        )
        assert low_risk.level == RiskLevel.LOW
        assert low_risk.score == 0.5
        with pytest.raises(AssessmentValidationError, match="unknown platform entities"):
            normalizer.normalize(
                assessment_task_id=uuid4(),
                asset=asset,
                result=AssessmentResult(
                    success=True,
                    plugin_name="fake-assessment",
                    plugin_version="1.0.0",
                    findings=[raw.model_copy(update={"evidence_ids": [uuid4()]})],
                ),
                evidence={},
                knowledge={knowledge.id: (knowledge, version)},
            )


async def test_assessment_api_fake_plugin_end_to_end_and_deduplication(
    client: AsyncClient,
) -> None:
    asset = await _asset(client)
    finding_payload = {
        "title": "Missing Security Header",
        "severity": "HIGH",
        "confidence": "HIGH",
        "description": "Synthetic result from non-scanning Fake Plugin",
        "affected_asset": "phase 6 app",
        "references": ["https://owasp.org/"],
        "tool": "fake-tool",
        "rule": "HEADER-001",
        "unique_id_from_tool": "header-001",
    }
    request = {
        "name": "Safe framework validation",
        "asset_id": asset["id"],
        "capabilities": ["header.scan"],
        "execute": True,
        "input": {"fake_findings": [finding_payload]},
    }
    first = await client.post("/assessment/tasks", json=request)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "SUCCESS"
    assert first.json()["result_summary"]["findings"] == 1

    second = await client.post("/assessment/tasks", json=request)
    assert second.status_code == 201, second.text
    assert second.json()["result_summary"]["duplicates"] == 1

    tasks = await client.get("/assessment/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 2
    detail = await client.get(f"/assessment/tasks/{first.json()['id']}")
    assert detail.status_code == 200

    findings = await client.get(
        "/assessment/findings", params={"severity": "HIGH", "asset_id": asset["id"]}
    )
    assert findings.status_code == 200
    assert findings.json()["total"] == 2
    assert sum(item["duplicate_of_id"] is not None for item in findings.json()["items"]) == 1
    finding_id = findings.json()["items"][0]["id"]
    finding = await client.get(f"/assessment/findings/{finding_id}")
    assert finding.status_code == 200
    assert finding.json()["assets"] == [asset["id"]]
    assert finding.json()["risk_level"] == "CRITICAL"

    plugins = await client.get("/assessment/plugins")
    capabilities = await client.get("/assessment/capabilities")
    assert plugins.status_code == 200
    assert plugins.json()[0]["name"] == "fake-assessment"
    assert capabilities.status_code == 200
    assert {item["name"] for item in capabilities.json()} == ASSESSMENT_CAPABILITIES

    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "AssessmentTaskCreated",
            "AssessmentExecutionStarted",
            "AssessmentResultNormalized",
            "FindingCreated",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(Finding)) == 2
        assert await session.scalar(select(func.count()).select_from(FindingReference)) == 2
        assert await session.scalar(select(func.count()).select_from(FindingAsset)) == 2


async def test_assessment_model_migration_and_error_contract(client: AsyncClient) -> None:
    tables = Base.metadata.tables
    for table in (
        "assessment_tasks",
        "assessment_plugins",
        "assessment_capabilities",
        "findings",
        "finding_references",
        "finding_evidence",
        "finding_knowledge",
        "finding_assets",
    ):
        assert table in tables
    assert {
        AssessmentTask,
        AssessmentPlugin,
        AssessmentCapability,
        Finding,
        FindingReference,
        FindingEvidence,
        FindingKnowledge,
        FindingAsset,
    }
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260731_0009_assessment_framework.py"
    )
    spec = importlib.util.spec_from_file_location("phase6_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260731_0009"
    assert module.down_revision == "20260730_0008"

    missing_task = await client.get("/assessment/tasks/00000000-0000-0000-0000-000000000001")
    missing_finding = await client.get("/assessment/findings/00000000-0000-0000-0000-000000000001")
    assert missing_task.status_code == 404
    assert missing_finding.status_code == 404
    assert missing_task.json()["error"]["code"] == "ASSESSMENT_NOT_FOUND"
    assert missing_finding.json()["error"]["code"] == "FINDING_NOT_FOUND"


async def test_runtime_service_coordinates_lifecycle_and_lookup_branches(
    tmp_path: Path,
) -> None:
    async with TestSessionFactory() as session:
        agent = Agent(name="service-agent", version="1.0.0", status="OFFLINE")
        unloaded_agent = Agent(name="unloaded-agent", version="1.0.0", status="OFFLINE")
        task = Task(name="service-task", task_type="test", status="RUNNING")
        session.add_all([agent, unloaded_agent, task])
        await session.flush()
        runtime = AgentRuntime(
            agent_id=agent.id,
            status="OFFLINE",
            manifest_path=str(tmp_path / "manifest.yaml"),
            entrypoint="module:Agent",
            last_health={},
        )
        session.add(runtime)
        await session.commit()

        manager = Mock()
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        manager.reload = AsyncMock()
        manager.health = AsyncMock(return_value={"status": "HEALTHY"})
        manager.execute = AsyncMock(return_value={"success": True})
        manager.load = AsyncMock(
            return_value=AgentRuntime(
                agent_id=unloaded_agent.id,
                status="OFFLINE",
                manifest_path=str(tmp_path / "manifest.yaml"),
                entrypoint="module:Agent",
                last_health={},
            )
        )
        manager.is_loaded = Mock(return_value=True)
        configuration = SimpleNamespace(
            config_directory=tmp_path,
            runtime=SimpleNamespace(runtime=SimpleNamespace(manifest_directory="agents")),
        )
        service = RuntimeService(
            session,
            configuration,
            manager,
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
        )
        service._manifest_path_for_agent = Mock(return_value=tmp_path / "manifest.yaml")

        assert await service.get(runtime.id) is runtime
        assert await service.start(agent.id, task, trace_id="start") is runtime
        assert await service.stop(runtime.id, trace_id="stop") is runtime
        assert await service.restart(runtime.id, task, trace_id="restart") is runtime
        assert await service.health(runtime.id, trace_id="health") == {"status": "HEALTHY"}
        assert await service.execute(agent, task, trace_id="execute") == {"success": True}
        loaded = await service.execute(unloaded_agent, task, trace_id="load")
        assert loaded == {"success": True}
        manager.load.assert_awaited_once()

        manager.is_loaded.return_value = False
        await service.execute(agent, task, trace_id="reload")
        assert manager.reload.await_count >= 2
        with pytest.raises(LookupError, match="Runtime"):
            await service.get(uuid4())
        with pytest.raises(LookupError, match="Agent"):
            await service.start(uuid4(), task, trace_id="missing-agent")


async def test_report_aggregates_normalized_assessment_findings() -> None:
    async with TestSessionFactory() as session:
        agent = Agent(name="report-agent", version="1.0.0", status="ONLINE")
        asset = Asset(
            asset_type="APPLICATION",
            name="Report App",
            value="Report App",
            canonical_value="report app",
            criticality="HIGH",
            capabilities=[],
            properties={},
        )
        plugin = AssessmentPlugin(
            name="report-plugin",
            version="1.0.0",
            enabled=True,
            permissions=[],
            configuration={"network_access": False},
        )
        session.add_all([agent, asset, plugin])
        await session.flush()
        task = Task(
            name="Report assessment",
            task_type="security-assessment",
            status="SUCCESS",
            asset_id=asset.id,
        )
        session.add(task)
        await session.flush()
        assessment = AssessmentTask(
            task_id=task.id,
            plugin_id=plugin.id,
            status="SUCCESS",
            requested_capabilities=["header.scan"],
            policy={},
            plan={},
            result_summary={"findings": 1},
        )
        session.add(assessment)
        await session.flush()
        finding = Finding(
            assessment_task_id=assessment.id,
            fingerprint="f" * 64,
            title="Synthetic report finding",
            severity="HIGH",
            confidence="HIGH",
            description="No scanner was invoked",
            affected_asset="report app",
            plugin="report-plugin",
            tool="fake-tool",
            rule="REPORT-001",
            risk_level="HIGH",
            risk_score=8.0,
            status="NEW",
            attributes={},
        )
        session.add(finding)
        await session.flush()

        report = await ReportService(session, InMemoryEventBus()).generate(
            task=task,
            agent_id=agent.id,
            trace_id="report-findings",
            status="SUCCESS",
        )
        await session.commit()
        persisted = await session.get(Report, report.id)
        assert persisted is not None
        assert persisted.json_content["statistics"]["finding_count"] == 1
        assert persisted.json_content["findings"][0] == {
            "id": str(finding.id),
            "title": "Synthetic report finding",
            "severity": "HIGH",
            "confidence": "HIGH",
            "risk_level": "HIGH",
            "risk_score": 8.0,
            "status": "NEW",
            "plugin": "report-plugin",
            "tool": "fake-tool",
            "rule": "REPORT-001",
        }
