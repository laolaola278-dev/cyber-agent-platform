"""Event-driven audit persistence tests."""

from sqlalchemy import select

from app.events import EventType, InMemoryEventBus, PlatformEvent
from app.events.audit import AuditSubscriber
from app.models import AuditLog
from app.repositories import AuditRepository
from app.services.audit import AuditService
from tests.conftest import TestSessionFactory


async def test_audit_subscriber_persists_normalized_event() -> None:
    async with TestSessionFactory() as session:
        bus = InMemoryEventBus()
        AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
        event = PlatformEvent(
            type=EventType.AGENT_REGISTERED,
            trace_id="trace-audit",
            actor="tester",
            resource="agent:test",
            payload={"name": "test"},
            result={"status": "OFFLINE"},
        )
        await bus.publish(event)
        await session.commit()

    async with TestSessionFactory() as session:
        record = await session.scalar(select(AuditLog).where(AuditLog.trace_id == "trace-audit"))
        assert record is not None
        assert record.action == "AgentRegistered"
        assert record.operator == "tester"
        assert record.result == {"status": "OFFLINE"}
