"""Agent heartbeat endpoint."""

from fastapi import APIRouter, Request

from app.dependencies import AgentServiceDependency
from app.schemas.registry import AgentRegistryRead, HeartbeatRequest

router = APIRouter(tags=["registry"])


@router.post("/heartbeat", response_model=AgentRegistryRead)
async def heartbeat(
    payload: HeartbeatRequest, request: Request, service: AgentServiceDependency
) -> AgentRegistryRead:
    agent = await service.heartbeat(payload, trace_id=request.state.request_id)
    return AgentRegistryRead.model_validate(agent)
