"""Incident and Investigation Case HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.enums import FindingSeverity, IncidentPriority, IncidentStatus, InvestigationStatus
from app.dependencies import IncidentServiceDependency, PlaybookServiceDependency
from app.schemas.common import PageResponse
from app.schemas.incident import (
    CaseCommentCreate,
    CaseCommentRead,
    IncidentArtifactCreate,
    IncidentArtifactRead,
    IncidentAssignmentCreate,
    IncidentCreate,
    IncidentRead,
    IncidentTransitionCreate,
    InvestigationCaseCreate,
    InvestigationCaseRead,
)

router = APIRouter(tags=["incident"])


@router.post("/incidents", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: IncidentCreate,
    request: Request,
    service: IncidentServiceDependency,
    _playbook_service: PlaybookServiceDependency,
) -> IncidentRead:
    # Resolve the Playbook dependency to attach incident.created to this request bus.
    return service.to_read(await service.create(payload, trace_id=request.state.request_id))


@router.get("/incidents", response_model=PageResponse[IncidentRead])
async def list_incidents(
    service: IncidentServiceDependency,
    severity: FindingSeverity | None = None,
    incident_status: IncidentStatus | None = Query(default=None, alias="status"),
    priority: IncidentPriority | None = None,
    owner: str | None = None,
    assignee: str | None = None,
    queue: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[IncidentRead]:
    result = await service.list(
        severity=severity.value if severity else None,
        status=incident_status.value if incident_status else None,
        priority=priority.value if priority else None,
        owner=owner,
        assignee=assignee,
        queue=queue,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: UUID, service: IncidentServiceDependency) -> IncidentRead:
    return service.to_read(await service.get(incident_id))


@router.post("/incidents/{incident_id}/transition", response_model=IncidentRead)
async def transition_incident(
    incident_id: UUID,
    payload: IncidentTransitionCreate,
    request: Request,
    service: IncidentServiceDependency,
) -> IncidentRead:
    return service.to_read(
        await service.transition(incident_id, payload, trace_id=request.state.request_id)
    )


@router.post("/incidents/{incident_id}/assign", response_model=IncidentRead)
async def assign_incident(
    incident_id: UUID,
    payload: IncidentAssignmentCreate,
    request: Request,
    service: IncidentServiceDependency,
) -> IncidentRead:
    return service.to_read(
        await service.assign(incident_id, payload, trace_id=request.state.request_id)
    )


@router.post(
    "/incidents/{incident_id}/artifacts",
    response_model=IncidentArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_incident_artifact(
    incident_id: UUID,
    payload: IncidentArtifactCreate,
    request: Request,
    service: IncidentServiceDependency,
) -> IncidentArtifactRead:
    artifact = await service.add_artifact(incident_id, payload, trace_id=request.state.request_id)
    return IncidentArtifactRead.model_validate(artifact)


@router.post(
    "/incidents/{incident_id}/cases",
    response_model=InvestigationCaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    incident_id: UUID,
    payload: InvestigationCaseCreate,
    request: Request,
    service: IncidentServiceDependency,
) -> InvestigationCaseRead:
    case = await service.create_case(incident_id, payload, trace_id=request.state.request_id)
    return InvestigationCaseRead.model_validate(case)


@router.get("/cases", response_model=PageResponse[InvestigationCaseRead])
async def list_cases(
    service: IncidentServiceDependency,
    incident_id: UUID | None = None,
    case_status: InvestigationStatus | None = Query(default=None, alias="status"),
    assignee: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[InvestigationCaseRead]:
    result = await service.list_cases(
        incident_id=incident_id,
        status=case_status.value if case_status else None,
        assignee=assignee,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[InvestigationCaseRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/cases/{case_id}", response_model=InvestigationCaseRead)
async def get_case(case_id: UUID, service: IncidentServiceDependency) -> InvestigationCaseRead:
    return InvestigationCaseRead.model_validate(await service.get_case(case_id))


@router.post(
    "/cases/{case_id}/comments",
    response_model=CaseCommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_case_comment(
    case_id: UUID,
    payload: CaseCommentCreate,
    request: Request,
    service: IncidentServiceDependency,
) -> CaseCommentRead:
    comment = await service.add_case_comment(case_id, payload, trace_id=request.state.request_id)
    return CaseCommentRead.model_validate(comment)
