"""Capability Registry HTTP endpoints."""

from fastapi import APIRouter, Depends, Query

from app.capabilities import CapabilityRegistryService
from app.dependencies.services import SessionDependency
from app.repositories.capability import CapabilityRepository
from app.schemas.capability import CapabilityRead
from app.schemas.common import PageResponse

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def get_capability_service(session: SessionDependency) -> CapabilityRegistryService:
    return CapabilityRegistryService(session, CapabilityRepository(session))


@router.get("", response_model=PageResponse[CapabilityRead])
async def list_capabilities(
    service: CapabilityRegistryService = Depends(get_capability_service),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> PageResponse[CapabilityRead]:
    result = await service.list(page=page, page_size=page_size)
    return PageResponse(
        items=[CapabilityRead.model_validate(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/{name}", response_model=CapabilityRead)
async def get_capability(
    name: str,
    service: CapabilityRegistryService = Depends(get_capability_service),
) -> CapabilityRead:
    return CapabilityRead.model_validate(await service.get(name))
