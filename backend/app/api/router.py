"""Top-level API router composition."""

from fastapi import APIRouter

from app.api.routes import (
    acquisition,
    agent,
    agents,
    assessment,
    assets,
    capabilities,
    detection,
    health,
    heartbeat,
    incident,
    knowledge,
    notification,
    playbook,
    productization,
    registry,
    response,
    runtime,
    suricata,
    tasks,
    telemetry,
    worker,
    workflow,
    zeek,
)

api_router = APIRouter()
api_router.include_router(acquisition.router)
api_router.include_router(health.router)
api_router.include_router(agent.router)
api_router.include_router(agents.router)
api_router.include_router(assessment.router)
api_router.include_router(assets.router)
api_router.include_router(knowledge.router)
api_router.include_router(registry.router)
api_router.include_router(capabilities.router)
api_router.include_router(detection.router)
api_router.include_router(suricata.router)
api_router.include_router(zeek.router)
api_router.include_router(incident.router)
api_router.include_router(response.router)
api_router.include_router(notification.router)
api_router.include_router(playbook.router)
api_router.include_router(productization.router)
api_router.include_router(tasks.router)
api_router.include_router(telemetry.router)
api_router.include_router(runtime.router)
api_router.include_router(worker.router)
api_router.include_router(workflow.router)
api_router.include_router(heartbeat.router)
