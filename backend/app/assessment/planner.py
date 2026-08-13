"""Deterministic Assessment planner with policy enforcement."""

from uuid import UUID

from app.assessment.contracts import AssessmentPluginContext
from app.assessment.registry import AssessmentRegistry
from app.exceptions import AssessmentPolicyViolation
from app.schemas.assessment import AssessmentPlan, AssessmentPolicy


class AssessmentPlanner:
    """Resolve a plugin only after target and capability policy checks pass."""

    def __init__(self, registry: AssessmentRegistry) -> None:
        self._registry = registry

    def plan(
        self,
        *,
        assessment_task_id: UUID,
        task_id: UUID,
        asset_id: UUID,
        trace_id: str,
        capabilities: list[str],
        policy: AssessmentPolicy,
        input_data: dict[str, object],
        plugin_name: str | None = None,
    ) -> tuple[AssessmentPlan, AssessmentPluginContext]:
        requested = set(capabilities)
        allowed = set(policy.capability_allowlist)
        if not requested <= allowed:
            raise AssessmentPolicyViolation(
                "Requested capabilities are not allowed by policy",
                details={"denied": sorted(requested - allowed)},
            )
        if asset_id in set(policy.asset_denylist):
            raise AssessmentPolicyViolation("Asset is explicitly denied by policy")
        if policy.asset_allowlist and asset_id not in set(policy.asset_allowlist):
            raise AssessmentPolicyViolation("Asset is not present in the policy allowlist")
        plugin = (
            self._registry.require(plugin_name)
            if plugin_name
            else self._registry.resolve(requested)
        )
        if not requested <= set(plugin.capabilities):
            raise AssessmentPolicyViolation(
                "Selected plugin does not provide requested capabilities"
            )
        plan = AssessmentPlan(
            asset_id=asset_id,
            capabilities=sorted(requested),
            plugin_name=plugin.name,
            steps=["initialize", "plan", "execute", "validate", "normalize", "shutdown"],
            limits={
                "max_concurrency": policy.max_concurrency,
                "max_requests": policy.max_requests,
                "rate_limit_per_second": policy.rate_limit_per_second,
                "scan_depth": policy.scan_depth,
                "timeout_seconds": policy.timeout_seconds,
            },
        )
        context = AssessmentPluginContext(
            assessment_task_id=assessment_task_id,
            task_id=task_id,
            asset_id=asset_id,
            trace_id=trace_id,
            capabilities=tuple(sorted(requested)),
            policy=policy,
            input=dict(input_data),
            granted_permissions=frozenset(plugin.permissions),
        )
        return plan, context
