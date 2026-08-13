"""Phase 9 Detection Framework tests with synthetic events only."""

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.database import Base
from app.detection import (
    DetectionPlanner,
    DetectionPluginContext,
    DetectionRegistry,
    DetectionResultNormalizer,
    DetectionRuntime,
    FakeDetectionPlugin,
    RuleBasedCorrelationEngine,
)
from app.exceptions import (
    DetectionExecutionError,
    DetectionPolicyViolation,
    DetectionValidationError,
)
from app.models import (
    Agent,
    AuditLog,
    DetectionCapability,
    DetectionPlugin,
    DetectionTask,
    EventAsset,
    EventEvidence,
    EventKnowledge,
    EventReference,
    Evidence,
    Knowledge,
    KnowledgeSource,
    KnowledgeVersion,
    SecurityEvent,
    Task,
)
from app.schemas.detection import (
    DETECTION_CAPABILITIES,
    DetectionPlan,
    DetectionPolicy,
    DetectionResult,
    RawSecurityEvent,
)
from tests.conftest import TestSessionFactory


class ForbiddenPlugin(FakeDetectionPlugin):
    name = "forbidden-detection"
    permissions = frozenset({"database.access"})


class WrongIdentityPlugin(FakeDetectionPlugin):
    name = "wrong-identity-detection"

    async def normalize(self, result: DetectionResult) -> DetectionResult:
        return result.model_copy(update={"plugin_name": "different-plugin"})


class TimeoutPlugin(FakeDetectionPlugin):
    name = "timeout-detection"

    async def collect(self, context: DetectionPluginContext) -> list[dict[str, object]]:
        raise TimeoutError("synthetic timeout")


class TrackingPlugin(FakeDetectionPlugin):
    name = "tracking-detection"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def initialize(self, context: DetectionPluginContext) -> None:
        self.calls.append("initialize")
        await super().initialize(context)

    async def collect(self, context: DetectionPluginContext) -> list[dict[str, object]]:
        self.calls.append("collect")
        return await super().collect(context)

    async def parse(
        self, records: list[dict[str, object]], context: DetectionPluginContext
    ) -> list[dict[str, object]]:
        self.calls.append("parse")
        return records

    async def detect(
        self, records: list[dict[str, object]], context: DetectionPluginContext
    ) -> DetectionResult:
        self.calls.append("detect")
        return await super().detect(records, context)

    async def normalize(self, result: DetectionResult) -> DetectionResult:
        self.calls.append("normalize")
        return await super().normalize(result)

    async def shutdown(self) -> None:
        self.calls.append("shutdown")
        await super().shutdown()


def _policy(**updates: object) -> DetectionPolicy:
    values: dict[str, object] = {
        "capability_allowlist": sorted(DETECTION_CAPABILITIES),
        "allowed_log_sources": ["synthetic"],
        "allowed_plugins": ["fake-detection"],
        "allowed_parsers": ["structured-json"],
        "timeout_seconds": 10,
        "max_events": 100,
    }
    values.update(updates)
    return DetectionPolicy(**values)


def _event(
    asset_id: UUID,
    *,
    timestamp: datetime | None = None,
    source: str = "Synthetic IDS",
    rule: str = "RULE-001",
    iocs: list[str] | None = None,
    unique_id: str = "event-1",
) -> dict[str, object]:
    return {
        "event_type": " Network.Alert ",
        "source": source,
        "severity": "HIGH",
        "confidence": "HIGH",
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
        "asset_ids": [str(asset_id)],
        "references": ["https://example.test/rule", "https://example.test/rule"],
        "tool": "fake-sensor",
        "rule": rule,
        "iocs": iocs or ["203.0.113.7"],
        "unique_id_from_tool": unique_id,
        "attributes": {
            "protocol": "tcp",
            "raw": {"must": "be discarded"},
            "tags": ["synthetic", {"discard": True}],
        },
    }


def _context(
    plugin: FakeDetectionPlugin,
    *,
    asset_id: UUID | None = None,
    policy: DetectionPolicy | None = None,
    events: list[dict[str, object]] | None = None,
) -> tuple[DetectionPlan, DetectionPluginContext]:
    selected_asset = asset_id or uuid4()
    selected_policy = policy or _policy(allowed_plugins=[plugin.name])
    registry = DetectionRegistry()
    registry.register(plugin)
    return DetectionPlanner(registry).plan(
        detection_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=selected_asset,
        trace_id="phase-9-runtime",
        capabilities=["network.detect"],
        log_source="synthetic",
        parser="structured-json",
        policy=selected_policy,
        input_data={"fake_events": events or [_event(selected_asset)]},
        plugin_name=plugin.name,
    )


