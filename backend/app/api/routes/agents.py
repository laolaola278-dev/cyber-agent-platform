"""Backward-compatible Agent Registry aliases.

New clients should use /registry/agents. These endpoints preserve the Phase 0 paths
while sharing the Phase 1.1 pagination contract.
"""

from fastapi import APIRouter, Query, Request, status

from app.dependencies import AgentServiceDependency
from app.schemas import AgentCreate, AgentRead
from app.schemas.common import PageResponse

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=PageResponse[AgentRead])
async def list_agents(
    service: AgentServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[AgentRead]:
    result = await service.list(page=page, page_size=page_size)
    return PageResponse(
        items=[AgentRead.model_validate(agent) for agent in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate, request: Request, service: AgentServiceDependency
) -> AgentRead:
    agent = await service.register(payload, trace_id=request.state.request_id)
    return AgentRead.model_validate(agent)
