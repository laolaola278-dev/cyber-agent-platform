"""Phase 13 Zeek Detection Plugin tests using controlled JSONL fixtures only."""

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.enums import FindingSeverity
from app.detection import (
    DetectionPlanner,
    DetectionPluginContext,
    DetectionRegistry,
    DetectionRuntime,
)
from app.exceptions import (
    DetectionExecutionError,
    DetectionPolicyViolation,
    DetectionValidationError,
    TelemetryValidationError,
)
from app.models import Incident, SecurityEvent
from app.plugins.zeek import ZeekDetectionPlugin, ZeekResultNormalizer
from app.plugins.zeek.telemetry import ZeekTelemetryPlugin
from app.schemas.detection import DetectionPolicy, DetectionResult, RawSecurityEvent
from app.schemas.telemetry import TelemetryPolicy
from app.schemas.zeek import ZeekDetectionCreate
from app.telemetry import TelemetryPlanner, TelemetryRegistry, TelemetryRuntime
from app.tools.zeek import ZeekAdapter, ZeekDataSource, ZeekSandboxProfile
from app.zeek import ZeekTelemetryBridge
from tests.conftest import TestSessionFactory

FIXTURE = Path(__file__).parent / "fixtures" / "zeek" / "logs.jsonl"


def _adapter(
    path: Path = FIXTURE,
    *,
    max_input_bytes: int = 5_000_000,
    max_records: int = 1_000,
) -> ZeekAdapter:
    return ZeekAdapter(
        {"phase13-fixture": ZeekDataSource(source_id="phase13-fixture", path=path, fixture=True)},
        profile=ZeekSandboxProfile(
            max_input_bytes=max_input_bytes,
            max_records=max_records,
        ),
    )


def _detection_context(
    plugin: ZeekDetectionPlugin,
    telemetry_records: list[dict[str, object]],
    *,
    asset_id: UUID | None = None,
) -> tuple[object, DetectionPluginContext, DetectionRegistry]:
    registry = DetectionRegistry()
    registry.register(plugin)
    policy = DetectionPolicy(
        allowed_log_sources=["zeek-telemetry"],
        allowed_plugins=["zeek-detection"],
        allowed_parsers=["zeek-jsonl"],
    )
    plan, context = DetectionPlanner(registry).plan(
        detection_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=asset_id or uuid4(),
        trace_id="phase-13-zeek",
        capabilities=["network.detect", "log.detect", "event.detect"],
        log_source="zeek-telemetry",
        parser="zeek-jsonl",
        policy=policy,
        input_data={
            "data_source_id": "phase13-fixture",
            "telemetry_records": telemetry_records,
        },
        plugin_name=plugin.name,
    )
    return plan, context, registry


async def _telemetry_records(adapter: ZeekAdapter | None = None) -> list[dict[str, object]]:
    selected = adapter or _adapter()
    registry = TelemetryRegistry()
    registry.register(ZeekTelemetryPlugin(selected))
    policy = TelemetryPolicy(allowed_plugins=["zeek-telemetry"], allowed_streams=["zeek"])
    bridge = ZeekTelemetryBridge(
        selected,
        TelemetryPlanner(registry),
        TelemetryRuntime(registry),
        policy,
    )
    return await bridge.collect(source_id="phase13-fixture")


