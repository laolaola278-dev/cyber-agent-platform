"""Task and execution persistence operations."""

from uuid import UUID

from sqlalchemy import select

from app.models import ExecutionLog, Task, TaskExecution, TaskLog
from app.repositories.base import SQLAlchemyRepository


class TaskRepository(SQLAlchemyRepository[Task]):
    model = Task

    async def add_log(self, entry: TaskLog) -> TaskLog:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def add_execution(self, execution: TaskExecution) -> TaskExecution:
        self.session.add(execution)
        await self.session.flush()
        await self.session.refresh(execution)
        return execution

    async def add_execution_log(self, entry: ExecutionLog) -> ExecutionLog:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def latest_execution(self, task_id: UUID) -> TaskExecution | None:
        statement = (
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id)
            .order_by(TaskExecution.start_time.desc().nullslast())
            .limit(1)
        )
        return await self.session.scalar(statement)
