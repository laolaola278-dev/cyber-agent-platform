"""Capability-aware database Worker placement."""

from __future__ import annotations

from app.exceptions import WorkerUnavailable
from app.worker.contracts import WorkerRecord, WorkerStatus
from app.worker.registry import WorkerRegistry


class WorkerScheduler:
    """Select a healthy feasible Worker from a fresh authoritative database read."""

    def __init__(self, registry: WorkerRegistry) -> None:
        self._registry = registry

    async def select(self, capability: str) -> WorkerRecord:
        feasible = [
            worker
            for worker in await self._registry.list()
            if worker.status in {WorkerStatus.ONLINE, WorkerStatus.BUSY}
            and capability in worker.capabilities
            and worker.active_executions < worker.max_concurrency
        ]
        if not feasible:
            raise WorkerUnavailable("No healthy Worker can execute the requested capability")
        return min(
            feasible,
            key=lambda worker: (
                worker.active_executions / worker.max_concurrency,
                worker.last_heartbeat_at,
                str(worker.id),
            ),
        )
