"""Unified Response Framework HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.auth.rbac import require_permission
from app.dependencies import ResponseServiceDependency
from app.schemas.common import PageResponse
from app.schemas.response import (
    ApprovalState,
    ResponseApprovalCreate,
    ResponseExecutionRequest,
    ResponseExecutionState,
    ResponsePlanCreate,
    ResponsePlanRead,
    ResponsePluginRead,
    ResponseRejectionCreate,
    ResponseRollbackRequest,
)

router = APIRouter(prefix="/response", tags=["response"])


@router.post(
    "/plans",
    response_model=ResponsePlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("response.plan"))],
)
async def create_response_plan(
    payload: ResponsePlanCreate,
    request: Request,
    service: ResponseServiceDependency,
) -> ResponsePlanRead:
    return service.to_read(await service.create(payload, trace_id=request.state.request_id))


@router.get("/plans", response_model=PageResponse[ResponsePlanRead])
async def list_response_plans(
    service: ResponseServiceDependency,
    incident_id: UUID | None = None,
    approval_state: ApprovalState | None = None,
    execution_state: ResponseExecutionState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[ResponsePlanRead]:
    result = await service.list(
        incident_id=incident_id,
        approval_state=approval_state.value if approval_state else None,
        execution_state=execution_state.value if execution_state else None,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/plans/{plan_id}", response_model=ResponsePlanRead)
async def get_response_plan(plan_id: UUID, service: ResponseServiceDependency) -> ResponsePlanRead:
    return service.to_read(await service.get(plan_id))


@router.post(
    "/plans/{plan_id}/approve",
    response_model=ResponsePlanRead,
    dependencies=[Depends(require_permission("approval.decide"))],
)
async def approve_response_plan(
    plan_id: UUID,
    payload: ResponseApprovalCreate,
    request: Request,
    service: ResponseServiceDependency,
) -> ResponsePlanRead:
    return service.to_read(
        await service.approve(plan_id, payload, trace_id=request.state.request_id)
    )


@router.post(
    "/plans/{plan_id}/reject",
    response_model=ResponsePlanRead,
    dependencies=[Depends(require_permission("approval.decide"))],
)
async def reject_response_plan(
    plan_id: UUID,
    payload: ResponseRejectionCreate,
    request: Request,
    service: ResponseServiceDependency,
) -> ResponsePlanRead:
    return service.to_read(
        await service.reject(plan_id, payload, trace_id=request.state.request_id)
    )


@router.post(
    "/plans/{plan_id}/execute",
    response_model=ResponsePlanRead,
    dependencies=[Depends(require_permission("response.execute"))],
)
async def execute_response_plan(
    plan_id: UUID,
    payload: ResponseExecutionRequest,
    request: Request,
    service: ResponseServiceDependency,
) -> ResponsePlanRead:
    return service.to_read(
        await service.execute(plan_id, payload, trace_id=request.state.request_id)
    )


@router.post(
    "/plans/{plan_id}/rollback",
    response_model=ResponsePlanRead,
    dependencies=[Depends(require_permission("response.rollback"))],
)
async def rollback_response_plan(
    plan_id: UUID,
    payload: ResponseRollbackRequest,
    request: Request,
    service: ResponseServiceDependency,
) -> ResponsePlanRead:
    return service.to_read(
        await service.rollback(plan_id, payload, trace_id=request.state.request_id)
    )


@router.get("/plugins", response_model=list[ResponsePluginRead])
async def list_response_plugins(
    service: ResponseServiceDependency,
) -> list[ResponsePluginRead]:
    return await service.list_plugins()
