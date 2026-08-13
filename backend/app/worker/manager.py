"""Database-authoritative Worker lifecycle facade."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime


class WorkerManager:
    """Coordinate registration, heartbeat, lease expiry and aggregate health."""

    def __init__(
        self,
        registry: WorkerRegistry,
        leases: WorkerLeaseManager,
        runtime: WorkerRuntime,
        *,
        heartbeat_timeout_seconds: int = 60,
    ) -> None:
        self._registry = registry
        self._leases = leases
        self._runtime = runtime
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    async def register(self, worker: WorkerRecord) -> WorkerRecord:
        return await self._registry.register(worker)

    async def list(self) -> tuple[WorkerRecord, ...]:
        return await self._registry.list()

    async def get(self, worker_id: UUID) -> WorkerRecord:
        return await self._registry.require(worker_id)

    async def heartbeat(
        self,
        worker_id: UUID,
        *,
        status: WorkerStatus,
        active_executions: int,
    ) -> WorkerRecord:
        return await self._registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker_id,
                status=status,
                active_executions=active_executions,
            )
        )

    async def health(self, *, now: datetime | None = None) -> dict[str, object]:
        observed = now or datetime.now(UTC)
        stale = await self._registry.mark_stale(
            heartbeat_timeout_seconds=self._heartbeat_timeout_seconds,
            now=observed,
        )
        expired = await self._leases.expire(now=observed)
        workers = await self._registry.list()
        healthy = [
            item for item in workers if item.status in {WorkerStatus.ONLINE, WorkerStatus.BUSY}
        ]
        sandbox_healthy = await self._runtime.health()
        return {
            "status": "ok" if healthy and sandbox_healthy else "degraded",
            "workers_total": len(workers),
            "workers_healthy": len(healthy),
            "workers_stale": len(stale),
            "leases_expired": len(expired),
            "sandbox_healthy": sandbox_healthy,
            "checked_at": observed,
        }
