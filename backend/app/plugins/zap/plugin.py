"""OWASP ZAP Assessment Plugin implementation."""

from app.assessment.contracts import AssessmentPluginContext
from app.exceptions import AssessmentExecutionError, AssessmentValidationError
from app.plugins.zap.normalizer import ZapResultNormalizer
from app.schemas.assessment import AssessmentPlan, AssessmentResult, ZapPolicy
from app.tools.zap import ZapAdapter, ZapExecutionRequest


class ZapAssessmentPlugin:
    """Second real Assessment Plugin; delegates every ZAP API call to its Adapter."""

    name = "zap-assessment"
    version = "1.0.0"
    capabilities = frozenset({"web.dast", "web.spider", "web.passive_scan", "web.active_scan"})
    permissions = frozenset({"assessment.execute", "tool.invoke", "evidence.write"})

    def __init__(self, adapter: ZapAdapter, normalizer: ZapResultNormalizer | None = None) -> None:
        self._adapter = adapter
        self._normalizer = normalizer or ZapResultNormalizer()
        self._initialized = False
        self._raw_result: AssessmentResult | None = None

    async def initialize(self, context: AssessmentPluginContext) -> None:
        if "tool.invoke" not in context.granted_permissions:
            raise AssessmentValidationError("ZAP Plugin requires tool.invoke permission")
        self._initialized = True
        self._raw_result = None

    async def plan(self, context: AssessmentPluginContext) -> AssessmentPlan:
        if not self._initialized:
            raise AssessmentExecutionError("ZAP Plugin is not initialized")
        policy = self._policy(context)
        steps = ["create-session", "create-context", "access-target"]
        if policy.spider_enabled:
            steps.append("spider")
        if policy.passive_scan_enabled:
            steps.append("passive-scan")
        if policy.active_scan_enabled:
            steps.append("active-scan")
        steps.extend(["fetch-alerts", "normalize"])
        return AssessmentPlan(
            asset_id=context.asset_id,
            capabilities=list(context.capabilities),
            plugin_name=self.name,
            steps=steps,
            limits={
                "max_concurrency": policy.max_concurrency,
                "max_requests": policy.max_requests,
                "scan_depth": policy.spider_depth,
                "timeout_seconds": policy.max_scan_time_seconds,
            },
        )

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        if not self._initialized:
            raise AssessmentExecutionError("ZAP Plugin is not initialized")
        target = context.input.get("target")
        active_scan_authorized = context.input.get("active_scan_authorized", False)
        if not isinstance(target, str) or not isinstance(active_scan_authorized, bool):
            raise AssessmentValidationError("ZAP requires platform-derived target/authorization")
        policy = self._policy(context)
        execution = await self._adapter.execute(
            ZapExecutionRequest(
                target=target,
                policy=policy,
                active_scan_authorized=active_scan_authorized,
            )
        )
        self._raw_result = self._normalizer.assessment_result(
            execution.alerts,
            plugin_name=self.name,
            plugin_version=self.version,
            requests_made=execution.requests_made,
            metadata={
                "sandboxed": True,
                "session": execution.session_name,
                "context": execution.context_name,
                "scan_policy": execution.scan_policy,
                "scan_scope": list(execution.scan_scope),
                "scan_duration": execution.duration_seconds,
                "tool_version": execution.tool_version,
                "mode": execution.mode,
                "urls_discovered": execution.urls_discovered,
                "alert_summary": execution.alert_summary,
            },
        )
        return self._raw_result

    async def validate(self, result: AssessmentResult) -> None:
        if result.plugin_name != self.name or result.plugin_version != self.version:
            raise AssessmentValidationError("ZAP result identity is invalid")
        if any(item.tool != "owasp-zap" for item in result.findings):
            raise AssessmentValidationError("ZAP result contains a foreign tool finding")

    async def normalize(self, result: AssessmentResult) -> AssessmentResult:
        return result

    async def shutdown(self) -> None:
        self._initialized = False
        self._raw_result = None

    @staticmethod
    def _policy(context: AssessmentPluginContext) -> ZapPolicy:
        return (
            context.policy
            if isinstance(context.policy, ZapPolicy)
            else ZapPolicy.model_validate(context.policy.model_dump())
        )
