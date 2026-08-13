"""Deterministic Incident to Plugin to Recipient routing engine."""

from dataclasses import dataclass

from app.core.enums import FindingSeverity
from app.exceptions import NotificationPolicyViolation
from app.schemas.notification import NotificationPolicy, TicketPriority


@dataclass(frozen=True, slots=True)
class NotificationRouteResult:
    recipient_group: str
    recipients: tuple[str, ...]
    template_name: str


class RoutingEngine:
    """Resolve configured routes without exposing unrestricted recipient input."""

    def route(
        self,
        policy: NotificationPolicy,
        *,
        capability: str,
        severity: FindingSeverity,
        priority: TicketPriority,
        requested_group: str | None,
        requested_template: str | None,
    ) -> NotificationRouteResult:
        groups = {item.name: tuple(item.recipients) for item in policy.recipient_groups}
        candidates = [
            item
            for item in policy.routes
            if item.capability == capability
            and severity in item.severities
            and priority in item.priorities
            and (requested_group is None or item.recipient_group == requested_group)
            and (requested_template is None or item.template_name == requested_template)
        ]
        if not candidates:
            raise NotificationPolicyViolation("No Notification route matches the request")
        route = sorted(candidates, key=lambda item: item.name)[0]
        try:
            recipients = groups[route.recipient_group]
        except KeyError as error:
            raise NotificationPolicyViolation(
                "Notification route references an unknown recipient group"
            ) from error
        if not recipients or not set(recipients) <= set(policy.recipient_allowlist):
            raise NotificationPolicyViolation("Notification route escaped recipient allowlist")
        return NotificationRouteResult(
            recipient_group=route.recipient_group,
            recipients=recipients,
            template_name=route.template_name,
        )
