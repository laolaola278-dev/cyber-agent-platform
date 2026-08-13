"""Phase 11 Suricata Detection Plugin tests using controlled EVE fixtures only."""

from dataclasses import replace
from datetime import UTC
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.enums import FindingConfidence, FindingSeverity
from app.detection import (
    DetectionPlanner,
    DetectionPluginContext,
    DetectionRegistry,
    DetectionResultNormalizer,
    DetectionRuntime,
    RuleBasedCorrelationEngine,
)
from app.exceptions import (
    DetectionExecutionError,
    DetectionPolicyViolation,
    DetectionValidationError,
)
from app.incident import IncidentCorrelation
from app.models import AuditLog, Incident, SecurityEvent
from app.plugins.suricata import SuricataDetectionPlugin, SuricataResultNormalizer
from app.schemas.detection import DetectionPolicy, DetectionResult, RawSecurityEvent
from app.schemas.suricata import SuricataDetectionCreate
from app.tools.suricata import SuricataAdapter, SuricataDataSource, SuricataSandboxProfile
from tests.conftest import TestSessionFactory

FIXTURE = Path(__file__).parent / "fixtures" / "suricata" / "eve.jsonl"


def _adapter(
    path: Path = FIXTURE,
    *,
    source_id: str = "phase11-fixture",
    max_input_bytes: int = 5_000_000,
    max_records: int = 1_000,
) -> SuricataAdapter:
    return SuricataAdapter(
        {
            source_id: SuricataDataSource(
                source_id=source_id,
                path=path,
                fixture=True,
            )
        },
        profile=SuricataSandboxProfile(
            max_input_bytes=max_input_bytes,
            max_records=max_records,
        ),
    )


def _policy() -> DetectionPolicy:
    return DetectionPolicy(
        allowed_log_sources=["suricata-eve"],
        allowed_plugins=["suricata-detection"],
        allowed_parsers=["eve-jsonl"],
    )


def _context(
    plugin: SuricataDetectionPlugin,
    *,
    asset_id: UUID | None = None,
    input_data: dict[str, object] | None = None,
) -> tuple[object, DetectionPluginContext, DetectionRegistry]:
    registry = DetectionRegistry()
    registry.register(plugin)
    plan, context = DetectionPlanner(registry).plan(
        detection_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=asset_id or uuid4(),
        trace_id="phase-11-suricata",
        capabilities=["network.detect", "ids.detect", "rule.detect"],
        log_source="suricata-eve",
        parser="eve-jsonl",
        policy=_policy(),
        input_data=input_data or {"data_source_id": "phase11-fixture"},
        plugin_name=plugin.name,
    )
    return plan, context, registry