async def _asset(client: AsyncClient, name: str = "Phase 9 Sensor") -> dict[str, object]:
    response = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": name,
            "value": name,
            "criticality": "HIGH",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_detection_policy_registry_and_planner_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Unsupported detection capabilities"):
        DetectionPolicy(capability_allowlist=["shell.execute"])
    with pytest.raises(ValidationError, match="At least one detection capability"):
        DetectionPolicy(capability_allowlist=[])
    for field in ("allowed_log_sources", "allowed_plugins", "allowed_parsers"):
        with pytest.raises(ValidationError):
            DetectionPolicy(**{field: []})
    with pytest.raises(ValidationError):
        DetectionPolicy(sampling_rate=0)
    with pytest.raises(ValidationError):
        DetectionPolicy(max_event_size_bytes=0)
    with pytest.raises(ValidationError):
        DetectionPolicy(rate_limit_per_second=0)
    with pytest.raises(ValidationError):
        DetectionPolicy(retention_days=0)

    registry = DetectionRegistry()
    invalid = FakeDetectionPlugin()
    invalid.name = ""
    with pytest.raises(DetectionValidationError, match="name and version"):
        registry.register(invalid)
    unsupported = FakeDetectionPlugin()
    unsupported.name = "unsupported-detection"
    unsupported.capabilities = frozenset({"unknown.detect"})
    with pytest.raises(DetectionValidationError, match="unsupported"):
        registry.register(unsupported)
    with pytest.raises(DetectionValidationError, match="forbidden permissions"):
        registry.register(ForbiddenPlugin())

    plugin = FakeDetectionPlugin()
    registry.register(plugin)
    with pytest.raises(DetectionValidationError, match="already registered"):
        registry.register(FakeDetectionPlugin())
    with pytest.raises(DetectionValidationError, match="not registered"):
        registry.require("missing")
    with pytest.raises(DetectionValidationError, match="provides all requested"):
        registry.resolve({"unknown.detect"})
    assert registry.resolve({"network.detect"}) is plugin

    planner = DetectionPlanner(registry)
    ids = (uuid4(), uuid4(), uuid4())
    plan, context = planner.plan(
        detection_task_id=ids[0],
        task_id=ids[1],
        asset_id=ids[2],
        trace_id="phase-9-plan",
        capabilities=["network.detect"],
        log_source="synthetic",
        parser="structured-json",
        policy=_policy(),
        input_data={},
    )
    assert plan.steps == ["initialize", "collect", "parse", "detect", "normalize", "shutdown"]
    assert plan.limits["retention_days"] == 30
    assert context.granted_permissions == frozenset({"detection.execute", "evidence.read"})
    assert not hasattr(context, "session")
    assert not hasattr(context, "workflow")
    assert not hasattr(context, "assessment")
    assert not hasattr(context, "report")

    denied_cases = (
        (
            {"capability_allowlist": ["host.detect"]},
            "network.detect",
            "synthetic",
            "structured-json",
        ),
        ({"allowed_plugins": ["other"]}, "network.detect", "synthetic", "structured-json"),
        ({"allowed_log_sources": ["other"]}, "network.detect", "synthetic", "structured-json"),
        ({"allowed_parsers": ["other"]}, "network.detect", "synthetic", "structured-json"),
    )
    for updates, capability, source, parser in denied_cases:
        with pytest.raises(DetectionPolicyViolation):
            planner.plan(
                detection_task_id=uuid4(),
                task_id=uuid4(),
                asset_id=uuid4(),
                trace_id="denied",
                capabilities=[capability],
                log_source=source,
                parser=parser,
                policy=_policy(**updates),
                input_data={},
            )


