"""Exclusive Notification Plugin lifecycle runtime."""

import json

from app.exceptions import (
    NotificationExecutionError,
    NotificationPolicyViolation,
    WorkerExecutionError,
)
from app.notification.contracts import NotificationPluginContext
from app.notification.registry import NotificationRegistry
from app.schemas.notification import NotificationPlanSpec, NotificationPolicy, NotificationResult
from app.worker import PluginWorkerRuntime


class NotificationRuntime:
    def __init__(
        self,
        registry: NotificationRegistry,
        worker_runtime: PluginWorkerRuntime | None = None,
    ) -> None:
        self._registry = registry
        capabilities = frozenset(
            capability for plugin in registry.plugins for capability in plugin.capabilities
        )
        self._worker_runtime = worker_runtime or PluginWorkerRuntime.synthetic(capabilities)

    async def execute(
        self,
        specification: NotificationPlanSpec,
        context: NotificationPluginContext,
        policy: NotificationPolicy,
    ) -> NotificationResult:
        plugin = self._registry.require(specification.plugin_name)
        self._authorize(plugin.permissions, context)

        async def lifecycle() -> NotificationResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                rendered = await plugin.render(specification, context)
                await plugin.validate(specification, rendered, context)
                result = await plugin.send(specification, rendered, context)
                return await plugin.verify(result, context)
            finally:
                if initialized:
                    await plugin.shutdown()

        try:
            verified = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=specification.capability,
                operation_name="notification.execute",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=NotificationResult,
                timeout_seconds=policy.execution_timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise NotificationExecutionError("Notification plugin timed out") from error
            raise
        self._validate_result(verified, plugin.name, plugin.version, specification, context, policy)
        return verified

    @staticmethod
    def _authorize(plugin_permissions: frozenset[str], context: NotificationPluginContext) -> None:
        required = {"notification.render", "notification.send", "notification.verify"}
        if context.granted_permissions != plugin_permissions or not required <= plugin_permissions:
            raise NotificationPolicyViolation(
                "Notification plugin permissions do not match execution plan"
            )

    @staticmethod
    def _validate_result(
        result: NotificationResult,
        plugin_name: str,
        plugin_version: str,
        specification: NotificationPlanSpec,
        context: NotificationPluginContext,
        policy: NotificationPolicy,
    ) -> None:
        if result.plugin_name != plugin_name or result.plugin_version != plugin_version:
            raise NotificationExecutionError("Plugin result identity does not match registration")
        if result.capability != specification.capability:
            raise NotificationExecutionError(
                "Plugin result capability does not match Notification Plan"
            )
        if tuple(item.casefold() for item in result.recipients) != context.recipients:
            raise NotificationExecutionError("Plugin modified immutable recipient scope")
        if not set(result.recipients) <= set(policy.recipient_allowlist):
            raise NotificationPolicyViolation("Plugin returned a recipient outside the allowlist")
        if len(result.evidence) > policy.max_evidence_items:
            raise NotificationPolicyViolation("Plugin exceeded Notification evidence limit")
        if result.success and not result.verification.verified:
            raise NotificationExecutionError("Successful notification did not pass verification")
        if len(result.model_dump_json().encode("utf-8")) > policy.max_result_bytes:
            raise NotificationPolicyViolation("Plugin returned an oversized NotificationResult")
        try:
            json.dumps(result.metadata)
        except (TypeError, ValueError) as error:
            raise NotificationExecutionError(
                "Plugin result metadata is not JSON serializable"
            ) from error