async def _asset(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 11 Suricata Sensor",
            "value": "phase11-suricata-sensor",
            "criticality": "HIGH",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_adapter_collects_only_allowlisted_bounded_eve_sources(tmp_path: Path) -> None:
    adapter = _adapter()
    collection = adapter.collect("  PHASE11-FIXTURE ")
    assert len(collection.records) == 8
    assert collection.source_id == "phase11-fixture"
    assert collection.bytes_read == FIXTURE.stat().st_size
    assert collection.lines_read == 8
    assert {record["event_type"] for record in collection.records} == {
        "alert",
        "flow",
        "stats",
        "dns",
        "http",
        "tls",
        "fileinfo",
    }

    for source_id in ("", "missing", str(FIXTURE)):
        with pytest.raises(DetectionPolicyViolation):
            adapter.require_source(source_id)

    invalid_extension = tmp_path / "eve.log"
    invalid_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(DetectionPolicyViolation, match="existing EVE JSON file"):
        _adapter(invalid_extension).require_source("phase11-fixture")
    with pytest.raises(DetectionPolicyViolation, match="existing EVE JSON file"):
        _adapter(tmp_path / "missing.jsonl").require_source("phase11-fixture")

    with pytest.raises(DetectionPolicyViolation, match="input byte limit"):
        _adapter(max_input_bytes=1).collect("phase11-fixture")
    with pytest.raises(DetectionPolicyViolation, match="record limit"):
        _adapter(max_records=1).collect("phase11-fixture")


def test_adapter_jsonl_and_eve_envelope_validation_fail_closed() -> None:
    adapter = _adapter()
    invalid_cases = (
        ("not-json", DetectionExecutionError, "invalid JSONL"),
        ("[]", DetectionExecutionError, "JSON object"),
        ('{"timestamp":"2026-07-31T18:00:00Z"}', DetectionExecutionError, "event_type"),
        ('{"event_type":"flow"}', DetectionExecutionError, "timestamp"),
        (
            '{"timestamp":"2026-07-31T18:00:00Z","event_type":"ssh"}',
            DetectionPolicyViolation,
            "not allowed",
        ),
        (
            '{"timestamp":"2026-07-31T18:00:00Z","event_type":"alert"}',
            DetectionExecutionError,
            "alert metadata",
        ),
    )
    for payload, exception, message in invalid_cases:
        with pytest.raises(exception, match=message):
            adapter.parse_jsonl(payload)

    assert adapter.parse_jsonl("\n\n") == []


def test_adapter_status_is_operational_and_never_exposes_paths(tmp_path: Path) -> None:
    healthy = _adapter().status()
    assert healthy["healthy"] is True
    assert healthy["version"] == "8.0.6"
    assert healthy["input_format"] == "eve-jsonl"
    assert healthy["sandbox"]["filesystem_policy"] == "configured-read-only-sources"
    assert healthy["sandbox"]["network_policy"] == "none"
    assert healthy["sandbox"]["permissions"] == ["eve.read"]
    assert "path" not in str(healthy).casefold()

    unhealthy = _adapter(tmp_path / "missing.jsonl").status()
    assert unhealthy["healthy"] is False
    assert unhealthy["sources"][0] == {
        "source_id": "phase11-fixture",
        "available": False,
        "fixture": True,
    }


async def test_plugin_lifecycle_and_runtime_boundaries() -> None:
    plugin = SuricataDetectionPlugin(_adapter())
    plan, context, registry = _context(plugin)
    result = await DetectionRuntime(registry).execute(plan, context)
    assert result.success is True
    assert result.records_collected == 8
    assert len(result.events) == 8
    assert result.plugin_name == "suricata-detection"
    assert result.metadata["data_source_id"] == "phase11-fixture"
    assert result.metadata["sandboxed"] is True
    assert plugin._initialized is False
    assert not hasattr(plugin, "session")
    assert not hasattr(plugin, "incident_service")

    for operation in (
        lambda: plugin.collect(context),
        lambda: plugin.parse([], context),
        lambda: plugin.detect([], context),
    ):
        with pytest.raises(DetectionExecutionError, match="not initialized"):
            await operation()

    with pytest.raises(DetectionValidationError, match="permissions"):
        await plugin.initialize(replace(context, granted_permissions=frozenset()))
    with pytest.raises(DetectionValidationError, match="data_source_id"):
        await plugin.initialize(replace(context, input={}))
    with pytest.raises(DetectionPolicyViolation, match="not allowlisted"):
        await plugin.initialize(replace(context, input={"data_source_id": "missing"}))


async def test_plugin_parse_normalize_and_identity_guards() -> None:
    plugin = SuricataDetectionPlugin(_adapter())
    _, context, _ = _context(plugin)
    await plugin.initialize(context)
    try:
        with pytest.raises(DetectionValidationError, match="non-object"):
            await plugin.parse(["invalid"], context)  # type: ignore[list-item]
        foreign = DetectionResult(
            success=True,
            plugin_name=plugin.name,
            plugin_version=plugin.version,
            events=[
                RawSecurityEvent(
                    event_type="network.alert",
                    source="suricata:test",
                    severity=FindingSeverity.HIGH,
                    timestamp="2026-07-31T18:00:00Z",
                    tool="foreign",
                )
            ],
        )
        with pytest.raises(DetectionValidationError, match="foreign tool"):
            await plugin.normalize(foreign)
        with pytest.raises(DetectionValidationError, match="identity"):
            await plugin.normalize(foreign.model_copy(update={"plugin_name": "other"}))
    finally:
        await plugin.shutdown()


def test_normalizer_maps_alert_rule_knowledge_and_bounded_payloads() -> None:
    records = _adapter().collect("phase11-fixture").records
    result = SuricataResultNormalizer().detection_result(
        list(records),
        plugin_name="suricata-detection",
        plugin_version="1.0.0",
        asset_id=uuid4(),
        source_id="phase11-fixture",
        collection_metadata={"sandboxed": True},
    )
    alert = result.events[0]
    blocked = result.events[1]
    by_type = {event.event_type: event for event in result.events[2:]}

    assert alert.rule == "1:2100498:7"
    assert alert.severity is FindingSeverity.CRITICAL
    assert alert.confidence is FindingConfidence.MEDIUM
    assert blocked.severity is FindingSeverity.HIGH
    assert blocked.confidence is FindingConfidence.HIGH
    assert alert.attributes["category"] == "Attempted Administrator Privilege Gain"
    assert alert.attributes["signature"] == "ET EXPLOIT Possible Web Exploit Attempt"
    assert alert.attributes["protocol"] == "TCP"
    assert alert.attributes["source_ip"] == "192.0.2.10"
    assert alert.attributes["destination_ip"] == "198.51.100.20"
    assert alert.attributes["knowledge_references"] == [
        "ATTACK:T1190",
        "CAPEC:CAPEC-100",
        "CVE:CVE-2024-12345",
    ]
    assert "https://attack.mitre.org/techniques/T1190/" in alert.references
    assert "https://capec.mitre.org/data/definitions/100.html" in alert.references
    assert "https://nvd.nist.gov/vuln/detail/CVE-2024-12345" in alert.references
    assert "https://rules.example.test/2100498" in alert.references
    assert by_type["network.flow"].attributes["flow_state"] == "closed"
    assert by_type["network.dns"].attributes["dns_rrname"] == "example.test"
    assert by_type["network.http"].attributes["http_http_method"] == "GET"
    assert by_type["network.tls"].attributes["tls_version"] == "TLS 1.3"
    assert by_type["network.fileinfo"].attributes["fileinfo_filename"] == "payload.bin"
    assert by_type["network.stats"].attributes["stats_uptime"] == 120
    assert all(event.timestamp.tzinfo is UTC for event in result.events)
    assert all("alert" not in event.attributes for event in result.events)
    assert all("flow" not in event.attributes for event in result.events)


def test_normalizer_rejects_invalid_severity_and_timestamp() -> None:
    normalizer = SuricataResultNormalizer()
    base = {
        "event_type": "alert",
        "timestamp": "2026-07-31T18:00:00Z",
        "alert": {"severity": 9, "signature_id": 1},
    }
    with pytest.raises(DetectionValidationError, match="severity"):
        normalizer.detection_result(
            [base],
            plugin_name="suricata-detection",
            plugin_version="1.0.0",
            asset_id=uuid4(),
            source_id="fixture",
            collection_metadata={},
        )
    with pytest.raises(DetectionValidationError, match="timestamp"):
        normalizer.detection_result(
            [{**base, "timestamp": "invalid", "alert": {"severity": 1}}],
            plugin_name="suricata-detection",
            plugin_version="1.0.0",
            asset_id=uuid4(),
            source_id="fixture",
            collection_metadata={},
        )


def test_schema_rejects_arbitrary_paths_and_empty_source() -> None:
    with pytest.raises(ValidationError):
        SuricataDetectionCreate(
            asset_id=uuid4(),
            data_source_id="phase11-fixture",
            path="C:/Windows/System32/log.jsonl",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        SuricataDetectionCreate(asset_id=uuid4(), data_source_id="   ")


async def test_suricata_api_security_event_correlation_candidate_and_audit(
    client: AsyncClient,
) -> None:
    asset = await _asset(client)
    asset_id = UUID(str(asset["id"]))
    status = await client.get("/detection/suricata/status")
    assert status.status_code == 200, status.text
    assert status.json()["healthy"] is True
    assert "path" not in status.text.casefold()

    payload = {
        "name": "Controlled Suricata EVE ingestion",
        "asset_id": str(asset_id),
        "data_source_id": "phase11-fixture",
        "execute": True,
    }
    response = await client.post("/detection/suricata", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["result_summary"] == {
        "success": True,
        "events": 8,
        "correlation_groups": 15,
        "records_collected": 8,
    }

    events_response = await client.get("/detection/events", params={"asset_id": str(asset_id)})
    assert events_response.status_code == 200, events_response.text
    assert events_response.json()["total"] == 8
    event_ids = [UUID(item["id"]) for item in events_response.json()["items"]]
    detail = await client.get(f"/detection/events/{event_ids[0]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["plugin"] == "suricata-detection"
    assert detail.json()["tool"] == "suricata"
    assert detail.json()["assets"] == [str(asset_id)]
    assert "raw" not in detail.json()["attributes"]

    invalid_path = await client.post(
        "/detection/suricata",
        json={**payload, "path": str(FIXTURE)},
    )
    assert invalid_path.status_code == 422
    missing_source = await client.post(
        "/detection/suricata",
        json={**payload, "data_source_id": "not-allowlisted"},
    )
    assert missing_source.status_code == 403
    assert missing_source.json()["error"]["code"] == "DETECTION_POLICY_VIOLATION"

    async with TestSessionFactory() as session:
        stored_events = list(
            await session.scalars(select(SecurityEvent).order_by(SecurityEvent.timestamp))
        )
        candidates = IncidentCorrelation().events(
            stored_events,
            window_seconds=300,
            threshold=2,
            asset_ids={event.id: [asset_id] for event in stored_events},
        )
        assert candidates
        assert all(candidate.source == "DETECTION" for candidate in candidates)
        assert any(candidate.correlation_key == "rule:1:2100498:7" for candidate in candidates)
        assert await session.scalar(select(func.count()).select_from(Incident)) == 0
        assert RuleBasedCorrelationEngine().correlate(
            stored_events,
            window_seconds=300,
            asset_ids={event.id: [asset_id] for event in stored_events},
        )
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "DetectionTaskCreated",
            "DetectionExecutionStarted",
            "DetectionResultNormalized",
            "SecurityEventCreated",
            "SecurityEventsCorrelated",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(SecurityEvent)) == 8


def test_platform_normalizer_discards_nested_suricata_payloads() -> None:
    event = RawSecurityEvent(
        event_type="network.alert",
        source="suricata:fixture",
        severity=FindingSeverity.HIGH,
        timestamp="2026-07-31T18:00:00Z",
        attributes={"raw": {"alert": {"signature": "must not persist"}}, "sid": 1},
    )
    normalized = DetectionResultNormalizer.normalize_event(event)
    assert normalized.attributes == {"sid": 1}
