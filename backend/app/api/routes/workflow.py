"""Workflow definition, planning, and durable execution HTTP endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.dependencies import WorkflowServiceDependency
from app.schemas import (
    CapabilityPlan,
    WorkflowDefinitionCreate,
    WorkflowDefinitionRead,
    WorkflowInstanceRead,
    WorkflowPlanRequest,
    WorkflowRunCreate,
)
from app.schemas.common import PageResponse

router = APIRouter(prefix="/workflow", tags=["workflow"])


@router.post("", response_model=WorkflowDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowDefinitionCreate,
    request: Request,
    service: WorkflowServiceDependency,
) -> WorkflowDefinitionRead:
    return WorkflowDefinitionRead.model_validate(
        await service.create_definition(payload, trace_id=request.state.request_id)
    )


@router.get("", response_model=PageResponse[WorkflowDefinitionRead])
async def list_workflows(
    service: WorkflowServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[WorkflowDefinitionRead]:
    result = await service.list_definitions(page=page, page_size=page_size)
    return PageResponse(
        items=[WorkflowDefinitionRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/plan", response_model=CapabilityPlan)
async def plan_workflow(
    payload: WorkflowPlanRequest, service: WorkflowServiceDependency
) -> CapabilityPlan:
    return service.plan(payload)


@router.post("/run", response_model=WorkflowInstanceRead, status_code=status.HTTP_201_CREATED)
async def run_workflow(
    payload: WorkflowRunCreate,
    request: Request,
    service: WorkflowServiceDependency,
) -> WorkflowInstanceRead:
    instance = await service.create_run(
        payload.workflow_id,
        payload.input,
        asset_id=payload.asset_id,
        trace_id=request.state.request_id,
        execute=payload.execute,
    )
    return WorkflowInstanceRead.model_validate(instance)


@router.post("/run/{instance_id}/resume", response_model=WorkflowInstanceRead)
async def resume_workflow(
    instance_id: UUID, service: WorkflowServiceDependency
) -> WorkflowInstanceRead:
    return WorkflowInstanceRead.model_validate(await service.resume(instance_id))


@router.get("/run/{instance_id}", response_model=WorkflowInstanceRead)
async def get_workflow_run(
    instance_id: UUID, service: WorkflowServiceDependency
) -> WorkflowInstanceRead:
    return WorkflowInstanceRead.model_validate(await service.get_run(instance_id))


@router.post("/cancel/{instance_id}", response_model=WorkflowInstanceRead)
async def cancel_workflow(
    instance_id: UUID, service: WorkflowServiceDependency
) -> WorkflowInstanceRead:
    return WorkflowInstanceRead.model_validate(await service.cancel(instance_id))


@router.get("/{definition_id}", response_model=WorkflowDefinitionRead)
async def get_workflow(
    definition_id: UUID, service: WorkflowServiceDependency
) -> WorkflowDefinitionRead:
    return WorkflowDefinitionRead.model_validate(await service.get_definition(definition_id))
