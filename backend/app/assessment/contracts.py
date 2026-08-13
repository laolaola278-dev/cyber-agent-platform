"""Assessment Plugin SDK contracts and least-privilege execution context."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.schemas.assessment import AssessmentPlan, AssessmentPolicy, AssessmentResult


@dataclass(frozen=True, slots=True)
class AssessmentPluginContext:
    """Narrow context deliberately excluding sessions, repositories, shell and report writers."""

    assessment_task_id: UUID
    task_id: UUID
    asset_id: UUID
    trace_id: str
    capabilities: tuple[str, ...]
    policy: AssessmentPolicy
    input: dict[str, Any]
    granted_permissions: frozenset[str]


class AssessmentPlugin(Protocol):
    """Lifecycle every assessment integration must implement."""

    name: str
    version: str
    capabilities: frozenset[str]
    permissions: frozenset[str]

    async def initialize(self, context: AssessmentPluginContext) -> None: ...

    async def plan(self, context: AssessmentPluginContext) -> AssessmentPlan: ...

    async def execute(
        self, plan: AssessmentPlan, context: AssessmentPluginContext
    ) -> AssessmentResult: ...

    async def validate(self, result: AssessmentResult) -> None: ...

    async def normalize(self, result: AssessmentResult) -> AssessmentResult: ...

    async def shutdown(self) -> None: ...
