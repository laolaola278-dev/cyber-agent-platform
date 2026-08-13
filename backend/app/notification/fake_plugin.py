"""Synthetic non-network Notification Plugin for framework certification."""

import hashlib
import json

from app.exceptions import NotificationExecutionError, NotificationPolicyViolation
from app.notification.contracts import NotificationPluginContext
from app.notification.template import TemplateDefinition, TemplateProvider
from app.schemas.notification import (
    NotificationEvidenceItem,
    NotificationPlanSpec,
    NotificationResult,
    NotificationVerification,
    RenderedNotification,
)


class FakeNotificationPlugin:
    name = "synthetic-notification"
    version = "1.0.0"
    description = "Synthetic non-network Notification Framework certification plugin"
    capabilities = frozenset(
        {
            "notification.email",
            "notification.webhook",
            "notification.chat",
            "notification.ticket",
            "notification.sms",
            "notification.custom",
        }
    )
    permissions = frozenset({"notification.render", "notification.send", "notification.verify"})
    supports_verification = True
    sandbox_compatible = True
    operational_documentation = "plugins/notification/synthetic/README.md"

    def __init__(self) -> None:
        self._context: NotificationPluginContext | None = None

    async def initialize(self, context: NotificationPluginContext) -> None:
        if "notification.send" not in context.granted_permissions:
            raise NotificationPolicyViolation("Notification send permission is required")
        self._context = context

    async def render(
        self, plan: NotificationPlanSpec, context: NotificationPluginContext
    ) -> RenderedNotification:
        self._require_context(context)
        if context.variables.get("force_render_failure"):
            raise NotificationExecutionError("Synthetic render failure requested")
        provider = TemplateProvider()
        template = TemplateDefinition(
            name=plan.template_name,
            format=plan.template_format,
            subject=plan.template_subject,
            body=plan.template_body,
            variables=frozenset(plan.variables),
        )
        provider.register(template)
        return provider.render(template, plan.variables)

    async def validate(
        self,
        plan: NotificationPlanSpec,
        rendered: RenderedNotification,
        context: NotificationPluginContext,
    ) -> None:
        self._require_context(context)
        if (
            plan.incident_id != context.incident_id
            or plan.response_plan_id != context.response_plan_id
        ):
            raise NotificationPolicyViolation("Notification context scope does not match plan")
        if tuple(plan.recipients) != context.recipients:
            raise NotificationPolicyViolation("Notification recipient scope does not match plan")
        if not rendered.body:
            raise NotificationPolicyViolation("Rendered notification body cannot be empty")
        if context.variables.get("force_validation_failure"):
            raise NotificationPolicyViolation("Synthetic validation failure requested")

    async def send(
        self,
        plan: NotificationPlanSpec,
        rendered: RenderedNotification,
        context: NotificationPluginContext,
    ) -> NotificationResult:
        self._require_context(context)
        if context.variables.get("force_send_failure"):
            raise NotificationExecutionError("Synthetic send failure requested")
        payload = {
            "plan_id": str(context.notification_plan_id),
            "incident_id": str(context.incident_id),
            "capability": plan.capability,
            "recipients": list(context.recipients),
            "subject": rendered.subject,
            "body_sha256": hashlib.sha256(rendered.body.encode("utf-8")).hexdigest(),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return NotificationResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            capability=plan.capability,
            status="ACCEPTED",
            recipients=list(context.recipients),
            verification=NotificationVerification(verified=False, status="PENDING"),
            evidence=[
                NotificationEvidenceItem(
                    evidence_type="NOTIFICATION_RECEIPT",
                    sha256=hashlib.sha256(encoded).hexdigest(),
                    reference=f"synthetic://{context.notification_plan_id}/accepted",
                    metadata=payload,
                )
            ],
            duration_ms=1,
            message="Synthetic notification accepted",
            metadata={"network_access": False, "destructive": False},
        )

    async def verify(
        self, result: NotificationResult, context: NotificationPluginContext
    ) -> NotificationResult:
        self._require_context(context)
        verified = not bool(context.variables.get("force_verification_failure"))
        return result.model_copy(
            update={
                "success": result.success and verified,
                "verification": NotificationVerification(
                    verified=verified,
                    status="VERIFIED" if verified else "FAILED",
                    external_reference=f"synthetic://{context.notification_plan_id}",
                    details={"accepted": verified},
                ),
            }
        )

    async def shutdown(self) -> None:
        self._context = None

    async def health(self) -> bool:
        return True

    def _require_context(self, context: NotificationPluginContext) -> None:
        if self._context != context:
            raise NotificationExecutionError(
                "Notification plugin was not initialized for this context"
            )
