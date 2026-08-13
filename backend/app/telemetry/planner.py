"""Fail-closed Telemetry Plugin planning."""

from uuid import UUID

from app.exceptions import TelemetryPolicyViolation
from app.schemas.telemetry import TelemetryPlan, TelemetryPolicy
from app.telemetry.contracts import TelemetryEnvelope, TelemetryPluginContext
from app.telemetry.registry import TelemetryRegistry


class TelemetryPlanner:
    def __init__(self, registry: TelemetryRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> TelemetryRegistry:
        return self._registry

    def plan(
        self,
        *,
        telemetry_task_id: UUID,
        task_id: UUID,
        trace_id: str,
        plugin_name: str,
        stream: str,
        partition: str,
        consumer: str,
        policy: TelemetryPolicy,
        input_data: tuple[TelemetryEnvelope, ...],
    ) -> tuple[TelemetryPlan, TelemetryPluginContext]:
        plugin = self._registry.require(plugin_name)
        if plugin.name.casefold() not in policy.allowed_plugins:
            raise TelemetryPolicyViolation("Telemetry plugin is not allowlisted")
        if stream.casefold() not in policy.allowed_streams:
            raise TelemetryPolicyViolation("Telemetry stream is not allowlisted")
        plan = TelemetryPlan(
            plugin_name=plugin.name,
            stream=stream,
            partition=partition,
            consumer=consumer,
            steps=["receiver", "parser", "transformer", "telemetry-record", "publisher"],
            limits={
                "timeout_seconds": policy.timeout_seconds,
                "max_records": policy.max_records,
                "max_record_size_bytes": policy.max_record_size_bytes,
                "batch_size": policy.batch_size,
                "window_seconds": policy.window_seconds,
                "queue_capacity": policy.queue_capacity,
                "backpressure_action": policy.backpressure_action.value,
            },
        )
        context = TelemetryPluginContext(
            telemetry_task_id=telemetry_task_id,
            task_id=task_id,
            trace_id=trace_id,
            stream=stream,
            partition=partition,
            consumer=consumer,
            policy=policy,
            input=input_data,
            granted_permissions=plugin.permissions,
        )
        return plan, context
