"""Phase 16 WAF Response Plugin safety and lifecycle acceptance tests."""

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
from app.plugins.waf import WAFResponsePlugin
from app.response import ResponsePlanner, ResponsePolicyEngine, ResponseRegistry, ResponseRuntime
from app.schemas.response import ResponsePolicy
from app.tools.waf import (
    MockWAFProvider,
    WAFAdapter,
    WAFPolicy,
    WAFPolicyProvider,
    WAFRollbackAction,
    WAFRule,
    WAFRuleAction,
    WAFRuleStatus,
)
from tests.conftest import TestSessionFactory


def _rule_payload(**overrides: object) -> dict[str, object]:
    rule = WAFRule.create(
        id="cap-rule-001",
        name="Block confirmed malicious client",
        action=WAFRuleAction.BLOCK,
        condition="client_ip:203.0.113.9",
        priority=500,
        version="1.0.0",
        source="cap",
    )
    payload: dict[str, object] = rule.model_dump(mode="json")
    if overrides:
        payload = WAFRule.create(
            id=str(overrides.get("id", payload["id"])),
            name=str(overrides.get("name", payload["name"])),
            action=WAFRuleAction(str(overrides.get("action", payload["action"]))),
            condition=str(overrides.get("condition", payload["condition"])),
            priority=int(overrides.get("priority", payload["priority"])),
            version=str(overrides.get("version", payload["version"])),
            source=str(overrides.get("source", payload["source"])),
        ).model_dump(mode="json")
    return payload


