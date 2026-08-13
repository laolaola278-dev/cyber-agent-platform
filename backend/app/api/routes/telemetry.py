"""Telemetry and stream control-plane HTTP API."""

from fastapi import APIRouter, Query, Request, status

from app.dependencies import TelemetryServiceDependency
from app.schemas.common import PageResponse
from app.schemas.telemetry import (
    TelemetryCheckpointRead,
    TelemetryReplayRead,
    TelemetryReplayRequest,
    TelemetryRuntimeRead,
    TelemetryTaskCreate,
    TelemetryTaskRead,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/tasks", response_model=TelemetryTaskRead, status_code=status.HTTP_201_CREATED)
async def create_telemetry_task(
    payload: TelemetryTaskCreate,
    request: Request,
    service: TelemetryServiceDependency,
) -> TelemetryTaskRead:
    return TelemetryTaskRead.model_validate(
        await service.create(payload, trace_id=request.state.request_id)
    )


@router.get("/tasks", response_model=PageResponse[TelemetryTaskRead])
async def list_telemetry_tasks(
    service: TelemetryServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[TelemetryTaskRead]:
    result = await service.list_tasks(page=page, page_size=page_size)
    return PageResponse(
        items=[TelemetryTaskRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/runtime", response_model=TelemetryRuntimeRead)
async def get_telemetry_runtime(
    service: TelemetryServiceDependency,
) -> TelemetryRuntimeRead:
    return await service.runtime_status()


@router.get("/checkpoints", response_model=list[TelemetryCheckpointRead])
async def list_telemetry_checkpoints(
    service: TelemetryServiceDependency,
) -> list[TelemetryCheckpointRead]:
    return [
        TelemetryCheckpointRead.model_validate(item) for item in await service.list_checkpoints()
    ]


@router.post("/replay", response_model=TelemetryReplayRead)
async def replay_telemetry(
    payload: TelemetryReplayRequest,
    request: Request,
    service: TelemetryServiceDependency,
) -> TelemetryReplayRead:
    return await service.replay(payload, trace_id=request.state.request_id)
