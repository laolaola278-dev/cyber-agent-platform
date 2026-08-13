"""Phase 15 Notification and Ticket Framework acceptance tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.enums import FindingSeverity
from app.database.base import Base
from app.exceptions import (
    NotificationExecutionError,
    NotificationPolicyViolation,
    NotificationValidationError,
)
from app.models import AuditLog
from app.models.notification import (
    NotificationEvidence,
    NotificationExecution,
    NotificationPlan,
    Ticket,
)
from app.notification import (
    FakeNotificationPlugin,
    NotificationPlanner,
    NotificationPolicyEngine,
    NotificationRegistry,
    NotificationRuntime,
    RoutingEngine,
    TemplateDefinition,
    TemplateProvider,
)
from app.notification.policy import NotificationPolicyInput
from app.schemas.notification import (
    EscalationRule,
    NotificationCreate,
    NotificationEvidenceRead,
    NotificationPolicy,
    NotificationResult,
    NotificationRoute,
    NotificationVerification,
    RecipientGroup,
    SilenceRule,
    TemplateFormat,
    TicketPriority,
)
from tests.conftest import TestSessionFactory


def _incident_payload() -> dict[str, object]:
    return {
        "title": "Confirmed notification fixture",
        "description": "Phase 15 governed notification fixture",
        "severity": "HIGH",
        "confidence": "HIGH",
        "source": "MANUAL",
        "owner": "soc-lead",
        "assignee": "analyst-1",
        "queue": "tier-2",
        "classification": "endpoint-compromise",
        "risk": "HIGH",
        "attributes": {"correlation_key": "phase15:notification"},
        "create_case": False,
    }


async def _incident(client: AsyncClient) -> dict[str, object]:
    response = await client.post("/incidents", json=_incident_payload())
    assert response.status_code == 201, response.text
    return response.json()


def _notification_payload(incident_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "incident_id": incident_id,
        "capability": "notification.custom",
        "severity": "HIGH",
        "priority": "HIGH",
        "requested_by": "analyst@example.test",
        "variables": {
            "incident_title": "Confirmed notification fixture",
            "incident_id": incident_id,
            "severity": "HIGH",
        },
    }
    payload.update(overrides)
    return payload


async def test_notification_api_lifecycle_evidence_audit_and_domain_immutability(
    client: AsyncClient,
) -> None:
    incident = await _incident(client)
    created = await client.post("/notifications", json=_notification_payload(str(incident["id"])))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "VERIFIED"
    assert body["recipients"] == ["soc@example.test"]
    assert body["plan"]["steps"] == [
        "initialize",
        "render",
        "validate",
        "send",
        "verify",
        "shutdown",
    ]
    assert body["executions"][0]["verification_status"] == "VERIFIED"
    assert body["evidence"][0]["metadata"]["capability"] == "notification.custom"

    detail = await client.get(f"/notifications/{body['id']}")
    assert detail.status_code == 200
    listed = await client.get(
        "/notifications", params={"incident_id": incident["id"], "status": "VERIFIED"}
    )
    assert listed.status_code == 200 and listed.json()["total"] == 1
    plugins = await client.get("/notification/plugins")
    assert plugins.status_code == 200
    assert plugins.json()[0]["name"] == "synthetic-notification"
    assert plugins.json()[0]["certified"] is True

    incident_after = (await client.get(f"/incidents/{incident['id']}")).json()
    for field in ("status", "severity", "owner", "assignee", "attributes"):
        assert incident_after[field] == incident[field]

    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            "NotificationPlanCreated",
            "NotificationExecutionStarted",
            "NotificationVerified",
        } <= actions
        assert await session.scalar(select(func.count()).select_from(NotificationPlan)) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationEvidence)) == 1


async def test_deduplication_suppresses_second_notification(client: AsyncClient) -> None:
    incident = await _incident(client)
    payload = _notification_payload(str(incident["id"]), deduplication_key="phase15:duplicate")
    first = await client.post("/notifications", json=payload)
    assert first.status_code == 201 and first.json()["status"] == "VERIFIED"
    second = await client.post("/notifications", json=payload)
    assert second.status_code == 201
    assert second.json()["status"] == "SUPPRESSED"
    assert second.json()["executions"] == []
    async with TestSessionFactory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
        assert "NotificationSuppressed" in actions


async def test_ticket_api_unified_model_and_strict_schema(client: AsyncClient) -> None:
    incident = await _incident(client)
    created = await client.post(
        "/tickets",
        json={
            "incident_id": incident["id"],
            "title": "Investigate endpoint compromise",
            "description": "Unified CAP Ticket independent of Jira or ServiceNow.",
            "priority": "HIGH",
            "status": "OPEN",
            "external_reference": "synthetic-ticket-15",
            "labels": ["Endpoint", "phase15", "endpoint"],
            "created_by": "analyst@example.test",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["labels"] == ["endpoint", "phase15"]
    listed = await client.get("/tickets", params={"status": "OPEN"})
    assert listed.status_code == 200 and listed.json()["total"] == 1
    strict = await client.post(
        "/tickets",
        json={
            "title": "unsafe",
            "description": "unsafe",
            "priority": "LOW",
            "created_by": "analyst",
            "direct_jira_transition": True,
        },
    )
    assert strict.status_code == 422
    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(Ticket)) == 1
        assert "TicketCreated" in set(await session.scalars(select(AuditLog.action)))


async def test_policy_allowlist_silence_rate_dedup_hours_and_escalation() -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    policy = NotificationPolicy(
        recipient_allowlist=["soc@example.test", "lead@example.test"],
        recipient_groups=[
            RecipientGroup(name="soc", recipients=["soc@example.test"]),
            RecipientGroup(name="leads", recipients=["lead@example.test"]),
        ],
        routes=[
            NotificationRoute(
                name="custom",
                capability="notification.custom",
                recipient_group="soc",
                template_name="default-text",
            )
        ],
        escalation_rules=[
            {
                "minimum_severity": "HIGH",
                "from_group": "soc",
                "to_group": "leads",
            }
        ],
        rate_limit_count=1,
        deduplication_window_seconds=300,
    )
    context = NotificationPolicyInput(
        incident_id=uuid4(),
        capability="notification.custom",
        severity=FindingSeverity.HIGH,
        priority=TicketPriority.HIGH,
        recipient_group="soc",
        recipients=("soc@example.test",),
        requested_at=now,
    )
    engine = NotificationPolicyEngine()
    decision = engine.decide(policy, context)
    assert decision.escalated and decision.recipient_group == "leads"
    assert decision.recipients == ("lead@example.test",)
    duplicate = engine.decide(policy, replace(context, recent_duplicate_at=now))
    assert duplicate.suppressed and duplicate.suppression_reason == "duplicate notification"
    limited = engine.decide(policy, replace(context, recent_send_count=1))
    assert limited.suppressed and limited.suppression_reason == "rate limit exceeded"
    silenced_policy = policy.model_copy(
        update={
            "silence_rules": [
                SilenceRule(
                    incident_id=context.incident_id,
                    starts_at=now - timedelta(minutes=1),
                    ends_at=now + timedelta(minutes=1),
                    reason="maintenance",
                )
            ]
        }
    )
    assert engine.decide(silenced_policy, context).suppression_reason == "matching silence rule"
    hours = policy.model_copy(
        update={
            "defer_outside_business_hours": True,
            "business_hours_start": 13,
            "business_hours_end": 18,
        }
    )
    assert engine.decide(hours, context).suppression_reason == "outside business hours"
    with pytest.raises(NotificationPolicyViolation, match="allowlist"):
        engine.decide(policy, replace(context, recipients=("attacker@example.test",)))


def test_template_provider_formats_and_code_execution_rejection() -> None:
    provider = TemplateProvider()
    for format_, body in (
        (TemplateFormat.TEXT, "Incident {{incident_id}}"),
        (TemplateFormat.MARKDOWN, "**{{incident_id}}**"),
        (TemplateFormat.HTML, "<strong>{{incident_id}}</strong>"),
        (TemplateFormat.JSON, '{"incident_id":"{{incident_id}}"}'),
    ):
        template = TemplateDefinition(
            name=f"safe-{format_.value.casefold()}",
            format=format_,
            subject="CAP {{incident_id}}",
            body=body,
            variables=frozenset({"incident_id"}),
        )
        provider.register(template)
        rendered = provider.render(template, {"incident_id": "INC-15"})
        assert "INC-15" in rendered.body
    for source in (
        "{{ dangerous() }}",
        "{% for x in values %}",
        "{{ __import__ }}",
        "{{ incident.owner }}",
    ):
        with pytest.raises(NotificationValidationError, match="execute|undeclared"):
            TemplateProvider().register(
                TemplateDefinition(
                    name=f"unsafe-{uuid4()}",
                    format=TemplateFormat.TEXT,
                    subject="unsafe",
                    body=source,
                    variables=frozenset({"dangerous", "values", "incident.owner"}),
                )
            )
    with pytest.raises(NotificationValidationError, match="scalar"):
        provider.render(provider.require("safe-text"), {"incident_id": {"nested": True}})


async def test_registry_context_runtime_and_result_fail_closed() -> None:
    registry = NotificationRegistry()
    plugin = FakeNotificationPlugin()
    registry.register(plugin)
    templates = TemplateProvider()
    templates.register(
        TemplateDefinition(
            name="default-text",
            format=TemplateFormat.TEXT,
            subject="CAP {{incident_id}}",
            body="Incident {{incident_id}} severity {{severity}}",
            variables=frozenset({"incident_id", "severity"}),
        )
    )
    policy = NotificationPolicy(
        recipient_allowlist=["soc@example.test"],
        recipient_groups=[RecipientGroup(name="soc", recipients=["soc@example.test"])],
        routes=[
            NotificationRoute(
                name="custom",
                capability="notification.custom",
                recipient_group="soc",
                template_name="default-text",
            )
        ],
    )
    specification, context, status, reason = NotificationPlanner(
        registry,
        NotificationPolicyEngine(),
        RoutingEngine(),
        templates,
    ).plan(
        notification_plan_id=uuid4(),
        incident_id=uuid4(),
        response_plan_id=None,
        capability="notification.custom",
        severity=FindingSeverity.HIGH,
        priority=TicketPriority.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase15-runtime",
        actor="analyst",
        variables={"incident_id": "INC-15", "severity": "HIGH", "labels": {"a"}},
        deduplication_key="phase15:runtime",
        policy=policy,
        plugin_name=None,
        recipient_group=None,
        template_name=None,
    )
    assert status == "PLANNED" and reason is None
    assert isinstance(context.variables, MappingProxyType)
    assert context.variables["labels"] == frozenset({"a"})
    executable = specification.model_copy(
        update={"variables": {"incident_id": "INC-15", "severity": "HIGH"}}
    )
    executable_context = replace(
        context,
        variables=MappingProxyType({"incident_id": "INC-15", "severity": "HIGH"}),
    )
    result = await NotificationRuntime(registry).execute(executable, executable_context, policy)
    assert result.success and result.verification.verified and plugin._context is None
    assert not any(
        hasattr(plugin, attribute)
        for attribute in (
            "session",
            "database",
            "repository",
            "incident_service",
            "response_service",
            "report_service",
        )
    )

    with pytest.raises(NotificationPolicyViolation, match="permissions"):
        await NotificationRuntime(registry).execute(
            specification,
            replace(context, granted_permissions=frozenset({"notification.send"})),
            policy,
        )
    base = NotificationResult(
        success=True,
        plugin_name=plugin.name,
        plugin_version=plugin.version,
        capability="notification.custom",
        status="ACCEPTED",
        recipients=["soc@example.test"],
        verification=NotificationVerification(verified=True, status="VERIFIED"),
        duration_ms=1,
        message="ok",
    )
    validate = NotificationRuntime._validate_result
    for invalid, message in (
        (base.model_copy(update={"plugin_name": "foreign"}), "identity"),
        (base.model_copy(update={"capability": "notification.email"}), "capability"),
        (base.model_copy(update={"recipients": ["other@example.test"]}), "recipient"),
        (
            base.model_copy(
                update={"verification": NotificationVerification(verified=False, status="FAILED")}
            ),
            "verification",
        ),
    ):
        with pytest.raises(
            (NotificationExecutionError, NotificationPolicyViolation), match=message
        ):
            validate(
                invalid,
                plugin.name,
                plugin.version,
                specification,
                context,
                policy,
            )


async def test_registry_certification_rejection_matrix() -> None:
    class Forbidden(FakeNotificationPlugin):
        name = "forbidden"
        permissions = frozenset(
            {"notification.render", "notification.send", "notification.verify", "database.access"}
        )

    class Unsupported(FakeNotificationPlugin):
        name = "unsupported"
        capabilities = frozenset({"notification.unknown"})

    class Incomplete(FakeNotificationPlugin):
        name = "incomplete"
        permissions = frozenset({"notification.send"})

    class NoVerification(FakeNotificationPlugin):
        name = "no-verification"
        supports_verification = False

    class NoSandbox(FakeNotificationPlugin):
        name = "no-sandbox"
        sandbox_compatible = False

    for candidate, message in (
        (Forbidden(), "forbidden"),
        (Unsupported(), "unsupported"),
        (Incomplete(), "lifecycle"),
        (NoVerification(), "lifecycle"),
        (NoSandbox(), "sandbox"),
    ):
        with pytest.raises(NotificationValidationError, match=message):
            NotificationRegistry().register(candidate)
    registry = NotificationRegistry()
    plugin = FakeNotificationPlugin()
    registry.register(plugin)
    with pytest.raises(NotificationValidationError, match="already registered"):
        registry.register(plugin)
    with pytest.raises(NotificationValidationError, match="not registered"):
        registry.require("missing")
    with pytest.raises(NotificationValidationError, match="No Notification plugin"):
        registry.resolve("notification.custom-missing")


async def test_failure_persisted_strict_request_alias_and_unknown_references(
    client: AsyncClient,
) -> None:
    incident = await _incident(client)
    failed = await client.post(
        "/notifications",
        json=_notification_payload(
            str(incident["id"]),
            variables={
                "incident_title": "fixture",
                "incident_id": str(incident["id"]),
                "severity": "HIGH",
                "force_send_failure": True,
            },
        ),
    )
    assert failed.status_code == 422
    async with TestSessionFactory() as session:
        row = await session.scalar(select(NotificationPlan))
        assert row is not None and row.status == "FAILED"
        execution = await session.scalar(select(NotificationExecution))
        assert execution is not None and execution.status == "FAILED"
        assert "NotificationExecutionFailed" in set(await session.scalars(select(AuditLog.action)))

    unknown = await client.post("/notifications", json=_notification_payload(str(uuid4())))
    assert unknown.status_code == 422
    strict_payload = _notification_payload(str(incident["id"]))
    strict_payload["direct_incident_close"] = True
    strict = await client.post("/notifications", json=strict_payload)
    assert strict.status_code == 422
    with pytest.raises(ValidationError, match="extra"):
        NotificationCreate.model_validate(strict_payload)

    evidence = NotificationEvidence(
        id=uuid4(),
        plan_id=uuid4(),
        evidence_type="NOTIFICATION_RECEIPT",
        sha256="a" * 64,
        reference="synthetic://notification",
        metadata_={"safe": True},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert NotificationEvidenceRead.model_validate(evidence).metadata == {"safe": True}


def test_model_migration_and_protected_domain_boundary() -> None:
    notification_tables = {
        name
        for name in Base.metadata.tables
        if name.startswith("notification_") or name == "tickets"
    }
    assert notification_tables == {
        "notification_evidence",
        "notification_executions",
        "notification_plans",
        "notification_plugins",
        "notification_templates",
        "tickets",
    }
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260801_0015_notification_ticket_framework.py"
    )
    migration = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "20260801_0015"' in migration
    assert 'down_revision: str | None = "20260801_0014"' in migration
    for table in notification_tables:
        assert f'"{table}"' in migration
    for protected in (
        'op.alter_column("incidents"',
        'op.alter_column("response_plans"',
        'op.alter_column("security_events"',
        'op.alter_column("findings"',
    ):
        assert protected not in migration


def test_policy_template_and_route_fail_closed_branches() -> None:
    now = datetime(2026, 8, 2, 3, tzinfo=UTC)
    incident_id = uuid4()
    policy = NotificationPolicy()
    context = NotificationPolicyInput(
        incident_id=incident_id,
        capability="notification.custom",
        severity=FindingSeverity.HIGH,
        priority=TicketPriority.HIGH,
        recipient_group="soc",
        recipients=("soc@example.test",),
        requested_at=now,
    )
    engine = NotificationPolicyEngine()
    for replacement, message in (
        ({"enabled": False}, "disabled"),
        ({"allowed_capabilities": ["notification.email"]}, "capability"),
        ({"allowed_severities": [FindingSeverity.LOW]}, "severity"),
        ({"allowed_priorities": [TicketPriority.LOW]}, "priority"),
    ):
        with pytest.raises(NotificationPolicyViolation, match=message):
            engine.decide(policy.model_copy(update=replacement), context)

    wrong_scope_silence = policy.model_copy(
        update={
            "silence_rules": [
                SilenceRule(
                    incident_id=uuid4(),
                    recipient_group="other",
                    capability="notification.email",
                    starts_at=now - timedelta(minutes=1),
                    ends_at=now + timedelta(minutes=1),
                    reason="unrelated",
                )
            ]
        }
    )
    assert not engine._silenced(wrong_scope_silence, context, "soc", now)
    unknown_escalation = policy.model_copy(
        update={
            "escalation_rules": [
                EscalationRule(
                    minimum_severity=FindingSeverity.HIGH,
                    from_group="soc",
                    to_group="missing",
                )
            ]
        }
    )
    with pytest.raises(NotificationPolicyViolation, match="unknown recipient"):
        engine.decide(unknown_escalation, context)

    with pytest.raises(NotificationPolicyViolation, match="No Notification route"):
        RoutingEngine().route(
            policy,
            capability="notification.email",
            severity=FindingSeverity.HIGH,
            priority=TicketPriority.HIGH,
            requested_group=None,
            requested_template=None,
        )
    broken_route_policy = policy.model_copy(
        update={
            "routes": [
                NotificationRoute(
                    name="broken",
                    capability="notification.custom",
                    recipient_group="soc",
                    template_name="default-text",
                )
            ]
        }
    )
    object.__setattr__(broken_route_policy.routes[0], "recipient_group", "unknown")
    with pytest.raises(NotificationPolicyViolation, match="unknown recipient group"):
        RoutingEngine().route(
            broken_route_policy,
            capability="notification.custom",
            severity=FindingSeverity.HIGH,
            priority=TicketPriority.HIGH,
            requested_group=None,
            requested_template=None,
        )

    provider = TemplateProvider()
    definition = TemplateDefinition(
        name="extra-variable",
        format=TemplateFormat.TEXT,
        subject="{{incident_id}}",
        body="{{extra}}",
        variables=frozenset({"incident_id", "extra"}),
    )
    provider.register(definition)
    with pytest.raises(NotificationValidationError, match="missing"):
        provider.render(definition, {"incident_id": "INC-15"})
    with pytest.raises(NotificationValidationError, match="not registered"):
        provider.require("absent")
    with pytest.raises(NotificationValidationError, match="duplicated"):
        provider.register(definition)
    invalid_json = TemplateDefinition(
        name="invalid-json",
        format=TemplateFormat.JSON,
        subject="",
        body='{"incident_id":"{{incident_id}}"',
        variables=frozenset({"incident_id"}),
    )
    provider.register(invalid_json)
    with pytest.raises(NotificationValidationError, match="invalid"):
        provider.render(invalid_json, {"incident_id": "INC-15"})


async def test_plugin_lifecycle_failure_and_immutable_context_branches() -> None:
    from app.notification.contracts import readonly_mapping

    frozen = readonly_mapping(
        {"nested": {"items": ["one", "two"]}, "labels": {"synthetic", "notification"}}
    )
    assert frozen["nested"]["items"] == ("one", "two")
    assert frozen["labels"] == frozenset({"synthetic", "notification"})

    registry = NotificationRegistry()
    plugin = FakeNotificationPlugin()
    registry.register(plugin)
    templates = TemplateProvider()
    templates.register(
        TemplateDefinition(
            name="default-text",
            format=TemplateFormat.TEXT,
            subject="CAP {{incident_id}}",
            body="Incident {{incident_id}} severity {{severity}}",
            variables=frozenset({"incident_id", "severity"}),
        )
    )
    policy = NotificationPolicy()
    specification, context, _, _ = NotificationPlanner(
        registry, NotificationPolicyEngine(), RoutingEngine(), templates
    ).plan(
        notification_plan_id=uuid4(),
        incident_id=uuid4(),
        response_plan_id=None,
        capability="notification.custom",
        severity=FindingSeverity.HIGH,
        priority=TicketPriority.HIGH,
        requested_at=datetime.now(UTC),
        trace_id="phase15-failure-branches",
        actor="analyst",
        variables={"incident_id": "INC-15", "severity": "HIGH", "force_render_failure": True},
        deduplication_key="phase15:failure-branches",
        policy=policy,
        plugin_name=None,
        recipient_group=None,
        template_name=None,
    )
    with pytest.raises(NotificationExecutionError, match="render failure"):
        await NotificationRuntime(registry).execute(specification, context, policy)
    assert plugin._context is None

    await plugin.initialize(context)
    result = NotificationResult(
        success=True,
        plugin_name=plugin.name,
        plugin_version=plugin.version,
        capability=specification.capability,
        status="ACCEPTED",
        recipients=["soc@example.test"],
        verification=NotificationVerification(verified=True, status="VERIFIED"),
        duration_ms=1,
        message="ok",
    )
    with pytest.raises(NotificationPolicyViolation, match="evidence"):
        NotificationRuntime._validate_result(
            result.model_copy(
                update={
                    "evidence": [
                        {
                            "evidence_type": "RECEIPT",
                            "sha256": "a" * 64,
                            "reference": "safe://receipt",
                        }
                    ]
                    * (policy.max_evidence_items + 1)
                }
            ),
            plugin.name,
            plugin.version,
            specification,
            context,
            policy,
        )
    with pytest.raises(NotificationExecutionError, match="JSON serializable"):
        NotificationRuntime._validate_result(
            result.model_copy(update={"metadata": {"unsafe": {1, 2}}}),
            plugin.name,
            plugin.version,
            specification,
            context,
            policy,
        )
    await plugin.shutdown()
    with pytest.raises(NotificationExecutionError, match="not initialized"):
        await plugin.verify(result, context)


def test_notification_configuration_models_reject_unsafe_boundaries() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        RecipientGroup(name="empty", recipients=[" "])
    with pytest.raises(ValidationError, match="Unsupported notification capability"):
        NotificationRoute(
            name="unsupported",
            capability="notification.shell",
            recipient_group="soc",
            template_name="default-text",
        )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="Silence end"):
        SilenceRule(starts_at=now, ends_at=now, reason="invalid window")
    with pytest.raises(ValidationError, match="Unsupported notification capabilities"):
        NotificationPolicy(allowed_capabilities=["notification.shell"])
    with pytest.raises(ValidationError, match="names must be unique"):
        NotificationPolicy(
            recipient_groups=[
                RecipientGroup(name="soc", recipients=["soc@example.test"]),
                RecipientGroup(name="soc", recipients=["soc@example.test"]),
            ]
        )
    with pytest.raises(ValidationError, match="explicitly allowlisted"):
        NotificationPolicy(
            recipient_groups=[RecipientGroup(name="soc", recipients=["outside@example.test"])]
        )
    with pytest.raises(ValidationError, match="configured recipient group"):
        NotificationPolicy(
            routes=[
                NotificationRoute(
                    name="unknown-group",
                    capability="notification.custom",
                    recipient_group="missing",
                    template_name="default-text",
                )
            ]
        )
    with pytest.raises(ValidationError, match="route capability must be allowed"):
        NotificationPolicy(
            allowed_capabilities=["notification.email"],
            routes=[
                NotificationRoute(
                    name="disallowed-capability",
                    capability="notification.custom",
                    recipient_group="soc",
                    template_name="default-text",
                )
            ],
        )
    disabled = NotificationPolicy(enabled=False, allowed_capabilities=[])
    assert disabled.enabled is False