async def _asset(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 13 Zeek Sensor",
            "value": "phase13-zeek-sensor",
            "criticality": "HIGH",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_adapter_allowlist_bounds_schema_and_evidence_lineage(tmp_path: Path) -> None:
    adapter = _adapter()
    collection = adapter.collect(" PHASE13-FIXTURE ")
    assert len(collection.records) == 6
    assert collection.lines_read == 6
    assert len(collection.source_sha256) == 64
    assert {record["payload"]["_log"] for record in collection.records} == {
        "conn",
        "dns",
        "http",
        "ssl",
        "files",
        "notice",
    }
    metadata = collection.records[0]["metadata"]
    assert metadata["line_number"] == 1
    assert len(str(metadata["raw_record_sha256"])) == 64
    assert len(str(metadata["schema_fingerprint"])) == 64
    assert "raw_line" not in metadata

    for source_id in ("", "missing", str(FIXTURE)):
        with pytest.raises(DetectionPolicyViolation):
            adapter.require_source(source_id)
    invalid = tmp_path / "conn.log"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(DetectionPolicyViolation, match="JSONL"):
        _adapter(invalid).require_source("phase13-fixture")
    with pytest.raises(DetectionPolicyViolation, match="input byte limit"):
        _adapter(max_input_bytes=1).collect("phase13-fixture")
    with pytest.raises(DetectionPolicyViolation, match="record limit"):
        _adapter(max_records=1).collect("phase13-fixture")
    # v1.0.1: the TSV rejection names the supported format and the
    # remediation instead of merely calling TSV "reserved".
    with pytest.raises(DetectionPolicyViolation, match="TSV input is not supported"):
        adapter.parse_tsv("#fields\tts\tuid")


def test_adapter_validation_and_operational_status() -> None:
    adapter = _adapter()
    invalid = (
        ("not-json", DetectionExecutionError, "invalid JSONL"),
        ("[]", DetectionExecutionError, "JSON object"),
        ('{"_log":"conn","uid":"C1"}', DetectionExecutionError, "missing ts"),
        ('{"_log":"weird","ts":1}', DetectionPolicyViolation, "not allowed"),
        ('{"_log":"dns","ts":1}', DetectionExecutionError, "missing uid"),
        ('{"_log":"files","ts":1}', DetectionExecutionError, "missing fuid"),
        ('{"_log":"notice","ts":1}', DetectionExecutionError, "missing note"),
    )
    for payload, exception, message in invalid:
        with pytest.raises(exception, match=message):
            adapter.parse_jsonl(payload)
    assert adapter.parse_jsonl("\n") == []
    status = adapter.status()
    assert status["healthy"] is True
    assert status["input_format"] == "jsonl"
    assert status["tsv_reserved"] is True
    assert status["supported_logs"] == ["conn", "dns", "files", "http", "notice", "ssl"]
    assert "path" not in str(status).casefold()


async def test_telemetry_bridge_and_plugin_lifecycle() -> None:
    records = await _telemetry_records()
    assert len(records) == 6
    assert all(record["stream"] == "zeek" for record in records)
    assert all(len(record["checksum"]) == 64 for record in records)
    assert all(record["metadata"]["telemetry_plugin"] == "zeek-telemetry" for record in records)

    plugin = ZeekDetectionPlugin()
    plan, context, registry = _detection_context(plugin, records)
    result = await DetectionRuntime(registry).execute(plan, context)
    assert result.records_collected == 6
    assert len(result.events) == 6
    assert result.metadata["telemetry_required"] is True
    assert result.metadata["records_from_telemetry"] == 6
    assert plugin._initialized is False
    assert not hasattr(plugin, "session")
    assert not hasattr(plugin, "adapter")
    assert not hasattr(plugin, "detection_runtime")

    with pytest.raises(DetectionValidationError, match="Telemetry-delivered"):
        await plugin.initialize(replace(context, input={"data_source_id": "phase13-fixture"}))
    with pytest.raises(DetectionValidationError, match="permissions"):
        await plugin.initialize(replace(context, granted_permissions=frozenset()))
    for operation in (
        lambda: plugin.collect(context),
        lambda: plugin.parse([], context),
        lambda: plugin.detect([], context),
    ):
        with pytest.raises(DetectionExecutionError, match="not initialized"):
            await operation()


async def test_telemetry_plugin_fail_closed_guards() -> None:
    adapter = _adapter()
    registry = TelemetryRegistry()
    plugin = ZeekTelemetryPlugin(adapter)
    registry.register(plugin)
    policy = TelemetryPolicy(allowed_plugins=[plugin.name], allowed_streams=["zeek"])
    plan, context = TelemetryPlanner(registry).plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase13",
        plugin_name=plugin.name,
        stream="zeek",
        partition="0",
        consumer="test",
        policy=policy,
        input_data=({"data_source_id": "phase13-fixture"},),
    )
    result = await TelemetryRuntime(registry).execute(plan, context)
    assert result.received_count == 6
    assert result.published_count == 6
    with pytest.raises(TelemetryValidationError, match="permissions"):
        await plugin.initialize(replace(context, granted_permissions=frozenset()))
    with pytest.raises(TelemetryValidationError, match="data_source_id"):
        await plugin.initialize(replace(context, input=()))


