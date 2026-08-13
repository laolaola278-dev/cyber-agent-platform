"""SOAR Playbook Engine HTTP endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.auth.rbac import CurrentUserDependency, require_permission
from app.dependencies import PlaybookServiceDependency
from app.playbook.contracts import (
    PlaybookCreate,
    PlaybookExecutionRead,
    PlaybookRead,
    PlaybookRunRequest,
)
from app.schemas.common import PageResponse

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


@router.post(
    "",
    response_model=PlaybookRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("playbook.write"))],
)
async def create_playbook(
    payload: PlaybookCreate,
    request: Request,
    service: PlaybookServiceDependency,
    user: CurrentUserDependency,
) -> PlaybookRead:
    return service.to_read(
        await service.create(
            payload,
            trace_id=request.state.request_id,
            actor=user.username,
        )
    )


@router.get("", response_model=PageResponse[PlaybookRead])
async def list_playbooks(
    service: PlaybookServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[PlaybookRead]:
    result = await service.list(page=page, page_size=page_size)
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post(
    "/{playbook_id}/run",
    response_model=PlaybookExecutionRead,
    dependencies=[Depends(require_permission("playbook.execute"))],
)
async def run_playbook(
    playbook_id: UUID,
    payload: PlaybookRunRequest,
    request: Request,
    service: PlaybookServiceDependency,
) -> PlaybookExecutionRead:
    execution = await service.run(
        playbook_id,
        payload,
        trace_id=request.state.request_id,
    )
    return PlaybookExecutionRead.model_validate(execution)


@router.get("/executions", response_model=PageResponse[PlaybookExecutionRead])
async def list_playbook_executions(
    service: PlaybookServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[PlaybookExecutionRead]:
    result = await service.list_executions(page=page, page_size=page_size)
    return PageResponse(
        items=[PlaybookExecutionRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post(
    "/executions/{execution_id}/resume",
    response_model=PlaybookExecutionRead,
    dependencies=[Depends(require_permission("playbook.execute"))],
)
async def resume_playbook_execution(
    execution_id: UUID,
    payload: PlaybookRunRequest,
    request: Request,
    service: PlaybookServiceDependency,
) -> PlaybookExecutionRead:
    execution = await service.resume(
        execution_id,
        payload,
        trace_id=request.state.request_id,
    )
    return PlaybookExecutionRead.model_validate(execution)


@router.get("/executions/{execution_id}", response_model=PlaybookExecutionRead)
async def get_playbook_execution(
    execution_id: UUID,
    service: PlaybookServiceDependency,
) -> PlaybookExecutionRead:
    return PlaybookExecutionRead.model_validate(await service.get_execution(execution_id))


@router.get("/{playbook_id}", response_model=PlaybookRead)
async def get_playbook(
    playbook_id: UUID,
    service: PlaybookServiceDependency,
) -> PlaybookRead:
    return service.to_read(await service.get(playbook_id))
