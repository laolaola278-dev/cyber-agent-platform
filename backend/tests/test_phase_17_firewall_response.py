"""Phase 17 Firewall Response Plugin safety and lifecycle acceptance tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.models import AuditLog
from app.models.response import ResponseEvidence
from app.plugins.firewall import FirewallResponsePlugin
from app.response import ResponsePlanner, ResponsePolicyEngine, ResponseRegistry, ResponseRuntime
from app.schemas.response import ResponsePolicy
from app.tools.firewall import (
    FirewallAction,
    FirewallAdapter,
    FirewallDirection,
    FirewallPolicy,
    FirewallPolicyProvider,
    FirewallProtocol,
    FirewallRollbackAction,
    FirewallRule,
    FirewallRuleStatus,
    MockFirewallProvider,
)
from tests.conftest import TestSessionFactory


def _rule_payload(asset_id: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "cap-firewall-rule-001",
        "name": "Block confirmed malicious network source",
        "action": FirewallAction.BLOCK,
        "direction": FirewallDirection.INGRESS,
        "source": "203.0.113.9/32",
        "destination": "198.51.100.20/32",
        "protocol": FirewallProtocol.TCP,
        "source_ports": (),
        "destination_ports": (8080,),
        "table": "filter",
        "chain": "INPUT",
        "priority": 500,
        "version": "1.0.0",
        "impact_scope": (asset_id,),
    }
    values.update(overrides)
    return FirewallRule.create(
        id=str(values["id"]),
        name=str(values["name"]),
        action=FirewallAction(values["action"]),
        direction=FirewallDirection(values["direction"]),
        source=str(values["source"]),
        destination=str(values["destination"]),
        protocol=FirewallProtocol(values["protocol"]),
        source_ports=tuple(values["source_ports"]),
        destination_ports=tuple(values["destination_ports"]),
        table=str(values["table"]),
        chain=str(values["chain"]),
        priority=int(values["priority"]),
        version=str(values["version"]),
        impact_scope=tuple(values["impact_scope"]),
    ).model_dump(mode="json")


async def _scope(client: AsyncClient) -> tuple[dict[str, object], dict[str, object]]:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "IP",
            "name": "Phase 17 protected workload",
            "value": f"198.51.100.{uuid4().int % 200 + 1}",
            "criticality": "HIGH",
            "properties": {"firewall_scope": "synthetic"},
        },
    )
    assert asset.status_code == 201, asset.text
    incident = await client.post(
        "/incidents",
        json={
            "title": "Confirmed malicious network activity",
            "description": "Phase 17 controlled Firewall response fixture",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "MANUAL",
            "owner": "soc-lead",
            "assignee": "analyst-1",
            "queue": "tier-2",
            "classification": "network-attack",
            "risk": "HIGH",
            "attributes": {"correlation_key": f"phase17:{uuid4()}"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    return incident.json(), asset.json()


async def _create_plan(
    client: AsyncClient,
    *,
    rollback_action: str = "DISABLE",
) -> dict[str, object]:
    incident, asset = await _scope(client)
    response = await client.post(
        "/response/plans",
        json={
            "incident_id": incident["id"],
            "asset_ids": [asset["id"]],
            "target_capability": "response.firewall",
            "plugin_name": "firewall-response",
            "requested_by": "firewall-requester@example.test",
            "reason": "Block a confirmed source in the synthetic Firewall provider",
            "risk_level": "HIGH",
            "parameters": {"rule": _rule_payload(str(asset["id"]))},
            "rollback_parameters": {"action": rollback_action},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_firewall_api_execution_verification_evidence_audit_and_rollback(
    client: AsyncClient,
) -> None:
    plan = await _create_plan(client)
    assert plan["approval_state"] == "PENDING_APPROVAL"
    assert plan["execution_state"] == "BLOCKED"
    assert plan["plan"]["plugin_name"] == "firewall-response"

    approved = await client.post(
        f"/response/plans/{plan['id']}/approve",
        json={"approver": "firewall-approver@example.test", "comment": "Scope reviewed"},
    )
    assert approved.status_code == 200, approved.text
    executed = await client.post(
        f"/response/plans/{plan['id']}/execute",
        json={"actor": "firewall-operator@example.test"},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["execution_state"] == "VERIFIED"
    evidence = body["evidence"][0]
    assert evidence["evidence_type"] == "FIREWALL_RULE_CHANGE"
    assert evidence["metadata"]["operation"] == "APPLY"
    assert evidence["metadata"]["network_access"] is False
    assert evidence["metadata"]["production_access"] is False
    assert "rollback_token" not in executed.text

    rolled_back = await client.post(
        f"/response/plans/{plan['id']}/rollback",
        json={"actor": "firewall-rollback@example.test", "reason": "Rollback test"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    rollback = rolled_back.json()
    assert rollback["approval_state"] == "ROLLED_BACK"
    assert rollback["rollback_state"] == "VERIFIED"
    assert {item["metadata"]["operation"] for item in rollback["evidence"]} == {
        "APPLY",
        "DISABLE",
    }

    incident_after = (await client.get(f"/incidents/{plan['incident_id']}")).json()
    asset_after = (await client.get(f"/assets/{plan['asset_ids'][0]}")).json()
    assert incident_after["status"] == "NEW"
    assert incident_after["classification"] == "network-attack"
    assert asset_after["properties"] == {"firewall_scope": "synthetic"}
    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "ResponsePlanCreated",
            "ResponsePlanApproved",
            "ResponseExecutionCompleted",
            "ResponseRollbackCompleted",
        } <= actions
        evidence_rows = list(await session.scalars(select(ResponseEvidence)))
        assert {item.metadata_["operation"] for item in evidence_rows} == {
            "APPLY",
            "DISABLE",
        }


async def test_adapter_supports_remove_disable_restore_and_state_readback() -> None:
    asset_id = str(uuid4())
    provider = MockFirewallProvider()
    adapter = FirewallAdapter(provider, FirewallPolicyProvider())
    original = FirewallRule.model_validate(_rule_payload(asset_id))
    await adapter.apply(original, approval_required=True)
    assert await adapter.verify_applied(original)

    for action, status in (
        (FirewallRollbackAction.DISABLE, FirewallRuleStatus.DISABLED),
        (FirewallRollbackAction.REMOVE, FirewallRuleStatus.REMOVED),
    ):
        change = await adapter.rollback(
            rule_id=original.id,
            action=action,
            original_rule=None,
        )
        assert change.rule.status is status
        assert await adapter.verify_rollback(
            rule_id=original.id,
            action=action,
            original_rule=None,
        )

    restored = await adapter.rollback(
        rule_id=original.id,
        action=FirewallRollbackAction.RESTORE,
        original_rule=original,
    )
    assert restored.rule == original
    assert await adapter.verify_rollback(
        rule_id=original.id,
        action=FirewallRollbackAction.RESTORE,
        original_rule=original,
    )
    assert provider.network_access is False
    assert provider.production_access is False


def test_firewall_rule_checksum_normalization_and_strict_schema() -> None:
    asset_id = str(uuid4())
    rule = FirewallRule.model_validate(_rule_payload(asset_id))
    assert rule.checksum == rule.calculate_checksum()
    assert rule.source == "203.0.113.9/32"
    assert rule.destination_ports == (8080,)
    with pytest.raises(ValidationError, match="checksum"):
        FirewallRule.model_validate({**rule.model_dump(mode="json"), "checksum": "0" * 64})
    with pytest.raises(ValueError, match="not-a-network"):
        FirewallRule.create(
            id="bad-cidr",
            name="bad",
            action=FirewallAction.BLOCK,
            direction=FirewallDirection.INGRESS,
            source="not-a-network",
            destination="198.51.100.20/32",
            protocol=FirewallProtocol.TCP,
            priority=1,
            version="1",
            impact_scope=(asset_id,),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"source": "0.0.0.0/0"}, "any network"),
        ({"source": "1.0.0.0/4"}, "too broad"),
        ({"direction": FirewallDirection.EGRESS, "chain": "INPUT"}, "chain"),
        ({"protocol": FirewallProtocol.ICMP, "destination_ports": (80,)}, "ICMP"),
        ({"protocol": FirewallProtocol.ANY, "destination_ports": (80,)}, "ANY"),
        ({"impact_scope": ("*",)}, "impact scope"),
    ],
)
def test_firewall_rule_rejects_unsafe_structure(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _rule_payload(str(uuid4()), **overrides)


def test_firewall_policy_prevents_lockout_and_limits_blast_radius() -> None:
    asset_id = str(uuid4())
    policy = FirewallPolicyProvider()
    safe = FirewallRule.model_validate(_rule_payload(asset_id))
    policy.validate_rule(safe, approval_required=True)
    with pytest.raises(ResponsePolicyViolation, match="approval"):
        policy.validate_rule(safe, approval_required=False)
    with pytest.raises(ResponsePolicyViolation, match="management or control-plane"):
        policy.validate_rule(
            FirewallRule.model_validate(_rule_payload(asset_id, source="10.255.1.8/32")),
            approval_required=True,
        )
    with pytest.raises(ResponsePolicyViolation, match="management port"):
        policy.validate_rule(
            FirewallRule.model_validate(_rule_payload(asset_id, destination_ports=(22,))),
            approval_required=True,
        )
    with pytest.raises(ResponsePolicyViolation, match="any-protocol"):
        policy.validate_rule(
            FirewallRule.model_validate(
                _rule_payload(asset_id, protocol=FirewallProtocol.ANY, destination_ports=())
            ),
            approval_required=True,
        )
    with pytest.raises(ValueError, match="mock-only"):
        FirewallPolicy(mock_only=False)


async def test_plugin_rejects_scope_expansion_owned_rule_and_bad_token() -> None:
    asset_id = uuid4()
    provider = MockFirewallProvider()
    plugin = FirewallResponsePlugin(FirewallAdapter(provider, FirewallPolicyProvider()))
    registry = ResponseRegistry()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.firewall"],
        denied_capabilities=[],
        approval_required_capabilities=["response.firewall"],
    )

    def plan(rule: dict[str, object]) -> tuple[object, object]:
        return ResponsePlanner(registry, ResponsePolicyEngine()).plan(
            response_plan_id=uuid4(),
            incident_id=uuid4(),
            asset_ids=[asset_id],
            asset_types={AssetType.IP},
            incident_type="network-attack",
            incident_severity=FindingSeverity.HIGH,
            capability="response.firewall",
            risk_level=RiskLevel.HIGH,
            requested_at=datetime.now(UTC),
            trace_id="phase17-runtime",
            actor="operator",
            parameters={"rule": rule},
            rollback_parameters={"action": "REMOVE"},
            policy=policy,
            plugin_name=plugin.name,
        )

    expanded, expanded_context = plan(_rule_payload(str(uuid4())))
    with pytest.raises(ResponsePolicyViolation, match="impact scope"):
        await ResponseRuntime(registry).execute(expanded, expanded_context, policy)

    owned, owned_context = plan(_rule_payload(str(asset_id), id="provider-managed-rule"))
    with pytest.raises(ResponsePolicyViolation, match="Provider-owned"):
        await ResponseRuntime(registry).execute(owned, owned_context, policy)

    safe, safe_context = plan(_rule_payload(str(asset_id), id="safe-rule"))
    result = await ResponseRuntime(registry).execute(safe, safe_context, policy)
    assert result.verification.verified
    invalid = replace(safe_context, rollback_token="firewall-rb:invalid")
    await plugin.initialize(invalid)
    with pytest.raises(ResponsePolicyViolation, match="rollback token"):
        await plugin.rollback(safe, invalid)
    await plugin.shutdown()


async def test_firewall_fail_closed_edge_matrix() -> None:
    asset_id = str(uuid4())
    rule = FirewallRule.model_validate(_rule_payload(asset_id))
    adapter = FirewallAdapter(MockFirewallProvider(), FirewallPolicyProvider())
    with pytest.raises(ResponsePolicyViolation, match="mapping"):
        adapter.parse_rule({})
    with pytest.raises(ResponsePolicyViolation, match="invalid"):
        adapter.parse_rule({"rule": {**_rule_payload(asset_id), "checksum": "0" * 64}})
    with pytest.raises(ResponsePolicyViolation, match="string"):
        adapter.parse_rollback_action({"action": 1})
    with pytest.raises(ResponsePolicyViolation, match="Unsupported"):
        adapter.parse_rollback_action({"action": "PURGE"})
    assert adapter.parse_rollback_action({}) is FirewallRollbackAction.DISABLE
    assert not await adapter.verify_rollback(
        rule_id="missing",
        action=FirewallRollbackAction.DISABLE,
        original_rule=None,
    )

    disabled = FirewallPolicyProvider(FirewallPolicy(enabled=False))
    with pytest.raises(ResponsePolicyViolation, match="disabled"):
        disabled.validate_rule(rule, approval_required=True)
    limited = FirewallPolicyProvider(FirewallPolicy(maximum_priority=100))
    with pytest.raises(ResponsePolicyViolation, match="priority"):
        limited.validate_rule(rule, approval_required=True)
    restricted = FirewallPolicyProvider(
        FirewallPolicy(allowed_rollback_actions=frozenset({FirewallRollbackAction.DISABLE}))
    )
    with pytest.raises(ResponsePolicyViolation, match="rollback action"):
        restricted.validate_rollback(FirewallRollbackAction.REMOVE)

    provider = MockFirewallProvider()
    for action, message in (
        (FirewallRollbackAction.REMOVE, "removal"),
        (FirewallRollbackAction.DISABLE, "disable"),
    ):
        with pytest.raises(ResponseExecutionError, match=message):
            await provider.rollback(rule_id="missing", action=action, original_rule=None)
    with pytest.raises(ResponseExecutionError, match="original"):
        await provider.rollback(
            rule_id="missing",
            action=FirewallRollbackAction.RESTORE,
            original_rule=None,
        )
    with pytest.raises(ResponsePolicyViolation, match="enabled"):
        await provider.apply(rule.model_copy(update={"status": FirewallRuleStatus.DISABLED}))
    assert await provider.snapshot() == {}


async def test_mock_provider_rejects_enabled_semantic_replacement() -> None:
    asset_id = str(uuid4())
    provider = MockFirewallProvider()
    first = FirewallRule.model_validate(_rule_payload(asset_id))
    changed = FirewallRule.model_validate(
        _rule_payload(asset_id, source="203.0.113.10/32", version="1.0.1")
    )
    await provider.apply(first)
    with pytest.raises(ResponsePolicyViolation, match="cannot be replaced"):
        await provider.apply(changed)
    assert await provider.get(first.id) == first


def test_firewall_policy_and_contract_fail_closed_coverage_matrix() -> None:
    asset_id = str(uuid4())
    rule = FirewallRule.model_validate(_rule_payload(asset_id))

    with pytest.raises(ValueError, match="allowlists"):
        FirewallPolicy(allowed_actions=frozenset())
    with pytest.raises(ValueError, match="network sets"):
        FirewallPolicy(management_networks=())
    with pytest.raises(ValueError, match="invalid protected network"):
        FirewallPolicy(management_networks=("invalid-network",))
    with pytest.raises(ValueError, match="cannot be disabled"):
        FirewallPolicy(protected_management_ports=frozenset())

    provider = FirewallPolicyProvider()
    assert provider.policy.mock_only is True
    rejection_matrix = (
        (
            FirewallPolicyProvider(FirewallPolicy(allowed_actions=frozenset({FirewallAction.LOG}))),
            rule,
            "action",
        ),
        (
            FirewallPolicyProvider(
                FirewallPolicy(allowed_directions=frozenset({FirewallDirection.EGRESS}))
            ),
            rule,
            "direction",
        ),
        (
            FirewallPolicyProvider(
                FirewallPolicy(allowed_protocols=frozenset({FirewallProtocol.UDP}))
            ),
            rule,
            "protocol",
        ),
        (
            FirewallPolicyProvider(FirewallPolicy(allowed_tables=frozenset({"safe"}))),
            rule,
            "table",
        ),
        (
            FirewallPolicyProvider(FirewallPolicy(allowed_chains=frozenset({"SAFE"}))),
            rule,
            "chain",
        ),
        (
            FirewallPolicyProvider(FirewallPolicy(maximum_ports_per_rule=1)),
            FirewallRule.model_validate(_rule_payload(asset_id, destination_ports=(8080, 8081))),
            "port scope",
        ),
    )
    for policy_provider, candidate, message in rejection_matrix:
        with pytest.raises(ResponsePolicyViolation, match=message):
            policy_provider.validate_rule(candidate, approval_required=True)

    raw = rule.model_dump(mode="json")
    with pytest.raises(ValidationError, match="valid CIDR"):
        FirewallRule.model_validate({**raw, "source": "invalid-network"})
    with pytest.raises(ValidationError, match="default route"):
        FirewallRule.model_validate({**raw, "source": "0.0.0.1/0"})
    with pytest.raises(ValidationError, match="ports"):
        FirewallRule.model_validate({**raw, "destination_ports": [0]})
    with pytest.raises(ValidationError, match="filter table"):
        FirewallRule.model_validate({**raw, "table": "nat"})
