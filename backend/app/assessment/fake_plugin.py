"""Non-scanning plugin used only to validate the Assessment Framework contract."""

from app.assessment.contracts import AssessmentPluginContext
from app.schemas.assessment import AssessmentPlan, AssessmentResult, RawFinding


class FakeAssessmentPlugin:
    """Deterministic in-memory plugin that performs no network, shell or file operations."""

    name = "fake-assessment"
    version = "1.0.0"
    capabilities = frozenset(
        {
            "web.scan",
            "port.scan",
            "template.scan",
            "host.scan",
            "container.scan",
            "dependency.scan",
            "ssl.scan",
            "header.scan",
            "dns.scan",
        }
    )
    permissions = frozenset({"assessment.execute", "evidence.write"})

    def __init__(self) -> None:
        self.initialized = False

    async def initialize(self, context: AssessmentPluginContext) -> None:
        self.initialized = True

    async def plan(self, context: AssessmentPluginContext) -> AssessmentPlan:
        return AssessmentPlan(
            asset_id=context.asset_id,
            capabilities=list(context.capabilities),
            plugin_name=self.name,
            steps=["fake-check"],
            limits={"max_requests": context.policy.max_requests},
        )

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult:
        raw_findings = context.input.get("fake_findings", [])
        return AssessmentResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            findings=[RawFinding.model_validate(item) for item in raw_findings],
            requests_made=0,
            metadata={"network_access": False},
        )

    async def validate(self, result: AssessmentResult) -> None:
        if not result.success:
            raise ValueError("Fake plugin only accepts successful deterministic results")

    async def normalize(self, result: AssessmentResult) -> AssessmentResult:
        return result

    async def shutdown(self) -> None:
        self.initialized = False
