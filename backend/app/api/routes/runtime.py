"""Managed Agent Runtime HTTP endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.dependencies.services import RuntimeServiceDependency, TaskServiceDependency
from app.schemas import RuntimeRead, RuntimeRestartRequest, RuntimeStartRequest

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.post("/start", response_model=RuntimeRead)
async def start_runtime(
    payload: RuntimeStartRequest,
    request: Request,
    service: RuntimeServiceDependency,
    tasks: TaskServiceDependency,
) -> RuntimeRead:
    runtime = await service.start(
        payload.agent_id,
        await tasks.get_task(payload.task_id),
        trace_id=request.state.request_id,
    )
    return RuntimeRead.model_validate(runtime)


@router.post("/stop", response_model=RuntimeRead)
async def stop_runtime(
    runtime_id: UUID, request: Request, service: RuntimeServiceDependency
) -> RuntimeRead:
    return RuntimeRead.model_validate(
        await service.stop(runtime_id, trace_id=request.state.request_id)
    )


@router.post("/restart/{runtime_id}", response_model=RuntimeRead)
async def restart_runtime(
    runtime_id: UUID,
    payload: RuntimeRestartRequest,
    request: Request,
    service: RuntimeServiceDependency,
    tasks: TaskServiceDependency,
) -> RuntimeRead:
    runtime = await service.restart(
        runtime_id,
        await tasks.get_task(payload.task_id),
        trace_id=request.state.request_id,
    )
    return RuntimeRead.model_validate(runtime)


@router.get("/status")
async def runtime_status(
    runtime_id: UUID, request: Request, service: RuntimeServiceDependency
) -> dict[str, object]:
    return await service.health(runtime_id, trace_id=request.state.request_id)


@router.get("/{runtime_id}", response_model=RuntimeRead)
async def get_runtime(runtime_id: UUID, service: RuntimeServiceDependency) -> RuntimeRead:
    return RuntimeRead.model_validate(await service.get(runtime_id))
