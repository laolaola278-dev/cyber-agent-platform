"""Common API response schemas."""

from pydantic import BaseModel, Field


class PageResponse[ItemT](BaseModel):
    """Uniform paginated API envelope."""

    items: list[ItemT]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class HealthResponse(BaseModel):
    """Liveness response."""

    status: str
    service: str
    version: str