async def test_runtime_lifecycle_guards_sampling_rate_and_limits() -> None:
    plugin = TrackingPlugin()
    asset_id = uuid4()
    events = [_event(asset_id, unique_id=f"event-{index}") for index in range(3)]
    plan, context = _context(
        plugin,
        asset_id=asset_id,
        policy=_policy(
            allowed_plugins=[plugin.name],
            rate_limit_per_second=2,
            sampling_rate=1.0,
        ),
        events=events,
    )
    registry = DetectionRegistry()
    registry.register(plugin)
    result = await DetectionRuntime(registry).execute(plan, context)
    assert plugin.calls == ["initialize", "collect", "parse", "detect", "normalize", "shutdown"]
    assert plugin.initialized is False
    assert len(result.events) == 2
    assert result.metadata["policy"]["events_before_ingestion_policy"] == 3
    assert result.metadata["policy"]["events_after_ingestion_policy"] == 2
    assert result.metadata["policy"]["retention_days"] == 30

    zero_sample_policy = _policy(
        allowed_plugins=[plugin.name], sampling_rate=0.0001, rate_limit_per_second=100
    )
    _, zero_context = _context(plugin, asset_id=asset_id, policy=zero_sample_policy, events=events)
    sampled = DetectionRuntime(registry)._apply_ingestion_policy(
        DetectionResult(
            success=True,
            plugin_name=plugin.name,
            plugin_version=plugin.version,
            events=[RawSecurityEvent.model_validate(item) for item in events],
        ),
        zero_context,
    )
    assert len(sampled.events) <= len(events)

    with pytest.raises(DetectionPolicyViolation, match="permissions"):
        await DetectionRuntime(registry).execute(
            plan, replace(context, granted_permissions=frozenset())
        )

    for guarded, expected in (
        (WrongIdentityPlugin(), "identity does not match"),
        (TimeoutPlugin(), "timed out"),
    ):
        guarded_plan, guarded_context = _context(guarded)
        guarded_registry = DetectionRegistry()
        guarded_registry.register(guarded)
        with pytest.raises(DetectionExecutionError, match=expected):
            await DetectionRuntime(guarded_registry).execute(guarded_plan, guarded_context)
        assert guarded.initialized is False

    max_plugin = FakeDetectionPlugin()
    max_plan, max_context = _context(
        max_plugin,
        policy=_policy(max_events=1),
        events=[_event(uuid4(), unique_id="a"), _event(uuid4(), unique_id="b")],
    )
    max_registry = DetectionRegistry()
    max_registry.register(max_plugin)
    with pytest.raises(DetectionPolicyViolation, match="maximum event count"):
        await DetectionRuntime(max_registry).execute(max_plan, max_context)

    size_asset = uuid4()
    size_plugin = FakeDetectionPlugin()
    size_plan, size_context = _context(
        size_plugin,
        asset_id=size_asset,
        policy=_policy(max_event_size_bytes=300),
        events=[{**_event(size_asset), "attributes": {"message": "x" * 1000}}],
    )
    size_registry = DetectionRegistry()
    size_registry.register(size_plugin)
    with pytest.raises(DetectionPolicyViolation, match="oversized event"):
        await DetectionRuntime(size_registry).execute(size_plan, size_context)

    metadata_result = DetectionResult(
        success=True,
        plugin_name="fake-detection",
        plugin_version="1.0.0",
        metadata={"message": "x" * 1000},
    )
    with pytest.raises(DetectionPolicyViolation, match="oversized result metadata"):
        DetectionRuntime._validate_result(
            metadata_result,
            "fake-detection",
            "1.0.0",
            replace(size_context, policy=_policy(max_event_size_bytes=100)),
        )


def test_normalizer_utc_fingerprint_and_raw_payload_sanitization() -> None:
    asset_id = uuid4()
    naive = datetime(2026, 7, 31, 10, 0, 0)
    raw = RawSecurityEvent.model_validate(
        {
            **_event(asset_id),
            "timestamp": naive,
            "references": ["https://b.test", "https://a.test", "https://a.test"],
            "iocs": ["ioc-b", "ioc-a", "ioc-a"],
        }
    )
    assert raw.timestamp.tzinfo is UTC
    assert raw.references == ["https://a.test", "https://b.test"]
    assert raw.iocs == ["ioc-a", "ioc-b"]

    normalizer = DetectionResultNormalizer()
    normalized = normalizer.normalize_event(raw)
    assert normalized.event_type == "network.alert"
    assert normalized.source == "synthetic ids"
    assert "raw" not in normalized.attributes
    assert normalized.attributes["tags"] == ["synthetic"]
    assert normalizer.fingerprint(raw, "fake-detection", asset_id) == normalizer.fingerprint(
        raw.model_copy(update={"attributes": {"changed": True}}),
        "FAKE-DETECTION",
        asset_id,
    )
    assert len(normalizer.fingerprint(raw, "fake-detection", asset_id)) == 64


