"""Notification policy decision point for routing and suppression controls."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.enums import FindingSeverity
from app.exceptions import NotificationPolicyViolation
from app.schemas.notification import NotificationPolicy, TicketPriority

_SEVERITY_RANK = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}


@dataclass(frozen=True, slots=True)
class NotificationPolicyInput:
    incident_id: UUID
    capability: str
    severity: FindingSeverity
    priority: TicketPriority
    recipient_group: str
    recipients: tuple[str, ...]
    requested_at: datetime
    recent_duplicate_at: datetime | None = None
    recent_send_count: int = 0


@dataclass(frozen=True, slots=True)
class NotificationPolicyDecision:
    allowed: bool
    suppressed: bool
    suppression_reason: str | None
    recipient_group: str
    recipients: tuple[str, ...]
    escalated: bool


class NotificationPolicyEngine:
    """Apply allowlist, hours, deduplication, silence, rate and escalation before plugins."""

    def decide(
        self, policy: NotificationPolicy, context: NotificationPolicyInput
    ) -> NotificationPolicyDecision:
        if not policy.enabled:
            raise NotificationPolicyViolation("Notification policy is disabled")
        if context.capability not in policy.allowed_capabilities:
            raise NotificationPolicyViolation("Notification capability is not allowed")
        if context.severity not in policy.allowed_severities:
            raise NotificationPolicyViolation("Notification severity is not allowed")
        if context.priority not in policy.allowed_priorities:
            raise NotificationPolicyViolation("Notification priority is not allowed")
        requested_at = self._utc(context.requested_at)
        group = context.recipient_group.casefold()
        recipients = tuple(item.casefold() for item in context.recipients)
        if not recipients or not set(recipients) <= set(policy.recipient_allowlist):
            raise NotificationPolicyViolation("Recipient is not present in the platform allowlist")

        group, recipients, escalated = self._escalate(policy, context.severity, group, recipients)
        if policy.defer_outside_business_hours and not (
            policy.business_hours_start <= requested_at.hour <= policy.business_hours_end
        ):
            return self._suppressed(group, recipients, escalated, "outside business hours")
        if self._silenced(policy, context, group, requested_at):
            return self._suppressed(group, recipients, escalated, "matching silence rule")
        if (
            context.recent_duplicate_at is not None
            and policy.deduplication_window_seconds > 0
            and requested_at - self._utc(context.recent_duplicate_at)
            <= timedelta(seconds=policy.deduplication_window_seconds)
        ):
            return self._suppressed(group, recipients, escalated, "duplicate notification")
        if context.recent_send_count >= policy.rate_limit_count:
            return self._suppressed(group, recipients, escalated, "rate limit exceeded")
        return NotificationPolicyDecision(
            allowed=True,
            suppressed=False,
            suppression_reason=None,
            recipient_group=group,
            recipients=recipients,
            escalated=escalated,
        )

    @staticmethod
    def _suppressed(
        group: str, recipients: tuple[str, ...], escalated: bool, reason: str
    ) -> NotificationPolicyDecision:
        return NotificationPolicyDecision(
            allowed=True,
            suppressed=True,
            suppression_reason=reason,
            recipient_group=group,
            recipients=recipients,
            escalated=escalated,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _silenced(
        policy: NotificationPolicy,
        context: NotificationPolicyInput,
        group: str,
        requested_at: datetime,
    ) -> bool:
        for rule in policy.silence_rules:
            if not (
                NotificationPolicyEngine._utc(rule.starts_at)
                <= requested_at
                <= NotificationPolicyEngine._utc(rule.ends_at)
            ):
                continue
            if rule.incident_id is not None and rule.incident_id != context.incident_id:
                continue
            if rule.recipient_group is not None and rule.recipient_group.casefold() != group:
                continue
            if rule.capability is not None and rule.capability.casefold() != context.capability:
                continue
            return True
        return False

    @staticmethod
    def _escalate(
        policy: NotificationPolicy,
        severity: FindingSeverity,
        group: str,
        recipients: tuple[str, ...],
    ) -> tuple[str, tuple[str, ...], bool]:
        groups = {item.name: tuple(item.recipients) for item in policy.recipient_groups}
        for rule in policy.escalation_rules:
            if (
                group == rule.from_group.casefold()
                and _SEVERITY_RANK[severity] >= _SEVERITY_RANK[rule.minimum_severity]
            ):
                target = rule.to_group.casefold()
                if target not in groups:
                    raise NotificationPolicyViolation(
                        "Escalation references an unknown recipient group"
                    )
                return target, groups[target], True
        return group, recipients, False