async def _scope(client: AsyncClient) -> tuple[dict[str, object], dict[str, object]]:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "WEBSITE",
            "name": "Phase 16 protected service",
            "value": f"https://phase16-{uuid4()}.example.test",
            "criticality": "HIGH",
            "properties": {"waf_scope": "synthetic"},
        },
    )
    assert asset.status_code == 201, asset.text
    incident = await client.post(
        "/incidents",
        json={
            "title": "Confirmed malicious client activity",
            "description": "Phase 16 controlled WAF response fixture",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "MANUAL",
            "owner": "soc-lead",
            "assignee": "analyst-1",
            "queue": "tier-2",
            "classification": "web-attack",
            "risk": "HIGH",
            "attributes": {"correlation_key": f"phase16:{uuid4()}"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    return incident.json(), asset.json()


async def _create_waf_plan(
    client: AsyncClient,
    *,
    rollback_action: str = "DISABLE",
    rule: dict[str, object] | None = None,
) -> dict[str, object]:
    incident, asset = await _scope(client)
    response = await client.post(
        "/response/plans",
        json={
            "incident_id": incident["id"],
            "asset_ids": [asset["id"]],
            "target_capability": "response.waf",
            "plugin_name": "waf-response",
            "requested_by": "waf-requester@example.test",
            "reason": "Block confirmed malicious source in synthetic WAF provider",
            "risk_level": "HIGH",
            "parameters": {"rule": rule or _rule_payload()},
            "rollback_parameters": {"action": rollback_action},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_waf_api_approval_execution_evidence_audit_and_disable_rollback(
    client: AsyncClient,
) -> None:
    plan = await _create_waf_plan(client)
    assert plan["approval_state"] == "PENDING_APPROVAL"
    assert plan["execution_state"] == "BLOCKED"
    assert plan["plan"]["plugin_name"] == "waf-response"
    assert plan["plan"]["target_capability"] == "response.waf"

    approved = await client.post(
        f"/response/plans/{plan['id']}/approve",
        json={"approver": "waf-approver@example.test", "comment": "Scoped rule reviewed"},
    )
    assert approved.status_code == 200, approved.text

    executed = await client.post(
        f"/response/plans/{plan['id']}/execute",
        json={"actor": "waf-operator@example.test"},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["execution_state"] == "VERIFIED"
    assert body["approval_state"] == "EXECUTED"
    evidence = body["evidence"][0]
    assert evidence["evidence_type"] == "WAF_RULE_CHANGE"
    assert evidence["metadata"]["operation"] == "APPLY"
    assert evidence["metadata"]["network_access"] is False
    assert evidence["metadata"]["production_access"] is False
    assert evidence["metadata"]["rule"]["checksum"] == _rule_payload()["checksum"]
    assert "rollback_token" not in executed.text

    rolled_back = await client.post(
        f"/response/plans/{plan['id']}/rollback",
        json={"actor": "waf-rollback@example.test", "reason": "Controlled rollback validation"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    rollback = rolled_back.json()
    assert rollback["approval_state"] == "ROLLED_BACK"
    assert rollback["rollback_state"] == "VERIFIED"
    assert {item["metadata"]["operation"] for item in rollback["evidence"]} == {"APPLY", "DISABLE"}

    incident_after = (await client.get(f"/incidents/{plan['incident_id']}")).json()
    asset_after = (await client.get(f"/assets/{plan['asset_ids'][0]}")).json()
    assert incident_after["status"] == "NEW"
    assert incident_after["classification"] == "web-attack"
    assert asset_after["properties"] == {"waf_scope": "synthetic"}
    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "ResponsePlanCreated",
            "ResponsePlanApproved",
            "ResponseExecutionStarted",
            "ResponseExecutionCompleted",
            "ResponseRollbackCompleted",
        } <= actions
        evidence_rows = list(await session.scalars(select(ResponseEvidence)))
        assert len(evidence_rows) == 2
        assert {item.metadata_["operation"] for item in evidence_rows} == {"APPLY", "DISABLE"}


async def test_adapter_supports_remove_disable_restore_and_verifies_each_state() -> None:
    provider = MockWAFProvider()
    adapter = WAFAdapter(provider, WAFPolicyProvider())
    original = WAFRule.model_validate(_rule_payload())
    await adapter.apply(original, approval_required=True)

    disabled = await adapter.rollback(
        rule_id=original.id,
        action=WAFRollbackAction.DISABLE,
        original_rule=None,
    )
    assert disabled.rule.status is WAFRuleStatus.DISABLED
    assert await adapter.verify_rollback(
        rule_id=original.id,
        action=WAFRollbackAction.DISABLE,
        original_rule=None,
    )

    restored = await adapter.rollback(
        rule_id=original.id,
        action=WAFRollbackAction.RESTORE,
        original_rule=original,
    )
    assert restored.rule == original
    assert await adapter.verify_rollback(
        rule_id=original.id,
        action=WAFRollbackAction.RESTORE,
        original_rule=original,
    )

    removed = await adapter.rollback(
        rule_id=original.id,
        action=WAFRollbackAction.REMOVE,
        original_rule=None,
    )
    assert removed.rule.status is WAFRuleStatus.REMOVED
    assert await adapter.verify_rollback(
        rule_id=original.id,
        action=WAFRollbackAction.REMOVE,
        original_rule=None,
    )
    assert provider.network_access is False
    assert provider.production_access is False


def test_waf_rule_checksum_canonicalization_and_strict_schema() -> None:
    rule = WAFRule.create(
        id="rule-1",
        name="Log suspicious user agent",
        action=WAFRuleAction.LOG,
        condition="header:User-Agent=Scanner",
        priority=100,
        version="2.0.0",
        source="assessment",
    )
    assert rule.checksum == rule.calculate_checksum()
    assert WAFRule.model_validate(rule.model_dump(mode="json")) == rule
    with pytest.raises(ValidationError, match="checksum"):
        WAFRule.model_validate({**rule.model_dump(mode="json"), "checksum": "0" * 64})
    with pytest.raises(ValidationError, match="unsafe"):
        WAFRule.create(
            id="rule-2",
            name="invalid",
            action=WAFRuleAction.BLOCK,
            condition="client_ip:203.0.113.9; drop",
            priority=1,
            version="1",
            source="cap",
        )


def test_waf_policy_fail_closed_rejection_matrix() -> None:
    rule = WAFRule.model_validate(_rule_payload())
    provider = WAFPolicyProvider()
    provider.validate_rule(rule, approval_required=True)
    with pytest.raises(ResponsePolicyViolation, match="governed approval"):
        provider.validate_rule(rule, approval_required=False)
    with pytest.raises(ResponsePolicyViolation, match="action"):
        provider.validate_rule(
            rule.model_copy(update={"action": WAFRuleAction.ALLOW}), approval_required=True
        )
    with pytest.raises(ResponsePolicyViolation, match="source"):
        provider.validate_rule(
            rule.model_copy(update={"source": "operator"}), approval_required=True
        )
    with pytest.raises(ResponsePolicyViolation, match="condition field"):
        provider.validate_rule(
            rule.model_copy(update={"condition": "host:example.test"}), approval_required=True
        )
    with pytest.raises(ValueError, match="mock-only"):
        WAFPolicy(mock_only=False)
    with pytest.raises(ValueError, match="Broad allow"):
        WAFPolicy(allowed_actions=frozenset({WAFRuleAction.ALLOW}))


async def test_plugin_runtime_rejects_provider_owned_rule_and_invalid_rollback_token() -> None:
    provider = MockWAFProvider()
    plugin = WAFResponsePlugin(WAFAdapter(provider, WAFPolicyProvider()))
    registry = ResponseRegistry()
    registry.register(plugin)
    policy = ResponsePolicy(
        allowed_capabilities=["response.waf"],
        denied_capabilities=[],
        approval_required_capabilities=["response.waf"],
    )
    specification, context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=[uuid4()],
        asset_types={AssetType.WEBSITE},
        incident_type="web-attack",
        incident_severity=FindingSeverity.HIGH,
        capability="response.waf",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase16-rejection",
        actor="operator",
        parameters={"rule": _rule_payload(id="system-owned-rule")},
        rollback_parameters={"action": "REMOVE"},
        policy=policy,
        plugin_name=plugin.name,
    )
    with pytest.raises(ResponsePolicyViolation, match="Provider-owned"):
        await ResponseRuntime(registry).execute(specification, context, policy)
    assert await provider.get("system-owned-rule") is None

    safe_specification, safe_context = ResponsePlanner(registry, ResponsePolicyEngine()).plan(
        response_plan_id=uuid4(),
        incident_id=uuid4(),
        asset_ids=[uuid4()],
        asset_types={AssetType.WEBSITE},
        incident_type="web-attack",
        incident_severity=FindingSeverity.HIGH,
        capability="response.waf",
        risk_level=RiskLevel.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase16-token",
        actor="operator",
        parameters={"rule": _rule_payload(id="safe-rule")},
        rollback_parameters={"action": "REMOVE"},
        policy=policy,
        plugin_name=plugin.name,
    )
    result = await ResponseRuntime(registry).execute(safe_specification, safe_context, policy)
    invalid_rollback_context = replace(safe_context, rollback_token="waf-rb:invalid")
    with pytest.raises(ResponsePolicyViolation, match="rollback token"):
        await plugin.initialize(invalid_rollback_context)
        await plugin.rollback(safe_specification, invalid_rollback_context)
    await plugin.shutdown()
    assert result.verification.verified


async def test_mock_provider_rejects_enabled_rule_replacement_without_rollback() -> None:
    provider = MockWAFProvider()
    first = WAFRule.model_validate(_rule_payload())
    changed = WAFRule.create(
        id=first.id,
        name="Different semantics",
        action=WAFRuleAction.BLOCK,
        condition="client_ip:203.0.113.10",
        priority=first.priority,
        version="1.0.1",
        source="cap",
    )
    await provider.apply(first)
    with pytest.raises(ResponsePolicyViolation, match="cannot be replaced"):
        await provider.apply(changed)
    assert (await provider.get(first.id)) == first


async def test_waf_fail_closed_edge_matrix() -> None:
    rule = WAFRule.model_validate(_rule_payload())
    adapter = WAFAdapter(MockWAFProvider(), WAFPolicyProvider())
    with pytest.raises(ResponsePolicyViolation, match="mapping"):
        adapter.parse_rule({})
    with pytest.raises(ResponsePolicyViolation, match="invalid"):
        adapter.parse_rule({"rule": {**_rule_payload(), "checksum": "0" * 64}})
    with pytest.raises(ResponsePolicyViolation, match="string"):
        adapter.parse_rollback_action({"action": 1})
    with pytest.raises(ResponsePolicyViolation, match="Unsupported"):
        adapter.parse_rollback_action({"action": "PURGE"})
    assert adapter.parse_rollback_action({}) is WAFRollbackAction.DISABLE
    assert not await adapter.verify_rollback(
        rule_id="missing",
        action=WAFRollbackAction.DISABLE,
        original_rule=None,
    )

    disabled = WAFPolicyProvider(WAFPolicy(enabled=False))
    with pytest.raises(ResponsePolicyViolation, match="disabled"):
        disabled.validate_rule(rule, approval_required=True)
    limited = WAFPolicyProvider(WAFPolicy(maximum_priority=100))
    with pytest.raises(ResponsePolicyViolation, match="priority"):
        limited.validate_rule(rule, approval_required=True)
    malformed = rule.model_copy(update={"condition": "client_ip:"})
    with pytest.raises(ResponsePolicyViolation, match="field:value"):
        WAFPolicyProvider().validate_rule(malformed, approval_required=True)
    restricted = WAFPolicyProvider(
        WAFPolicy(allowed_rollback_actions=frozenset({WAFRollbackAction.DISABLE}))
    )
    with pytest.raises(ResponsePolicyViolation, match="rollback action"):
        restricted.validate_rollback(WAFRollbackAction.REMOVE)

    provider = MockWAFProvider()
    with pytest.raises(ResponseExecutionError, match="removal"):
        await provider.rollback(
            rule_id="missing",
            action=WAFRollbackAction.REMOVE,
            original_rule=None,
        )
    with pytest.raises(ResponseExecutionError, match="disable"):
        await provider.rollback(
            rule_id="missing",
            action=WAFRollbackAction.DISABLE,
            original_rule=None,
        )
    with pytest.raises(ResponseExecutionError, match="original"):
        await provider.rollback(
            rule_id="missing",
            action=WAFRollbackAction.RESTORE,
            original_rule=None,
        )
    with pytest.raises(ResponsePolicyViolation, match="enabled"):
        await provider.apply(rule.model_copy(update={"status": WAFRuleStatus.DISABLED}))
    assert await provider.snapshot() == {}
