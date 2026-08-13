"""Task management HTTP endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.dependencies import TaskServiceDependency
from app.schemas import DataAcquisitionRequest, TaskCreate, TaskRead
from app.schemas.common import PageResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=PageResponse[TaskRead], summary="List platform tasks")
async def list_tasks(
    service: TaskServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[TaskRead]:
    result = await service.list_tasks(page=page, page_size=page_size)
    return PageResponse(
        items=[TaskRead.model_validate(task) for task in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: UUID, service: TaskServiceDependency) -> TaskRead:
    return TaskRead.model_validate(await service.get_task(task_id))


@router.post("/data-acquisition", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_data_acquisition_task(
    payload: DataAcquisitionRequest, request: Request, service: TaskServiceDependency
) -> TaskRead:
    return TaskRead.model_validate(
        await service.create_data_acquisition_task(
            str(payload.url), trace_id=request.state.request_id
        )
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate, request: Request, service: TaskServiceDependency
) -> TaskRead:
    return TaskRead.model_validate(
        await service.create_task(payload, trace_id=request.state.request_id)
    )
