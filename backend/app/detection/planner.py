"""Deterministic Detection planner with policy enforcement."""

from uuid import UUID

from app.detection.contracts import DetectionPluginContext
from app.detection.registry import DetectionRegistry
from app.exceptions import DetectionPolicyViolation
from app.schemas.detection import DetectionPlan, DetectionPolicy


class DetectionPlanner:
    """Resolve a plugin only after source, parser and capability checks pass."""

    def __init__(self, registry: DetectionRegistry) -> None:
        self._registry = registry

    def plan(
        self,
        *,
        detection_task_id: UUID,
        task_id: UUID,
        asset_id: UUID,
        trace_id: str,
        capabilities: list[str],
        log_source: str,
        parser: str,
        policy: DetectionPolicy,
        input_data: dict[str, object],
        plugin_name: str | None = None,
    ) -> tuple[DetectionPlan, DetectionPluginContext]:
        requested = set(capabilities)
        denied = requested - set(policy.capability_allowlist)
        if denied:
            raise DetectionPolicyViolation(
                "Requested capabilities are not allowed by policy",
                details={"denied": sorted(denied)},
            )
        plugin = (
            self._registry.require(plugin_name)
            if plugin_name
            else self._registry.resolve(requested)
        )
        if plugin.name.casefold() not in set(policy.allowed_plugins):
            raise DetectionPolicyViolation("Detection plugin is not allowed by policy")
        if log_source.casefold() not in set(policy.allowed_log_sources):
            raise DetectionPolicyViolation("Detection log source is not allowed by policy")
        if parser.casefold() not in set(policy.allowed_parsers):
            raise DetectionPolicyViolation("Detection parser is not allowed by policy")
        if not requested <= set(plugin.capabilities):
            raise DetectionPolicyViolation(
                "Selected plugin does not provide requested capabilities"
            )
        plan = DetectionPlan(
            asset_id=asset_id,
            capabilities=sorted(requested),
            plugin_name=plugin.name,
            log_source=log_source.casefold(),
            parser=parser.casefold(),
            steps=["initialize", "collect", "parse", "detect", "normalize", "shutdown"],
            limits={
                "sampling_rate": policy.sampling_rate,
                "max_event_size_bytes": policy.max_event_size_bytes,
                "rate_limit_per_second": policy.rate_limit_per_second,
                "retention_days": policy.retention_days,
                "timeout_seconds": policy.timeout_seconds,
                "max_events": policy.max_events,
            },
        )
        context = DetectionPluginContext(
            detection_task_id=detection_task_id,
            task_id=task_id,
            asset_id=asset_id,
            trace_id=trace_id,
            capabilities=tuple(sorted(requested)),
            policy=policy,
            input=dict(input_data),
            granted_permissions=frozenset(plugin.permissions),
        )
        return plan, context
