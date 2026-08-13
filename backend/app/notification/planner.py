"""Notification planner with route and policy enforcement before execution."""

from datetime import datetime
from uuid import UUID

from app.core.enums import FindingSeverity
from app.exceptions import NotificationPolicyViolation
from app.notification.contracts import NotificationPluginContext, readonly_mapping
from app.notification.policy import NotificationPolicyEngine, NotificationPolicyInput
from app.notification.registry import NotificationRegistry
from app.notification.routing import RoutingEngine
from app.notification.template import TemplateProvider
from app.schemas.notification import (
    NotificationPlanSpec,
    NotificationPolicy,
    NotificationStatus,
    TicketPriority,
)


class NotificationPlanner:
    def __init__(
        self,
        registry: NotificationRegistry,
        policy_engine: NotificationPolicyEngine,
        routing: RoutingEngine,
        templates: TemplateProvider,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._routing = routing
        self._templates = templates

    def plan(
        self,
        *,
        notification_plan_id: UUID,
        incident_id: UUID,
        response_plan_id: UUID | None,
        capability: str,
        severity: FindingSeverity,
        priority: TicketPriority,
        requested_at: datetime,
        trace_id: str,
        actor: str,
        variables: dict[str, object],
        deduplication_key: str,
        policy: NotificationPolicy,
        plugin_name: str | None,
        recipient_group: str | None,
        template_name: str | None,
        recent_duplicate_at: datetime | None = None,
        recent_send_count: int = 0,
    ) -> tuple[
        NotificationPlanSpec,
        NotificationPluginContext,
        NotificationStatus,
        str | None,
    ]:
        route = self._routing.route(
            policy,
            capability=capability,
            severity=severity,
            priority=priority,
            requested_group=recipient_group,
            requested_template=template_name,
        )
        decision = self._policy.decide(
            policy,
            NotificationPolicyInput(
                incident_id=incident_id,
                capability=capability,
                severity=severity,
                priority=priority,
                recipient_group=route.recipient_group,
                recipients=route.recipients,
                requested_at=requested_at,
                recent_duplicate_at=recent_duplicate_at,
                recent_send_count=recent_send_count,
            ),
        )
        plugin = (
            self._registry.require(plugin_name)
            if plugin_name
            else self._registry.resolve(capability)
        )
        if capability not in plugin.capabilities:
            raise NotificationPolicyViolation(
                "Selected Notification plugin does not provide the requested capability"
            )
        template = self._templates.require(route.template_name)
        specification = NotificationPlanSpec(
            incident_id=incident_id,
            response_plan_id=response_plan_id,
            capability=capability,
            plugin_name=plugin.name,
            recipient_group=decision.recipient_group,
            recipients=list(decision.recipients),
            template_name=template.name,
            template_format=template.format,
            template_subject=template.subject,
            template_body=template.body,
            variables=variables,
            severity=severity,
            priority=priority,
            policy_name=policy.policy_name,
            deduplication_key=deduplication_key,
            steps=["initialize", "render", "validate", "send", "verify", "shutdown"],
        )
        context = NotificationPluginContext(
            notification_plan_id=notification_plan_id,
            incident_id=incident_id,
            response_plan_id=response_plan_id,
            trace_id=trace_id,
            actor=actor,
            capability=capability,
            recipients=decision.recipients,
            variables=readonly_mapping(variables),
            granted_permissions=frozenset(plugin.permissions),
        )
        status = (
            NotificationStatus.SUPPRESSED if decision.suppressed else NotificationStatus.PLANNED
        )
        return specification, context, status, decision.suppression_reason
