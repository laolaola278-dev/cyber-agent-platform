"""Rejected and destructive API calls must name the caller that made them.

Found by running the shipped application against a real PostgreSQL server and
reading the audit rows it produced: a validation failure submitted as
``administrator`` was recorded with ``operator="api-user"`` -- the trail that
exists to answer "who did this" answered with a shared placeholder for every
caller. app/api/errors.py and the asset delete route hard-coded it even though
the authorization middleware has already verified the principal and put it on
the request.
"""

from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Asset
from tests.conftest import TestSessionFactory


async def _audit_rows(client: AsyncClient) -> list[dict[str, Any]]:
    payload = await client.get("/audit", params={"page": 1, "page_size": 100})
    assert payload.status_code == 200
    return payload.json()["items"]


async def test_rejected_request_is_attributed_to_the_authenticated_user(
    client: AsyncClient,
) -> None:
    """The client fixture authenticates as the Administrator directory user."""
    response = await client.post(
        "/assets",
        json={"asset_type": "NOT_A_TYPE", "name": "x", "value": "y"},
    )
    assert response.status_code == 422

    rejected = [row for row in await _audit_rows(client) if row["action"] == "ValidationError"]
    assert rejected, "a rejected request wrote nothing to the audit trail"
    # "api-user" is exactly what the defect looked like.
    assert [(row["action"], row["operator"]) for row in rejected] == [
        ("ValidationError", "administrator")
    ] * len(rejected)


async def test_soft_delete_records_the_authenticated_user_on_the_asset(
    client: AsyncClient,
) -> None:
    """Asset.deleted_by is the durable "who removed this" field.

    Business events are audited under the acting subsystem (the ASSET_SOFT_DELETED
    row reads asset-service), so the attributable record for a deletion is this
    column -- and the route's placeholder was writing "api-user" into it for
    every caller.
    """
    created = await client.post(
        "/assets",
        json={"asset_type": "DOMAIN", "name": "audit-attribution", "value": "a.example"},
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]

    before = {row["id"] for row in await _audit_rows(client)}
    deleted = await client.delete(f"/assets/{asset_id}")
    assert deleted.status_code == 204

    assert any(
        row["action"] == "AssetSoftDeleted"
        for row in await _audit_rows(client) if row["id"] not in before
    ), "the deletion was not audited at all"

    async with TestSessionFactory() as session:
        stored = await session.scalar(select(Asset.deleted_by).where(Asset.id == UUID(asset_id)))
    assert stored == "administrator", stored
