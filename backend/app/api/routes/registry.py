"""Agent and Tool Registry HTTP endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.dependencies import AgentServiceDependency, ToolServiceDependency
from app.schemas.common import PageResponse
from app.schemas.registry import (
    AgentRegister,
    AgentRegistryRead,
    AgentUpdate,
    AgentVersionRead,
    ToolRead,
    ToolRegister,
    ToolVersionRead,
)

router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("/agents", response_model=PageResponse[AgentRegistryRead])
async def list_agents(
    service: AgentServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[AgentRegistryRead]:
    result = await service.list(page=page, page_size=page_size)
    return PageResponse(
        items=[AgentRegistryRead.model_validate(agent) for agent in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/agents", response_model=AgentRegistryRead, status_code=status.HTTP_201_CREATED)
async def register_agent(
    payload: AgentRegister, request: Request, service: AgentServiceDependency
) -> AgentRegistryRead:
    agent = await service.register(payload, trace_id=request.state.request_id)
    return AgentRegistryRead.model_validate(agent)


@router.put("/agents/{agent_id}", response_model=AgentRegistryRead)
async def update_agent(
    agent_id: UUID,
    payload: AgentUpdate,
    request: Request,
    service: AgentServiceDependency,
) -> AgentRegistryRead:
    return AgentRegistryRead.model_validate(
        await service.update(agent_id, payload, trace_id=request.state.request_id)
    )


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID, request: Request, service: AgentServiceDependency
) -> Response:
    await service.delete(agent_id, trace_id=request.state.request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{agent_id}/versions", response_model=PageResponse[AgentVersionRead])
async def list_agent_versions(
    agent_id: UUID,
    service: AgentServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[AgentVersionRead]:
    result = await service.list_versions(agent_id, page=page, page_size=page_size)
    return PageResponse(
        items=[AgentVersionRead.model_validate(version) for version in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/tools", response_model=PageResponse[ToolRead])
async def list_tools(
    service: ToolServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[ToolRead]:
    result = await service.list(page=page, page_size=page_size)
    return PageResponse(
        items=[ToolRead.model_validate(tool) for tool in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/tools", response_model=ToolRead, status_code=status.HTTP_201_CREATED)
async def register_tool(
    payload: ToolRegister, request: Request, service: ToolServiceDependency
) -> ToolRead:
    return ToolRead.model_validate(
        await service.register(payload, trace_id=request.state.request_id)
    )


@router.post("/tools/{tool_id}/disable", response_model=ToolRead)
async def disable_tool(tool_id: UUID, request: Request, service: ToolServiceDependency) -> ToolRead:
    return ToolRead.model_validate(
        await service.disable(tool_id, trace_id=request.state.request_id)
    )


@router.get("/tools/{tool_id}/versions", response_model=PageResponse[ToolVersionRead])
async def list_tool_versions(
    tool_id: UUID,
    service: ToolServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[ToolVersionRead]:
    result = await service.list_versions(tool_id, page=page, page_size=page_size)
    return PageResponse(
        items=[ToolVersionRead.model_validate(version) for version in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
