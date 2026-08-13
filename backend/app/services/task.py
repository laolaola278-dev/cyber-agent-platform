"""Task application service and orchestration entry point."""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TaskStatus
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import AssetNotFound, TaskNotFound
from app.models import Asset, Task
from app.orchestrator.dispatcher import TaskDispatcher
from app.repositories import PageResult, TaskRepository
from app.runtime.service import RuntimeService
from app.schemas import TaskCreate


class TaskService:
    """Create, retrieve, and dispatch tasks through injected boundaries."""

    def __init__(
        self,
        session: AsyncSession,
        repository: TaskRepository,
        publisher: EventPublisher,
        dispatcher: TaskDispatcher,
        runtime_service: "RuntimeService | None" = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._publisher = publisher
        self._dispatcher = dispatcher
        self._runtime_service = runtime_service

    async def list_tasks(self, *, page: int = 1, page_size: int = 100) -> PageResult[Task]:
        return await self._repository.list_page(page=page, page_size=page_size)

    async def get_task(self, task_id: UUID) -> Task:
        task = await self._repository.get(task_id)
        if task is None:
            raise TaskNotFound(f"Task {task_id} not found")
        return task

    async def create_task(self, payload: TaskCreate, *, trace_id: str | None = None) -> Task:
        active_trace_id = trace_id or str(uuid4())
        await self._require_asset(payload.asset_id)
        task = Task(**payload.model_dump(), status=TaskStatus.CREATED.value)
        await self._repository.add(task)
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.TASK_CREATED,
                trace_id=active_trace_id,
                aggregate_id=task.id,
                actor="api-user",
                resource=f"task:{task.id}",
                task_id=task.id,
                agent_id=task.target_agent_id,
                payload={"task_type": task.task_type},
                result={"status": task.status},
            )
        )
        execution = await self._dispatcher.dispatch(task, trace_id=active_trace_id)
        if task.task_type == "data-acquisition":
            await self._dispatcher.execute(execution, task, trace_id=active_trace_id)
        await self._session.commit()
        await self._session.refresh(task)
        return task

    async def execute_capability(
        self,
        capability: str,
        payload: dict[str, object],
        *,
        trace_id: str,
        asset_id: UUID | None = None,
    ) -> dict[str, object]:
        """Submit one workflow node through Dispatcher and Runtime by capability."""

        await self._require_asset(asset_id)
        task = Task(
            name=f"Workflow capability: {capability}",
            task_type="workflow-capability",
            input=payload,
            required_permissions=[],
            required_capabilities=[capability],
            asset_id=asset_id,
            status=TaskStatus.CREATED.value,
        )
        await self._repository.add(task)
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.TASK_CREATED,
                trace_id=trace_id,
                aggregate_id=task.id,
                actor="workflow-runtime",
                resource=f"task:{task.id}",
                task_id=task.id,
                payload={"task_type": task.task_type, "capability": capability},
                result={"status": task.status},
            )
        )
        execution = await self._dispatcher.dispatch(task, trace_id=trace_id)
        finished = await self._dispatcher.execute(execution, task, trace_id=trace_id)
        await self._session.commit()
        return finished.result or {"success": finished.status == TaskStatus.SUCCESS.value}

    async def _require_asset(self, asset_id: UUID | None) -> None:
        if asset_id is None:
            return
        asset = await self._session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise AssetNotFound(f"Asset {asset_id} not found")

    async def create_data_acquisition_task(self, url: str, *, trace_id: str | None = None) -> Task:
        """Create the Phase 2 public-page task with fixed least-privilege permissions."""

        if self._runtime_service is None:
            raise RuntimeError("RuntimeService is required for data acquisition")
        active_trace_id = trace_id or str(uuid4())
        agent = await self._runtime_service.ensure_data_acquisition_agent(trace_id=active_trace_id)
        return await self.create_task(
            TaskCreate(
                name=f"Data acquisition: {url}",
                task_type="data-acquisition",
                input={"url": url},
                required_permissions=[
                    "crawl.public",
                    "tool.playwright",
                    "evidence.write",
                    "report.write",
                ],
                required_capabilities=[
                    "crawl.html",
                    "browser.render",
                    "evidence.generate",
                ],
                target_agent_id=agent.id,
            ),
            trace_id=active_trace_id,
        )
