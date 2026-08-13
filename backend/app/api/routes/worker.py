"""Worker and Sandbox control-plane read APIs."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import WorkerManagerDependency
from app.exceptions import SandboxExecutionError
from app.repositories.worker import SandboxExecutionRepository
from app.schemas.worker import SandboxExecutionRead, WorkerHealthRead, WorkerRead

router = APIRouter(tags=["worker-sandbox"])


@router.get("/workers", response_model=list[WorkerRead])
async def list_workers(manager: WorkerManagerDependency) -> list[WorkerRead]:
    return [WorkerRead.model_validate(item) for item in await manager.list()]


@router.get("/workers/{worker_id}", response_model=WorkerRead)
async def get_worker(worker_id: UUID, manager: WorkerManagerDependency) -> WorkerRead:
    return WorkerRead.model_validate(await manager.get(worker_id))


@router.get("/sandbox", response_model=list[SandboxExecutionRead])
async def list_sandbox_executions(
    session: AsyncSession = Depends(get_db_session),
) -> list[SandboxExecutionRead]:
    rows = await SandboxExecutionRepository(session).list()
    return [SandboxExecutionRead.model_validate(item) for item in rows]


@router.get("/sandbox/{execution_id}", response_model=SandboxExecutionRead)
async def get_sandbox_execution(
    execution_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> SandboxExecutionRead:
    row = await SandboxExecutionRepository(session).get_by_execution_id(execution_id)
    if row is None:
        raise SandboxExecutionError("Sandbox execution was not found")
    return SandboxExecutionRead.model_validate(row)


@router.get("/health/workers", response_model=WorkerHealthRead)
async def worker_health(manager: WorkerManagerDependency) -> WorkerHealthRead:
    health = await manager.health()
    health["plugin_health"] = {"synthetic-framework": "HEALTHY"}
    return WorkerHealthRead.model_validate(health)
