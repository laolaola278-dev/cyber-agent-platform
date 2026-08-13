"""Read-only productization projections over existing domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AssessmentPlugin,
    Asset,
    AuditLog,
    DetectionPlugin,
    Finding,
    Incident,
    NotificationExecution,
    NotificationPlugin,
    PlaybookExecution,
    ResponseExecution,
    ResponsePlan,
    ResponsePlugin,
    SecurityEvent,
    Worker,
)
from app.schemas.productization import (
    ApprovalCenterItem,
    AuditEventRead,
    AuditPageRead,
    DashboardCounts,
    DashboardExecutionSummary,
    DashboardRead,
    PluginInventoryItem,
    PluginSummary,
    WorkerSummary,
)


async def _count(session: AsyncSession, model: type[Any], *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int((await session.execute(statement)).scalar_one())


def _rate(success: int, total: int) -> float:
    return round(success / total, 4) if total else 0.0


class ProductizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def dashboard(self) -> DashboardRead:
        asset_count = await _count(self.session, Asset)
        incident_count = await _count(self.session, Incident)
        event_count = await _count(self.session, SecurityEvent)
        finding_count = await _count(self.session, Finding)

        playbook_total = await _count(self.session, PlaybookExecution)
        playbook_succeeded = await _count(
            self.session, PlaybookExecution, PlaybookExecution.status == "SUCCEEDED"
        )
        playbook_failed = await _count(
            self.session,
            PlaybookExecution,
            PlaybookExecution.status.in_(
                {"FAILED", "COMPENSATION_FAILED", "TIMED_OUT", "CANCELLED"}
            ),
        )
        playbook_waiting = await _count(
            self.session, PlaybookExecution, PlaybookExecution.status == "WAITING_APPROVAL"
        )

        workers = list((await self.session.scalars(select(Worker))).all())
        worker_capacity = sum(worker.max_concurrency for worker in workers)
        worker_active = sum(worker.active_executions for worker in workers)
        healthy_workers = sum(worker.status in {"ONLINE", "HEALTHY"} for worker in workers)

        plugin_models = (AssessmentPlugin, DetectionPlugin, ResponsePlugin, NotificationPlugin)
        plugin_total = plugin_enabled = plugin_healthy = 0
        for model in plugin_models:
            plugin_total += await _count(self.session, model)
            plugin_enabled += await _count(self.session, model, model.enabled.is_(True))
            if hasattr(model, "health_status"):
                plugin_healthy += await _count(
                    self.session, model, model.health_status.in_({"HEALTHY", "OK"})
                )

        response_total = await _count(self.session, ResponseExecution)
        response_succeeded = await _count(
            self.session, ResponseExecution, ResponseExecution.status.in_({"SUCCEEDED", "VERIFIED"})
        )
        response_failed = await _count(
            self.session, ResponseExecution, ResponseExecution.status == "FAILED"
        )

        notification_total = await _count(self.session, NotificationExecution)
        notification_succeeded = await _count(
            self.session,
            NotificationExecution,
            NotificationExecution.status.in_({"SENT", "VERIFIED", "SUCCEEDED"}),
        )
        notification_failed = await _count(
            self.session, NotificationExecution, NotificationExecution.status == "FAILED"
        )

        return DashboardRead(
            counts=DashboardCounts(
                assets=asset_count,
                incidents=incident_count,
                security_events=event_count,
                findings=finding_count,
            ),
            playbooks=DashboardExecutionSummary(
                total=playbook_total,
                succeeded=playbook_succeeded,
                failed=playbook_failed,
                waiting_approval=playbook_waiting,
                success_rate=_rate(playbook_succeeded, playbook_total),
            ),
            workers=WorkerSummary(
                total=len(workers),
                healthy=healthy_workers,
                active_executions=worker_active,
                capacity=worker_capacity,
                utilization=_rate(worker_active, worker_capacity),
            ),
            plugins=PluginSummary(
                total=plugin_total,
                healthy=plugin_healthy,
                enabled=plugin_enabled,
            ),
            responses=DashboardExecutionSummary(
                total=response_total,
                succeeded=response_succeeded,
                failed=response_failed,
                success_rate=_rate(response_succeeded, response_total),
            ),
            notifications=DashboardExecutionSummary(
                total=notification_total,
                succeeded=notification_succeeded,
                failed=notification_failed,
                success_rate=_rate(notification_succeeded, notification_total),
            ),
        )

    async def audit(
        self,
        *,
        operator: str | None,
        event_type: str | None,
        resource: str | None,
        plugin: str | None,
        incident: str | None,
        worker: str | None,
        start: datetime | None,
        end: datetime | None,
        page: int,
        page_size: int,
    ) -> AuditPageRead:
        filters = []
        if operator:
            filters.append(AuditLog.operator == operator)
        if event_type:
            filters.append(AuditLog.action == event_type)
        if resource:
            filters.append(AuditLog.resource.contains(resource))
        if plugin:
            filters.append(AuditLog.resource.contains(plugin))
        if incident:
            filters.append(AuditLog.resource.contains(incident))
        if worker:
            filters.append(AuditLog.resource.contains(worker))
        if start:
            filters.append(AuditLog.timestamp >= start)
        if end:
            filters.append(AuditLog.timestamp <= end)

        count_statement = select(func.count()).select_from(AuditLog).where(*filters)
        total = int((await self.session.execute(count_statement)).scalar_one())
        statement: Select[tuple[AuditLog]] = (
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list((await self.session.scalars(statement)).all())
        return AuditPageRead(
            items=[AuditEventRead.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
        )

    async def plugins(self) -> list[PluginInventoryItem]:
        inventory: list[PluginInventoryItem] = []
        for domain, model in (
            ("assessment", AssessmentPlugin),
            ("detection", DetectionPlugin),
            ("response", ResponsePlugin),
            ("notification", NotificationPlugin),
        ):
            statement = select(model).order_by(model.name)
            if domain in {"assessment", "detection"}:
                statement = statement.options(selectinload(model.capabilities))
            items = (await self.session.scalars(statement)).all()
            for item in items:
                raw_capabilities = list(item.capabilities)
                capabilities = [
                    (
                        capability.capability.name
                        if hasattr(capability, "capability")
                        else str(capability)
                    )
                    for capability in raw_capabilities
                ]
                inventory.append(
                    PluginInventoryItem(
                        id=item.id,
                        domain=domain,
                        name=item.name,
                        version=item.version,
                        enabled=item.enabled,
                        health_status=getattr(item, "health_status", "UNKNOWN"),
                        capabilities=capabilities,
                        certified=getattr(item, "certified", False),
                        sandbox_compatible=getattr(item, "sandbox_compatible", False),
                    )
                )
        return inventory

    async def approvals(self) -> list[ApprovalCenterItem]:
        plans = (
            await self.session.scalars(
                select(ResponsePlan)
                .options(selectinload(ResponsePlan.approvals))
                .order_by(ResponsePlan.created_at.desc())
            )
        ).all()
        result = []
        for plan in plans:
            approval = max(plan.approvals, key=lambda item: item.decided_at, default=None)
            result.append(
                ApprovalCenterItem(
                    plan_id=plan.id,
                    incident_id=plan.incident_id,
                    capability=plan.target_capability,
                    requested_by=plan.requested_by,
                    risk_level=plan.risk_level,
                    approval_state=plan.approval_state,
                    execution_state=plan.execution_state,
                    rollback_state=plan.rollback_state,
                    expires_at=plan.expires_at,
                    approver=approval.approver if approval else None,
                    decision=approval.decision if approval else None,
                    comment=approval.comment if approval else None,
                    decided_at=approval.decided_at if approval else None,
                )
            )
        return result
