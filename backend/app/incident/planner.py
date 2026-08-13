"""Policy-aware Incident escalation planner."""

import hashlib

from app.core.enums import FindingSeverity, IncidentPriority
from app.exceptions import IncidentPolicyViolation
from app.incident.registry import IncidentRegistry
from app.schemas.incident import IncidentCandidate, IncidentPlan, IncidentPolicy

SEVERITY_ORDER = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}


class IncidentPlanner:
    """Validate an Incident candidate and create a deterministic platform execution plan."""

    def __init__(self, registry: IncidentRegistry) -> None:
        self._registry = registry

    def plan(self, candidate: IncidentCandidate, policy: IncidentPolicy) -> IncidentPlan:
        source = self._registry.require(candidate.source)
        if source not in policy.allowed_sources:
            raise IncidentPolicyViolation(f"Incident source {source} is denied by policy")
        if SEVERITY_ORDER[candidate.severity] < SEVERITY_ORDER[
            policy.minimum_severity
        ] or not self._confidence_allowed(
            candidate.confidence.value, policy.minimum_confidence.value
        ):
            raise IncidentPolicyViolation("Incident candidate is below policy thresholds")
        if source == "DETECTION" and len(set(candidate.event_ids)) < policy.event_threshold:
            raise IncidentPolicyViolation(
                "Detection Incident requires the configured correlated event threshold"
            )
        if not candidate.finding_ids and not candidate.event_ids and source != "MANUAL":
            raise IncidentPolicyViolation(
                "Automatic Incident requires Finding or SecurityEvent input"
            )
        priority = self._priority(candidate.severity, policy.default_priority)
        key = candidate.correlation_key.strip() or self._fallback_key(candidate)
        return IncidentPlan(
            source=source,
            correlation_key=key,
            priority=priority,
            queue=policy.default_queue,
            sla_minutes=policy.sla_targets_minutes[priority],
            finding_ids=sorted(set(candidate.finding_ids), key=str),
            event_ids=sorted(set(candidate.event_ids), key=str),
        )

    @staticmethod
    def _confidence_allowed(current: str, minimum: str) -> bool:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        return order[current] >= order[minimum]

    @staticmethod
    def _priority(severity: FindingSeverity, default: IncidentPriority) -> IncidentPriority:
        return {
            FindingSeverity.CRITICAL: IncidentPriority.P1,
            FindingSeverity.HIGH: IncidentPriority.P2,
            FindingSeverity.MEDIUM: IncidentPriority.P3,
            FindingSeverity.LOW: IncidentPriority.P4,
            FindingSeverity.INFO: IncidentPriority.P4,
        }.get(severity, default)

    @staticmethod
    def _fallback_key(candidate: IncidentCandidate) -> str:
        material = "|".join(
            [
                candidate.source.upper(),
                candidate.title.strip().casefold(),
                *sorted(str(item) for item in candidate.asset_ids),
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()
