"""Phase 28 -- Data Acquisition HTTP endpoints (spec 32).

Phase 28.2: the API is a pure ENQUEUE boundary. POST /acquisitions creates
an AcquisitionRun in QUEUED state and returns 202; execution is performed
exclusively by the Worker Claim Loop (the durable DB queue is the source of
truth). The API MUST NOT call asyncio.create_task / BackgroundTasks to run
long acquisitions, and never constructs acquisition adapters.

Explicitly does NOT expose bypass / captcha / stealth / proxy-rotation /
auth-bypass capabilities.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.acquisition.exceptions import AcquisitionNotFound
from app.acquisition.models_db import (
    AcquisitionArtifactRecord,
    AcquisitionRun,
    CompletenessReportRecord,
)
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionWorkerPath
from app.dependencies.services import SessionDependency
from app.evidence.service import EvidenceService
from app.worker.plugin_runtime import PluginWorkerRuntime

router = APIRouter(prefix="/acquisitions", tags=["acquisitions"])


class AcquisitionCreateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2000)
    target_asset: str = ""
    expected_fields: list[str] = []
    expected_time_range: list[str] = []
    expected_record_count: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class AcquisitionCreateResponse(BaseModel):
    id: UUID
    status: str
    goal: str
    source_type: str
    strategy: str
    blocked_reason: str = "NONE"
    blocked_detail: str | None = None


class AcquisitionSummary(BaseModel):
    id: UUID
    goal: str
    status: str
    source_type: str
    strategy: str
    blocked_reason: str = "NONE"
    total_bytes: int = 0
    total_requests: int = 0
    duration_seconds: float = 0.0
    replans: int = 0


def get_acquisition_service(
    session: SessionDependency,
) -> AcquisitionService:
    evidence = EvidenceService(session, publisher=None, storage_directory=Path("outputs"))  # type: ignore[arg-type]
    return AcquisitionService(session, evidence)


def get_acquisition_worker_path(
    service: AcquisitionService = Depends(get_acquisition_service),
) -> AcquisitionWorkerPath:
    """Default worker boundary: database-backed synthetic control plane.

    Used ONLY for cancellation plumbing (terminate / status transitions).
    Execution claims are performed by the Worker Claim Loop.
    """
    plugin = PluginWorkerRuntime.synthetic(frozenset({"acquisition.http"}))
    return AcquisitionWorkerPath(plugin, service)


async def _backpressure_guard(service: AcquisitionService) -> None:
    """Reject enqueue when the durable queue exceeds configured capacity.

    The DB is the queue's source of truth: the platform can keep accepting
    and queueing, OR return 429/503 per policy. The limit is configurable
    via AcquisitionPolicy.max_queued_runs (0/None = unlimited). This is NOT a
    memory-capacity check -- it bounds queue growth at the API boundary so
    the platform never unboundedly grows work in flight.
    """
    limit = service.policy.max_queued_runs
    if limit is None or limit <= 0:
        return
    from app.acquisition.claim import AcquisitionClaimCoordinator

    pending = await AcquisitionClaimCoordinator.pending_count(service.session)
    if pending >= limit:
        raise HTTPException(
            status_code=503,
            detail=f"acquisition queue at capacity ({pending}/{limit})",
        )


@router.post(
    "",
    response_model=AcquisitionCreateResponse,
    status_code=202,
)
async def create_acquisition(
    payload: AcquisitionCreateRequest,
    service: AcquisitionService = Depends(get_acquisition_service),
) -> AcquisitionCreateResponse:
    """Enqueue an acquisition run (202). Execution happens via the claim loop.

    The request never performs network acquisition and never schedules a
    background task: the run is persisted as QUEUED and claimed by a Worker.
    """
    await _backpressure_guard(service)
    run, created = await service.create(
        goal=payload.goal,
        url=payload.url,
        target_asset=payload.target_asset,
        expected_fields=payload.expected_fields,
        expected_time_range=payload.expected_time_range,
        expected_record_count=payload.expected_record_count,
        idempotency_key=payload.idempotency_key,
    )
    # 202: accepted for processing. No create_task, no BackgroundTasks.
    return AcquisitionCreateResponse(
        id=run.id,
        status=run.status,
        goal=run.goal,
        source_type=run.source_type,
        strategy=run.strategy,
        blocked_reason=run.blocked_reason,
        blocked_detail=run.blocked_detail,
    )


@router.get("")
async def list_acquisitions(
    session: SessionDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    statement = (
        select(AcquisitionRun)
        .order_by(AcquisitionRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(statement)
    rows = result.scalars().all()
    return {
        "items": [
            AcquisitionSummary(
                id=row.id,
                goal=row.goal,
                status=row.status,
                source_type=row.source_type,
                strategy=row.strategy,
                blocked_reason=row.blocked_reason,
                total_bytes=row.total_bytes,
                total_requests=row.total_requests,
                duration_seconds=row.duration_seconds,
                replans=row.replans,
            ).model_dump()
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": len(rows),
    }


@router.get("/{run_id}")
async def get_acquisition(run_id: UUID, session: SessionDependency) -> dict:
    run = await session.get(AcquisitionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="acquisition not found")
    return {
        "id": run.id,
        "goal": run.goal,
        "status": run.status,
        "source_type": run.source_type,
        "strategy": run.strategy,
        "blocked_reason": run.blocked_reason,
        "blocked_detail": run.blocked_detail,
        "replans": run.replans,
        "retries": run.retries,
        "total_bytes": run.total_bytes,
        "total_requests": run.total_requests,
        "duration_seconds": run.duration_seconds,
        "strategy_history": run.strategy_history,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/{run_id}/resume", status_code=202)
async def resume_acquisition(
    run_id: UUID,
    service: AcquisitionService = Depends(get_acquisition_service),
) -> dict:
    """Re-enqueue the SAME AcquisitionRun for the Worker Claim Loop.

    Phase 28.2: resume does NOT schedule a background task. It durably
    resets the run to QUEUED (keeping the persisted checkpoint) so the next
    claim loop tick picks it up and resumes pagination from the checkpoint
    cursor (never restarting from page 1).
    """
    try:
        run = await service.get_run(run_id)
    except AcquisitionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if run.status in ("COMPLETE", "CANCELLED", "FAILED", "BLOCKED"):
        raise HTTPException(status_code=409, detail="acquisition already terminal")
    await service.requeue(run_id)
    return {"id": run.id, "status": "QUEUED", "resumed": True}


@router.post("/{run_id}/cancel", status_code=202)
async def cancel_acquisition(
    run_id: UUID,
    service: AcquisitionService = Depends(get_acquisition_service),
    worker_path: AcquisitionWorkerPath = Depends(get_acquisition_worker_path),
) -> dict:
    """Request cancellation: CANCEL_REQUESTED -> terminate -> CANCELLED.

    The run is durably marked CANCEL_REQUESTED; the owning worker (or the
    claim loop, if not yet claimed) terminates the sandbox execution,
    releases the lease and resources, and only then finalizes CANCELLED.
    """
    try:
        payload = await worker_path.cancel(run_id)
    except AcquisitionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "id": run_id,
        "status": payload.status,
        "cancelled": payload.status == "CANCELLED",
        "cancel_requested": payload.status == "CANCEL_REQUESTED",
    }


@router.get("/{run_id}/evidence")
async def acquisition_evidence(run_id: UUID, session: SessionDependency) -> dict:
    run = await session.get(AcquisitionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="acquisition not found")
    artifacts = await session.execute(
        select(AcquisitionArtifactRecord).where(AcquisitionArtifactRecord.run_id == run_id)
    )
    rows = artifacts.scalars().all()
    return {
        "run_id": run_id,
        "evidence": [
            {
                "object_key": row.object_key,
                "sha256": row.sha256,
                "size": row.size,
                "content_type": row.content_type,
                "source_url": row.source_url,
                "final_url": row.final_url,
                "http_status": row.http_status,
                "etag": row.etag,
                "last_modified": row.last_modified,
                "tool": row.tool,
            }
            for row in rows
        ],
    }


@router.get("/{run_id}/completeness")
async def acquisition_completeness(run_id: UUID, session: SessionDependency) -> dict:
    report = await session.execute(
        select(CompletenessReportRecord).where(CompletenessReportRecord.run_id == run_id)
    )
    row = report.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="completeness not found")
    return {
        "run_id": run_id,
        "coverage_score": row.coverage_score,
        "field_completeness": row.field_completeness,
        "time_coverage": row.time_coverage,
        "pagination_complete": row.pagination_complete,
        "duplicates": row.duplicates,
        "gaps": row.gaps,
        "errors": row.errors,
        "confidence": row.confidence,
        "verdict": row.verdict,
    }
