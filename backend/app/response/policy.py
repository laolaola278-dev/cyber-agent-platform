"""Response policy decision point separated from runtime enforcement."""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.enums import AssetType, FindingSeverity, RiskLevel
from app.exceptions import ResponsePolicyViolation
from app.schemas.response import ResponsePolicy

_RANK = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}
_SEVERITY_RANK = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}


@dataclass(frozen=True, slots=True)
class ResponsePolicyInput:
    capability: str
    risk_level: RiskLevel
    incident_type: str
    incident_severity: FindingSeverity
    asset_types: frozenset[AssetType]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ResponsePolicyDecision:
    allowed: bool
    approval_required: bool
    automatic_execution: bool
    reason: str


class ResponsePolicyEngine:
    """Return structured decisions; callers remain responsible for enforcement."""

    def decide(
        self, policy: ResponsePolicy, context: ResponsePolicyInput
    ) -> ResponsePolicyDecision:
        capability = context.capability.casefold()
        if not policy.enabled:
            raise ResponsePolicyViolation("Response policy is disabled")
        if capability in policy.denied_capabilities:
            raise ResponsePolicyViolation("Response capability is explicitly denied")
        if capability not in policy.allowed_capabilities:
            raise ResponsePolicyViolation("Response capability is not allowed")
        incident_type = context.incident_type.strip().casefold()
        if "*" not in policy.allowed_incident_types and incident_type not in set(
            policy.allowed_incident_types
        ):
            raise ResponsePolicyViolation("Incident type is not allowed for response")
        if not context.asset_types <= set(policy.allowed_asset_types):
            raise ResponsePolicyViolation("One or more Asset types are not allowed for response")
        requested_at = context.requested_at
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=UTC)
        requested_at = requested_at.astimezone(UTC)
        in_hours = policy.business_hours_start <= requested_at.hour <= policy.business_hours_end
        if not in_hours:
            raise ResponsePolicyViolation("Request is outside configured business hours")
        if not self._in_maintenance_window(requested_at, policy.maintenance_windows):
            raise ResponsePolicyViolation("Request is outside configured maintenance windows")
        explicitly_requires_approval = capability in policy.approval_required_capabilities
        below_automatic_threshold = (
            _RANK[context.risk_level] <= _RANK[policy.automatic_execution_max_risk]
            and _SEVERITY_RANK[context.incident_severity]
            <= _SEVERITY_RANK[policy.automatic_execution_max_incident_severity]
        )
        automatic_execution = not explicitly_requires_approval and below_automatic_threshold
        return ResponsePolicyDecision(
            allowed=True,
            approval_required=not automatic_execution,
            automatic_execution=automatic_execution,
            reason="allowed by configuration-backed response policy",
        )

    @staticmethod
    def _in_maintenance_window(requested_at: datetime, windows: list[str]) -> bool:
        if "*" in windows:
            return True
        minute = requested_at.hour * 60 + requested_at.minute
        for window in windows:
            try:
                start_text, end_text = window.split("-", maxsplit=1)
                start_hour, start_minute = (int(item) for item in start_text.split(":"))
                end_hour, end_minute = (int(item) for item in end_text.split(":"))
            except (TypeError, ValueError):
                continue
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            if start <= end and start <= minute <= end:
                return True
            if start > end and (minute >= start or minute <= end):
                return True
        return False
