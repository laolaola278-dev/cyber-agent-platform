"""Assessment Plugin lifecycle runtime."""

from app.assessment.contracts import AssessmentPluginContext
from app.assessment.registry import AssessmentRegistry
from app.exceptions import (
    AssessmentExecutionError,
    AssessmentPolicyViolation,
    WorkerExecutionError,
)
from app.schemas.assessment import AssessmentPlan, AssessmentResult
from app.worker import PluginWorkerRuntime


class AssessmentRuntime:
    """Execute one plugin through all SDK phases and always perform shutdown."""

    def __init__(
        self,
        registry: AssessmentRegistry,
        worker_runtime: PluginWorkerRuntime | None = None,
    ) -> None:
        self._registry = registry
        capabilities = frozenset(
            capability for plugin in registry.plugins for capability in plugin.capabilities
        )
        self._worker_runtime = worker_runtime or PluginWorkerRuntime.synthetic(capabilities)

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        plugin = self._registry.require(plan.plugin_name)

        async def lifecycle() -> AssessmentResult:
            initialized = False
            try:
                await plugin.initialize(context)
                initialized = True
                plugin_plan = await plugin.plan(context)
                if plugin_plan.asset_id != plan.asset_id:
                    raise AssessmentExecutionError("Plugin plan changed the authorized asset")
                if set(plugin_plan.capabilities) - set(plan.capabilities):
                    raise AssessmentExecutionError("Plugin plan expanded authorized capabilities")
                result = await plugin.execute(plugin_plan, context)
                if result.requests_made > context.policy.max_requests:
                    raise AssessmentPolicyViolation(
                        "Plugin exceeded the maximum request count",
                        details={
                            "requests_made": result.requests_made,
                            "max_requests": context.policy.max_requests,
                        },
                    )
                await plugin.validate(result)
                return await plugin.normalize(result)
            finally:
                if initialized:
                    await plugin.shutdown()

        try:
            normalized = await self._worker_runtime.execute(
                plugin_name=plugin.name,
                plugin_version=plugin.version,
                capability=plan.capabilities[0],
                operation_name="assessment.execute",
                owner=context.trace_id,
                operation=lifecycle,
                result_type=AssessmentResult,
                timeout_seconds=context.policy.timeout_seconds,
            )
        except WorkerExecutionError as error:
            if error.details.get("timed_out"):
                raise AssessmentExecutionError("Assessment plugin timed out") from error
            raise
        if normalized.plugin_name != plugin.name or normalized.plugin_version != plugin.version:
            raise AssessmentExecutionError("Plugin result identity does not match registration")
        return normalized


class AssessmentScheduler:
    """Reserved scheduler port; scheduling is intentionally outside Phase 6 scope."""

    async def schedule(self, assessment_task_id: object) -> None:
        raise NotImplementedError("Assessment scheduling is reserved for a later phase")
