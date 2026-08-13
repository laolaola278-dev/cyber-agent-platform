"""Phase 14 unified Response Framework security and lifecycle acceptance tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.database.base import Base
from app.exceptions import (
    ResponseConflict,
    ResponseExecutionError,
    ResponsePolicyViolation,
    ResponseValidationError,
)
from app.models import AuditLog, Incident
from app.models.response import (
    ResponseApproval,
    ResponseEvidence,
    ResponseExecution,
    ResponsePlan,
    ResponsePlugin,
    ResponsePolicyRecord,
    ResponseRollback,
)
from app.response import (
    ApprovalService,
    FakeResponsePlugin,
    ResponsePlanner,
    ResponsePluginContext,
    ResponsePolicyEngine,
    ResponseRegistry,
    ResponseRuntime,
)
from app.response.policy import ResponsePolicyInput
from app.response.rollback import RollbackService
from app.schemas.response import (
    ApprovalState,
    ResponseApprovalCreate,
    ResponseEvidenceItem,
    ResponseEvidenceRead,
    ResponsePlanCreate,
    ResponsePlanSpec,
    ResponsePolicy,
    ResponseRejectionCreate,
    ResponseResult,
    ResponseRollbackRequest,
    ResponseVerification,
)
from tests.conftest import TestSessionFactory


def _incident_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Confirmed endpoint compromise",
        "description": "Phase 14 controlled response fixture",
        "severity": "HIGH",
        "confidence": "HIGH",
        "source": "MANUAL",
        "owner": "soc-lead",
        "assignee": "analyst-1",
        "queue": "tier-2",
        "classification": "endpoint-compromise",
        "risk": "HIGH",
        "attributes": {"correlation_key": "phase14:endpoint"},
        "create_case": False,
    }
    payload.update(overrides)
    return payload


async def _fixture_scope(client: AsyncClient) -> tuple[dict[str, object], dict[str, object]]:
    asset_response = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 14 endpoint",
            "value": f"phase14-{uuid4()}.example.test",
            "criticality": "HIGH",
            "properties": {"response_owner": "soc"},
        },
    )
    assert asset_response.status_code == 201, asset_response.text
    incident_response = await client.post("/incidents", json=_incident_payload())
    assert incident_response.status_code == 201, incident_response.text
    return incident_response.json(), asset_response.json()


def _plan_payload(
    incident_id: str,
    asset_id: str,
    *,
    capability: str = "response.block",
    requested_by: str = "requester@example.test",
    risk_level: str = "HIGH",
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "asset_ids": [asset_id],
        "target_capability": capability,
        "requested_by": requested_by,
        "reason": "Contain confirmed compromise in a controlled synthetic test",
        "risk_level": risk_level,
        "parameters": parameters or {},
        "rollback_parameters": {"restore": True},
    }


async def _create_plan(
    client: AsyncClient,
    *,
    capability: str = "response.block",
    risk_level: str = "HIGH",
    parameters: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    incident, asset = await _fixture_scope(client)
    response = await client.post(
        "/response/plans",
        json=_plan_payload(
            str(incident["id"]),
            str(asset["id"]),
            capability=capability,
            risk_level=risk_level,
            parameters=parameters,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json(), incident, asset


async def test_response_api_plan_approval_execution_rollback_and_audit(
    client: AsyncClient,
) -> None:
    plan, incident_before, asset_before = await _create_plan(client)
    plan_id = str(plan["id"])
    assert plan["approval_state"] == "PENDING_APPROVAL"
    assert plan["execution_state"] == "BLOCKED"
    assert plan["rollback_state"] == "AVAILABLE"
    assert plan["asset_ids"] == [asset_before["id"]]
    assert plan["plan"]["steps"] == [
        "initialize",
        "plan",
        "validate",
        "execute",
        "verify",
        "shutdown",
    ]

    premature = await client.post(
        f"/response/plans/{plan_id}/execute", json={"actor": "operator@example.test"}
    )
    assert premature.status_code == 403
    assert premature.json()["error"]["code"] == "RESPONSE_POLICY_VIOLATION"

    approved = await client.post(
        f"/response/plans/{plan_id}/approve",
        json={"approver": "approver@example.test", "comment": "Scope reviewed"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_state"] == "APPROVED"
    assert approved.json()["execution_state"] == "READY"

    executed = await client.post(
        f"/response/plans/{plan_id}/execute", json={"actor": "operator@example.test"}
    )
    assert executed.status_code == 200, executed.text
    executed_body = executed.json()
    assert executed_body["approval_state"] == "EXECUTED"
    assert executed_body["execution_state"] == "VERIFIED"
    assert executed_body["executions"][0]["verification_status"] == "VERIFIED"
    assert "rollback_token" not in executed.text
    assert executed_body["evidence"][0]["metadata"]["action"] == "execute"

    rolled_back = await client.post(
        f"/response/plans/{plan_id}/rollback",
        json={
            "actor": "rollback-operator@example.test",
            "reason": "Containment no longer required",
        },
    )
    assert rolled_back.status_code == 200, rolled_back.text
    rollback_body = rolled_back.json()
    assert rollback_body["approval_state"] == "ROLLED_BACK"
    assert rollback_body["rollback_state"] == "VERIFIED"
    assert rollback_body["rollbacks"][0]["verification_status"] == "VERIFIED"
    assert {item["metadata"]["action"] for item in rollback_body["evidence"]} == {
        "execute",
        "rollback",
    }

    incident_after = (await client.get(f"/incidents/{incident_before['id']}")).json()
    asset_after = (await client.get(f"/assets/{asset_before['id']}")).json()
    for field in ("status", "severity", "priority", "owner", "assignee", "attributes"):
        assert incident_after[field] == incident_before[field]
    for field in ("name", "value", "criticality", "properties", "deleted_at"):
        assert asset_after[field] == asset_before[field]

    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "ResponsePlanCreated",
            "ResponsePlanApproved",
            "ResponseExecutionStarted",
            "ResponseExecutionCompleted",
            "ResponseRollbackCompleted",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(ResponsePlan)) == 1
        assert await session.scalar(select(func.count()).select_from(ResponseApproval)) == 1
        assert await session.scalar(select(func.count()).select_from(ResponseExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(ResponseRollback)) == 1
        assert await session.scalar(select(func.count()).select_from(ResponseEvidence)) == 2


async def test_reject_self_approval_duplicate_and_expired_state_machine(
    client: AsyncClient,
) -> None:
    plan, _, _ = await _create_plan(client)
    plan_id = str(plan["id"])
    self_approval = await client.post(
        f"/response/plans/{plan_id}/approve",
        json={"approver": "requester@example.test"},
    )
    assert self_approval.status_code == 403

    rejected = await client.post(
        f"/response/plans/{plan_id}/reject",
        json={"approver": "approver@example.test", "comment": "Unsafe scope"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["approval_state"] == "REJECTED"
    second_rejection = await client.post(
        f"/response/plans/{plan_id}/reject",
        json={"approver": "approver-2@example.test", "comment": "Still unsafe"},
    )
    assert second_rejection.status_code == 409

    plan2, _, _ = await _create_plan(client)
    plan2_id = UUID(str(plan2["id"]))
    async with TestSessionFactory() as session:
        row = await session.get(ResponsePlan, plan2_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired = await client.post(
        f"/response/plans/{plan2_id}/approve",
        json={"approver": "approver@example.test"},
    )
    assert expired.status_code == 403
    detail = await client.get(f"/response/plans/{plan2_id}")
    assert detail.json()["approval_state"] == "EXPIRED"
    assert detail.json()["execution_state"] == "BLOCKED"


async def test_policy_capability_threshold_business_hours_and_maintenance_window() -> None:
    engine = ResponsePolicyEngine()
    base = ResponsePolicy(
        allowed_capabilities=["response.notify", "response.block"],
        denied_capabilities=["response.waf"],
        approval_required_capabilities=["response.block"],
        allowed_incident_types=["endpoint-compromise"],
        allowed_asset_types=[AssetType.HOST],
        maintenance_windows=["08:00-18:00"],
        business_hours_start=8,
        business_hours_end=18,
    )
    low = ResponsePolicyInput(
        capability="response.notify",
        risk_level=RiskLevel.LOW,
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.LOW,
        asset_types=frozenset({AssetType.HOST}),
        requested_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
    )
    decision = engine.decide(base, low)
    assert decision.allowed and decision.automatic_execution and not decision.approval_required
    elevated = replace(low, incident_severity=FindingSeverity.HIGH)
    elevated_decision = engine.decide(base, elevated)
    assert elevated_decision.approval_required and not elevated_decision.automatic_execution
    block = engine.decide(base, replace(low, capability="response.block"))
    assert block.approval_required
    with pytest.raises(ResponsePolicyViolation, match="explicitly denied"):
        engine.decide(base, replace(low, capability="response.waf"))
    with pytest.raises(ResponsePolicyViolation, match="business hours"):
        engine.decide(base, replace(low, requested_at=datetime(2026, 8, 1, 7, tzinfo=UTC)))
    narrow = base.model_copy(update={"maintenance_windows": ["10:00-11:00"]})
    with pytest.raises(ResponsePolicyViolation, match="maintenance"):
        engine.decide(narrow, low)


async def test_registry_certification_plugin_boundary_and_readonly_context() -> None:
    registry = ResponseRegistry()
    plugin = FakeResponsePlugin()
    registry.register(plugin)
    assert registry.resolve("response.block") is plugin
    assert not any(
        hasattr(plugin, attribute)
        for attribute in (
            "session",
            "database",
            "repository",
            "incident_service",
            "asset_service",
            "report_service",
            "workflow_service",
        )
    )

    class ForbiddenPlugin(FakeResponsePlugin):
        name = "forbidden-response"
        permissions = frozenset({"response.execute", "response.verify", "database.access"})

    with pytest.raises(ResponseValidationError, match="forbidden permissions"):
        ResponseRegistry().register(ForbiddenPlugin())

    policy = ResponsePolicy(
        allowed_capabilities=["response.block"],
        denied_capabilities=[],
        approval_required_capabilities=["response.block"],
    )
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=[uuid4()],
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.HIGH,
        capability="response.block",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase14-boundary",
        actor="requester",
        parameters={
            "nested": {"items": ["one", "two"]},
            "labels": {"containment", "synthetic"},
        },
        rollback_parameters={"restore": True},
        policy=policy,
        plugin_name=plugin.name,
    )
    assert isinstance(context.parameters, MappingProxyType)
    assert context.parameters["nested"]["items"] == ("one", "two")
    assert context.parameters["labels"] == frozenset({"containment", "synthetic"})
    with pytest.raises(TypeError):
        context.parameters["forbidden"] = True  # type: ignore[index]
    result = await ResponseRuntime(registry).execute(specification, context, policy)
    assert result.success and result.verification.verified
    assert plugin._context is None


async def test_runtime_rejects_permission_mismatch_and_immutable_scope() -> None:
    registry = ResponseRegistry()
    plugin = FakeResponsePlugin()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.block"],
        denied_capabilities=[],
        approval_required_capabilities=["response.block"],
    )
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=[uuid4()],
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.HIGH,
        capability="response.block",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase14-runtime",
        actor="operator",
        parameters={},
        rollback_parameters={},
        policy=policy,
        plugin_name=plugin.name,
    )
    with pytest.raises(ResponsePolicyViolation, match="permissions"):
        await ResponseRuntime(registry).execute(
            specification,
            replace(context, granted_permissions=frozenset({"response.execute"})),
            policy,
        )

    class ScopeMutatingPlugin(FakeResponsePlugin):
        name = "scope-mutating-response"

        async def plan(self, plan: object, context: ResponsePluginContext) -> object:
            planned = await super().plan(plan, context)  # type: ignore[arg-type]
            return planned.model_copy(update={"incident_id": uuid4()})

    malicious_registry = ResponseRegistry()
    malicious = ScopeMutatingPlugin()
    malicious_registry.register(malicious)
    malicious_specification, malicious_context = ResponsePlanner(
        malicious_registry, ResponsePolicyEngine()
    ).plan(
        response_plan_id=uuid4(),
        incident_id=specification.incident_id,
        asset_ids=specification.asset_ids,
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.HIGH,
        capability="response.block",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase14-immutable",
        actor="operator",
        parameters={},
        rollback_parameters={},
        policy=policy,
        plugin_name=malicious.name,
    )
    with pytest.raises(ResponseExecutionError, match="immutable"):
        await ResponseRuntime(malicious_registry).execute(
            malicious_specification, malicious_context, policy
        )
    assert malicious._context is None


async def test_execution_failure_is_persisted_and_audited(client: AsyncClient) -> None:
    plan, _, _ = await _create_plan(client, parameters={"force_execution_failure": True})
    approved = await client.post(
        f"/response/plans/{plan['id']}/approve",
        json={"approver": "approver@example.test"},
    )
    assert approved.status_code == 200
    failed = await client.post(
        f"/response/plans/{plan['id']}/execute",
        json={"actor": "operator@example.test"},
    )
    assert failed.status_code == 422
    detail = await client.get(f"/response/plans/{plan['id']}")
    body = detail.json()
    assert body["execution_state"] == "FAILED"
    assert body["executions"][0]["status"] == "FAILED"
    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert "ResponseExecutionFailed" in actions


async def test_plugin_list_policy_persistence_filtering_and_strict_requests(
    client: AsyncClient,
) -> None:
    plan, incident, _ = await _create_plan(client)
    plugins = await client.get("/response/plugins")
    assert plugins.status_code == 200
    assert {item["name"] for item in plugins.json()} == {
        "fake-response",
        "waf-response",
        "firewall-response",
        "edr-response",
    }
    assert all(item["certified"] is True for item in plugins.json())
    filtered = await client.get(
        "/response/plans",
        params={"incident_id": incident["id"], "approval_state": "PENDING_APPROVAL"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == plan["id"]

    strict = await client.post(
        "/response/plans",
        json={
            **_plan_payload(str(incident["id"]), str(uuid4())),
            "direct_database_access": True,
        },
    )
    assert strict.status_code == 422
    denied = await client.post(
        "/response/plans",
        json=_plan_payload(str(incident["id"]), str(uuid4()), capability="response.edr"),
    )
    assert denied.status_code in {403, 422}

    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(ResponsePlugin)) == 4
        assert await session.scalar(select(func.count()).select_from(ResponsePolicyRecord)) == 1


async def test_unknown_incident_asset_and_closed_incident_fail_closed(client: AsyncClient) -> None:
    incident, asset = await _fixture_scope(client)
    unknown_incident = await client.post(
        "/response/plans",
        json=_plan_payload(str(uuid4()), str(asset["id"])),
    )
    assert unknown_incident.status_code == 422
    unknown_asset = await client.post(
        "/response/plans",
        json=_plan_payload(str(incident["id"]), str(uuid4())),
    )
    assert unknown_asset.status_code == 422

    incident_id = UUID(str(incident["id"]))
    async with TestSessionFactory() as session:
        row = await session.get(Incident, incident_id)
        assert row is not None
        row.status = "CLOSED"
        await session.commit()
    closed = await client.post(
        "/response/plans",
        json=_plan_payload(str(incident_id), str(asset["id"])),
    )
    assert closed.status_code == 403


async def test_response_schema_alias_and_extra_forbid() -> None:
    evidence = ResponseEvidence(
        id=uuid4(),
        plan_id=uuid4(),
        evidence_type="RESPONSE_RECEIPT",
        sha256="a" * 64,
        reference="synthetic://response",
        metadata_={"safe": True},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    read = ResponseEvidenceRead.model_validate(evidence)
    assert read.metadata == {"safe": True}
    with pytest.raises(ValidationError, match="extra"):
        ResponsePlanCreate.model_validate(
            {
                "incident_id": uuid4(),
                "asset_ids": [uuid4()],
                "target_capability": "response.block",
                "requested_by": "requester",
                "reason": "controlled test",
                "risk_level": "HIGH",
                "database": "forbidden",
            }
        )


def test_approval_service_multi_level_and_duplicate_approval() -> None:
    plan = ResponsePlan(
        id=uuid4(),
        incident_id=uuid4(),
        plugin_id=uuid4(),
        target_capability="response.block",
        requested_by="requester",
        reason="controlled",
        risk_level="HIGH",
        approval_state=ApprovalState.PENDING_APPROVAL.value,
        execution_state="BLOCKED",
        rollback_state="AVAILABLE",
        policy_snapshot={},
        plan={},
        parameters={},
        rollback_parameters={},
        supports_rollback=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        approvals=[],
        assets=[],
    )
    policy = ResponsePolicy(
        allowed_capabilities=["response.block"],
        denied_capabilities=[],
        approval_required_capabilities=["response.block"],
        required_approval_levels=2,
    )
    service = ApprovalService()
    first = service.approve(
        plan,
        ResponseApprovalCreate(approver="approver-1", level=1),
        policy,
    )
    plan.approvals.append(first)
    assert plan.approval_state == "PENDING_APPROVAL"
    with pytest.raises(ResponseConflict, match="already approved"):
        service.approve(
            plan,
            ResponseApprovalCreate(approver="approver-1", level=2),
            policy,
        )
    second = service.approve(
        plan,
        ResponseApprovalCreate(approver="approver-2", level=2),
        policy,
    )
    assert second.approval_level == 2
    assert plan.approval_state == "APPROVED"
    assert plan.execution_state == "READY"
    with pytest.raises(ResponseConflict, match="not pending"):
        service.reject(
            plan,
            ResponseRejectionCreate(approver="approver-3", comment="late rejection"),
        )


def test_response_model_and_migration_boundary_contract() -> None:
    response_tables = {name for name in Base.metadata.tables if name.startswith("response_")}
    assert response_tables == {
        "response_approvals",
        "response_evidence",
        "response_executions",
        "response_plan_assets",
        "response_plans",
        "response_plugins",
        "response_policies",
        "response_rollbacks",
    }
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260801_0014_response_framework.py"
    )
    migration = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "20260801_0014"' in migration
    assert 'down_revision: str | None = "20260801_0013"' in migration
    for table in response_tables:
        assert f'"{table}"' in migration
    for protected_table in (
        'op.alter_column("incidents"',
        'op.alter_column("security_events"',
        'op.alter_column("findings"',
        'op.alter_column("evidence"',
        'op.alter_column("assets"',
    ):
        assert protected_table not in migration


def test_policy_fail_closed_matrix_and_cross_midnight_window() -> None:
    engine = ResponsePolicyEngine()
    context = ResponsePolicyInput(
        capability="response.notify",
        risk_level=RiskLevel.LOW,
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.LOW,
        asset_types=frozenset({AssetType.HOST}),
        requested_at=datetime(2026, 8, 2, 1),
    )
    with pytest.raises(ResponsePolicyViolation, match="disabled"):
        engine.decide(ResponsePolicy(enabled=False), context)
    restricted = ResponsePolicy(
        allowed_capabilities=["response.ticket"],
        denied_capabilities=[],
        allowed_incident_types=["phishing"],
        allowed_asset_types=[AssetType.IP],
    )
    with pytest.raises(ResponsePolicyViolation, match="not allowed"):
        engine.decide(restricted, context)
    incident_restricted = restricted.model_copy(
        update={"allowed_capabilities": ["response.notify"]}
    )
    with pytest.raises(ResponsePolicyViolation, match="Incident type"):
        engine.decide(incident_restricted, context)
    asset_restricted = incident_restricted.model_copy(
        update={"allowed_incident_types": ["endpoint-compromise"]}
    )
    with pytest.raises(ResponsePolicyViolation, match="Asset types"):
        engine.decide(asset_restricted, context)
    overnight = asset_restricted.model_copy(
        update={
            "allowed_asset_types": [AssetType.HOST],
            "maintenance_windows": ["23:00-02:00", "invalid"],
        }
    )
    assert engine.decide(overnight, context).automatic_execution


def test_registry_certification_rejection_matrix() -> None:
    class EmptyIdentity(FakeResponsePlugin):
        name = ""

    class UnsupportedCapability(FakeResponsePlugin):
        name = "unsupported-capability"
        capabilities = frozenset({"response.unknown"})

    class MissingPermission(FakeResponsePlugin):
        name = "missing-permission"
        permissions = frozenset({"response.execute"})

    class NoApproval(FakeResponsePlugin):
        name = "no-approval"
        supports_approval = False

    class NoSandbox(FakeResponsePlugin):
        name = "no-sandbox"
        sandbox_compatible = False

    registry = ResponseRegistry()
    for plugin, message in (
        (EmptyIdentity(), "name and version"),
        (UnsupportedCapability(), "unsupported"),
        (MissingPermission(), "lifecycle"),
        (NoApproval(), "approval"),
        (NoSandbox(), "sandbox"),
    ):
        with pytest.raises(ResponseValidationError, match=message):
            registry.register(plugin)
    plugin = FakeResponsePlugin()
    registry.register(plugin)
    with pytest.raises(ResponseValidationError, match="already registered"):
        registry.register(plugin)
    with pytest.raises(ResponseValidationError, match="not registered"):
        registry.require("missing")
    with pytest.raises(ResponseValidationError, match="No Response plugin"):
        registry.resolve("response.waf")


async def test_plugin_and_runtime_result_validation_fail_closed() -> None:
    plugin = FakeResponsePlugin()
    incident_id = uuid4()
    asset_id = uuid4()
    policy = ResponsePolicy(
        allowed_capabilities=["response.block"],
        denied_capabilities=[],
        approval_required_capabilities=["response.block"],
        max_evidence_items=1,
    )
    registry = ResponseRegistry()
    registry.register(plugin)
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=incident_id,
        asset_ids=[asset_id],
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.HIGH,
        capability="response.block",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase14-result-boundary",
        actor="operator",
        parameters={},
        rollback_parameters={},
        policy=policy,
        plugin_name=plugin.name,
    )
    with pytest.raises(ResponseExecutionError, match="not initialized"):
        await plugin.plan(specification, context)
    await plugin.initialize(context)
    with pytest.raises(ResponsePolicyViolation, match="capability"):
        await plugin.validate(
            specification.model_copy(update={"target_capability": "response.ticket"}),
            context,
        )
    with pytest.raises(ResponsePolicyViolation, match="scope"):
        await plugin.validate(specification.model_copy(update={"asset_ids": [uuid4()]}), context)
    await plugin.shutdown()

    base_result = ResponseResult(
        success=True,
        plugin_name=plugin.name,
        plugin_version=plugin.version,
        capability="response.block",
        execution_status="EXECUTED",
        verification=ResponseVerification(verified=True, status="VERIFIED"),
        evidence=[],
        duration_ms=1,
        message="synthetic",
        rollback_supported=True,
        metadata={},
    )
    validate = ResponseRuntime._validate_result
    for result, message in (
        (base_result.model_copy(update={"plugin_name": "foreign"}), "identity"),
        (base_result.model_copy(update={"capability": "response.ticket"}), "capability"),
        (base_result.model_copy(update={"rollback_supported": False}), "rollback"),
        (
            base_result.model_copy(
                update={
                    "evidence": [
                        ResponseEvidenceItem(
                            evidence_type="RECEIPT",
                            sha256="a" * 64,
                            reference="synthetic://one",
                        ),
                        ResponseEvidenceItem(
                            evidence_type="RECEIPT",
                            sha256="b" * 64,
                            reference="synthetic://two",
                        ),
                    ]
                }
            ),
            "evidence limit",
        ),
        (
            base_result.model_copy(
                update={"verification": ResponseVerification(verified=False, status="FAILED")}
            ),
            "verification",
        ),
    ):
        with pytest.raises((ResponseExecutionError, ResponsePolicyViolation), match=message):
            validate(result, plugin.name, plugin.version, specification, policy)


async def test_rollback_service_rejects_missing_prerequisites() -> None:
    plan = ResponsePlan(
        id=uuid4(),
        incident_id=uuid4(),
        plugin_id=uuid4(),
        target_capability="response.block",
        requested_by="requester",
        reason="controlled",
        risk_level="HIGH",
        approval_state="EXECUTED",
        execution_state="VERIFIED",
        rollback_state="AVAILABLE",
        policy_snapshot={},
        plan={},
        parameters={},
        rollback_parameters={},
        supports_rollback=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    execution = ResponseExecution(
        id=uuid4(),
        plan_id=plan.id,
        plugin_id=plan.plugin_id,
        status="SUCCEEDED",
        verification_status="VERIFIED",
        result={},
        duration_ms=1,
        message="done",
        started_at=datetime.now(UTC),
    )
    specification = ResponsePlanSpec(
        incident_id=plan.incident_id,
        asset_ids=[],
        target_capability="response.block",
        plugin_name="fake-response",
        parameters={},
        rollback_parameters={},
        risk_level=RiskLevel.HIGH,
        approval_required=True,
        supports_rollback=True,
        policy_name="test",
        steps=[],
    )
    context = ResponsePluginContext(
        response_plan_id=plan.id,
        incident_id=plan.incident_id,
        asset_ids=(),
        trace_id="rollback-preconditions",
        actor="operator",
        capability="response.block",
        parameters=MappingProxyType({}),
        rollback_parameters=MappingProxyType({}),
        rollback_token=None,
        granted_permissions=frozenset(),
    )
    service = RollbackService()
    request = ResponseRollbackRequest(actor="operator", reason="controlled")
    with pytest.raises(ResponsePolicyViolation, match="does not support"):
        await service.rollback(
            plan=plan,
            execution=execution,
            specification=specification,
            context=context,
            policy=ResponsePolicy(),
            payload=request,
            runtime=ResponseRuntime(ResponseRegistry()),
        )
    plan.supports_rollback = True
    plan.rollback_state = "VERIFIED"
    with pytest.raises(ResponseConflict, match="not rollback eligible"):
        await service.rollback(
            plan=plan,
            execution=execution,
            specification=specification,
            context=context,
            policy=ResponsePolicy(),
            payload=request,
            runtime=ResponseRuntime(ResponseRegistry()),
        )
    plan.rollback_state = "AVAILABLE"
    with pytest.raises(ResponsePolicyViolation, match="no rollback token"):
        await service.rollback(
            plan=plan,
            execution=execution,
            specification=specification,
            context=context,
            policy=ResponsePolicy(),
            payload=request,
            runtime=ResponseRuntime(ResponseRegistry()),
        )


async def test_runtime_rejects_undeclared_rollback() -> None:
    class NonRollbackPlugin(FakeResponsePlugin):
        name = "non-rollback-response"
        supports_rollback = False
        permissions = frozenset({"response.execute", "response.verify"})

    registry = ResponseRegistry()
    plugin = NonRollbackPlugin()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.notify"],
        denied_capabilities=[],
        approval_required_capabilities=[],
    )
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=[uuid4()],
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.LOW,
        capability="response.notify",
        risk_level=RiskLevel.LOW,
        requested_at=datetime.now(UTC),
        trace_id="phase14-no-rollback",
        actor="operator",
        parameters={},
        rollback_parameters={},
        policy=policy,
        plugin_name=plugin.name,
    )
    assert specification.supports_rollback is False
    with pytest.raises(ResponsePolicyViolation, match="does not support rollback"):
        await ResponseRuntime(registry).rollback(specification, context, policy)
