"""Nuclei Assessment Plugin implementation."""

from app.assessment.contracts import AssessmentPluginContext
from app.exceptions import AssessmentExecutionError, AssessmentValidationError
from app.plugins.nuclei.normalizer import NucleiResultNormalizer
from app.schemas.assessment import AssessmentPlan, AssessmentResult
from app.tools.nuclei import NucleiAdapter, NucleiExecutionRequest


class NucleiAssessmentPlugin:
    """First real Assessment Plugin; delegates all process access to its adapter."""

    name = "nuclei-assessment"
    version = "1.0.0"
    capabilities = frozenset({"template.scan", "web.scan"})
    permissions = frozenset({"assessment.execute", "tool.invoke", "evidence.write"})

    def __init__(
        self,
        adapter: NucleiAdapter,
        normalizer: NucleiResultNormalizer | None = None,
    ) -> None:
        self._adapter = adapter
        self._normalizer = normalizer or NucleiResultNormalizer()
        self._initialized = False
        self._raw_result: AssessmentResult | None = None

    async def initialize(self, context: AssessmentPluginContext) -> None:
        if "tool.invoke" not in context.granted_permissions:
            raise AssessmentValidationError("Nuclei Plugin requires tool.invoke permission")
        self._initialized = True
        self._raw_result = None

    async def plan(self, context: AssessmentPluginContext) -> AssessmentPlan:
        if not self._initialized:
            raise AssessmentExecutionError("Nuclei Plugin is not initialized")
        return AssessmentPlan(
            asset_id=context.asset_id,
            capabilities=list(context.capabilities),
            plugin_name=self.name,
            steps=["validate-target", "validate-templates", "sandbox-execute", "normalize"],
            limits={
                "max_concurrency": context.policy.max_concurrency,
                "max_requests": context.policy.max_requests,
                "rate_limit_per_second": context.policy.rate_limit_per_second,
                "timeout_seconds": context.policy.timeout_seconds,
            },
        )

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        if not self._initialized:
            raise AssessmentExecutionError("Nuclei Plugin is not initialized")
        target = context.input.get("target")
        templates = context.input.get("templates")
        if not isinstance(target, str) or not isinstance(templates, list):
            raise AssessmentValidationError(
                "Nuclei execution requires platform-derived target/templates"
            )
        execution = await self._adapter.execute(
            NucleiExecutionRequest(
                target=target,
                templates=tuple(str(item) for item in templates),
                policy=context.policy,
            )
        )
        self._raw_result = self._normalizer.assessment_result(
            execution.records,
            plugin_name=self.name,
            plugin_version=self.version,
            requests_made=execution.request_budget,
            metadata={
                "sandboxed": True,
                "templates": list(templates),
                "duration_seconds": execution.duration_seconds,
                "stderr": execution.stderr,
            },
        )
        return self._raw_result

    async def validate(self, result: AssessmentResult) -> None:
        if result.plugin_name != self.name or result.plugin_version != self.version:
            raise AssessmentValidationError("Nuclei result identity is invalid")
        if any(item.tool != "nuclei" for item in result.findings):
            raise AssessmentValidationError("Nuclei result contains a foreign tool finding")

    async def normalize(self, result: AssessmentResult) -> AssessmentResult:
        return result

    async def shutdown(self) -> None:
        self._initialized = False
        self._raw_result = None
