"""Transactional platform-event to audit-log persistence."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.contracts import PlatformEvent
from app.models.audit_log import AuditLog
from app.repositories.audit import AuditRepository


async def publish_audit(session: AsyncSession, event: PlatformEvent) -> None:
    """Persist one normalized audit record inside the caller transaction."""

    await AuditRepository(session).add(
        AuditLog(
            operator=event.actor,
            action=event.type.value,
            resource=event.resource,
            trace_id=event.trace_id,
            agent_id=str(event.agent_id) if event.agent_id else None,
            task_id=str(event.task_id) if event.task_id else None,
            tool_id=str(event.tool_id) if event.tool_id else None,
            details=event.payload,
            result=event.result,
            error=event.error,
        )
    )