def _correlation_event(
    *,
    timestamp: datetime,
    source: str,
    rule: str | None,
    iocs: list[str],
) -> object:
    return type(
        "SyntheticEvent",
        (),
        {
            "id": uuid4(),
            "timestamp": timestamp,
            "source": source,
            "rule": rule,
            "attributes": {"iocs": iocs},
        },
    )()


def test_rule_based_correlation_uses_time_asset_source_ioc_and_rule() -> None:
    now = datetime.now(UTC)
    first = _correlation_event(timestamp=now, source="sensor-a", rule="R-1", iocs=["ioc-1"])
    second = _correlation_event(
        timestamp=now + timedelta(seconds=30), source="sensor-a", rule="R-1", iocs=["ioc-1"]
    )
    outside = _correlation_event(
        timestamp=now + timedelta(seconds=600), source="sensor-a", rule="R-1", iocs=["ioc-1"]
    )
    asset_id = uuid4()
    groups = RuleBasedCorrelationEngine().correlate(
        [outside, second, first],
        window_seconds=60,
        asset_ids={first.id: [asset_id], second.id: [asset_id], outside.id: [asset_id]},
    )
    assert {group.key_type for group in groups} == {"asset", "source", "ioc", "rule"}
    assert all(group.event_ids == [first.id, second.id] for group in groups)
    assert all(outside.id not in group.event_ids for group in groups)
    assert not hasattr(groups[0], "incident_id")


async def test_detection_api_end_to_end_correlation_filters_and_audit(
    client: AsyncClient,
) -> None:
    asset = await _asset(client)
    asset_id = UUID(str(asset["id"]))
    now = datetime.now(UTC)
    request = {
        "name": "Synthetic network detection",
        "asset_id": str(asset_id),
        "capabilities": ["network.detect", "ioc.detect", "rule.detect"],
        "log_source": "synthetic",
        "parser": "structured-json",
        "execute": True,
        "input": {
            "fake_events": [
                _event(asset_id, timestamp=now, unique_id="one"),
                _event(
                    asset_id,
                    timestamp=now + timedelta(seconds=10),
                    unique_id="two",
                ),
            ]
        },
    }
    response = await client.post("/detection/tasks", json=request)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["result_summary"] == {
        "success": True,
        "events": 2,
        "correlation_groups": 4,
        "records_collected": 2,
    }

    tasks = await client.get("/detection/tasks")
    events = await client.get(
        "/detection/events",
        params={"severity": "HIGH", "status": "CORRELATED", "asset_id": str(asset_id)},
    )
    plugins = await client.get("/detection/plugins")
    capabilities = await client.get("/detection/capabilities")
    assert tasks.status_code == 200 and tasks.json()["total"] == 1
    assert events.status_code == 200 and events.json()["total"] == 2
    assert plugins.json()[0]["name"] == "fake-detection"
    assert {item["name"] for item in capabilities.json()} == DETECTION_CAPABILITIES

    event_id = events.json()["items"][0]["id"]
    detail = await client.get(f"/detection/events/{event_id}")
    assert detail.status_code == 200
    assert detail.json()["assets"] == [str(asset_id)]
    assert detail.json()["event_type"] == "network.alert"
    assert detail.json()["attributes"]["iocs"] == ["203.0.113.7"]
    assert "raw" not in detail.json()["attributes"]

    invalid = await client.post(
        "/detection/tasks",
        json={**request, "target": "https://unauthorized.example", "execute": False},
    )
    assert invalid.status_code == 422
    missing_task = await client.get("/detection/events/00000000-0000-0000-0000-000000000001")
    assert missing_task.status_code == 404
    assert missing_task.json()["error"]["code"] == "SECURITY_EVENT_NOT_FOUND"

    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "DetectionTaskCreated",
            "DetectionExecutionStarted",
            "DetectionResultNormalized",
            "SecurityEventCreated",
            "SecurityEventsCorrelated",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(SecurityEvent)) == 2
        assert await session.scalar(select(func.count()).select_from(EventAsset)) == 2


