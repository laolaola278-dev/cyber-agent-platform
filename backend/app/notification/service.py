"""Unified Notification and Ticket Framework application service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.service import CapabilityRegistryService
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    NotificationConflict,
    NotificationNotFound,
    NotificationPolicyViolation,
    NotificationValidationError,
)
from app.models import Incident, ResponsePlan
from app.models.notification import (
    NotificationEvidence,
    NotificationExecution,
    NotificationPlan,
    NotificationPlugin,
    NotificationTemplate,
    Ticket,
)
from app.notification.contracts import NotificationPluginContext, readonly_mapping
from app.notification.planner import NotificationPlanner
from app.notification.registry import NotificationRegistry
from app.notification.runtime import NotificationRuntime
from app.notification.template import TemplateDefinition, TemplateProvider
from app.repositories.notification import (
    NotificationPlanRepository,
    NotificationPluginRepository,
    NotificationTemplateRepository,
    TicketRepository,
)
from app.repositories.pagination import PageResult
from app.schemas.notification import (
    NotificationCreate,
    NotificationPlanSpec,
    NotificationPluginRead,
    NotificationPolicy,
    NotificationRead,
    NotificationStatus,
    TemplateFormat,
    TicketCreate,
    TicketRead,
    TicketStatus,
)


class NotificationService:
    """Own notification planning, execution, evidence, Ticket and audit persistence."""

    def __init__(
        self,
        session: AsyncSession,
        plans: NotificationPlanRepository,
        plugins: NotificationPluginRepository,
        templates: NotificationTemplateRepository,
        tickets: TicketRepository,
        capabilities: CapabilityRegistryService,
        registry: NotificationRegistry,
        template_provider: TemplateProvider,
        planner: NotificationPlanner,
        runtime: NotificationRuntime,
        publisher: EventPublisher,
        default_policy: NotificationPolicy,
    ) -> None:
        self._session = session
        self._plans = plans
        self._plugins = plugins
        self._templates = templates
        self._tickets = tickets
        self._capabilities = capabilities
        self._registry = registry
        self._template_provider = template_provider
        self._planner = planner
        self._runtime = runtime
        self._publisher = publisher
        self._policy = default_policy

    async def bootstrap(self) -> None:
        for runtime_plugin in self._registry.plugins:
            healthy = await runtime_plugin.health()
            row = await self._plugins.get_by_identity(runtime_plugin.name, runtime_plugin.version)
            values = {
                "description": runtime_plugin.description,
                "enabled": healthy,
                "permissions": sorted(runtime_plugin.permissions),
                "capabilities": sorted(runtime_plugin.capabilities),
                "supports_verification": runtime_plugin.supports_verification,
                "health_status": "HEALTHY" if healthy else "UNHEALTHY",
                "sandbox_compatible": runtime_plugin.sandbox_compatible,
                "certified": healthy
                and runtime_plugin.sandbox_compatible
                and runtime_plugin.supports_verification,
                "operational_documentation": runtime_plugin.operational_documentation,
                "configuration": {},
            }
            if row is None:
                row = NotificationPlugin(
                    name=runtime_plugin.name,
                    version=runtime_plugin.version,
                    **values,
                )
                self._session.add(row)
            else:
                for field, value in values.items():
                    setattr(row, field, value)
            for capability in runtime_plugin.capabilities:
                await self._capabilities.register(
                    capability,
                    description=f"Notification capability {capability}",
                    risk_level="LOW",
                )
        for template in self._template_provider.templates:
            row = await self._templates.get_by_identity(template.name, "1.0.0")
            if row is None:
                self._session.add(
                    NotificationTemplate(
                        name=template.name,
                        version="1.0.0",
                        format=template.format.value,
                        subject=template.subject,
                        body=template.body,
                        variables=sorted(template.variables),
                        enabled=True,
                    )
                )
        await self._session.flush()

    async def create(self, payload: NotificationCreate, *, trace_id: str) -> NotificationPlan:
        await self.bootstrap()
        incident = await self._require_incident(payload.incident_id)
        await self._require_response(payload.response_plan_id, incident.id)
        now = datetime.now(UTC)
        key = payload.deduplication_key or self._deduplication_key(payload)
        duplicate = await self._plans.latest_duplicate(key)
        count_since = await self._plans.count_sent_since(
            now - timedelta(seconds=self._policy.rate_limit_window_seconds)
        )
        plan_id = uuid4()
        specification, _, plan_status, suppression_reason = self._planner.plan(
            notification_plan_id=plan_id,
            incident_id=incident.id,
            response_plan_id=payload.response_plan_id,
            capability=payload.capability,
            severity=payload.severity,
            priority=payload.priority,
            requested_at=now,
            trace_id=trace_id,
            actor=payload.requested_by,
            variables=payload.variables,
            deduplication_key=key,
            policy=self._policy,
            plugin_name=payload.plugin_name,
            recipient_group=payload.recipient_group,
            template_name=payload.template_name,
            recent_duplicate_at=duplicate.created_at if duplicate is not None else None,
            recent_send_count=count_since,
        )
        runtime_plugin = self._registry.require(specification.plugin_name)
        plugin = await self._plugins.get_by_identity(runtime_plugin.name, runtime_plugin.version)
        template = await self._templates.get_by_identity(specification.template_name, "1.0.0")
        if plugin is None or not plugin.certified or template is None or not template.enabled:
            raise NotificationValidationError(
                "Certified Notification plugin or template persistence is unavailable"
            )
        plan = NotificationPlan(
            id=plan_id,
            incident_id=incident.id,
            response_plan_id=payload.response_plan_id,
            plugin_id=plugin.id,
            template_id=template.id,
            capability=payload.capability,
            recipient_group=specification.recipient_group,
            recipients=specification.recipients,
            severity=payload.severity.value,
            priority=payload.priority.value,
            status=plan_status.value,
            requested_by=payload.requested_by,
            deduplication_key=key,
            policy_snapshot=self._policy.model_dump(mode="json"),
            plan=specification.model_dump(mode="json"),
            suppression_reason=suppression_reason,
        )
        self._session.add(plan)
        await self._session.flush()
        event = (
            EventType.NOTIFICATION_SUPPRESSED
            if plan_status == NotificationStatus.SUPPRESSED
            else EventType.NOTIFICATION_PLAN_CREATED
        )
        await self._publish(
            event,
            plan.id,
            trace_id,
            payload.requested_by,
            {
                "incident_id": str(incident.id),
                "capability": payload.capability,
                "status": plan.status,
                "suppression_reason": suppression_reason,
            },
        )
        await self._session.commit()
        return await self.get(plan.id)

    async def send(self, plan_id: UUID, *, actor: str, trace_id: str) -> NotificationPlan:
        plan = await self.get(plan_id)
        if plan.status == NotificationStatus.SUPPRESSED.value:
            raise NotificationPolicyViolation("Suppressed Notification Plans cannot be sent")
        if plan.status != NotificationStatus.PLANNED.value:
            raise NotificationConflict("Notification Plan is not ready for sending")
        specification = NotificationPlanSpec.model_validate(plan.plan)
        context = self._context(plan, trace_id=trace_id, actor=actor)
        execution = NotificationExecution(
            plan_id=plan.id,
            plugin_id=plan.plugin_id,
            status=NotificationStatus.RUNNING.value,
            verification_status="PENDING",
            result={},
            duration_ms=0,
            message="",
            started_at=datetime.now(UTC),
        )
        plan.status = NotificationStatus.RUNNING.value
        self._session.add(execution)
        await self._session.flush()
        await self._publish(
            EventType.NOTIFICATION_EXECUTION_STARTED,
            plan.id,
            trace_id,
            actor,
            {"execution_id": str(execution.id)},
        )
        execution_error: Exception | None = None
        try:
            result = await self._runtime.execute(specification, context, self._policy_for(plan))
            execution.status = (
                NotificationStatus.SENT.value if result.success else NotificationStatus.FAILED.value
            )
            execution.verification_status = result.verification.status
            execution.external_reference = result.verification.external_reference
            execution.result = result.model_dump(mode="json")
            execution.duration_ms = result.duration_ms
            execution.message = result.message
            plan.status = (
                NotificationStatus.VERIFIED.value
                if result.success and result.verification.verified
                else NotificationStatus.FAILED.value
            )
            await self._persist_evidence(plan, execution.id, result.evidence)
            await self._publish(
                EventType.NOTIFICATION_VERIFIED,
                plan.id,
                trace_id,
                actor,
                {
                    "execution_id": str(execution.id),
                    "verified": result.verification.verified,
                },
            )
        except Exception as error:
            execution_error = error
            execution.status = NotificationStatus.FAILED.value
            execution.verification_status = "FAILED"
            execution.message = str(error)
            plan.status = NotificationStatus.FAILED.value
            await self._publish(
                EventType.NOTIFICATION_EXECUTION_FAILED,
                plan.id,
                trace_id,
                actor,
                {"execution_id": str(execution.id)},
                error=str(error),
            )
        finally:
            execution.finished_at = datetime.now(UTC)
        await self._session.commit()
        if execution_error is not None:
            raise execution_error
        return await self.get(plan.id)

    async def get(self, plan_id: UUID) -> NotificationPlan:
        plan = await self._plans.get(plan_id)
        if plan is None:
            raise NotificationNotFound(f"Notification Plan {plan_id} not found")
        return plan

    async def list(
        self,
        *,
        incident_id: UUID | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[NotificationPlan]:
        return await self._plans.search(
            incident_id=incident_id, status=status, page=page, page_size=page_size
        )

    async def list_plugins(self) -> list[NotificationPluginRead]:
        await self.bootstrap()
        await self._session.commit()
        return [
            NotificationPluginRead.model_validate(item)
            for item in await self._plugins.list_enabled()
        ]

    async def create_ticket(self, payload: TicketCreate, *, trace_id: str) -> Ticket:
        if payload.incident_id is not None:
            await self._require_incident(payload.incident_id)
        ticket = Ticket(
            incident_id=payload.incident_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority.value,
            status=payload.status.value,
            external_reference=payload.external_reference,
            labels=payload.labels,
            created_by=payload.created_by,
        )
        self._session.add(ticket)
        await self._session.flush()
        await self._publish(
            EventType.TICKET_CREATED,
            ticket.id,
            trace_id,
            payload.created_by,
            {"incident_id": str(payload.incident_id) if payload.incident_id else None},
            resource="ticket",
        )
        await self._session.commit()
        return ticket

    async def list_tickets(
        self, *, status: str | None, page: int, page_size: int
    ) -> PageResult[Ticket]:
        return await self._tickets.search(status=status, page=page, page_size=page_size)

    async def close_ticket(self, ticket_id: UUID, *, actor: str, trace_id: str) -> Ticket:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            raise NotificationNotFound(f"Ticket {ticket_id} not found")
        if ticket.status == TicketStatus.CLOSED.value:
            return ticket
        ticket.status = TicketStatus.CLOSED.value
        await self._publish(
            EventType.TICKET_CLOSED,
            ticket.id,
            trace_id,
            actor,
            {"incident_id": str(ticket.incident_id) if ticket.incident_id else None},
            resource="ticket",
        )
        await self._session.commit()
        return ticket

    @staticmethod
    def to_read(plan: NotificationPlan) -> NotificationRead:
        return NotificationRead(
            id=plan.id,
            incident_id=plan.incident_id,
            response_plan_id=plan.response_plan_id,
            plugin_id=plan.plugin_id,
            template_id=plan.template_id,
            capability=plan.capability,
            recipient_group=plan.recipient_group,
            recipients=plan.recipients,
            severity=plan.severity,
            priority=plan.priority,
            status=plan.status,
            requested_by=plan.requested_by,
            deduplication_key=plan.deduplication_key,
            suppression_reason=plan.suppression_reason,
            policy_snapshot=plan.policy_snapshot,
            plan=plan.plan,
            executions=plan.executions,
            evidence=plan.evidence,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def ticket_to_read(ticket: Ticket) -> TicketRead:
        return TicketRead.model_validate(ticket)

    async def _require_incident(self, incident_id: UUID) -> Incident:
        incident = await self._session.get(Incident, incident_id)
        if incident is None:
            raise NotificationValidationError(f"Incident {incident_id} not found")
        return incident

    async def _require_response(self, response_plan_id: UUID | None, incident_id: UUID) -> None:
        if response_plan_id is None:
            return
        response = await self._session.get(ResponsePlan, response_plan_id)
        if response is None or response.incident_id != incident_id:
            raise NotificationValidationError(
                "Notification references an unknown or unrelated Response Plan"
            )

    def _context(
        self, plan: NotificationPlan, *, trace_id: str, actor: str
    ) -> NotificationPluginContext:
        plugin = self._registry.require(plan.plugin.name)
        specification = NotificationPlanSpec.model_validate(plan.plan)
        return NotificationPluginContext(
            notification_plan_id=plan.id,
            incident_id=plan.incident_id,
            response_plan_id=plan.response_plan_id,
            trace_id=trace_id,
            actor=actor,
            capability=plan.capability,
            recipients=tuple(plan.recipients),
            variables=readonly_mapping(specification.variables),
            granted_permissions=frozenset(plugin.permissions),
        )

    @staticmethod
    def _policy_for(plan: NotificationPlan) -> NotificationPolicy:
        return NotificationPolicy.model_validate(plan.policy_snapshot)

    async def _persist_evidence(
        self, plan: NotificationPlan, execution_id: UUID, evidence: list[object]
    ) -> None:
        from app.schemas.notification import NotificationEvidenceItem

        for raw in evidence:
            item = NotificationEvidenceItem.model_validate(raw)
            self._session.add(
                NotificationEvidence(
                    plan_id=plan.id,
                    execution_id=execution_id,
                    evidence_type=item.evidence_type,
                    sha256=item.sha256,
                    reference=item.reference,
                    metadata_=item.metadata,
                )
            )
        await self._session.flush()

    @staticmethod
    def _deduplication_key(payload: NotificationCreate) -> str:
        return ":".join(
            (
                str(payload.incident_id),
                payload.capability,
                payload.recipient_group or "auto",
                payload.template_name or "auto",
            )
        )

    async def _publish(
        self,
        event_type: EventType,
        aggregate_id: UUID,
        trace_id: str,
        actor: str,
        payload: dict[str, object],
        *,
        error: str | None = None,
        resource: str = "notification",
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                aggregate_id=aggregate_id,
                trace_id=trace_id,
                actor=actor,
                resource=resource,
                payload=payload,
                error=error,
            )
        )


def default_templates() -> tuple[TemplateDefinition, ...]:
    return (
        TemplateDefinition(
            name="default-text",
            format=TemplateFormat.TEXT,
            subject="CAP Incident {{incident_title}}",
            body="Incident {{incident_id}} severity {{severity}} requires attention.",
            variables=frozenset({"incident_title", "incident_id", "severity"}),
        ),
    )
