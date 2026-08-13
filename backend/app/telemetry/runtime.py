"""Governed Telemetry Plugin lifecycle runtime."""

import hashlib
import json

from app.exceptions import (
    TelemetryExecutionError,
    TelemetryPolicyViolation,
    WorkerExecutionError,
)
from app.schemas.telemetry import TelemetryExecutionResult, TelemetryPlan, TelemetryRecord
from app.telemetry.contracts import TelemetryPluginContext
from app.telemetry.registry import TelemetryRegistry

# PluginWorkerRuntime is imported lazily inside __init__ (not at module
# scope): `app.worker.contracts -> app.sandbox -> ... -> app.repositories ->
# app.telemetry.runtime` forms an import cycle when `app.worker` is the first
# package imported in a fresh process (e.g. the acquisition worker daemon).
# Deferring the import until instantiation breaks the cycle.


class TelemetryRuntime:
    def __init__(
        self,
        registry: TelemetryRegistry,
        worker_runtime: "PluginWorkerRuntime | None" = None,  # noqa: F821 -- lazy import
    ) -> None:
        from app.worker.plugin_runtime import PluginWorkerRuntime

        self._registry = registry
        capabilities = frozenset(
            capability for plugin in registry.plugins for capability in plugin.capabilities
        )
        self._worker_runtime = worker_runtime or PluginWorkerRuntime.synthetic(capabilities)

    async def execute(
        self, plan: TelemetryPlan, context: TelemetryPluginContext
    ) -> TelemetryExecutionResult:
        plugin = self._registry.require(plan.plugin_name)
        if context.granted_permissions != plugin.permissions:
            raise TelemetryPolicyViolation("Telemetry plugin permissions do not match")

        async def lifecycle() -> TelemetryExecutionResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                received = await plugin.receive(context)
                parsed = await plugin.parse(received, context)
                records = await plugin.transform(parsed, context)
                self._validate_records(records, context)
                return await plugin.publish(records, context)
            finally:
                if initialized:
                    await plugin.shutdown()

        capability = sorted(plugin.capabilities)[0]
        try:
            result = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=capability,
                operation_name="telemetry.execute",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=TelemetryExecutionResult,
                timeout_seconds=context.policy.timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise TelemetryExecutionError("Telemetry plugin timed out") from error
            raise
        self._validate_result(result, plugin.name, plugin.version)
        return result

    @staticmethod
    def _validate_records(records: list[TelemetryRecord], context: TelemetryPluginContext) -> None:
        if len(records) > context.policy.max_records:
            raise TelemetryPolicyViolation("Telemetry plugin exceeded maximum record count")
        for record in records:
            size = len(record.model_dump_json().encode("utf-8"))
            if size > context.policy.max_record_size_bytes:
                raise TelemetryPolicyViolation("Telemetry plugin returned an oversized record")
            expected = hashlib.sha256(
                json.dumps(record.payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if record.checksum != expected:
                raise TelemetryExecutionError("Telemetry record checksum mismatch")

    @staticmethod
    def _validate_result(
        result: TelemetryExecutionResult,
        plugin_name: str,
        plugin_version: str,
    ) -> None:
        if result.plugin_name != plugin_name or result.plugin_version != plugin_version:
            raise TelemetryExecutionError("Telemetry result identity does not match registration")
        if result.published_count != len(result.records):
            raise TelemetryExecutionError("Telemetry result publish count is inconsistent")
