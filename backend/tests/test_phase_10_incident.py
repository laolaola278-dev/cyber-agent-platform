"""Phase 10 Incident and Investigation Case Management acceptance tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.enums import FindingConfidence, FindingSeverity, IncidentStatus
from app.exceptions import IncidentExecutionError, IncidentPolicyViolation, IncidentValidationError
from app.incident import IncidentCorrelation, IncidentPlanner, IncidentRegistry, IncidentRuntime
from app.models import AuditLog, Incident, IncidentArtifact, IncidentTimeline, InvestigationCase
from app.schemas.incident import IncidentCandidate, IncidentPlan, IncidentPolicy
from tests.conftest import TestSessionFactory


def _manual_incident(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Suspicious privileged account activity",
        "description": "Manual SOC triage record",
        "severity": "HIGH",
        "confidence": "HIGH",
        "source": "MANUAL",
        "owner": "soc-lead",
        "assignee": "analyst-1",
        "queue": "tier-2",
        "classification": "account-compromise",
        "risk": "HIGH",
        "attributes": {"correlation_key": "manual:privileged-account"},
        "create_case": True,
    }
    payload.update(overrides)
    return payload


async def test_manual_incident_lifecycle_case_artifact_and_audit(client: AsyncClient) -> None:
    created = await client.post("/incidents", json=_manual_incident())
    assert created.status_code == 201, created.text
    body = created.json()
    incident_id = body["id"]
    assert body["status"] == "NEW"
    assert body["priority"] == "P2"
    assert body["queue"] == "tier-2"
    assert len(body["cases"]) == 1
    assert body["timelines"][0]["event_type"] == "CREATED"

    assigned = await client.post(
        f"/incidents/{incident_id}/assign",
        json={
            "actor": "soc-manager",
            "assignee": "analyst-2",
            "priority": "P1",
            "reason": "Confirmed active compromise",
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"] == "analyst-2"
    assert assigned.json()["priority"] == "P1"

    triaged = await client.post(
        f"/incidents/{incident_id}/transition",
        json={"status": "TRIAGED", "actor": "analyst-2", "reason": "Scope confirmed"},
    )
    assert triaged.status_code == 200, triaged.text
    assert triaged.json()["status"] == "TRIAGED"

    artifact = await client.post(
        f"/incidents/{incident_id}/artifacts",
        json={
            "artifact_type": "IP",
            "value": "203.0.113.7",
            "label": "suspected-command-and-control",
            "actor": "analyst-2",
        },
    )
    assert artifact.status_code == 201, artifact.text
    assert artifact.json()["artifact_type"] == "IP"

    case_id = body["cases"][0]["id"]
    comment = await client.post(
        f"/cases/{case_id}/comments",
        json={"author": "analyst-2", "body": "Collected authentication logs."},
    )
    assert comment.status_code == 201, comment.text

    second_case = await client.post(
        f"/incidents/{incident_id}/cases",
        json={
            "title": "Identity compromise investigation",
            "assignee": "identity-analyst",
            "actor": "soc-manager",
            "attributes": {"scope": "identity"},
        },
    )
    assert second_case.status_code == 201, second_case.text
    assert second_case.json()["incident_id"] == incident_id
    assert second_case.json()["assignee"] == "identity-analyst"

    detail = await client.get(f"/incidents/{incident_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["status"] == "TRIAGED"
    assert len(detail_body["artifacts"]) == 1
    assert len(detail_body["cases"]) == 2
    assert {item["event_type"] for item in detail_body["timelines"]} >= {
        "CREATED",
        "ASSIGNMENT_CHANGED",
        "STATUS_CHANGED",
        "ARTIFACT_LINKED",
        "COMMENTED",
        "INVESTIGATION_ACTION",
    }

    filtered = await client.get(
        "/incidents", params={"status": "TRIAGED", "priority": "P1", "assignee": "analyst-2"}
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    cases = await client.get("/cases", params={"incident_id": incident_id})
    assert cases.status_code == 200 and cases.json()["total"] == 2
    assert sum(len(item["comments"]) for item in cases.json()["items"]) == 1

    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "IncidentCreated",
            "IncidentAssigned",
            "IncidentTransitioned",
            "IncidentArtifactLinked",
            "CaseCommentAdded",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(InvestigationCase)) == 2
        assert await session.scalar(select(func.count()).select_from(IncidentArtifact)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentTimeline)) == 6


async def test_incident_state_machine_rejects_illegal_transition(client: AsyncClient) -> None:
    created = await client.post("/incidents", json=_manual_incident(create_case=False))
    incident_id = created.json()["id"]
    invalid = await client.post(
        f"/incidents/{incident_id}/transition",
        json={"status": "CLOSED", "actor": "analyst", "reason": "Skip lifecycle"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    detail = await client.get(f"/incidents/{incident_id}")
    assert detail.json()["status"] == "NEW"


async def test_duplicate_manual_incident_merges_source_facts(client: AsyncClient) -> None:
    first = await client.post("/incidents", json=_manual_incident(create_case=False))
    second = await client.post(
        "/incidents",
        json=_manual_incident(
            title="Duplicate observation",
            description="Same correlation key from another analyst",
            create_case=False,
        ),
    )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert "MERGED" in {item["event_type"] for item in second.json()["timelines"]}
    listing = await client.get("/incidents")
    assert listing.json()["total"] == 1
    async with TestSessionFactory() as session:
        actions = list(await session.scalars(select(AuditLog.action)))
        assert actions.count("IncidentCreated") == 1
        assert actions.count("IncidentMerged") == 1


async def test_incident_references_fail_closed(client: AsyncClient) -> None:
    missing_asset = await client.post(
        "/incidents",
        json=_manual_incident(asset_ids=[str(uuid4())], create_case=False),
    )
    assert missing_asset.status_code == 403
    assert missing_asset.json()["error"]["code"] == "INCIDENT_POLICY_VIOLATION"
    invalid_artifact = await client.post(
        "/incidents/00000000-0000-0000-0000-000000000001/artifacts",
        json={"artifact_type": "IP", "value": "203.0.113.8", "actor": "analyst"},
    )
    assert invalid_artifact.status_code == 404
    assert invalid_artifact.json()["error"]["code"] == "INCIDENT_NOT_FOUND"


async def test_priority_override_uses_matching_sla(client: AsyncClient) -> None:
    before = datetime.now(UTC)
    created = await client.post(
        "/incidents", json=_manual_incident(priority="P4", create_case=False)
    )
    assert created.status_code == 201, created.text
    due_at = datetime.fromisoformat(created.json()["sla_due_at"])
    assert timedelta(hours=23, minutes=59) < due_at - before < timedelta(days=1, minutes=1)


async def test_assessment_and_detection_facts_link_without_lifecycle_copy(
    client: AsyncClient,
) -> None:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 10 governed source",
            "value": "phase10-authorized-host",
            "criticality": "HIGH",
        },
    )
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]

    assessment = await client.post(
        "/assessment/tasks",
        json={
            "name": "Phase 10 synthetic assessment fact",
            "asset_id": asset_id,
            "capabilities": ["header.scan"],
            "execute": True,
            "input": {
                "fake_findings": [
                    {
                        "title": "Synthetic governed finding",
                        "severity": "HIGH",
                        "confidence": "HIGH",
                        "description": "No active scan was performed",
                        "affected_asset": "phase10-authorized-host",
                        "tool": "fake-tool",
                        "rule": "PHASE10-FINDING",
                        "unique_id_from_tool": "phase10-finding-1",
                    }
                ]
            },
        },
    )
    assert assessment.status_code == 201, assessment.text
    findings = await client.get("/assessment/findings", params={"asset_id": asset_id})
    assert findings.status_code == 200
    finding_id = findings.json()["items"][0]["id"]

    now = datetime.now(UTC)
    fake_events = [
        {
            "event_type": "network.alert",
            "source": "synthetic-sensor",
            "severity": "HIGH",
            "confidence": "HIGH",
            "timestamp": (now + timedelta(seconds=offset)).isoformat(),
            "asset_ids": [asset_id],
            "tool": "fake-sensor",
            "rule": "PHASE10-EVENT",
            "unique_id_from_tool": unique_id,
        }
        for offset, unique_id in ((0, "phase10-event-1"), (10, "phase10-event-2"))
    ]
    detection = await client.post(
        "/detection/tasks",
        json={
            "name": "Phase 10 synthetic detection facts",
            "asset_id": asset_id,
            "capabilities": ["network.detect", "rule.detect"],
            "log_source": "synthetic",
            "parser": "structured-json",
            "execute": True,
            "input": {"fake_events": fake_events},
        },
    )
    assert detection.status_code == 201, detection.text
    events = await client.get("/detection/events", params={"asset_id": asset_id})
    assert events.status_code == 200 and events.json()["total"] == 2
    event_ids = [item["id"] for item in events.json()["items"]]

    assessment_incident = await client.post(
        "/incidents",
        json={
            "title": "Assessment escalation",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "ASSESSMENT",
            "finding_ids": [finding_id],
            "asset_ids": [asset_id],
            "attributes": {"correlation_key": "assessment:phase10"},
            "create_case": False,
        },
    )
    assert assessment_incident.status_code == 201, assessment_incident.text
    assert assessment_incident.json()["finding_ids"] == [finding_id]
    assert assessment_incident.json()["source"] == "ASSESSMENT"

    detection_incident = await client.post(
        "/incidents",
        json={
            "title": "Detection escalation",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "DETECTION",
            "event_ids": event_ids,
            "asset_ids": [asset_id],
            "attributes": {"correlation_key": "detection:phase10"},
            "create_case": False,
        },
    )
    assert detection_incident.status_code == 201, detection_incident.text
    assert set(detection_incident.json()["event_ids"]) == set(event_ids)
    assert detection_incident.json()["source"] == "DETECTION"

    finding_detail = await client.get(f"/assessment/findings/{finding_id}")
    event_detail = await client.get(f"/detection/events/{event_ids[0]}")
    assert finding_detail.json()["status"] == "NEW"
    assert event_detail.json()["status"] == "CORRELATED"


async def test_correlation_planner_and_runtime_keep_incident_lifecycle_private() -> None:
    now = datetime.now(UTC)

    class Event:
        def __init__(self, event_id: UUID, timestamp: datetime) -> None:
            self.id = event_id
            self.timestamp = timestamp
            self.source = "suricata"
            self.severity = "HIGH"
            self.confidence = "HIGH"
            self.rule = "ET TEST"
            self.attributes: dict[str, object] = {}

    first, second = Event(uuid4(), now), Event(uuid4(), now + timedelta(seconds=10))
    asset_id = uuid4()
    candidates = IncidentCorrelation().events(
        [second, first],
        window_seconds=60,
        threshold=2,
        asset_ids={first.id: [asset_id], second.id: [asset_id]},
    )
    assert candidates
    candidate = candidates[0]
    planner = IncidentPlanner(IncidentRegistry())
    plan = planner.plan(candidate, IncidentPolicy())
    assert plan.steps == ["validate", "correlate", "create", "link", "audit"]
    assert not hasattr(IncidentRuntime(), "create_incident")
    assert not hasattr(candidate, "status")

    manual = IncidentCandidate(
        title="Manual",
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        source="MANUAL",
        correlation_key="manual:test",
    )
    manual_plan = planner.plan(
        manual,
        IncidentPolicy(
            minimum_severity=FindingSeverity.HIGH,
            minimum_confidence=FindingConfidence.MEDIUM,
        ),
    )
    assert manual_plan.source == "MANUAL"
    assert IncidentStatus.NEW.value == "NEW"


async def test_incident_policy_registry_planner_and_runtime_fail_closed() -> None:
    with pytest.raises(ValueError, match="At least one Incident source"):
        IncidentPolicy(allowed_sources=[])
    with pytest.raises(ValueError, match="Unsupported Incident sources"):
        IncidentPolicy(allowed_sources=["PLUGIN"])
    with pytest.raises(ValueError, match="every Incident priority"):
        IncidentPolicy(sla_targets_minutes={"P1": 15})
    with pytest.raises(ValueError, match="positive"):
        IncidentPolicy(sla_targets_minutes={"P1": 0, "P2": 60, "P3": 240, "P4": 1_440})
    with pytest.raises(IncidentValidationError, match="untrusted"):
        IncidentRegistry(frozenset({"PLUGIN"}))

    registry = IncidentRegistry()
    assert registry.sources == ("ASSESSMENT", "DETECTION", "MANUAL")
    with pytest.raises(IncidentValidationError, match="not registered"):
        registry.require("plugin")

    planner = IncidentPlanner(registry)
    low_candidate = IncidentCandidate(
        title="Below threshold",
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.LOW,
        source="ASSESSMENT",
        correlation_key="finding:low",
        finding_ids=[uuid4()],
    )
    with pytest.raises(IncidentPolicyViolation, match="below policy thresholds"):
        planner.plan(low_candidate, IncidentPolicy())

    detection_candidate = IncidentCandidate(
        title="Insufficient events",
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        source="DETECTION",
        correlation_key="detection:one",
        event_ids=[uuid4()],
    )
    with pytest.raises(IncidentPolicyViolation, match="correlated event threshold"):
        planner.plan(detection_candidate, IncidentPolicy())

    empty_assessment = IncidentCandidate(
        title="No facts",
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        source="ASSESSMENT",
        correlation_key="assessment:none",
    )
    with pytest.raises(IncidentPolicyViolation, match="requires Finding"):
        planner.plan(empty_assessment, IncidentPolicy())
    with pytest.raises(IncidentPolicyViolation, match="denied by policy"):
        planner.plan(
            empty_assessment.model_copy(update={"finding_ids": [uuid4()]}),
            IncidentPolicy(allowed_sources=["MANUAL"]),
        )

    fallback = planner.plan(
        IncidentCandidate(
            title="Fallback key",
            severity=FindingSeverity.HIGH,
            confidence=FindingConfidence.HIGH,
            source="MANUAL",
            correlation_key="",
            asset_ids=[uuid4()],
        ),
        IncidentPolicy(),
    )
    assert len(fallback.correlation_key) == 64

    invalid_plan = IncidentPlan(
        source="MANUAL",
        correlation_key="manual:invalid-plan",
        priority="P3",
        queue="security-operations",
        sla_minutes=240,
        steps=["create"],
    )
    with pytest.raises(IncidentExecutionError, match="unsupported lifecycle steps"):
        await IncidentRuntime().execute(
            IncidentCandidate(
                title="Invalid runtime plan",
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.HIGH,
                source="MANUAL",
                correlation_key="manual:invalid-plan",
            ),
            invalid_plan,
            object(),
        )


async def test_incident_full_lifecycle_reopen_and_case_filters(client: AsyncClient) -> None:
    created = await client.post("/incidents", json=_manual_incident(create_case=True))
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]

    for status in ("TRIAGED", "INVESTIGATING", "CONTAINED", "RESOLVED", "CLOSED"):
        response = await client.post(
            f"/incidents/{incident_id}/transition",
            json={"status": status, "actor": "lifecycle-tester"},
        )
        assert response.status_code == 200, response.text

    reopened = await client.post(
        f"/incidents/{incident_id}/transition",
        json={"status": "REOPENED", "actor": "lifecycle-tester"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "REOPENED"
    assert reopened.json()["resolved_at"] is None
    assert reopened.json()["closed_at"] is None

    case_id = created.json()["cases"][0]["id"]
    case_detail = await client.get(f"/cases/{case_id}")
    assert case_detail.status_code == 200, case_detail.text
    filtered = await client.get(
        "/cases",
        params={"status": "OPEN", "assignee": "analyst-1"},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1


async def test_incident_assignment_rejects_noop_update(client: AsyncClient) -> None:
    created = await client.post("/incidents", json=_manual_incident(create_case=False))
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]
    unchanged = await client.post(
        f"/incidents/{incident_id}/assign",
        json={
            "actor": "soc-manager",
            "owner": "soc-lead",
            "assignee": "analyst-1",
            "queue": "tier-2",
        },
    )
    assert unchanged.status_code == 409, unchanged.text
    assert unchanged.json()["error"]["code"] == "INCIDENT_CONFLICT"


async def test_incident_schema_validation_rejects_invalid_artifact_contracts() -> None:
    with pytest.raises(ValueError, match="At least one assignment field"):
        from app.schemas.incident import IncidentAssignmentCreate

        IncidentAssignmentCreate(actor="analyst")
    with pytest.raises(ValueError, match="require reference_id"):
        from app.schemas.incident import IncidentArtifactCreate

        IncidentArtifactCreate(artifact_type="FINDING")
    with pytest.raises(ValueError, match="require value"):
        from app.schemas.incident import IncidentArtifactCreate

        IncidentArtifactCreate(artifact_type="IP")


async def test_correlation_finding_and_event_edge_cases() -> None:
    correlation = IncidentCorrelation()

    class Finding:
        id = uuid4()
        title = "Synthetic finding"
        description = "Finding candidate"
        severity = "CRITICAL"
        confidence = "LOW"
        fingerprint = "fingerprint-1"

    finding_candidates = correlation.findings([Finding()])
    assert finding_candidates[0].source == "ASSESSMENT"
    assert finding_candidates[0].finding_ids == [Finding.id]

    class Event:
        def __init__(self, event_id: UUID, timestamp: datetime) -> None:
            self.id = event_id
            self.timestamp = timestamp
            self.source = "sensor"
            self.severity = "HIGH"
            self.confidence = "HIGH"
            self.rule = None
            self.attributes = {}

    now = datetime.now(UTC)
    events = [Event(uuid4(), now), Event(uuid4(), now + timedelta(seconds=10))]
    candidates = correlation.events(
        events,
        window_seconds=60,
        threshold=2,
        asset_ids={events[0].id: [], events[1].id: []},
    )
    assert candidates
    assert candidates[0].correlation_key == "source:sensor"

    windowed_events = [
        Event(uuid4(), now),
        Event(uuid4(), now + timedelta(seconds=120)),
        Event(uuid4(), now + timedelta(seconds=130)),
    ]
    windowed = correlation.events(
        windowed_events,
        window_seconds=60,
        threshold=2,
        asset_ids={event.id: [] for event in windowed_events},
    )
    assert len(windowed) == 1
    assert len(windowed[0].event_ids) == 2


async def test_incident_service_defensive_boundaries(client: AsyncClient) -> None:
    from app.events.bus import InMemoryEventBus
    from app.incident.service import IncidentService
    from app.repositories.incident import IncidentRepository, InvestigationCaseRepository

    async with TestSessionFactory() as session:
        registry = IncidentRegistry()
        service = IncidentService(
            session,
            IncidentRepository(session),
            InvestigationCaseRepository(session),
            registry,
            IncidentPlanner(registry),
            IncidentRuntime(),
            InMemoryEventBus(),
            IncidentPolicy(duplicate_merge_enabled=False),
        )
        candidate = IncidentCandidate(
            title="No duplicate lookup",
            severity=FindingSeverity.HIGH,
            confidence=FindingConfidence.HIGH,
            source="MANUAL",
            correlation_key="manual:no-duplicate",
        )
        plan = IncidentPlanner(registry).plan(candidate, IncidentPolicy())
        await service.correlate(candidate, plan)
        with pytest.raises(TypeError, match="non-Incident"):
            service._as_incident(object())

    missing_case = await client.get(f"/cases/{uuid4()}")
    assert missing_case.status_code == 404, missing_case.text
    assert missing_case.json()["error"]["code"] == "INVESTIGATION_CASE_NOT_FOUND"
