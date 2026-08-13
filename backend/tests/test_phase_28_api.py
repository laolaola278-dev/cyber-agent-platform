"""Phase 28 -- API integration tests for /acquisitions endpoints (spec 32).

Confirms the acquisition API surface exists, returns structured responses,
and that NO bypass / captcha / stealth / proxy-rotation / auth-bypass
capability is exposed. DB-dependent handlers are covered by the acquisition
service tests; here we assert routing and validation only.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

HEADERS = {
    "X-CAP-User": "administrator",
    "X-CAP-Proxy-Secret": "change-me-proxy-secret",
}


def _router_paths(app) -> set[str]:
    """All API paths from the OpenAPI schema (covers included routers)."""
    schema = app.openapi()
    return set(schema.get("paths", {}).keys())


@pytest.mark.asyncio
async def test_acquisitions_router_registered(client: AsyncClient) -> None:
    app = client._transport.app  # type: ignore[union-attr]
    paths = _router_paths(app)
    assert "/acquisitions" in paths
    assert "/acquisitions/{run_id}" in paths
    assert "/acquisitions/{run_id}/evidence" in paths
    assert "/acquisitions/{run_id}/completeness" in paths
    assert "/acquisitions/{run_id}/resume" in paths


@pytest.mark.asyncio
async def test_no_bypass_capability_endpoints(client: AsyncClient) -> None:
    app = client._transport.app  # type: ignore[union-attr]
    paths = _router_paths(app)
    lower = {p.lower() for p in paths}
    for forbidden in (
        "bypass",
        "captcha",
        "stealth",
        "proxy-rotation",
        "auth-bypass",
        "proxy_rotation",
    ):
        assert not any(forbidden in p for p in lower), forbidden


@pytest.mark.asyncio
async def test_create_acquisition_validation(client: AsyncClient) -> None:
    # missing goal -> 422 (validation happens before DB access)
    response = await client.post("/acquisitions", json={"url": "https://example.com/"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resume_missing_acquisition_404(client: AsyncClient) -> None:
    response = await client.post(
        f"/acquisitions/{uuid4()}/resume", headers=HEADERS
    )
    assert response.status_code == 404
