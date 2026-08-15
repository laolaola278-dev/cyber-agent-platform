"""Database-authoritative Worker lease and fencing-token semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventType, PlatformEvent
from app.events.transactional import publish_audit
from app.exceptions import WorkerLeaseConflict, WorkerLeaseNotFound
from app.models.worker import WorkerLease as WorkerLeaseModel
from app.repositories.worker import WorkerLeaseRepository
from app.worker.contracts import LeaseStatus, WorkerLease


class WorkerLeaseManager:
    """Persist owner-bound leases and reject stale writers with fencing tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = WorkerLeaseRepository(session)

    @staticmethod
    def _contract(row: WorkerLeaseModel) -> WorkerLease:
        return WorkerLease.model_validate(row)

    async def acquire(
        self,
        *,
        worker_id: UUID,
        execution_id: UUID,
        owner: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        if ttl_seconds < 1:
            raise WorkerLeaseConflict("Worker lease TTL must be positive")
        observed = now or datetime.now(UTC)
        existing = await self._repository.get_by_execution_id(execution_id)
        if existing is not None:
            expires_at = existing.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if existing.status == LeaseStatus.ACTIVE.value and expires_at > observed:
                raise WorkerLeaseConflict("Execution already has an active lease")
            raise WorkerLeaseConflict("Execution lease identity cannot be reused")
        lease = WorkerLease.acquire(
            worker_id=worker_id,
            execution_id=execution_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            now=observed,
        )
        row = await self._repository.add(
            WorkerLeaseModel(
                id=lease.id,
                worker_id=lease.worker_id,
                execution_id=lease.execution_id,
                owner=lease.owner,
                status=lease.status.value,
                acquired_at=lease.acquired_at,
                renewed_at=lease.renewed_at,
                expires_at=lease.expires_at,
                version=lease.version,
                fencing_token=lease.fencing_token,
            )
        )
        await self._audit(EventType.WORKER_LEASE_ACQUIRED, row)
        await self._session.commit()
        return self._contract(row)

    async def require(self, lease_id: UUID) -> WorkerLease:
        row = await self._repository.get(lease_id)
        if row is None:
            raise WorkerLeaseNotFound("Worker lease was not found")
        return self._contract(row)

    async def renew(
        self,
        lease_id: UUID,
        *,
        owner: str,
        fencing_token: UUID,
        expected_version: int,
        ttl_seconds: int,
        now: datetime | None = None,
        extra_guard: Any | None = None,
    ) -> WorkerLease:
        """Atomically renew a lease via a single conditional UPDATE.

        ``extra_guard`` is an optional SQLAlchemy boolean clause AND-ed into the
        UPDATE's WHERE (e.g. an ownership EXISTS subquery supplied by the claim
        coordinator), so a renew can be gated on run ownership in the SAME
        atomic statement -- closing any verify-then-renew TOCTOU.
        """
        observed = now or datetime.now(UTC)
        updated = await self._repository.update_active(
            lease_id=lease_id,
            owner=owner,
            expected_version=expected_version,
            expected_token=fencing_token,
            values={
                "renewed_at": observed,
                "expires_at": observed + timedelta(seconds=ttl_seconds),
                "version": expected_version + 1,
            },
            extra_guard=extra_guard,
        )
        if updated is None:
            raise WorkerLeaseConflict("Worker lease renewal failed fencing validation")
        await self._audit(EventType.WORKER_LEASE_RENEWED, updated)
        await self._session.commit()
        return self._contract(updated)

    async def release(
        self,
        lease_id: UUID,
        *,
        owner: str,
        fencing_token: UUID,
        expected_version: int,
    ) -> WorkerLease:
        updated = await self._repository.update_active(
            lease_id=lease_id,
            owner=owner,
            expected_version=expected_version,
            expected_token=fencing_token,
            values={"status": LeaseStatus.RELEASED.value, "version": expected_version + 1},
        )
        if updated is None:
            raise WorkerLeaseConflict("Worker lease release failed fencing validation")
        await self._audit(EventType.WORKER_LEASE_RELEASED, updated)
        await self._session.commit()
        return self._contract(updated)

    async def expire(self, *, now: datetime | None = None) -> tuple[WorkerLease, ...]:
        observed = now or datetime.now(UTC)
        rows = await self._repository.expire_active(now=observed)
        for row in rows:
            await self._audit(EventType.WORKER_LEASE_EXPIRED, row)
        if rows:
            await self._session.commit()
        return tuple(self._contract(row) for row in rows)

    async def _audit(self, event_type: EventType, lease: WorkerLeaseModel) -> None:
        await publish_audit(
            self._session,
            PlatformEvent(
                type=event_type,
                trace_id=str(lease.execution_id),
                aggregate_id=lease.id,
                actor=lease.owner,
                resource=f"worker-lease:{lease.id}",
                payload={
                    "worker_id": str(lease.worker_id),
                    "execution_id": str(lease.execution_id),
                    "status": lease.status,
                    "version": lease.version,
                },
            ),
        )
