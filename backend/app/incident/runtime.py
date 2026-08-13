"""Platform-owned Incident plan runtime."""

from dataclasses import dataclass
from typing import Protocol

from app.exceptions import IncidentExecutionError
from app.schemas.incident import IncidentCandidate, IncidentPlan


class IncidentPlanHandler(Protocol):
    async def validate(self, candidate: IncidentCandidate, plan: IncidentPlan) -> None: ...

    async def correlate(self, candidate: IncidentCandidate, plan: IncidentPlan) -> None: ...

    async def create_incident(self, candidate: IncidentCandidate, plan: IncidentPlan) -> object: ...

    async def link(
        self, incident: object, candidate: IncidentCandidate, plan: IncidentPlan
    ) -> None: ...

    async def audit(
        self, incident: object, candidate: IncidentCandidate, plan: IncidentPlan
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class IncidentRuntimeResult:
    incident: object
    completed_steps: tuple[str, ...]


class IncidentRuntime:
    """Execute the fixed internal plan; this boundary is never exposed to Plugins."""

    async def execute(
        self,
        candidate: IncidentCandidate,
        plan: IncidentPlan,
        handler: IncidentPlanHandler,
    ) -> IncidentRuntimeResult:
        expected = ["validate", "correlate", "create", "link", "audit"]
        if plan.steps != expected:
            raise IncidentExecutionError("Incident plan contains unsupported lifecycle steps")
        completed: list[str] = []
        await handler.validate(candidate, plan)
        completed.append("validate")
        await handler.correlate(candidate, plan)
        completed.append("correlate")
        incident = await handler.create_incident(candidate, plan)
        completed.append("create")
        await handler.link(incident, candidate, plan)
        completed.append("link")
        await handler.audit(incident, candidate, plan)
        completed.append("audit")
        return IncidentRuntimeResult(incident=incident, completed_steps=tuple(completed))
