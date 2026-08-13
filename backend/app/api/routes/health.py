"""Platform and Registry health endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.enums import AgentStatus, ToolStatus
from app.database import get_db_session
from app.dependencies import AgentServiceDependency
from app.schemas import HealthResponse
from app.schemas.registry import AgentRegistryRead, RegistryStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check API liveness")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.app_name, version=settings.app_version)


@router.get("/ready", response_model=HealthResponse, summary="Check API readiness")
async def readiness(session: AsyncSession = Depends(get_db_session)) -> HealthResponse:
    await session.execute(select(1))
    settings = get_settings()
    return HealthResponse(status="ready", service=settings.app_name, version=settings.app_version)


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    from app.models import (
        PlaybookExecution,
        ResponseApproval,
        ResponseExecution,
        SandboxExecution,
        Task,
        Worker,
    )

    execution_count = float(
        await session.scalar(select(func.count()).select_from(SandboxExecution)) or 0
    )
    execution_duration = (
        float(
            await session.scalar(select(func.coalesce(func.sum(ResponseExecution.duration_ms), 0)))
            or 0
        )
        / 1000
    )
    worker_capacity, worker_active = (
        await session.execute(
            select(
                func.coalesce(func.sum(Worker.max_concurrency), 0),
                func.coalesce(func.sum(Worker.active_executions), 0),
            )
        )
    ).one()
    queue_depth = float(
        await session.scalar(
            select(func.count()).select_from(Task).where(Task.status.in_({"pending", "queued"}))
        )
        or 0
    )
    response_total, response_success = (
        await session.execute(
            select(
                func.count(ResponseExecution.id),
                func.coalesce(
                    func.sum(
                        case(
                            (ResponseExecution.status.in_({"SUCCEEDED", "VERIFIED"}), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
        )
    ).one()
    approval_latency = float(
        await session.scalar(
            select(
                func.coalesce(
                    func.avg(
                        func.extract(
                            "epoch",
                            ResponseApproval.decided_at - ResponseApproval.created_at,
                        )
                    ),
                    0,
                )
            )
        )
        or 0
    )
    playbook_total, playbook_success = (
        await session.execute(
            select(
                func.count(PlaybookExecution.id),
                func.coalesce(
                    func.sum(case((PlaybookExecution.status == "SUCCEEDED", 1), else_=0)),
                    0,
                ),
            )
        )
    ).one()
    registry = request.app.state.metrics_registry
    registry.set_business_gauges(
        {
            "cap_execution_count": execution_count,
            "cap_execution_duration_seconds": execution_duration,
            "cap_worker_utilization_ratio": (
                float(worker_active) / float(worker_capacity) if worker_capacity else 0
            ),
            "cap_queue_depth": queue_depth,
            "cap_plugin_success_ratio": (
                float(response_success) / float(response_total) if response_total else 0
            ),
            "cap_approval_latency_seconds": approval_latency,
            "cap_playbook_success_ratio": (
                float(playbook_success) / float(playbook_total) if playbook_total else 0
            ),
        }
    )
    return PlainTextResponse(
        registry.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/registry/status", response_model=RegistryStatus)
async def registry_status(
    session: AsyncSession = Depends(get_db_session),
) -> RegistryStatus:
    from app.models import Agent, Tool

    agents_total = await session.scalar(select(func.count()).select_from(Agent)) or 0
    agents_online = (
        await session.scalar(
            select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ONLINE)
        )
        or 0
    )
    tools_enabled = (
        await session.scalar(
            select(func.count()).select_from(Tool).where(Tool.status == ToolStatus.ENABLED)
        )
        or 0
    )
    return RegistryStatus(
        agents_total=agents_total,
        agents_online=agents_online,
        tools_enabled=tools_enabled,
    )


@router.get("/agents/{agent_id}/health", response_model=AgentRegistryRead)
async def agent_health(agent_id: UUID, service: AgentServiceDependency) -> AgentRegistryRead:
    return AgentRegistryRead.model_validate(await service.get(agent_id))
