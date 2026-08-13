"""Audit Everything application service."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.repositories.audit import AuditRepository


class AuditService:
    """Append normalized governance records within the caller transaction."""

    def __init__(self, session: AsyncSession, repository: AuditRepository) -> None:
        self._session = session
        self._repository = repository

    async def record(
        self,
        *,
        operator: str,
        action: str,
        resource: str,
        trace_id: str,
        agent_id: UUID | None = None,
        task_id: UUID | None = None,
        tool_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AuditLog:
        return await self._repository.add(
            AuditLog(
                operator=operator,
                action=action,
                resource=resource,
                trace_id=trace_id,
                agent_id=str(agent_id) if agent_id else None,
                task_id=str(task_id) if task_id else None,
                tool_id=str(tool_id) if tool_id else None,
                details=details or {},
                result=result,
                error=error,
            )
        )
