"""Agentic engine HTTP endpoints (v2.0 / Phase 25-26).

High-risk capabilities are intentionally NOT exposed here: there is no direct
execution API for response.waf / response.firewall / response.edr /
host.isolate. Agents only produce plans, observations, triage and hypotheses.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.service import AgentEngineService
from app.agent.service2 import Phase26Service
from app.dependencies.services import SessionDependency
from app.exceptions import AgentError
from app.schemas.agent_engine import (
    EvaluationReportRead,
    InvestigationRead,
    RunRead,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_service(session: SessionDependency) -> AgentEngineService:
    return AgentEngineService(session)


def get_phase26_service(session: SessionDependency) -> Phase26Service:
    return Phase26Service(session)


def _http_error(exc: AgentError) -> HTTPException:
    return HTTPException(
        status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)
    )


@router.post("/investigations", status_code=201, response_model=InvestigationRead)
async def create_investigation(
    payload: dict,
    service: AgentEngineService = Depends(get_agent_service),
) -> InvestigationRead:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=422, detail="goal is required")
    try:
        return InvestigationRead.model_validate(
            await service.create_investigation(
                goal=goal,
                context=dict(payload.get("context") or {}),
                data_blocks=list(payload.get("data_blocks") or []),
            )
        )
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.get("/investigations/{session_id}", response_model=InvestigationRead)
async def get_investigation(
    session_id: UUID,
    service: AgentEngineService = Depends(get_agent_service),
) -> InvestigationRead:
    try:
        return InvestigationRead.model_validate(await service.get_investigation(session_id))
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.post("/investigations/{session_id}/continue", response_model=InvestigationRead)
async def continue_investigation(
    session_id: UUID,
    payload: dict,
    service: AgentEngineService = Depends(get_agent_service),
) -> InvestigationRead:
    try:
        return InvestigationRead.model_validate(
            await service.continue_investigation(
                session_id,
                goal=str(payload.get("goal") or "").strip() or None,
                context=dict(payload.get("context") or {}),
                data_blocks=list(payload.get("data_blocks") or []),
            )
        )
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}", response_model=RunRead)
async def get_run(
    run_id: UUID,
    service: AgentEngineService = Depends(get_agent_service),
) -> RunRead:
    try:
        return RunRead.model_validate(await service.get_run(run_id))
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.get("/evaluations", response_model=EvaluationReportRead)
async def get_evaluations(
    service: AgentEngineService = Depends(get_agent_service),
    limit: int = Query(default=100, ge=1, le=500),
) -> EvaluationReportRead:
    return EvaluationReportRead.model_validate(await service.run_evaluations())


# -- Phase 26 ---------------------------------------------------------------


@router.post("/triage")
async def triage(
    payload: dict,
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    try:
        return await service.triage(
            source=dict(payload.get("source") or {}),
            context=dict(payload.get("context") or {}) if payload.get("context") else None,
            data_blocks=list(payload.get("data_blocks") or []),
            prefer_real=bool(payload.get("prefer_real", False)),
        )
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.post("/attack-chain")
async def attack_chain(
    payload: dict,
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    try:
        return await service.attack_chain(
            events=list(payload.get("events") or []),
            findings=list(payload.get("findings") or []),
            asset_relations=list(payload.get("asset_relations") or []),
            knowledge=list(payload.get("knowledge") or []),
            data_blocks=list(payload.get("data_blocks") or []),
            prefer_real=bool(payload.get("prefer_real", False)),
        )
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.get("/evaluations/v2")
async def get_evaluations_v2(
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    return await service.run_evaluations_v2()


@router.get("/model-comparison")
async def get_model_comparison(
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    return await service.model_comparison()


# -- Phase 27 (hybrid engine) ------------------------------------------------


@router.post("/hybrid/triage")
async def hybrid_triage(
    payload: dict,
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    """Deterministic + retrieval + LLM-rank hybrid triage (Phase 27).

    The response exposes facts, retrieved knowledge, candidate techniques,
    scores, severity/FP factors, grounding, explanation and calibrated
    confidence -- the full reasoning basis for a judgment.
    """
    try:
        return await service.hybrid_triage(
            source=dict(payload.get("source") or {}),
            context=dict(payload.get("context") or {}),
            events=list(payload.get("events") or []),
            data_blocks=list(payload.get("data_blocks") or []),
            prefer_real=bool(payload.get("prefer_real", False)),
        )
    except AgentError as exc:
        raise _http_error(exc) from exc


@router.get("/hybrid/evaluation")
async def hybrid_evaluation(
    service: Phase26Service = Depends(get_phase26_service),
) -> dict[str, Any]:
    """Phase 27 hybrid evaluation (rules/retrieval/hybrid groups)."""
    return await service.hybrid_evaluation()
