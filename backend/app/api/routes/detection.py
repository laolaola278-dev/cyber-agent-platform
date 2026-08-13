"""Detection Framework HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.enums import FindingSeverity, SecurityEventStatus
from app.dependencies import DetectionServiceDependency
from app.schemas import (
    DetectionCapabilityRead,
    DetectionPluginRead,
    DetectionTaskCreate,
    DetectionTaskRead,
    SecurityEventRead,
)
from app.schemas.common import PageResponse

router = APIRouter(prefix="/detection", tags=["detection"])


@router.post("/tasks", response_model=DetectionTaskRead, status_code=status.HTTP_201_CREATED)
async def create_detection_task(
    payload: DetectionTaskCreate,
    request: Request,
    service: DetectionServiceDependency,
) -> DetectionTaskRead:
    return DetectionTaskRead.model_validate(
        await service.create(payload, trace_id=request.state.request_id)
    )


@router.get("/tasks", response_model=PageResponse[DetectionTaskRead])
async def list_detection_tasks(
    service: DetectionServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[DetectionTaskRead]:
    result = await service.list_tasks(page=page, page_size=page_size)
    return PageResponse(
        items=[DetectionTaskRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/events", response_model=PageResponse[SecurityEventRead])
async def list_security_events(
    service: DetectionServiceDependency,
    severity: FindingSeverity | None = None,
    event_status: SecurityEventStatus | None = Query(default=None, alias="status"),
    asset_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[SecurityEventRead]:
    result = await service.list_events(
        severity=severity.value if severity else None,
        status=event_status.value if event_status else None,
        asset_id=asset_id,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_event_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/events/{event_id}", response_model=SecurityEventRead)
async def get_security_event(
    event_id: UUID, service: DetectionServiceDependency
) -> SecurityEventRead:
    return service.to_event_read(await service.get_event(event_id))


@router.get("/plugins", response_model=list[DetectionPluginRead])
async def list_detection_plugins(
    service: DetectionServiceDependency,
) -> list[DetectionPluginRead]:
    return await service.list_plugins()


@router.get("/capabilities", response_model=list[DetectionCapabilityRead])
async def list_detection_capabilities(
    service: DetectionServiceDependency,
) -> list[DetectionCapabilityRead]:
    return await service.list_capabilities()
