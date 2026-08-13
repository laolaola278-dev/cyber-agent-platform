"""Unified Knowledge Center HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.dependencies import KnowledgeServiceDependency
from app.schemas.common import PageResponse
from app.schemas.knowledge import KnowledgeImportRead, KnowledgeImportRequest, KnowledgeRead

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=PageResponse[KnowledgeRead])
async def list_knowledge(
    service: KnowledgeServiceDependency,
    knowledge_type: str | None = Query(default=None, max_length=64),
    source: str | None = Query(default=None, max_length=128),
    status_value: str | None = Query(default=None, alias="status", max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[KnowledgeRead]:
    result = await service.search(
        knowledge_type=knowledge_type,
        source=source,
        status=status_value,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/import", response_model=KnowledgeImportRead, status_code=status.HTTP_201_CREATED)
async def import_knowledge(
    payload: KnowledgeImportRequest,
    request: Request,
    service: KnowledgeServiceDependency,
) -> KnowledgeImportRead:
    result = await service.import_payload(
        source=payload.source,
        provider=payload.provider,
        format_name=payload.format,
        payload=payload.payload,
        trace_id=request.state.request_id,
    )
    return KnowledgeImportRead(
        source=result.source,
        imported=result.imported,
        unchanged=result.unchanged,
        relations=result.relations,
        knowledge_ids=result.knowledge_ids,
    )


@router.get("/search", response_model=PageResponse[KnowledgeRead])
async def search_knowledge(
    service: KnowledgeServiceDependency,
    q: str = Query(min_length=1, max_length=512),
    knowledge_type: str | None = Query(default=None, max_length=64),
    source: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[KnowledgeRead]:
    result = await service.search(
        query=q,
        knowledge_type=knowledge_type,
        source=source,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[service.to_read(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/cve/{external_id}", response_model=KnowledgeRead)
async def get_cve(external_id: str, service: KnowledgeServiceDependency) -> KnowledgeRead:
    return service.to_read(await service.get_by_external_id("CVE", external_id))


@router.get("/cwe/{external_id}", response_model=KnowledgeRead)
async def get_cwe(external_id: str, service: KnowledgeServiceDependency) -> KnowledgeRead:
    return service.to_read(await service.get_by_external_id("CWE", external_id))


@router.get("/attack/{external_id}", response_model=KnowledgeRead)
async def get_attack(external_id: str, service: KnowledgeServiceDependency) -> KnowledgeRead:
    return service.to_read(await service.get_by_external_id("ATTACK_TECHNIQUE", external_id))


@router.get("/{knowledge_id}", response_model=KnowledgeRead)
async def get_knowledge(knowledge_id: UUID, service: KnowledgeServiceDependency) -> KnowledgeRead:
    return service.to_read(await service.get(knowledge_id))
