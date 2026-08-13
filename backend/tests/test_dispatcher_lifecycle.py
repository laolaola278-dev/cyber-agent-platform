"""Direct Dispatcher lifecycle and failure-path tests."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import ConfigurationProvider
from app.core.enums import TaskStatus
from app.events import EventType, InMemoryEventBus
from app.exceptions import PermissionDenied, RegistryError
from app.models import Agent, Task, TaskExecution
from app.orchestrator import FirstAvailableStrategy, TaskDispatcher
from app.repositories import AgentRepository, TaskRepository
from tests.conftest import TestSessionFactory

CONFIG_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "config"


async def _dispatcher(session, bus: InMemoryEventBus) -> TaskDispatcher:
    config = ConfigurationProvider(CONFIG_DIR)
    config.load()
    return TaskDispatcher(
        session,
        TaskRepository(session),
        AgentRepository(session),
        bus,
        FirstAvailableStrategy(),
        config.orchestrator,
        config.registry,
    )


async def _capture(events: list[object], event: object) -> None:
    events.append(event)


async def test_dispatcher_runs_and_finishes_execution() -> None:
    async with TestSessionFactory() as session:
        agent = Agent(
            name="lifecycle-agent",
            version="1",
            author="test",
            status="ONLINE",
            permissions=["task:execute"],
            heartbeat_time=datetime.now(UTC),
        )
        task = Task(
            name="lifecycle",
            task_type="test",
            required_permissions=["task:execute"],
            status="CREATED",
        )
        session.add_all([agent, task])
        await session.flush()
        events = []
        bus = InMemoryEventBus()
        bus.subscribe(EventType.TASK_STATE_CHANGED, lambda event: _capture(events, event))
        dispatcher = await _dispatcher(session, bus)

        execution = await dispatcher.dispatch(task, trace_id="trace-life")
        await dispatcher.mark_running(execution, trace_id="trace-life")
        await dispatcher.mark_finished(
            execution, success=True, result={"ok": True}, trace_id="trace-life"
        )
        assert task.status == TaskStatus.SUCCESS
        assert execution.status == TaskStatus.SUCCESS
        assert len(events) == 3


async def test_dispatcher_rejects_target_without_permission() -> None:
    async with TestSessionFactory() as session:
        agent = Agent(
            name="permission-agent",
            version="1",
            author="test",
            status="ONLINE",
            permissions=[],
            heartbeat_time=datetime.now(UTC),
        )
        task = Task(
            name="permission",
            task_type="test",
            required_permissions=["task:execute"],
            status="CREATED",
        )
        session.add_all([agent, task])
        await session.flush()
        task.target_agent_id = agent.id
        events = []
        bus = InMemoryEventBus()
        bus.subscribe(EventType.PERMISSION_REJECTED, lambda event: _capture(events, event))

        with __import__("pytest").raises(PermissionDenied):
            await (await _dispatcher(session, bus)).dispatch(task, trace_id="trace-denied")
        assert events[0].agent_id == agent.id


async def test_dispatcher_reports_no_available_agent() -> None:
    async with TestSessionFactory() as session:
        task = Task(name="unavailable", task_type="test", status="CREATED")
        session.add(task)
        await session.flush()
        events = []
        bus = InMemoryEventBus()
        bus.subscribe(EventType.DISPATCH_FAILED, lambda event: _capture(events, event))

        with __import__("pytest").raises(RegistryError):
            await (await _dispatcher(session, bus)).dispatch(task, trace_id="trace-failed")
        assert events[0].error == "No eligible Agent found for task"


async def test_latest_execution_returns_newest_attempt() -> None:
    async with TestSessionFactory() as session:
        agent = Agent(name="latest-agent", version="1", author="test", status="ONLINE")
        task = Task(name="latest", task_type="test", status="QUEUED")
        session.add_all([agent, task])
        await session.flush()
        first = TaskExecution(task_id=task.id, agent_id=agent.id, status="QUEUED", trace_id="first")
        second = TaskExecution(
            task_id=task.id, agent_id=agent.id, status="QUEUED", trace_id="second"
        )
        session.add_all([first, second])
        await session.flush()
        await session.execute(select(TaskExecution).options(selectinload(TaskExecution.task)))
        latest = await TaskRepository(session).latest_execution(task.id)
        assert latest is not None