def test_normalizer_compatibility_matrix_and_evidence_preservation() -> None:
    collection = _adapter().collect("phase13-fixture")
    records = [dict(item) for item in collection.records]
    result = ZeekResultNormalizer().detection_result(
        records,
        plugin_name="zeek-detection",
        plugin_version="1.0.0",
        asset_id=uuid4(),
        source_id="phase13-fixture",
        collection_metadata={"telemetry_required": True},
    )
    by_log = {event.attributes["zeek_log"]: event for event in result.events}
    assert set(by_log) == {"conn", "dns", "http", "ssl", "files", "notice"}
    assert by_log["conn"].attributes["direction"] == "originator_to_responder"
    assert by_log["dns"].attributes["zeek_fields"]["query"] == "c2.example.test"
    assert by_log["http"].attributes["zeek_fields"]["status_code"] == 200
    assert by_log["ssl"].attributes["zeek_fields"]["version"] == "TLSv1.3"
    assert len(by_log["files"].attributes["zeek_fields"]["sha256"]) == 64
    assert by_log["notice"].severity is FindingSeverity.HIGH
    assert by_log["notice"].rule == "Scan::Address_Scan"
    assert all(event.tool == "zeek" for event in result.events)
    assert all(event.source == "zeek:phase13-fixture" for event in result.events)
    assert all("raw_line" not in str(event.attributes) for event in result.events)
    assert all(
        len(event.attributes["evidence_lineage"]["raw_record_sha256"]) == 64
        for event in result.events
    )

    knowledge_record = dict(records[5])
    knowledge_record["payload"] = {
        **knowledge_record["payload"],
        "attack": "T1190",
        "capec": "CAPEC-100",
        "cve": "CVE-2024-12345",
        "reference": "https://example.test/reference",
    }
    knowledge = (
        ZeekResultNormalizer()
        .detection_result(
            [knowledge_record],
            plugin_name="zeek-detection",
            plugin_version="1.0.0",
            asset_id=uuid4(),
            source_id="phase13-fixture",
            collection_metadata={},
        )
        .events[0]
    )
    assert "https://attack.mitre.org/techniques/T1190/" in knowledge.references
    assert "https://capec.mitre.org/data/definitions/100.html" in knowledge.references
    assert "https://nvd.nist.gov/vuln/detail/CVE-2024-12345" in knowledge.references
    assert "https://example.test/reference" in knowledge.references


def test_plugin_and_schema_identity_guards() -> None:
    with pytest.raises(ValidationError):
        ZeekDetectionCreate(
            asset_id=uuid4(),
            data_source_id="phase13-fixture",
            path="C:/Windows/System32/conn.log",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ZeekDetectionCreate(asset_id=uuid4(), data_source_id="   ")


async def test_zeek_api_end_to_end_telemetry_detection_and_no_incident(
    client: AsyncClient,
) -> None:
    asset = await _asset(client)
    asset_id = UUID(str(asset["id"]))
    status = await client.get("/detection/zeek/status")
    assert status.status_code == 200, status.text
    assert status.json()["healthy"] is True
    assert "path" not in status.text.casefold()

    payload = {
        "name": "Controlled Zeek ingestion",
        "asset_id": str(asset_id),
        "data_source_id": "phase13-fixture",
        "execute": True,
    }
    response = await client.post("/detection/zeek", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["result_summary"]["events"] == 6
    assert response.json()["result_summary"]["records_collected"] == 6

    events = await client.get("/detection/events", params={"asset_id": str(asset_id)})
    assert events.status_code == 200, events.text
    assert events.json()["total"] == 6
    detail = await client.get(f"/detection/events/{events.json()['items'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["plugin"] == "zeek-detection"
    assert detail.json()["tool"] == "zeek"
    assert detail.json()["assets"] == [str(asset_id)]

    rejected = await client.post("/detection/zeek", json={**payload, "path": str(FIXTURE)})
    assert rejected.status_code == 422
    missing = await client.post("/detection/zeek", json={**payload, "data_source_id": "missing"})
    assert missing.status_code == 403

    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(SecurityEvent)) == 6
        assert await session.scalar(select(func.count()).select_from(Incident)) == 0


async def test_detection_plugin_normalize_rejects_foreign_result() -> None:
    records = await _telemetry_records()
    plugin = ZeekDetectionPlugin()
    _, context, _ = _detection_context(plugin, records)
    await plugin.initialize(context)
    try:
        foreign = DetectionResult(
            success=True,
            plugin_name=plugin.name,
            plugin_version=plugin.version,
            events=[
                RawSecurityEvent(
                    event_type="network.zeek.notice",
                    source="zeek:test",
                    severity=FindingSeverity.HIGH,
                    timestamp="2026-08-01T04:00:00Z",
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
