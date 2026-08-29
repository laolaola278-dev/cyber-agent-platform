"""Phase 28.8 (v1.0.1) -- targeted ResponseService coverage (PATCH-GATE 13).

``app/response/service.py`` sat at 47.4% because tests only ever reached it
through the HTTP API, which exercises the happy path. These tests drive the
service directly and target the branches that carry security meaning:

* approval / denial (who may authorise a response)
* audit persistence (every transition must be recorded)
* rollback (a response must be reversible, and only after execution)
* provider failure (a failing plugin must not look successful)
* invalid action (executing an unapproved or non-ready plan)
* expiry (a stale approval must not stay executable)
* mock-only enforcement is covered in
  ``test_phase_28_8_capability_disclosure.py``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.capabilities.service import CapabilityRegistryService
from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.events.audit import AuditSubscriber
from app.events.bus import InMemoryEventBus
from app.exceptions import (
    ResponseConflict,
    ResponseNotFound,
    ResponsePolicyViolation,
    ResponseValidationError,
)
from app.models import Asset, Incident
from app.models.response import ResponseEvidence, ResponsePlugin, ResponsePolicyRecord
from app.repositories.audit import AuditRepository
from app.repositories.capability import CapabilityRepository
from app.repositories.response import (
    ResponsePlanRepository,
    ResponsePluginRepository,
    ResponsePolicyRepository,
)
from app.response import (
    ApprovalService,
    FakeResponsePlugin,
    ResponsePlanner,
    ResponsePolicyEngine,
    ResponseRegistry,
    ResponseRuntime,
    RollbackService,
)
from app.response.service import ResponseService
from app.schemas.response import (
    ApprovalState,
    ResponseApprovalCreate,
    ResponseExecutionRequest,
    ResponseExecutionState,
    ResponsePlanCreate,
    ResponseRejectionCreate,
    ResponseRollbackRequest,
)
from app.services.audit import AuditService
from app.worker.plugin_runtime import PluginWorkerRuntime
from tests.conftest import TestSessionFactory


def _policy():
    """A policy that permits the containment capabilities under test.

    The shipped default allowlists only ``response.notify``/``response.ticket``
    and denies ``response.isolate`` outright, so the approval, execution and
    rollback branches below could not be reached with it.
    """

    from app.schemas.response import ResponsePolicy

    return ResponsePolicy(
        allowed_capabilities=frozenset(
            {"response.notify", "response.ticket", "response.isolate", "response.rollback"}
        ),
        denied_capabilities=frozenset(),
        approval_required_capabilities=frozenset({"response.isolate"}),
        allowed_incident_types=frozenset({"*"}),
        allowed_asset_types=frozenset(AssetType),
    )


async def _incident(session, *, status: str = "NEW") -> Incident:
    incident = Incident(
        title="ransomware beacon on workstation-14",
        description="Egress beacon correlated with a known C2",
        severity=FindingSeverity.HIGH.value,
        priority="P1",
        status=status,
        confidence="HIGH",
        source="detection",
        correlation_key=f"corr-{uuid4().hex[:12]}",
    )
    session.add(incident)
    await session.flush()
    return incident


async def _asset(session) -> Asset:
    asset = Asset(
        asset_type=AssetType.HOST.value,
        name="workstation-14",
        value="10.20.30.40",
        canonical_value="10.20.30.40",
    )
    session.add(asset)
    await session.flush()
    return asset


def _registry() -> ResponseRegistry:
    registry = ResponseRegistry()
    registry.register(FakeResponsePlugin())
    return registry


async def _service(bus: InMemoryEventBus, registry: ResponseRegistry):
    session = TestSessionFactory()
    AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
    runtime = ResponseRuntime(
        registry,
        PluginWorkerRuntime.synthetic(frozenset(FakeResponsePlugin.capabilities)),
    )
    return (
        session,
        ResponseService(
            session,
            ResponsePlanRepository(session),
            ResponsePluginRepository(session),
            ResponsePolicyRepository(session),
            CapabilityRegistryService(session, CapabilityRepository(session)),
            registry,
            ResponsePlanner(registry, ResponsePolicyEngine()),
            runtime,
            ApprovalService(),
            RollbackService(),
            bus,
            _policy(),
        ),
    )


def _create_payload(incident: Incident, asset: Asset, **overrides) -> ResponsePlanCreate:
    values = {
        "incident_id": incident.id,
        "asset_ids": [asset.id],
        "target_capability": "response.isolate",
        "requested_by": "analyst@example.test",
        "reason": "contain confirmed C2 beacon",
        "risk_level": RiskLevel.HIGH,
    }
    values.update(overrides)
    return ResponsePlanCreate(**values)


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------


async def test_bootstrap_registers_plugin_policy_and_capabilities() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        await service.bootstrap()
        await session.commit()

        plugin = (
            await session.scalars(
                select(ResponsePlugin).where(ResponsePlugin.name == "fake-response")
            )
        ).one()
        assert plugin.certified is True, "a healthy sandbox-compatible plugin must be certified"
        assert plugin.health_status == "HEALTHY"
        assert "response.isolate" in plugin.capabilities

        policy = (
            await session.scalars(
                select(ResponsePolicyRecord).where(
                    ResponsePolicyRecord.name == _policy().policy_name
                )
            )
        ).one()
        assert policy.enabled is True
    finally:
        await session.close()


async def test_bootstrap_is_idempotent_and_updates_existing_rows() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        await service.bootstrap()
        await session.commit()
        await service.bootstrap()
        await session.commit()
        rows = (
            await session.scalars(
                select(ResponsePlugin).where(ResponsePlugin.name == "fake-response")
            )
        ).all()
        assert len(rows) == 1, "bootstrap must not duplicate the plugin row"
    finally:
        await session.close()


# --------------------------------------------------------------------------
# create / validation
# --------------------------------------------------------------------------


async def test_create_high_risk_plan_requires_approval() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(incident, asset), trace_id="trace-create"
        )
        assert plan.approval_state == ApprovalState.PENDING_APPROVAL.value
        assert plan.execution_state == ResponseExecutionState.BLOCKED.value
        assert plan.rollback_state == "AVAILABLE"
        assert len(plan.assets) == 1
    finally:
        await session.close()


async def test_create_publishes_creation_event() -> None:
    from app.events import EventType, PlatformEvent

    seen: list[PlatformEvent] = []

    async def _capture(event: PlatformEvent) -> None:
        seen.append(event)

    bus = InMemoryEventBus()
    bus.subscribe(EventType.RESPONSE_PLAN_CREATED, _capture)
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(incident, asset), trace_id="trace-evt"
        )
        assert seen and seen[0].aggregate_id == plan.id
        assert seen[0].payload["approval_required"] is True
    finally:
        await session.close()


async def test_create_rejects_unknown_incident() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        asset = await _asset(session)
        with pytest.raises(ResponseValidationError, match="Incident"):
            await service.create(
                _create_payload(type("I", (), {"id": uuid4()})(), asset),
                trace_id="trace-missing",
            )
    finally:
        await session.close()


async def test_create_rejects_resolved_incident() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session, status="RESOLVED")
        asset = await _asset(session)
        with pytest.raises(ResponsePolicyViolation, match="cannot receive new responses"):
            await service.create(
                _create_payload(incident, asset), trace_id="trace-resolved"
            )
    finally:
        await session.close()


async def test_create_rejects_unknown_or_deleted_asset() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        with pytest.raises(ResponseValidationError, match="unknown or deleted Assets"):
            await service.create(
                _create_payload(incident, type("A", (), {"id": uuid4()})()),
                trace_id="trace-asset",
            )
    finally:
        await session.close()


async def test_create_honours_explicit_expiry() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        expires = datetime.now(UTC) + timedelta(seconds=30)
        plan = await service.create(
            _create_payload(incident, asset, expires_at=expires), trace_id="trace-exp"
        )
        assert plan.expires_at is not None
    finally:
        await session.close()


# --------------------------------------------------------------------------
# get / expiry
# --------------------------------------------------------------------------


async def test_get_unknown_plan_raises_not_found() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        with pytest.raises(ResponseNotFound):
            await service.get(uuid4())
    finally:
        await session.close()


async def test_expired_pending_approval_is_marked_expired() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(
                incident, asset, expires_at=datetime.now(UTC) - timedelta(seconds=1)
            ),
            trace_id="trace-expired",
        )
        reloaded = await service.get(plan.id)
        assert reloaded.approval_state == ApprovalState.EXPIRED.value
        assert reloaded.execution_state == ResponseExecutionState.BLOCKED.value
        # an expired plan must not become executable through approval
        with pytest.raises(ResponsePolicyViolation):
            await service.approve(
                plan.id,
                ResponseApprovalCreate(approver="ciso@example.test", level=1),
                trace_id="trace-expired-approve",
            )
    finally:
        await session.close()


# --------------------------------------------------------------------------
# approval / denial / audit
# --------------------------------------------------------------------------


async def _approved_plan(service: ResponseService, session, trace: str = "trace"):
    incident = await _incident(session)
    asset = await _asset(session)
    plan = await service.create(_create_payload(incident, asset), trace_id=trace)
    approved = await service.approve(
        plan.id,
        ResponseApprovalCreate(approver="ciso@example.test", level=1, comment="contain it"),
        trace_id=trace,
    )
    return approved


async def test_approve_records_approval_and_transitions_to_ready() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session)
        assert plan.approval_state == ApprovalState.APPROVED.value
        assert plan.execution_state == ResponseExecutionState.READY.value
        assert any(item.approver == "ciso@example.test" for item in plan.approvals)
    finally:
        await session.close()


async def test_approval_is_written_to_the_audit_log() -> None:
    from app.models import AuditLog

    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        await _approved_plan(service, session, trace="trace-audit")
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.resource == "response")
            )
        ).all()
        assert rows, "every response transition must be auditable"
        # every state transition is audited under the request trace id; the
        # audit row carries the transition action (the event payload stored in
        # ``details`` holds level/state, not the aggregate id)
        actions = {row.action for row in rows}
        assert "ResponsePlanCreated" in actions
        assert "ResponsePlanApproved" in actions
        assert any(
            row.action == "ResponsePlanApproved" and row.trace_id == "trace-audit"
            for row in rows
        )
    finally:
        await session.close()


async def test_reject_records_rejection_and_blocks_execution() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(incident, asset), trace_id="trace-reject"
        )
        rejected = await service.reject(
            plan.id,
            ResponseRejectionCreate(
                approver="ciso@example.test", comment="business impact too high"
            ),
            trace_id="trace-reject",
        )
        assert rejected.approval_state == ApprovalState.REJECTED.value
        with pytest.raises(ResponsePolicyViolation, match="not approved"):
            await service.execute(
                plan.id,
                ResponseExecutionRequest(actor="analyst@example.test"),
                trace_id="trace-reject-exec",
            )
    finally:
        await session.close()


# --------------------------------------------------------------------------
# execute
# --------------------------------------------------------------------------


async def test_execute_requires_approval() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(incident, asset), trace_id="trace-notapproved"
        )
        with pytest.raises(ResponsePolicyViolation, match="not approved"):
            await service.execute(
                plan.id,
                ResponseExecutionRequest(actor="analyst@example.test"),
                trace_id="trace-notapproved",
            )
    finally:
        await session.close()


async def test_execute_rejects_a_plan_that_is_not_ready() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session, trace="trace-notready")
        plan.execution_state = ResponseExecutionState.BLOCKED.value
        await session.commit()
        with pytest.raises(ResponseConflict, match="not ready for execution"):
            await service.execute(
                plan.id,
                ResponseExecutionRequest(actor="analyst@example.test"),
                trace_id="trace-notready",
            )
    finally:
        await session.close()


async def test_execute_persists_evidence_and_marks_verified() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session, trace="trace-exec")
        executed = await service.execute(
            plan.id,
            ResponseExecutionRequest(actor="analyst@example.test"),
            trace_id="trace-exec",
        )
        assert executed.approval_state == ApprovalState.EXECUTED.value
        assert executed.execution_state == ResponseExecutionState.VERIFIED.value
        evidence = (
            await session.scalars(
                select(ResponseEvidence).where(ResponseEvidence.plan_id == plan.id)
            )
        ).all()
        assert evidence, "a verified response must leave evidence behind"
    finally:
        await session.close()


async def test_execute_failure_is_recorded_and_reraised() -> None:
    """A failing plugin must not leave the plan looking successful."""

    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(
                incident, asset, parameters={"force_validation_failure": True}
            ),
            trace_id="trace-fail",
        )
        await service.approve(
            plan.id,
            ResponseApprovalCreate(approver="ciso@example.test", level=1),
            trace_id="trace-fail",
        )
        with pytest.raises(ResponsePolicyViolation):
            await service.execute(
                plan.id,
                ResponseExecutionRequest(actor="analyst@example.test"),
                trace_id="trace-fail",
            )
        reloaded = await service.get(plan.id)
        assert reloaded.execution_state == ResponseExecutionState.FAILED.value
        assert reloaded.approval_state != ApprovalState.EXECUTED.value
    finally:
        await session.close()


# --------------------------------------------------------------------------
# rollback
# --------------------------------------------------------------------------


async def test_rollback_requires_an_executed_plan() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        incident = await _incident(session)
        asset = await _asset(session)
        plan = await service.create(
            _create_payload(incident, asset), trace_id="trace-rb"
        )
        with pytest.raises(ResponseConflict, match="Only executed Response Plans"):
            await service.rollback(
                plan.id,
                ResponseRollbackRequest(actor="analyst@example.test", reason="false positive"),
                trace_id="trace-rb",
            )
    finally:
        await session.close()


async def test_rollback_requires_a_successful_execution() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session, trace="trace-rb2")
        plan.approval_state = ApprovalState.EXECUTED.value
        await session.commit()
        with pytest.raises(ResponseConflict, match="No successful execution"):
            await service.rollback(
                plan.id,
                ResponseRollbackRequest(actor="analyst@example.test", reason="false positive"),
                trace_id="trace-rb2",
            )
    finally:
        await session.close()


async def test_rollback_after_success_restores_state() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session, trace="trace-rb3")
        await service.execute(
            plan.id,
            ResponseExecutionRequest(actor="analyst@example.test"),
            trace_id="trace-rb3",
        )
        rolled = await service.rollback(
            plan.id,
            ResponseRollbackRequest(actor="analyst@example.test", reason="false positive"),
            trace_id="trace-rb3",
        )
        assert rolled.rollbacks, "rollback must be persisted on the plan"
        # RollbackState: AVAILABLE -> RUNNING -> SUCCEEDED -> VERIFIED
        assert rolled.rollback_state in {"SUCCEEDED", "VERIFIED"}
    finally:
        await session.close()


# --------------------------------------------------------------------------
# read model
# --------------------------------------------------------------------------


async def test_to_read_maps_assets_and_collections() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plan = await _approved_plan(service, session, trace="trace-read")
        read = ResponseService.to_read(plan)
        assert read.id == plan.id
        assert read.asset_ids == [item.asset_id for item in plan.assets]
        assert isinstance(read.approvals, list)
    finally:
        await session.close()


async def test_list_plugins_returns_certified_plugins() -> None:
    bus = InMemoryEventBus()
    session, service = await _service(bus, _registry())
    try:
        plugins = await service.list_plugins()
        assert any(item.name == "fake-response" for item in plugins)
    finally:
        await session.close()


def test_unused_placeholder() -> None:
    """Keep UUID import meaningful for readers of the fixtures above."""

    assert isinstance(uuid4(), UUID)
