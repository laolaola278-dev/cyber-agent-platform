"""Security Assessment Framework HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.enums import FindingSeverity, FindingStatus
from app.dependencies import AssessmentServiceDependency
from app.schemas import (
    AssessmentCapabilityRead,
    AssessmentPluginRead,
    AssessmentReportRead,
    AssessmentTaskCreate,
    AssessmentTaskRead,
    FindingRead,
    FindingTransitionCreate,
    FindingTransitionRead,
    NucleiAssessmentCreate,
    ZapAssessmentCreate,
    ZapPolicyRead,
    ZapStatusRead,
)
from app.schemas.common import PageResponse

router = APIRouter(prefix="/assessment", tags=["assessment"])


@router.post("/tasks", response_model=AssessmentTaskRead, status_code=status.HTTP_201_CREATED)
async def create_assessment_task(
    payload: AssessmentTaskCreate,
    request: Request,
    service: AssessmentServiceDependency,
) -> AssessmentTaskRead:
    return AssessmentTaskRead.model_validate(
        await service.create(payload, trace_id=request.state.request_id)
    )


@router.post("/nuclei", response_model=AssessmentTaskRead, status_code=status.HTTP_201_CREATED)
async def create_nuclei_assessment(
    payload: NucleiAssessmentCreate,
    request: Request,
    service: AssessmentServiceDependency,
) -> AssessmentTaskRead:
    return AssessmentTaskRead.model_validate(
        await service.create_nuclei(payload, trace_id=request.state.request_id)
    )


@router.post("/zap", response_model=AssessmentTaskRead, status_code=status.HTTP_201_CREATED)
async def create_zap_assessment(
    payload: ZapAssessmentCreate,
    request: Request,
    service: AssessmentServiceDependency,
) -> AssessmentTaskRead:
    return AssessmentTaskRead.model_validate(
        await service.create_zap(payload, trace_id=request.state.request_id)
    )


@router.get("/zap/policies", response_model=list[ZapPolicyRead])
async def list_zap_policies(
    service: AssessmentServiceDependency,
) -> list[ZapPolicyRead]:
    return service.list_zap_policies()


@router.get("/zap/status", response_model=ZapStatusRead)
async def get_zap_status(service: AssessmentServiceDependency) -> ZapStatusRead:
    return await service.get_zap_status()


@router.get("/tasks", response_model=PageResponse[AssessmentTaskRead])
async def list_assessment_tasks(
    service: AssessmentServiceDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[AssessmentTaskRead]:
    result = await service.list_tasks(page=page, page_size=page_size)
    return PageResponse(
        items=[AssessmentTaskRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/tasks/{assessment_id}", response_model=AssessmentTaskRead)
async def get_assessment_task(
    assessment_id: UUID, service: AssessmentServiceDependency
) -> AssessmentTaskRead:
    return AssessmentTaskRead.model_validate(await service.get_task(assessment_id))


@router.get("/findings", response_model=PageResponse[FindingRead])
async def list_findings(
    service: AssessmentServiceDependency,
    severity: FindingSeverity | None = None,
    finding_status: FindingStatus | None = Query(default=None, alias="status"),
    asset_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[FindingRead]:
    result = await service.list_findings(
        severity=severity.value if severity else None,
        status=finding_status.value if finding_status else None,
        asset_id=asset_id,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_finding_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/findings/{finding_id}", response_model=FindingRead)
async def get_finding(finding_id: UUID, service: AssessmentServiceDependency) -> FindingRead:
    return service.to_finding_read(await service.get_finding(finding_id))


@router.post(
    "/findings/{finding_id}/transition",
    response_model=FindingTransitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def transition_finding(
    finding_id: UUID,
    payload: FindingTransitionCreate,
    request: Request,
    service: AssessmentServiceDependency,
) -> FindingTransitionRead:
    return FindingTransitionRead.model_validate(
        await service.transition_finding(finding_id, payload, trace_id=request.state.request_id)
    )


@router.get("/reports/{report_id}", response_model=AssessmentReportRead)
async def get_assessment_report(
    report_id: UUID, service: AssessmentServiceDependency
) -> AssessmentReportRead:
    return AssessmentReportRead.model_validate(await service.get_report(report_id))


@router.get("/plugins", response_model=list[AssessmentPluginRead])
async def list_assessment_plugins(
    service: AssessmentServiceDependency,
) -> list[AssessmentPluginRead]:
    return await service.list_plugins()


@router.get("/capabilities", response_model=list[AssessmentCapabilityRead])
async def list_assessment_capabilities(
    service: AssessmentServiceDependency,
) -> list[AssessmentCapabilityRead]:
    return await service.list_capabilities()
