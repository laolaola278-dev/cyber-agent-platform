"""Phase 19 EDR Response Plugin safety, lifecycle and certification tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.models import AuditLog
from app.models.response import ResponseEvidence
from app.plugins.edr import EDRResponsePlugin
from app.response import ResponsePlanner, ResponsePolicyEngine, ResponseRegistry, ResponseRuntime
from app.response.contracts import ResponsePluginContext, readonly_mapping
from app.runtime.plugin_manifest import PluginManifestV2
from app.schemas.response import ResponsePolicy
from app.tools.edr import (
    EDRAction,
    EDRAdapter,
    EDRPolicy,
    EDRPolicyProvider,
    HostAction,
    HostActionStatus,
    HostIsolationState,
    MockEDRProvider,
)
from tests.conftest import TestSessionFactory

ROOT = Path(__file__).resolve().parents[2]


def _action_payload(
    host_id: str,
    *,
    action: EDRAction = EDRAction.HOST_ISOLATE,
    action_id: str = "cap-host-action-001",
    requested_by: str = "edr-requester@example.test",
    approved_by: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, object]:
    return HostAction.create(
        id=action_id,
        host_id=host_id,
        action=action,
        version="1.0.0",
        requested_by=requested_by,
        approved_by=approved_by,
        reason="Contain a confirmed synthetic compromised host",
        created_at=created_at or datetime.now(UTC),
    ).model_dump(mode="json")


async def _scope(client: AsyncClient) -> tuple[dict[str, object], dict[str, object]]:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 19 synthetic endpoint",
            "value": f"phase19-host-{uuid4()}.example.test",
            "criticality": "HIGH",
            "properties": {"edr_scope": "synthetic", "provider": "mock-edr"},
        },
    )
    assert asset.status_code == 201, asset.text
    incident = await client.post(
        "/incidents",
        json={
            "title": "Confirmed endpoint compromise",
            "description": "Phase 19 controlled EDR response fixture",
            "severity": "CRITICAL",
            "confidence": "HIGH",
            "source": "MANUAL",
            "owner": "soc-lead",
            "assignee": "analyst-1",
            "queue": "tier-3",
            "classification": "endpoint-compromise",
            "risk": "CRITICAL",
            "attributes": {"correlation_key": f"phase19:{uuid4()}"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    return incident.json(), asset.json()


async def _create_plan(
    client: AsyncClient,
    *,
    action: EDRAction = EDRAction.HOST_ISOLATE,
) -> dict[str, object]:
    incident, asset = await _scope(client)
    host_id = str(asset["id"])
    app = client._transport.app  # type: ignore[attr-defined]
    if not hasattr(app.state, "mock_edr_provider"):
        app.state.mock_edr_provider = MockEDRProvider()
    app.state.mock_edr_provider.seed_host(host_id)
    response = await client.post(
        "/response/plans",
        json={
            "incident_id": incident["id"],
            "asset_ids": [host_id],
            "target_capability": "response.edr",
            "plugin_name": "edr-response",
            "requested_by": "edr-requester@example.test",
            "reason": "Contain a confirmed endpoint in the synthetic Provider",
            "risk_level": "CRITICAL",
            "parameters": {"host_action": _action_payload(host_id, action=action)},
            "rollback_parameters": {
                "action": (
                    EDRAction.HOST_UNISOLATE.value
                    if action is EDRAction.HOST_ISOLATE
                    else EDRAction.HOST_ISOLATE.value
                ),
                "reason": "Restore prior synthetic host isolation state",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_edr_api_isolate_readback_evidence_audit_and_rollback(
    client: AsyncClient,
) -> None:
    plan = await _create_plan(client)
    assert plan["approval_state"] == "PENDING_APPROVAL"
    assert plan["execution_state"] == "BLOCKED"
    assert plan["plan"]["plugin_name"] == "edr-response"
    assert plan["parameters"]["host_action"]["status"] == "REQUESTED"

    approved = await client.post(
        f"/response/plans/{plan['id']}/approve",
        json={"approver": "edr-approver@example.test", "comment": "Host scope reviewed"},
    )
    assert approved.status_code == 200, approved.text
    executed = await client.post(
        f"/response/plans/{plan['id']}/execute",
        json={"actor": "edr-operator@example.test"},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["execution_state"] == "VERIFIED"
    assert body["approval_state"] == "EXECUTED"
    evidence = body["evidence"][0]
    assert evidence["evidence_type"] == "EDR_HOST_ACTION"
    assert evidence["metadata"]["desired_state"] == "ISOLATED"
    assert evidence["metadata"]["observed_state"] == "ISOLATED"
    assert evidence["metadata"]["network_access"] is False
    assert evidence["metadata"]["production_access"] is False
    assert evidence["metadata"]["filesystem_write"] is False
    assert evidence["metadata"]["shell_execute"] is False
    assert "rollback_token" not in executed.text

    rolled_back = await client.post(
        f"/response/plans/{plan['id']}/rollback",
        json={"actor": "edr-rollback@example.test", "reason": "Containment released"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    rollback = rolled_back.json()
    assert rollback["approval_state"] == "ROLLED_BACK"
    assert rollback["rollback_state"] == "VERIFIED"
    assert [item["evidence_type"] for item in rollback["evidence"]] == [
        "EDR_HOST_ACTION",
        "EDR_HOST_ROLLBACK",
    ]
    assert rollback["evidence"][1]["metadata"]["observed_state"] == "UNISOLATED"

    incident_after = (await client.get(f"/incidents/{plan['incident_id']}")).json()
    asset_after = (await client.get(f"/assets/{plan['asset_ids'][0]}")).json()
    assert incident_after["status"] == "NEW"
    assert incident_after["classification"] == "endpoint-compromise"
    assert asset_after["properties"] == {"edr_scope": "synthetic", "provider": "mock-edr"}
    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "ResponsePlanCreated",
            "ResponsePlanApproved",
            "ResponseExecutionCompleted",
            "ResponseRollbackCompleted",
        } <= actions
        evidence_rows = list(await session.scalars(select(ResponseEvidence)))
        assert [item.evidence_type for item in evidence_rows] == [
            "EDR_HOST_ACTION",
            "EDR_HOST_ROLLBACK",
        ]


async def test_edr_unisolate_action_and_inverse_rollback(client: AsyncClient) -> None:
    plan = await _create_plan(client, action=EDRAction.HOST_UNISOLATE)
    provider = client._transport.app.state.mock_edr_provider  # type: ignore[attr-defined]
    provider.inject_observed_state(plan["asset_ids"][0], HostIsolationState.ISOLATED)
    await client.post(
        f"/response/plans/{plan['id']}/approve",
        json={"approver": "edr-approver@example.test"},
    )
    executed = await client.post(
        f"/response/plans/{plan['id']}/execute",
        json={"actor": "edr-operator@example.test"},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["evidence"][0]["metadata"]["observed_state"] == "UNISOLATED"
    rollback = await client.post(
        f"/response/plans/{plan['id']}/rollback",
        json={"actor": "edr-rollback@example.test", "reason": "Restore containment"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["evidence"][1]["metadata"]["observed_state"] == "ISOLATED"


def test_host_action_strict_checksum_identity_and_time_validation() -> None:
    host_id = str(uuid4())
    action = HostAction.model_validate(_action_payload(host_id))
    assert action.checksum == action.calculate_checksum()
    assert action.status is HostActionStatus.REQUESTED
    assert action.host_id == host_id
    with pytest.raises(ValidationError, match="checksum"):
        HostAction.model_validate({**action.model_dump(mode="json"), "checksum": "0" * 64})
    with pytest.raises(ValueError, match="canonical Asset UUID"):
        _action_payload("not-an-asset-id")
    with pytest.raises(ValidationError, match="future"):
        HostAction.create(
            id="future",
            host_id=host_id,
            action=EDRAction.HOST_ISOLATE,
            version="1",
            requested_by="requester",
            reason="future action",
            created_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    with pytest.raises(ValidationError, match="extra"):
        HostAction.model_validate({**action.model_dump(mode="json"), "endpoint": "real"})


async def test_mock_provider_is_idempotent_and_has_zero_dangerous_capabilities() -> None:
    host_id = str(uuid4())
    provider = MockEDRProvider()
    provider.seed_host(host_id)
    action = HostAction.model_validate(_action_payload(host_id))
    first = await provider.execute(action)
    second = await provider.execute(action)
    assert first.changed is True
    assert second.changed is False
    assert second.metadata["idempotent_replay"] is True
    assert (await provider.read_host(host_id)).isolation_state is HostIsolationState.ISOLATED
    assert provider.network_access is False
    assert provider.production_access is False
    assert provider.filesystem_write is False
    assert provider.shell_execute is False
    changed = HostAction.create(
        id=action.id,
        host_id=host_id,
        action=EDRAction.HOST_UNISOLATE,
        version="2",
        requested_by="requester",
        reason="different content",
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ResponsePolicyViolation, match="idempotency"):
        await provider.execute(changed)


@pytest.mark.parametrize(("fault", "message"), [("offline", "offline"), ("missing", "missing")])
async def test_provider_fails_closed_when_agent_offline_or_host_missing(
    fault: str, message: str
) -> None:
    host_id = str(uuid4())
    provider = MockEDRProvider()
    provider.seed_host(host_id)
    if fault == "offline":
        provider.set_online(host_id, False)
    else:
        provider.remove_host(host_id)
    action = HostAction.model_validate(_action_payload(host_id))
    with pytest.raises(ResponseExecutionError, match=message):
        await provider.execute(action)
    observed = await provider.read_host(host_id)
    if fault == "missing":
        assert not observed.present
        assert observed.isolation_state is HostIsolationState.UNKNOWN


async def test_adapter_reports_drift_without_auto_remediation() -> None:
    host_id = str(uuid4())
    provider = MockEDRProvider()
    provider.seed_host(host_id)
    adapter = EDRAdapter(provider, EDRPolicyProvider())
    action = HostAction.model_validate(_action_payload(host_id))
    receipt = await adapter.execute(action, approval_required=True)
    provider.inject_observed_state(host_id, HostIsolationState.UNISOLATED)
    verified, observed, drift = await adapter.verify(action, receipt)
    assert verified is False
    assert drift is True
    assert observed.isolation_state is HostIsolationState.UNISOLATED
    assert (await provider.read_host(host_id)).isolation_state is HostIsolationState.UNISOLATED


def test_edr_policy_blocks_reserved_actions_self_approval_and_unsafe_configuration() -> None:
    host_id = str(uuid4())
    policy = EDRPolicyProvider()
    isolate = HostAction.model_validate(_action_payload(host_id))
    policy.validate_action(isolate, approval_required=True)
    with pytest.raises(ResponsePolicyViolation, match="approval"):
        policy.validate_action(isolate, approval_required=False)
    reserved = HostAction.model_validate(
        _action_payload(host_id, action=EDRAction.PROCESS_TERMINATE)
    )
    with pytest.raises(ResponsePolicyViolation, match="reserved"):
        policy.validate_action(reserved, approval_required=True)
    client_approved = HostAction.model_validate(
        _action_payload(
            host_id,
            requested_by="requester@example.test",
            approved_by="forged-approver@example.test",
        )
    )
    with pytest.raises(ResponsePolicyViolation, match="platform-owned"):
        policy.validate_action(client_approved, approval_required=True)
    with pytest.raises(ValueError, match="mock-only"):
        EDRPolicy(mock_only=False)
    with pytest.raises(ValueError, match="cannot be disabled"):
        EDRPolicy(require_approval=False)
    with pytest.raises(ValueError, match="cannot be executable"):
        EDRPolicy(allowed_actions=frozenset({EDRAction.PROCESS_TERMINATE}))


async def test_plugin_rejects_scope_expansion_reserved_action_and_bad_token() -> None:
    asset_id = uuid4()
    provider = MockEDRProvider()
    provider.seed_host(str(asset_id))
    plugin = EDRResponsePlugin(EDRAdapter(provider, EDRPolicyProvider()))
    registry = ResponseRegistry()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.edr"],
        denied_capabilities=[],
        approval_required_capabilities=["response.edr"],
    )

    def plan(action: dict[str, object]) -> tuple[object, object]:
        return ResponsePlanner(registry, ResponsePolicyEngine()).plan(
            response_plan_id=uuid4(),
            incident_id=uuid4(),
            asset_ids=[asset_id],
            asset_types={AssetType.HOST},
            incident_type="endpoint-compromise",
            incident_severity=FindingSeverity.CRITICAL,
            capability="response.edr",
            risk_level=RiskLevel.CRITICAL,
            requested_at=datetime.now(UTC),
            trace_id="phase19-runtime",
            actor="operator",
            parameters={"host_action": action},
            rollback_parameters={"action": "host.unisolate"},
            policy=policy,
            plugin_name=plugin.name,
        )

    expanded, expanded_context = plan(_action_payload(str(uuid4())))
    with pytest.raises(ResponsePolicyViolation, match="host_id"):
        await ResponseRuntime(registry).execute(expanded, expanded_context, policy)

    reserved, reserved_context = plan(
        _action_payload(str(asset_id), action=EDRAction.COLLECT_PACKAGE)
    )
    with pytest.raises(ResponsePolicyViolation, match="reserved"):
        await ResponseRuntime(registry).execute(reserved, reserved_context, policy)

    safe, safe_context = plan(_action_payload(str(asset_id), action_id="safe-action"))
    result = await ResponseRuntime(registry).execute(safe, safe_context, policy)
    assert result.verification.verified
    invalid = replace(safe_context, rollback_token="edr-rb:invalid")
    await plugin.initialize(invalid)
    with pytest.raises(ResponsePolicyViolation, match="rollback token"):
        await plugin.rollback(safe, invalid)
    await plugin.shutdown()


async def test_edr_adapter_fail_closed_parse_and_verification_matrix() -> None:
    host_id = str(uuid4())
    provider = MockEDRProvider()
    provider.seed_host(host_id)
    adapter = EDRAdapter(provider, EDRPolicyProvider())
    action = HostAction.model_validate(_action_payload(host_id))
    with pytest.raises(ResponsePolicyViolation, match="mapping"):
        adapter.parse_action({})
    with pytest.raises(ResponsePolicyViolation, match="invalid"):
        adapter.parse_action(
            {"host_action": {**action.model_dump(mode="json"), "checksum": "0" * 64}}
        )
    with pytest.raises(ResponsePolicyViolation, match="exactly one"):
        adapter.validate_scope(action, ())
    with pytest.raises(ResponsePolicyViolation, match="immutable"):
        adapter.validate_scope(action, (uuid4(),))
    with pytest.raises(ResponsePolicyViolation, match="string"):
        adapter.parse_rollback_action({"action": 1}, original=action, actor="operator")
    with pytest.raises(ResponsePolicyViolation, match="Unsupported"):
        adapter.parse_rollback_action({"action": "wipe"}, original=action, actor="operator")
    rollback = adapter.parse_rollback_action({}, original=action, actor="operator")
    assert rollback.action is EDRAction.HOST_UNISOLATE

    receipt = await adapter.execute(action, approval_required=True)
    verified, _, drift = await adapter.verify(action, receipt)
    assert verified and not drift
    provider.set_online(host_id, False)
    verified, observation, drift = await adapter.verify(action, receipt)
    assert not verified
    assert not observation.online
    assert not drift


def test_edr_manifest_v2_certification_boundary() -> None:
    raw = yaml.safe_load((ROOT / "plugins" / "edr" / "manifest.yaml").read_text("utf-8"))
    manifest = PluginManifestV2.model_validate(raw)
    assert manifest.schema_version == "v2"
    assert manifest.capabilities == ("response.edr",)
    assert manifest.worker.runtime_version == manifest.runtime_version == "phase-18.1"
    assert manifest.worker.max_concurrency == 1
    assert manifest.secret.references == manifest.sandbox.secret_references == ()
    assert manifest.network.enabled is manifest.sandbox.network_enabled is False
    assert manifest.filesystem.writable is manifest.sandbox.filesystem_writable is False
    assert manifest.provider_requirements.network is False
    assert manifest.provider_requirements.filesystem is False
    assert manifest.provider_requirements.secret is False
    with pytest.raises(ValidationError, match="extra"):
        PluginManifestV2.model_validate({**raw, "production_endpoint": "https://edr.example"})


async def test_edr_plugin_fail_closed_lifecycle_branches() -> None:
    host_id = uuid4()
    action = _action_payload(str(host_id))
    provider = MockEDRProvider()
    provider.seed_host(str(host_id))
    plugin = EDRResponsePlugin(EDRAdapter(provider, EDRPolicyProvider()))
    base = ResponsePluginContext(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=(host_id,),
        trace_id="phase19-branches",
        actor="operator",
        capability="response.edr",
        parameters=readonly_mapping({"host_action": action}),
        rollback_parameters=readonly_mapping({"action": "host.unisolate"}),
        rollback_token=None,
        granted_permissions=plugin.permissions,
    )
    with pytest.raises(ResponsePolicyViolation, match="permission"):
        await plugin.initialize(replace(base, granted_permissions=frozenset()))
    with pytest.raises(ResponsePolicyViolation, match="only response.edr"):
        await plugin.initialize(replace(base, capability="response.firewall"))
    with pytest.raises(ResponseExecutionError, match="not initialized"):
        await plugin.plan(None, base)  # type: ignore[arg-type]

    registry = ResponseRegistry()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.edr"],
        denied_capabilities=[],
        approval_required_capabilities=["response.edr"],
    )
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=base.response_plan_id,
        incident_id=base.incident_id,
        asset_ids=[host_id],
        asset_types={AssetType.HOST},
        incident_type="endpoint-compromise",
        incident_severity=FindingSeverity.CRITICAL,
        capability="response.edr",
        risk_level=RiskLevel.CRITICAL,
        requested_at=datetime.now(UTC),
        trace_id=base.trace_id,
        actor=base.actor,
        parameters={"host_action": action},
        rollback_parameters={"action": "host.unisolate"},
        policy=policy,
        plugin_name=plugin.name,
    )
    await plugin.initialize(context)
    bad_capability = specification.model_copy(update={"target_capability": "response.firewall"})
    with pytest.raises(ResponsePolicyViolation, match="capability"):
        await plugin.validate(bad_capability, context)
    bad_scope = specification.model_copy(update={"incident_id": uuid4()})
    with pytest.raises(ResponsePolicyViolation, match="scope"):
        await plugin.validate(bad_scope, context)
    no_approval = specification.model_copy(update={"approval_required": False})
    with pytest.raises(ResponsePolicyViolation, match="approval"):
        await plugin.validate(no_approval, context)
    owned = HostAction.model_validate(
        _action_payload(str(host_id), action_id="provider-owned-action")
    )
    owned_plan = specification.model_copy(
        update={"parameters": {"host_action": owned.model_dump(mode="json")}}
    )
    with pytest.raises(ResponsePolicyViolation, match="Provider-owned"):
        await plugin.validate(owned_plan, context)
    pending = await plugin.execute(specification, context)
    await plugin.shutdown()
    await plugin.initialize(context)
    with pytest.raises(ResponseExecutionError, match="no pending"):
        await plugin.verify(pending, context)
    await plugin.shutdown()


async def test_edr_policy_and_provider_rejection_branches() -> None:
    host_id = str(uuid4())
    action = HostAction.model_validate(_action_payload(host_id))
    disabled = EDRPolicyProvider(EDRPolicy(enabled=False))
    assert disabled.policy.enabled is False
    with pytest.raises(ResponsePolicyViolation, match="disabled"):
        disabled.validate_action(action, approval_required=True)
    restricted = EDRPolicyProvider(EDRPolicy(allowed_actions=frozenset({EDRAction.HOST_UNISOLATE})))
    with pytest.raises(ResponsePolicyViolation, match="allowlisted"):
        restricted.validate_action(action, approval_required=True)
    completed = action.model_copy(update={"status": HostActionStatus.SUCCEEDED})
    with pytest.raises(ResponsePolicyViolation, match="REQUESTED"):
        EDRPolicyProvider().validate_action(completed, approval_required=True)
    with pytest.raises(ValueError, match="empty"):
        EDRPolicy(allowed_actions=frozenset())

    other_host = str(uuid4())
    rollback = HostAction.model_validate(
        _action_payload(other_host, action=EDRAction.HOST_UNISOLATE, action_id="rollback")
    )
    with pytest.raises(ResponsePolicyViolation, match="target host"):
        EDRPolicyProvider().validate_rollback(action, rollback)
    wrong_inverse = HostAction.model_validate(
        _action_payload(host_id, action=EDRAction.HOST_ISOLATE, action_id="wrong-rollback")
    )
    with pytest.raises(ResponsePolicyViolation, match="inverse"):
        EDRPolicyProvider().validate_rollback(action, wrong_inverse)

    provider = MockEDRProvider()
    provider.seed_host(host_id)
    reserved = HostAction.model_validate(
        _action_payload(host_id, action=EDRAction.PROCESS_TERMINATE)
    )
    with pytest.raises(ResponsePolicyViolation, match="unsupported"):
        await provider.execute(reserved)
    with pytest.raises(KeyError):
        provider.set_online("missing", False)
    with pytest.raises(KeyError):
        provider.inject_observed_state("missing", HostIsolationState.ISOLATED)


async def test_edr_plugin_health_and_registry_api(client: AsyncClient) -> None:
    plugins = await client.get("/response/plugins")
    assert plugins.status_code == 200, plugins.text
    edr = next(item for item in plugins.json() if item["name"] == "edr-response")
    assert edr["capabilities"] == ["response.edr"]
    assert edr["supports_approval"] is True
    assert edr["supports_rollback"] is True
    assert edr["health_status"] == "HEALTHY"
    assert edr["certified"] is True

    provider = MockEDRProvider()
    plugin = EDRResponsePlugin(EDRAdapter(provider, EDRPolicyProvider()))
    assert await plugin.health()
    provider.network_access = True
    assert not await plugin.health()
