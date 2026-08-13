"""Phase 21 Web Console backend, RBAC, aggregate API, and observability tests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.auth.rbac import (
    ALL_PERMISSIONS,
    ROLES,
    get_current_user,
    list_users,
    require_permission,
)
from app.middleware.authorization import _permission_for
from app.observability import MetricsRegistry, start_server_span
from app.services.productization import ProductizationService


async def test_public_health_readiness_metrics_and_trace_headers(client: AsyncClient) -> None:
    public = AsyncClient(transport=client._transport, base_url="http://test")  # type: ignore[attr-defined]
    health = await public.get("/health")
    ready = await public.get("/ready")
    metrics = await public.get("/metrics")
    await public.aclose()

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.headers["X-Trace-ID"]
    assert health.headers["traceparent"].startswith("00-")
    assert metrics.status_code == 200
    assert "cap_http_requests_total" in metrics.text
    assert "cap_http_request_duration_seconds" in metrics.text


async def test_private_routes_fail_closed_without_trusted_identity(client: AsyncClient) -> None:
    public = AsyncClient(transport=client._transport, base_url="http://test")  # type: ignore[attr-defined]
    missing = await public.get("/dashboard")
    forged = await public.get("/dashboard", headers={"X-CAP-User": "administrator"})
    unknown = await public.get(
        "/dashboard",
        headers={"X-CAP-User": "unknown", "X-CAP-Proxy-Secret": "change-me-proxy-secret"},
    )
    await public.aclose()

    assert missing.status_code == 401
    assert forged.status_code == 401
    assert unknown.status_code == 401


async def test_rbac_catalog_contains_required_roles_and_resources(client: AsyncClient) -> None:
    roles = await client.get("/roles")
    permissions = await client.get("/permissions")
    users = await client.get("/users")

    assert roles.status_code == permissions.status_code == users.status_code == 200
    assert {item["name"] for item in roles.json()} == {
        "Administrator",
        "SOC Analyst",
        "Incident Responder",
        "Auditor",
        "Read Only",
    }
    resources = {item["resource"] for item in permissions.json()}
    assert {
        "asset",
        "knowledge",
        "evidence",
        "incident",
        "response",
        "playbook",
        "worker",
        "plugin",
        "approval",
    } <= resources
    assert len(users.json()) == 5


async def test_read_only_user_cannot_approve_or_query_audit(client: AsyncClient) -> None:
    headers = {"X-CAP-User": "read-only", "X-CAP-Proxy-Secret": "change-me-proxy-secret"}
    dashboard = await client.get("/dashboard", headers=headers)
    audit = await client.get("/audit", headers=headers)
    approve = await client.post(
        "/response/plans/00000000-0000-0000-0000-000000000001/approve",
        json={"approver": "read-only", "comment": "not allowed"},
        headers=headers,
    )

    assert dashboard.status_code == 200
    assert audit.status_code == 403
    assert approve.status_code == 403


async def test_productization_aggregate_endpoints_are_read_only(client: AsyncClient) -> None:
    dashboard = await client.get("/dashboard")
    audit = await client.get("/audit")
    plugins = await client.get("/plugins")
    approvals = await client.get("/approvals")
    settings = await client.get("/settings")

    assert dashboard.status_code == 200
    assert dashboard.json()["counts"] == {
        "assets": 0,
        "incidents": 0,
        "security_events": 0,
        "findings": 0,
    }
    assert audit.status_code == 200
    assert audit.json()["total"] == 0
    assert plugins.status_code == 200
    assert approvals.status_code == 200
    assert settings.status_code == 200
    assert "database_url" not in settings.json()
    assert "redis_url" not in settings.json()
    assert "rbac_trusted_proxy_secret" not in settings.json()


async def test_auditor_can_query_governance_but_cannot_manage_platform(client: AsyncClient) -> None:
    headers = {"X-CAP-User": "auditor", "X-CAP-Proxy-Secret": "change-me-proxy-secret"}
    assert (await client.get("/audit", headers=headers)).status_code == 200
    assert (await client.get("/settings", headers=headers)).status_code == 200
    assert (await client.get("/roles", headers=headers)).status_code == 200
    assert (
        await client.post(
            "/playbooks",
            json={"yaml": "dsl_version: v1"},
            headers=headers,
        )
    ).status_code == 403


def test_rbac_catalog_is_immutable_and_administrator_has_all_permissions() -> None:
    assert set(ROLES["Administrator"].permissions) == set(ALL_PERMISSIONS)
    assert len(list_users()) == 5
    assert all(user.permissions for user in list_users())


def test_trace_context_accepts_valid_parent_and_rejects_invalid_parent() -> None:
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    continued = start_server_span(parent)
    fresh = start_server_span("not-a-traceparent")

    assert continued.trace_id == "0123456789abcdef0123456789abcdef"
    assert continued.parent_span_id == "0123456789abcdef"
    assert continued.sampled is True
    assert fresh.trace_id != continued.trace_id
    assert fresh.parent_span_id is None


def test_prometheus_registry_uses_low_cardinality_route_labels() -> None:
    registry = MetricsRegistry()
    registry.begin()
    registry.observe("GET", "/incidents/{incident_id}", 200, 0.02)
    registry.set_business_gauges({"cap_queue_depth": 3.0})
    output = registry.render()

    assert 'route="/incidents/{incident_id}"' in output
    assert 'status_class="2xx"' in output
    assert "incident_id" not in output.replace("/incidents/{incident_id}", "")
    assert "cap_queue_depth 3.0" in output
    with pytest.raises(ValueError, match="Unsupported business metric"):
        registry.set_business_gauges({"cap_unbounded_label": 1.0})


def test_rbac_dependency_authentication_and_authorization_edges() -> None:
    settings = SimpleNamespace(
        rbac_trusted_proxy_secret="trusted",
        rbac_trusted_proxy_header="X-Proxy",
        rbac_identity_header="X-User",
    )

    def request(headers: dict[str, str]) -> SimpleNamespace:
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            headers=headers,
            state=SimpleNamespace(),
        )

    with pytest.raises(HTTPException) as missing:
        get_current_user(request({}))
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as unknown:
        get_current_user(request({"X-Proxy": "trusted", "X-User": "missing"}))
    assert unknown.value.status_code == 401

    principal = get_current_user(request({"X-Proxy": "trusted", "X-User": "read-only"}))
    assert require_permission("dashboard.read")(principal) is principal
    with pytest.raises(HTTPException) as denied:
        require_permission("audit.read")(principal)
    assert denied.value.status_code == 403
    with pytest.raises(ValueError, match="Unknown permission"):
        require_permission("unknown.execute")


@pytest.mark.parametrize(
    ("method", "path", "permission"),
    [
        ("POST", "/assets", "asset.write"),
        ("POST", "/knowledge/import", "knowledge.write"),
        ("POST", "/assessment/run", "assessment.execute"),
        ("POST", "/detection/run", "detection.execute"),
        ("POST", "/incidents", "incident.write"),
        ("GET", "/response/plans", "response.read"),
        ("POST", "/response/plans/1/approve", "approval.decide"),
        ("POST", "/response/plans/1/execute", "response.execute"),
        ("POST", "/response/plans/1/rollback", "response.rollback"),
        ("POST", "/response/plans", "response.plan"),
        ("GET", "/playbooks", "playbook.read"),
        ("POST", "/playbooks/1/run", "playbook.execute"),
        ("POST", "/playbooks", "playbook.write"),
        ("GET", "/workers", "worker.read"),
        ("GET", "/sandbox/executions", "sandbox.read"),
        ("POST", "/notifications", "notification.send"),
        ("POST", "/tickets", "ticket.write"),
        ("GET", "/unmapped-control-plane", "platform.manage"),
    ],
)
def test_path_permission_mapping_is_explicit_and_fail_closed(
    method: str, path: str, permission: str
) -> None:
    assert _permission_for(method, path) == permission


async def test_dashboard_non_empty_projection_covers_success_failure_and_capacity() -> None:
    counts = iter([1, 2, 3, 4, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 2, 1, 2, 1, 1])
    session = SimpleNamespace(
        scalars=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(status="ONLINE", max_concurrency=4, active_executions=2)
                ]
            )
        )
    )
    count_mock = AsyncMock(side_effect=lambda *_: next(counts))
    with patch("app.services.productization._count", count_mock):
        result = await ProductizationService(session).dashboard()

    assert result.counts.model_dump() == {
        "assets": 1,
        "incidents": 2,
        "security_events": 3,
        "findings": 4,
    }
    assert result.playbooks.total == 4
    assert result.playbooks.success_rate == 0.5
    assert result.workers.utilization == 0.5
    assert result.plugins.total == 4
    assert result.responses.success_rate == pytest.approx(2 / 3, abs=0.0001)
    assert result.notifications.success_rate == 0.5


async def test_audit_projection_applies_all_filters_and_pagination() -> None:
    now = datetime.now(UTC)
    audit_row = SimpleNamespace(
        id=uuid4(),
        operator="auditor",
        action="response.execute",
        resource="incident/i-1/plugin/p-1/worker/w-1",
        details={"approved": True},
        trace_id="trace-1",
        result={"ok": True},
        error=None,
        timestamp=now,
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: 1)),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [audit_row])),
    )
    result = await ProductizationService(session).audit(
        operator="auditor",
        event_type="response.execute",
        resource="incident/i-1",
        plugin="p-1",
        incident="i-1",
        worker="w-1",
        start=now - timedelta(minutes=1),
        end=now + timedelta(minutes=1),
        page=2,
        page_size=10,
    )

    assert result.total == 1
    assert result.page == 2
    assert result.items[0].trace_id == "trace-1"


async def test_plugin_inventory_projects_relational_and_json_capabilities() -> None:
    relational = SimpleNamespace(
        id=uuid4(),
        name="assessment-plugin",
        version="1.0.0",
        enabled=True,
        capabilities=[SimpleNamespace(capability=SimpleNamespace(name="web.scan"))],
    )
    relational_detection = SimpleNamespace(
        id=uuid4(),
        name="detection-plugin",
        version="1.0.0",
        enabled=True,
        capabilities=[SimpleNamespace(capability=SimpleNamespace(name="network.detect"))],
    )
    response = SimpleNamespace(
        id=uuid4(),
        name="response-plugin",
        version="1.0.0",
        enabled=True,
        health_status="HEALTHY",
        capabilities=["response.block"],
        certified=True,
        sandbox_compatible=True,
    )
    notification = SimpleNamespace(
        id=uuid4(),
        name="notification-plugin",
        version="1.0.0",
        enabled=False,
        health_status="UNKNOWN",
        capabilities=["notification.send"],
        certified=False,
        sandbox_compatible=False,
    )
    session = SimpleNamespace(
        scalars=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: [relational]),
                SimpleNamespace(all=lambda: [relational_detection]),
                SimpleNamespace(all=lambda: [response]),
                SimpleNamespace(all=lambda: [notification]),
            ]
        )
    )
    inventory = await ProductizationService(session).plugins()

    assert [item.domain for item in inventory] == [
        "assessment",
        "detection",
        "response",
        "notification",
    ]
    assert inventory[0].capabilities == ["web.scan"]
    assert inventory[2].certified is True


async def test_approval_center_selects_latest_decision_and_supports_pending() -> None:
    now = datetime.now(UTC)
    older = SimpleNamespace(
        approver="first",
        decision="APPROVED",
        comment="first decision",
        decided_at=now - timedelta(minutes=1),
    )
    latest = SimpleNamespace(
        approver="second",
        decision="APPROVED",
        comment="latest decision",
        decided_at=now,
    )

    def plan(approvals: list[SimpleNamespace]) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            incident_id=uuid4(),
            target_capability="response.edr",
            requested_by="soc-analyst",
            risk_level="HIGH",
            approval_state="APPROVED" if approvals else "PENDING_APPROVAL",
            execution_state="READY" if approvals else "BLOCKED",
            rollback_state="AVAILABLE",
            expires_at=now + timedelta(hours=1),
            approvals=approvals,
        )

    session = SimpleNamespace(
        scalars=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [plan([older, latest]), plan([])])
        )
    )
    result = await ProductizationService(session).approvals()

    assert result[0].approver == "second"
    assert result[0].comment == "latest decision"
    assert result[1].approver is None
