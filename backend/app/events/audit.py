"""Subscriber that persists every governance-relevant platform event."""

from app.events import EventSubscriber, EventType, PlatformEvent
from app.services.audit import AuditService


class AuditSubscriber:
    """Translate platform events into normalized immutable audit records."""

    def __init__(self, service: AuditService) -> None:
        self._service = service

    def register(self, subscriber: EventSubscriber) -> None:
        for event_type in EventType:
            subscriber.subscribe(event_type, self.handle)

    async def handle(self, event: PlatformEvent) -> None:
        await self._service.record(
            operator=event.actor,
            action=event.type.value,
            resource=event.resource,
            trace_id=event.trace_id,
            agent_id=event.agent_id,
            task_id=event.task_id,
            tool_id=event.tool_id,
            details=event.payload,
            result=event.result,
            error=event.error,
        )
