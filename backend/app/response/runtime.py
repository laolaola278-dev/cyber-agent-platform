"""Response Plugin lifecycle runtime and exclusive execution boundary."""

import json

from app.exceptions import (
    ResponseExecutionError,
    ResponsePolicyViolation,
    WorkerExecutionError,
)
from app.response.contracts import ResponsePluginContext
from app.response.registry import ResponseRegistry
from app.schemas.response import ResponsePlanSpec, ResponsePolicy, ResponseResult
from app.worker import PluginWorkerRuntime


class ResponseRuntime:
    """Exclusively execute and roll back certified Response plugins."""

    def __init__(
        self,
        registry: ResponseRegistry,
        worker_runtime: PluginWorkerRuntime | None = None,
    ) -> None:
        self._registry = registry
        capabilities = frozenset(
            capability for plugin in registry.plugins for capability in plugin.capabilities
        )
        self._worker_runtime = worker_runtime or PluginWorkerRuntime.synthetic(capabilities)

    async def execute(
        self,
        specification: ResponsePlanSpec,
        context: ResponsePluginContext,
        policy: ResponsePolicy,
    ) -> ResponseResult:
        plugin = self._registry.require(specification.plugin_name)
        self._authorize(plugin.permissions, context, "response.execute")

        async def lifecycle() -> ResponseResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                planned = await plugin.plan(specification, context)
                self._validate_plan(planned, specification)
                await plugin.validate(planned, context)
                result = await plugin.execute(planned, context)
                return await plugin.verify(result, context)
            finally:
                if initialized:
                    await plugin.shutdown()

        try:
            verified = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=specification.target_capability,
                operation_name="response.execute",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=ResponseResult,
                timeout_seconds=policy.execution_timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise ResponseExecutionError("Response plugin timed out") from error
            raise
        self._validate_result(verified, plugin.name, plugin.version, specification, policy)
        return verified

    async def rollback(
        self,
        specification: ResponsePlanSpec,
        context: ResponsePluginContext,
        policy: ResponsePolicy,
    ) -> ResponseResult:
        plugin = self._registry.require(specification.plugin_name)
        if not specification.supports_rollback or not plugin.supports_rollback:
            raise ResponsePolicyViolation("Response does not support rollback")
        self._authorize(plugin.permissions, context, "response.rollback")

        async def lifecycle() -> ResponseResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                await plugin.validate(specification, context)
                result = await plugin.rollback(specification, context)
                return await plugin.verify(result, context)
            finally:
                if initialized:
                    await plugin.shutdown()

        try:
            verified = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=specification.target_capability,
                operation_name="response.rollback",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=ResponseResult,
                timeout_seconds=policy.execution_timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise ResponseExecutionError("Response rollback timed out") from error
            raise
        self._validate_result(verified, plugin.name, plugin.version, specification, policy)
        return verified

    @staticmethod
    def _authorize(
        plugin_permissions: frozenset[str],
        context: ResponsePluginContext,
        required: str,
    ) -> None:
        if context.granted_permissions != plugin_permissions or required not in plugin_permissions:
            raise ResponsePolicyViolation("Response plugin permissions do not match execution plan")

    @staticmethod
    def _validate_plan(planned: ResponsePlanSpec, expected: ResponsePlanSpec) -> None:
        immutable = ("incident_id", "asset_ids", "target_capability", "plugin_name")
        if any(getattr(planned, field) != getattr(expected, field) for field in immutable):
            raise ResponseExecutionError("Plugin modified immutable Response Plan scope")

    @staticmethod
    def _validate_result(
        result: ResponseResult,
        plugin_name: str,
        plugin_version: str,
        specification: ResponsePlanSpec,
        policy: ResponsePolicy,
    ) -> None:
        if result.plugin_name != plugin_name or result.plugin_version != plugin_version:
            raise ResponseExecutionError("Plugin result identity does not match registration")
        if result.capability != specification.target_capability:
            raise ResponseExecutionError("Plugin result capability does not match Response Plan")
        if result.rollback_supported != specification.supports_rollback:
            raise ResponseExecutionError("Plugin result rollback declaration changed")
        if len(result.evidence) > policy.max_evidence_items:
            raise ResponsePolicyViolation("Plugin exceeded Response evidence limit")
        if result.success and not result.verification.verified:
            raise ResponseExecutionError("Successful response did not pass verification")
        result_size = len(result.model_dump_json().encode("utf-8"))
        if result_size > 2_000_000:
            raise ResponsePolicyViolation("Plugin returned an oversized ResponseResult")
        try:
            json.dumps(result.metadata)
        except (TypeError, ValueError) as error:
            raise ResponseExecutionError(
                "Plugin result metadata is not JSON serializable"
            ) from error
