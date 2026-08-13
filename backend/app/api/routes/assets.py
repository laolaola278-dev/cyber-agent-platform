"""Unified Asset Center HTTP API."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.core.enums import AssetType
from app.dependencies import AssetServiceDependency
from app.schemas import (
    AssetCreate,
    AssetDiscoveryRead,
    AssetDiscoveryRequest,
    AssetEvidenceRead,
    AssetRead,
    AssetRelationCreate,
    AssetRelationRead,
    AssetReportRead,
    AssetUpdate,
)
from app.schemas.common import PageResponse

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=PageResponse[AssetRead])
async def list_assets(
    service: AssetServiceDependency,
    name: str | None = Query(default=None, max_length=256),
    asset_type: AssetType | None = None,
    tag: str | None = Query(default=None, max_length=128),
    owner: str | None = Query(default=None, max_length=256),
    risk: str | None = Query(default=None, max_length=32),
    environment: str | None = Query(default=None, max_length=64),
    capability: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[AssetRead]:
    result = await service.search(
        name=name,
        asset_type=asset_type,
        tag=tag,
        owner=owner,
        risk=risk,
        environment=environment,
        capability=capability,
        page=page,
        page_size=page_size,
    )
    return PageResponse(
        items=[AssetRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreate, request: Request, service: AssetServiceDependency
) -> AssetRead:
    return AssetRead.model_validate(
        await service.create(payload, trace_id=request.state.request_id)
    )


@router.post("/discover", response_model=AssetDiscoveryRead)
async def discover_assets(
    payload: AssetDiscoveryRequest,
    request: Request,
    service: AssetServiceDependency,
) -> AssetDiscoveryRead:
    website, domain, ips, relations = await service.discover(
        payload, trace_id=request.state.request_id
    )
    return AssetDiscoveryRead(
        website=AssetRead.model_validate(website),
        domain=AssetRead.model_validate(domain),
        ips=[AssetRead.model_validate(item) for item in ips],
        relations=[AssetRelationRead.model_validate(item) for item in relations],
    )


@router.get("/{asset_id}/relations", response_model=list[AssetRelationRead])
async def list_asset_relations(
    asset_id: UUID, service: AssetServiceDependency
) -> list[AssetRelationRead]:
    return [
        AssetRelationRead.model_validate(item) for item in await service.list_relations(asset_id)
    ]


@router.post(
    "/{asset_id}/relations",
    response_model=AssetRelationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_relation(
    asset_id: UUID,
    payload: AssetRelationCreate,
    request: Request,
    service: AssetServiceDependency,
) -> AssetRelationRead:
    return AssetRelationRead.model_validate(
        await service.add_relation(asset_id, payload, trace_id=request.state.request_id)
    )


@router.get("/{asset_id}/evidence", response_model=list[AssetEvidenceRead])
async def list_asset_evidence(
    asset_id: UUID, service: AssetServiceDependency
) -> list[AssetEvidenceRead]:
    return [
        AssetEvidenceRead.model_validate(item) for item in await service.list_evidence(asset_id)
    ]


@router.get("/{asset_id}/reports", response_model=list[AssetReportRead])
async def list_asset_reports(
    asset_id: UUID, service: AssetServiceDependency
) -> list[AssetReportRead]:
    return [AssetReportRead.model_validate(item) for item in await service.list_reports(asset_id)]


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(asset_id: UUID, service: AssetServiceDependency) -> AssetRead:
    return AssetRead.model_validate(await service.get(asset_id))


@router.put("/{asset_id}", response_model=AssetRead)
async def update_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    request: Request,
    service: AssetServiceDependency,
) -> AssetRead:
    return AssetRead.model_validate(
        await service.update(asset_id, payload, trace_id=request.state.request_id)
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: UUID, request: Request, service: AssetServiceDependency
) -> Response:
    await service.soft_delete(
        asset_id,
        trace_id=request.state.request_id,
        actor="api-user",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
