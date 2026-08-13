"""Deterministic Response planner with policy and plugin enforcement."""

from datetime import datetime
from uuid import UUID

from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.exceptions import ResponsePolicyViolation
from app.response.contracts import ResponsePluginContext, readonly_mapping
from app.response.policy import ResponsePolicyEngine, ResponsePolicyInput
from app.response.registry import ResponseRegistry
from app.schemas.response import ResponsePlanSpec, ResponsePolicy


class ResponsePlanner:
    """Resolve a plugin only after policy and capability checks pass."""

    def __init__(self, registry: ResponseRegistry, policy_engine: ResponsePolicyEngine) -> None:
        self._registry = registry
        self._policy_engine = policy_engine

    def plan(
        self,
        *,
        response_plan_id: UUID,
        incident_id: UUID,
        asset_ids: list[UUID],
        asset_types: set[AssetType],
        incident_type: str,
        incident_severity: FindingSeverity,
        capability: str,
        risk_level: RiskLevel,
        requested_at: datetime,
        trace_id: str,
        actor: str,
        parameters: dict[str, object],
        rollback_parameters: dict[str, object],
        policy: ResponsePolicy,
        plugin_name: str | None,
    ) -> tuple[ResponsePlanSpec, ResponsePluginContext]:
        decision = self._policy_engine.decide(
            policy,
            ResponsePolicyInput(
                capability=capability,
                risk_level=risk_level,
                incident_type=incident_type,
                incident_severity=incident_severity,
                asset_types=frozenset(asset_types),
                requested_at=requested_at,
            ),
        )
        plugin = (
            self._registry.require(plugin_name)
            if plugin_name
            else self._registry.resolve(capability)
        )
        if capability not in plugin.capabilities:
            raise ResponsePolicyViolation(
                "Selected Response plugin does not provide the requested capability"
            )
        specification = ResponsePlanSpec(
            incident_id=incident_id,
            asset_ids=asset_ids,
            target_capability=capability,
            plugin_name=plugin.name,
            parameters=parameters,
            rollback_parameters=rollback_parameters,
            risk_level=risk_level,
            approval_required=decision.approval_required,
            supports_rollback=plugin.supports_rollback,
            policy_name=policy.policy_name,
            steps=[
                "initialize",
                "plan",
                "validate",
                "execute",
                "verify",
                "shutdown",
            ],
        )
        context = ResponsePluginContext(
            response_plan_id=response_plan_id,
            incident_id=incident_id,
            asset_ids=tuple(asset_ids),
            trace_id=trace_id,
            actor=actor,
            capability=capability,
            parameters=readonly_mapping(parameters),
            rollback_parameters=readonly_mapping(rollback_parameters),
            rollback_token=None,
            granted_permissions=frozenset(plugin.permissions),
        )
        return specification, context
