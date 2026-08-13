"""Unified Response Framework application service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.service import CapabilityRegistryService
from app.core.enums import AssetType, FindingSeverity
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    ResponseConflict,
    ResponseNotFound,
    ResponsePolicyViolation,
    ResponseValidationError,
)
from app.models import Asset, Incident
from app.models.response import (
    ResponseEvidence,
    ResponseExecution,
    ResponsePlan,
    ResponsePlanAsset,
    ResponsePlugin,
    ResponsePolicyRecord,
)
from app.repositories.pagination import PageResult
from app.repositories.response import (
    ResponsePlanRepository,
    ResponsePluginRepository,
    ResponsePolicyRepository,
)
from app.response.approval import ApprovalService
from app.response.contracts import ResponsePluginContext, readonly_mapping
from app.response.planner import ResponsePlanner
from app.response.registry import ResponseRegistry
from app.response.rollback import RollbackService
from app.response.runtime import ResponseRuntime
from app.schemas.response import (
    ApprovalState,
    ResponseApprovalCreate,
    ResponseExecutionRequest,
    ResponseExecutionState,
    ResponsePlanCreate,
    ResponsePlanRead,
    ResponsePlanSpec,
    ResponsePluginRead,
    ResponsePolicy,
    ResponseRejectionCreate,
    ResponseRollbackRequest,
    RollbackState,
)


class ResponseService:
    """Own planning, approval, execution, rollback, evidence and audit persistence."""

    def __init__(
        self,
        session: AsyncSession,
        plans: ResponsePlanRepository,
        plugins: ResponsePluginRepository,
        policies: ResponsePolicyRepository,
        capabilities: CapabilityRegistryService,
        registry: ResponseRegistry,
        planner: ResponsePlanner,
        runtime: ResponseRuntime,
        approval_service: ApprovalService,
        rollback_service: RollbackService,
        publisher: EventPublisher,
        default_policy: ResponsePolicy,
    ) -> None:
        self._session = session
        self._plans = plans
        self._plugins = plugins
        self._policies = policies
        self._capabilities = capabilities
        self._registry = registry
        self._planner = planner
        self._runtime = runtime
        self._approvals = approval_service
        self._rollbacks = rollback_service
        self._publisher = publisher
        self._default_policy = default_policy

    async def bootstrap(self) -> None:
        for runtime_plugin in self._registry.plugins:
            healthy = await runtime_plugin.health()
            plugin = await self._plugins.get_by_identity(
                runtime_plugin.name, runtime_plugin.version
            )
            values = {
                "description": runtime_plugin.description,
                "enabled": healthy,
                "permissions": sorted(runtime_plugin.permissions),
                "capabilities": sorted(runtime_plugin.capabilities),
                "supports_approval": runtime_plugin.supports_approval,
                "supports_rollback": runtime_plugin.supports_rollback,
                "health_status": "HEALTHY" if healthy else "UNHEALTHY",
                "sandbox_compatible": runtime_plugin.sandbox_compatible,
                "certified": healthy and runtime_plugin.sandbox_compatible,
                "operational_documentation": runtime_plugin.operational_documentation,
                "configuration": {},
            }
            if plugin is None:
                plugin = ResponsePlugin(
                    name=runtime_plugin.name, version=runtime_plugin.version, **values
                )
                self._session.add(plugin)
            else:
                for field, value in values.items():
                    setattr(plugin, field, value)
            for capability_name in runtime_plugin.capabilities:
                await self._capabilities.register(
                    capability_name,
                    description=f"Response capability {capability_name}",
                    risk_level=(
                        "LOW"
                        if capability_name in {"response.notify", "response.ticket"}
                        else "HIGH"
                    ),
                )
        policy_row = await self._policies.get_by_identity(self._default_policy.policy_name, "1.0.0")
        if policy_row is None:
            self._session.add(
                ResponsePolicyRecord(
                    name=self._default_policy.policy_name,
                    version="1.0.0",
                    enabled=self._default_policy.enabled,
                    configuration=self._default_policy.model_dump(mode="json"),
                )
            )
        await self._session.flush()

    async def create(self, payload: ResponsePlanCreate, *, trace_id: str) -> ResponsePlan:
        await self.bootstrap()
        incident = await self._require_incident(payload.incident_id)
        assets = await self._require_assets(payload.asset_ids)
        now = datetime.now(UTC)
        expires_at = payload.expires_at or now + timedelta(
            seconds=self._default_policy.approval_ttl_seconds
        )
        plan_id = uuid4()
        specification, _ = self._planner.plan(
            response_plan_id=plan_id,
            incident_id=incident.id,
            asset_ids=payload.asset_ids,
            asset_types={AssetType(item.asset_type) for item in assets},
            incident_type=incident.classification or incident.source,
            incident_severity=FindingSeverity(incident.severity),
            capability=payload.target_capability,
            risk_level=payload.risk_level,
            requested_at=now,
            trace_id=trace_id,
            actor=payload.requested_by,
            parameters=payload.parameters,
            rollback_parameters=payload.rollback_parameters,
            policy=self._default_policy,
            plugin_name=payload.plugin_name,
        )
        runtime_plugin = self._registry.require(specification.plugin_name)
        plugin = await self._plugins.get_by_identity(runtime_plugin.name, runtime_plugin.version)
        if plugin is None or not plugin.certified:
            raise ResponseValidationError("Certified Response plugin persistence is unavailable")
        approval_state = (
            ApprovalState.PENDING_APPROVAL
            if specification.approval_required
            else ApprovalState.APPROVED
        )
        execution_state = (
            ResponseExecutionState.BLOCKED
            if specification.approval_required
            else ResponseExecutionState.READY
        )
        response_plan = ResponsePlan(
            id=plan_id,
            incident_id=incident.id,
            plugin_id=plugin.id,
            target_capability=payload.target_capability,
            requested_by=payload.requested_by,
            reason=payload.reason,
            risk_level=payload.risk_level.value,
            approval_state=approval_state.value,
            execution_state=execution_state.value,
            rollback_state=(
                RollbackState.AVAILABLE.value
                if specification.supports_rollback
                else RollbackState.NOT_SUPPORTED.value
            ),
            policy_snapshot=self._default_policy.model_dump(mode="json"),
            plan=specification.model_dump(mode="json"),
            parameters=payload.parameters,
            rollback_parameters=payload.rollback_parameters,
            supports_rollback=specification.supports_rollback,
            expires_at=expires_at,
            assets=[ResponsePlanAsset(asset_id=item.id) for item in assets],
        )
        self._session.add(response_plan)
        await self._session.flush()
        await self._publish(
            EventType.RESPONSE_PLAN_CREATED,
            response_plan.id,
            trace_id,
            payload.requested_by,
            {
                "incident_id": str(incident.id),
                "capability": payload.target_capability,
                "approval_required": specification.approval_required,
            },
        )
        await self._session.commit()
        return await self.get(response_plan.id)

    async def get(self, plan_id: UUID) -> ResponsePlan:
        plan = await self._plans.get(plan_id)
        if plan is None:
            raise ResponseNotFound(f"Response Plan {plan_id} not found")
        if self._mark_expired(plan):
            await self._session.commit()
        return plan

    async def list(
        self,
        *,
        incident_id: UUID | None,
        approval_state: str | None,
        execution_state: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[ResponsePlan]:
        return await self._plans.search(
            incident_id=incident_id,
            approval_state=approval_state,
            execution_state=execution_state,
            page=page,
            page_size=page_size,
        )

    async def approve(
        self,
        plan_id: UUID,
        payload: ResponseApprovalCreate,
        *,
        trace_id: str,
    ) -> ResponsePlan:
        plan = await self.get(plan_id)
        approval = self._approvals.approve(plan, payload, self._policy(plan))
        self._session.add(approval)
        await self._publish(
            EventType.RESPONSE_PLAN_APPROVED,
            plan.id,
            trace_id,
            payload.approver,
            {"level": payload.level, "state": plan.approval_state},
        )
        await self._session.commit()
        return await self.get(plan.id)

    async def reject(
        self,
        plan_id: UUID,
        payload: ResponseRejectionCreate,
        *,
        trace_id: str,
    ) -> ResponsePlan:
        plan = await self.get(plan_id)
        approval = self._approvals.reject(plan, payload)
        self._session.add(approval)
        await self._publish(
            EventType.RESPONSE_PLAN_REJECTED,
            plan.id,
            trace_id,
            payload.approver,
            {"state": plan.approval_state},
        )
        await self._session.commit()
        return await self.get(plan.id)

    async def execute(
        self,
        plan_id: UUID,
        payload: ResponseExecutionRequest,
        *,
        trace_id: str,
    ) -> ResponsePlan:
        plan = await self.get(plan_id)
        if plan.approval_state != ApprovalState.APPROVED.value:
            raise ResponsePolicyViolation("Response Plan is not approved")
        if plan.execution_state != ResponseExecutionState.READY.value:
            raise ResponseConflict("Response Plan is not ready for execution")
        policy = self._policy(plan)
        specification = ResponsePlanSpec.model_validate(plan.plan)
        context = self._context(plan, trace_id=trace_id, actor=payload.actor)
        execution = ResponseExecution(
            plan_id=plan.id,
            plugin_id=plan.plugin_id,
            status=ResponseExecutionState.RUNNING.value,
            verification_status="PENDING",
            result={},
            duration_ms=0,
            message="",
            started_at=datetime.now(UTC),
        )
        plan.execution_state = ResponseExecutionState.RUNNING.value
        self._session.add(execution)
        await self._session.flush()
        await self._publish(
            EventType.RESPONSE_EXECUTION_STARTED,
            plan.id,
            trace_id,
            payload.actor,
            {"execution_id": str(execution.id), "plugin": specification.plugin_name},
        )
        execution_error: Exception | None = None
        try:
            result = await self._runtime.execute(specification, context, policy)
            execution.status = (
                ResponseExecutionState.SUCCEEDED.value
                if result.success
                else ResponseExecutionState.FAILED.value
            )
            execution.verification_status = result.verification.status
            execution.result = result.model_dump(mode="json", exclude={"rollback_token"})
            execution.rollback_token = result.rollback_token
            execution.duration_ms = result.duration_ms
            execution.message = result.message
            plan.execution_state = (
                ResponseExecutionState.VERIFIED.value
                if result.success and result.verification.verified
                else ResponseExecutionState.FAILED.value
            )
            if plan.execution_state == ResponseExecutionState.VERIFIED.value:
                plan.approval_state = ApprovalState.EXECUTED.value
            await self._persist_evidence(plan, result.evidence, execution_id=execution.id)
            await self._publish(
                EventType.RESPONSE_EXECUTION_COMPLETED,
                plan.id,
                trace_id,
                payload.actor,
                {
                    "execution_id": str(execution.id),
                    "status": execution.status,
                    "verified": result.verification.verified,
                },
            )
        except Exception as error:
            execution_error = error
            execution.status = ResponseExecutionState.FAILED.value
            execution.verification_status = "FAILED"
            execution.message = str(error)
            plan.execution_state = ResponseExecutionState.FAILED.value
            await self._publish(
                EventType.RESPONSE_EXECUTION_FAILED,
                plan.id,
                trace_id,
                payload.actor,
                {"execution_id": str(execution.id)},
                error=str(error),
            )
        finally:
            execution.finished_at = datetime.now(UTC)
        await self._session.commit()
        if execution_error is not None:
            raise execution_error
        return await self.get(plan.id)

    async def rollback(
        self,
        plan_id: UUID,
        payload: ResponseRollbackRequest,
        *,
        trace_id: str,
    ) -> ResponsePlan:
        plan = await self.get(plan_id)
        if plan.approval_state != ApprovalState.EXECUTED.value:
            raise ResponseConflict("Only executed Response Plans can be rolled back")
        execution = next(
            (item for item in reversed(plan.executions) if item.status == "SUCCEEDED"), None
        )
        if execution is None:
            raise ResponseConflict("No successful execution is available for rollback")
        rollback, result = await self._rollbacks.rollback(
            plan=plan,
            execution=execution,
            specification=ResponsePlanSpec.model_validate(plan.plan),
            context=self._context(plan, trace_id=trace_id, actor=payload.actor),
            policy=self._policy(plan),
            payload=payload,
            runtime=self._runtime,
        )
        self._session.add(rollback)
        await self._session.flush()
        await self._persist_evidence(plan, result.evidence, rollback_id=rollback.id)
        await self._publish(
            EventType.RESPONSE_ROLLBACK_COMPLETED,
            plan.id,
            trace_id,
            payload.actor,
            {
                "rollback_id": str(rollback.id),
                "status": rollback.status,
                "verified": result.verification.verified,
            },
        )
        await self._session.commit()
        return await self.get(plan.id)

    async def list_plugins(self) -> list[ResponsePluginRead]:
        await self.bootstrap()
        await self._session.commit()
        rows = await self._plugins.list_enabled()
        return [ResponsePluginRead.model_validate(item) for item in rows]

    @staticmethod
    def to_read(plan: ResponsePlan) -> ResponsePlanRead:
        return ResponsePlanRead(
            id=plan.id,
            incident_id=plan.incident_id,
            plugin_id=plan.plugin_id,
            target_capability=plan.target_capability,
            requested_by=plan.requested_by,
            reason=plan.reason,
            risk_level=plan.risk_level,
            approval_state=plan.approval_state,
            execution_state=plan.execution_state,
            rollback_state=plan.rollback_state,
            policy_snapshot=plan.policy_snapshot,
            plan=plan.plan,
            parameters=plan.parameters,
            rollback_parameters=plan.rollback_parameters,
            supports_rollback=plan.supports_rollback,
            expires_at=plan.expires_at,
            asset_ids=[item.asset_id for item in plan.assets],
            approvals=plan.approvals,
            executions=plan.executions,
            rollbacks=plan.rollbacks,
            evidence=plan.evidence,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    async def _require_incident(self, incident_id: UUID) -> Incident:
        incident = await self._session.get(Incident, incident_id)
        if incident is None:
            raise ResponseValidationError(f"Incident {incident_id} not found")
        if incident.status in {"RESOLVED", "CLOSED"}:
            raise ResponsePolicyViolation(
                "Resolved or closed Incidents cannot receive new responses"
            )
        return incident

    async def _require_assets(self, asset_ids: list[UUID]) -> list[Asset]:
        rows = list(await self._session.scalars(select(Asset).where(Asset.id.in_(asset_ids))))
        if len(rows) != len(set(asset_ids)) or any(item.deleted_at is not None for item in rows):
            raise ResponseValidationError("Response Plan references unknown or deleted Assets")
        return rows

    def _context(self, plan: ResponsePlan, *, trace_id: str, actor: str) -> ResponsePluginContext:
        plugin = self._registry.require(plan.plugin.name)
        return ResponsePluginContext(
            response_plan_id=plan.id,
            incident_id=plan.incident_id,
            asset_ids=tuple(item.asset_id for item in plan.assets),
            trace_id=trace_id,
            actor=actor,
            capability=plan.target_capability,
            parameters=readonly_mapping(plan.parameters),
            rollback_parameters=readonly_mapping(plan.rollback_parameters),
            rollback_token=None,
            granted_permissions=frozenset(plugin.permissions),
        )

    @staticmethod
    def _policy(plan: ResponsePlan) -> ResponsePolicy:
        return ResponsePolicy.model_validate(plan.policy_snapshot)

    async def _persist_evidence(
        self,
        plan: ResponsePlan,
        evidence: list[object],
        *,
        execution_id: UUID | None = None,
        rollback_id: UUID | None = None,
    ) -> None:
        from app.schemas.response import ResponseEvidenceItem

        for raw in evidence:
            item = ResponseEvidenceItem.model_validate(raw)
            self._session.add(
                ResponseEvidence(
                    plan_id=plan.id,
                    execution_id=execution_id,
                    rollback_id=rollback_id,
                    evidence_type=item.evidence_type,
                    sha256=item.sha256,
                    reference=item.reference,
                    metadata_=item.metadata,
                )
            )
        await self._session.flush()

    @staticmethod
    def _mark_expired(plan: ResponsePlan) -> bool:
        expires_at = plan.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)
        if (
            plan.approval_state == ApprovalState.PENDING_APPROVAL.value
            and expires_at <= datetime.now(UTC)
        ):
            plan.approval_state = ApprovalState.EXPIRED.value
            plan.execution_state = ResponseExecutionState.BLOCKED.value
            return True
        return False

    async def _publish(
        self,
        event_type: EventType,
        aggregate_id: UUID,
        trace_id: str,
        actor: str,
        payload: dict[str, object],
        *,
        error: str | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                aggregate_id=aggregate_id,
                trace_id=trace_id,
                actor=actor,
                resource="response",
                payload=payload,
                error=error,
            )
        )
