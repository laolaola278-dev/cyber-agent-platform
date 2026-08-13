"""Notification and Ticket Framework HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.dependencies import NotificationServiceDependency
from app.schemas.common import PageResponse
from app.schemas.notification import (
    NotificationCreate,
    NotificationPluginRead,
    NotificationRead,
    NotificationStatus,
    TicketCreate,
    TicketRead,
    TicketStatus,
)

router = APIRouter(tags=["notification"])


@router.post("/notifications", response_model=NotificationRead, status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: NotificationCreate,
    request: Request,
    service: NotificationServiceDependency,
) -> NotificationRead:
    plan = await service.create(payload, trace_id=request.state.request_id)
    if plan.status == NotificationStatus.PLANNED.value:
        plan = await service.send(
            plan.id, actor=payload.requested_by, trace_id=request.state.request_id
        )
    return service.to_read(plan)


@router.get("/notifications", response_model=PageResponse[NotificationRead])
async def list_notifications(
    service: NotificationServiceDependency,
    incident_id: UUID | None = None,
    notification_status: NotificationStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[NotificationRead]:
    result = await service.list(
        incident_id=incident_id,
        status=notification_status.value if notification_status else None,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/notifications/{notification_id}", response_model=NotificationRead)
async def get_notification(
    notification_id: UUID, service: NotificationServiceDependency
) -> NotificationRead:
    return service.to_read(await service.get(notification_id))


@router.get("/notification/plugins", response_model=list[NotificationPluginRead])
async def list_notification_plugins(
    service: NotificationServiceDependency,
) -> list[NotificationPluginRead]:
    return await service.list_plugins()


@router.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    request: Request,
    service: NotificationServiceDependency,
) -> TicketRead:
    return service.ticket_to_read(
        await service.create_ticket(payload, trace_id=request.state.request_id)
    )


@router.get("/tickets", response_model=PageResponse[TicketRead])
async def list_tickets(
    service: NotificationServiceDependency,
    ticket_status: TicketStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[TicketRead]:
    result = await service.list_tickets(
        status=ticket_status.value if ticket_status else None,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.ticket_to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
