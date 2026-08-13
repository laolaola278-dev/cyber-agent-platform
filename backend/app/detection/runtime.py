"""Detection Plugin lifecycle runtime."""

import hashlib
import json

from app.detection.contracts import DetectionPluginContext
from app.detection.registry import DetectionRegistry
from app.exceptions import (
    DetectionExecutionError,
    DetectionPolicyViolation,
    WorkerExecutionError,
)
from app.schemas.detection import DetectionPlan, DetectionResult
from app.worker import PluginWorkerRuntime


class DetectionRuntime:
    """Execute one plugin through the governed six-stage lifecycle."""

    def __init__(
        self,
        registry: DetectionRegistry,
        worker_runtime: PluginWorkerRuntime | None = None,
    ) -> None:
        self._registry = registry
        capabilities = frozenset(
            capability for plugin in registry.plugins for capability in plugin.capabilities
        )
        self._worker_runtime = worker_runtime or PluginWorkerRuntime.synthetic(capabilities)

    async def execute(
        self, plan: DetectionPlan, context: DetectionPluginContext
    ) -> DetectionResult:
        plugin = self._registry.require(plan.plugin_name)
        if (
            "detection.execute" not in context.granted_permissions
            or context.granted_permissions != plugin.permissions
        ):
            raise DetectionPolicyViolation("Detection plugin permissions do not match the plan")

        async def lifecycle() -> DetectionResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                collected = await plugin.collect(context)
                parsed = await plugin.parse(collected, context)
                result = await plugin.detect(parsed, context)
                return await plugin.normalize(result)
            finally:
                if initialized:
                    await plugin.shutdown()

        try:
            normalized = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=plan.capabilities[0],
                operation_name="detection.execute",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=DetectionResult,
                timeout_seconds=context.policy.timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise DetectionExecutionError("Detection plugin timed out") from error
            raise
        self._validate_result(normalized, plugin.name, plugin.version, context)
        return self._apply_ingestion_policy(normalized, context)

    @staticmethod
    def _validate_result(
        result: DetectionResult,
        plugin_name: str,
        plugin_version: str,
        context: DetectionPluginContext,
    ) -> None:
        if result.plugin_name != plugin_name or result.plugin_version != plugin_version:
            raise DetectionExecutionError("Plugin result identity does not match registration")
        if len(result.events) > context.policy.max_events:
            raise DetectionPolicyViolation(
                "Plugin exceeded the maximum event count",
                details={"events": len(result.events), "max_events": context.policy.max_events},
            )
        for event in result.events:
            size = len(event.model_dump_json().encode("utf-8"))
            if size > context.policy.max_event_size_bytes:
                raise DetectionPolicyViolation(
                    "Plugin returned an oversized event",
                    details={
                        "event_size": size,
                        "max_event_size_bytes": context.policy.max_event_size_bytes,
                    },
                )
        metadata_size = len(json.dumps(result.metadata, default=str).encode("utf-8"))
        if metadata_size > context.policy.max_event_size_bytes:
            raise DetectionPolicyViolation("Plugin returned oversized result metadata")

    @staticmethod
    def _apply_ingestion_policy(
        result: DetectionResult, context: DetectionPluginContext
    ) -> DetectionResult:
        """Apply deterministic sampling and per-batch rate bounds after validation."""

        rate_limit = max(1, int(context.policy.rate_limit_per_second))
        events = result.events[:rate_limit]
        if context.policy.sampling_rate < 1.0:
            threshold = int(context.policy.sampling_rate * 10_000)
            events = [
                event
                for event in events
                if int(
                    hashlib.sha256(event.model_dump_json().encode("utf-8")).hexdigest()[:8],
                    16,
                )
                % 10_000
                < threshold
            ]
        metadata = dict(result.metadata)
        metadata["policy"] = {
            "events_before_ingestion_policy": len(result.events),
            "events_after_ingestion_policy": len(events),
            "sampling_rate": context.policy.sampling_rate,
            "rate_limit_per_second": context.policy.rate_limit_per_second,
            "retention_days": context.policy.retention_days,
        }
        return result.model_copy(update={"events": events, "metadata": metadata})