async def test_detection_event_links_knowledge_evidence_and_fail_closed(
    client: AsyncClient,
) -> None:
    asset = await _asset(client, "Detection knowledge host")
    asset_id = UUID(str(asset["id"]))
    async with TestSessionFactory() as session:
        agent = Agent(name="phase9-agent", version="1.0.0", status="ONLINE")
        evidence_task = Task(name="evidence task", task_type="test", status="SUCCESS")
        source = KnowledgeSource(name="phase9-source", provider_type="test", configuration={})
        session.add_all([agent, evidence_task, source])
        await session.flush()
        evidence = Evidence(
            task_id=evidence_task.id,
            agent_id=agent.id,
            trace_id="phase9-evidence",
            url="memory://synthetic",
            evidence_type="JSON",
            sha256="a" * 64,
            html_hash="b" * 64,
            content_hash="c" * 64,
        )
        knowledge = Knowledge(
            source_id=source.id,
            knowledge_type="IOC",
            external_id="IOC-203.0.113.7",
            current_version="1",
            current_content_hash="d" * 64,
            title="Synthetic IOC",
            description="Phase 9 test knowledge",
            references=[],
            status="ACTIVE",
            attributes={},
        )
        session.add_all([evidence, knowledge])
        await session.flush()
        version = KnowledgeVersion(
            knowledge_id=knowledge.id,
            version="1",
            content_hash="d" * 64,
            payload={"ioc": "203.0.113.7"},
        )
        session.add(version)
        await session.commit()
        evidence_id = evidence.id
        knowledge_id = knowledge.id
        version_id = version.id

    linked_event = _event(asset_id)
    linked_event["evidence_ids"] = [str(evidence_id)]
    linked_event["knowledge_ids"] = [str(knowledge_id)]
    response = await client.post(
        "/detection/tasks",
        json={
            "name": "Linked detection",
            "asset_id": str(asset_id),
            "capabilities": ["ioc.detect"],
            "log_source": "synthetic",
            "parser": "structured-json",
            "execute": True,
            "input": {"fake_events": [linked_event]},
        },
    )
    assert response.status_code == 201, response.text
    event_page = await client.get("/detection/events")
    detail = await client.get(f"/detection/events/{event_page.json()['items'][0]['id']}")
    assert detail.json()["evidence"] == [str(evidence_id)]
    assert detail.json()["knowledge"] == [str(knowledge_id)]

    async with TestSessionFactory() as session:
        event_knowledge = await session.scalar(select(EventKnowledge))
        assert event_knowledge is not None
        assert event_knowledge.knowledge_version_id == version_id
        assert await session.scalar(select(func.count()).select_from(EventEvidence)) == 1
        assert await session.scalar(select(func.count()).select_from(EventReference)) == 1

    for field in ("evidence_ids", "knowledge_ids", "asset_ids"):
        invalid_event = _event(asset_id)
        invalid_event[field] = [str(uuid4())]
        failed = await client.post(
            "/detection/tasks",
            json={
                "name": f"Invalid {field}",
                "asset_id": str(asset_id),
                "capabilities": ["event.detect"],
                "log_source": "synthetic",
                "parser": "structured-json",
                "execute": True,
                "input": {"fake_events": [invalid_event]},
            },
        )
        assert failed.status_code in {404, 422}, failed.text


async def test_detection_models_migration_and_error_contract(client: AsyncClient) -> None:
    for table in (
        "detection_tasks",
        "detection_plugins",
        "detection_capabilities",
        "security_events",
        "event_references",
        "event_knowledge",
        "event_evidence",
        "event_assets",
    ):
        assert table in Base.metadata.tables
    assert {
        DetectionTask,
        DetectionPlugin,
        DetectionCapability,
        SecurityEvent,
        EventReference,
        EventKnowledge,
        EventEvidence,
        EventAsset,
    }
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260731_0011_detection_framework.py"
    )
    spec = importlib.util.spec_from_file_location("phase9_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260731_0011"
    assert module.down_revision == "20260731_0010"

    unknown_asset = await client.post(
        "/detection/tasks",
        json={
            "name": "Unknown asset",
            "asset_id": str(uuid4()),
            "capabilities": ["host.detect"],
            "log_source": "synthetic",
            "parser": "structured-json",
        },
    )
    assert unknown_asset.status_code == 404
    assert unknown_asset.json()["error"]["code"] == "ASSET_NOT_FOUND"
