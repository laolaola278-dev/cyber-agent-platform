"""Database-authoritative Worker registry with an optional disposable read cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventType, PlatformEvent
from app.events.transactional import publish_audit
from app.exceptions import InvalidStateTransition, WorkerConflict, WorkerNotFound
from app.models.worker import Worker
from app.repositories.worker import WorkerRepository
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.state_machine import validate_transition


class WorkerRegistry:
    """Persist every Worker mutation before updating the disposable read cache."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = WorkerRepository(session)
        self._cache: dict[UUID, WorkerRecord] = {}

    @staticmethod
    def _record(row: Worker) -> WorkerRecord:
        return WorkerRecord.model_validate(row)

    async def register(self, worker: WorkerRecord, *, actor: str = "system") -> WorkerRecord:
        existing = await self._repository.get_by_name(worker.name)
        if existing is not None:
            record = self._record(existing)
            self._cache[record.id] = record
            return record
        row = Worker(
            id=worker.id,
            name=worker.name,
            runtime_version=worker.runtime_version,
            capabilities=sorted(worker.capabilities),
            status=WorkerStatus.REGISTERED.value,
            max_concurrency=worker.max_concurrency,
            active_executions=0,
            registered_at=worker.registered_at,
            last_heartbeat_at=worker.last_heartbeat_at,
            metadata_=worker.metadata,
            state_version=1,
        )
        await self._repository.add(row)
        await publish_audit(
            self._session,
            PlatformEvent(
                type=EventType.WORKER_REGISTERED,
                trace_id=str(worker.id),
                aggregate_id=worker.id,
                actor=actor,
                resource=f"worker:{worker.id}",
                payload={"name": worker.name, "status": WorkerStatus.REGISTERED.value},
            ),
        )
        await self._session.commit()
        record = self._record(row)
        self._cache[record.id] = record
        return record

    async def require(self, worker_id: UUID) -> WorkerRecord:
        row = await self._repository.get(worker_id)
        if row is None:
            self._cache.pop(worker_id, None)
            raise WorkerNotFound("Worker was not found")
        record = self._record(row)
        self._cache[record.id] = record
        return record

    async def list(self) -> tuple[WorkerRecord, ...]:
        records = tuple(self._record(row) for row in await self._repository.list())
        self._cache = {record.id: record for record in records}
        return records

    async def heartbeat(self, heartbeat: WorkerHeartbeat, *, actor: str = "worker") -> WorkerRecord:
        current = await self.require(heartbeat.worker_id)
        if heartbeat.active_executions > current.max_concurrency:
            raise WorkerConflict("Worker heartbeat exceeds registered concurrency")
        validate_transition(current.status, heartbeat.status)
        updated = await self._repository.update_state(
            worker_id=current.id,
            expected_version=current.state_version,
            status=heartbeat.status.value,
            active_executions=heartbeat.active_executions,
            observed_at=heartbeat.observed_at,
            metadata=heartbeat.metadata or None,
        )
        if updated is None:
            raise WorkerConflict("Worker state changed concurrently")
        event_type = (
            EventType.WORKER_HEARTBEAT
            if current.status is heartbeat.status
            else EventType.WORKER_STATE_CHANGED
        )
        await publish_audit(
            self._session,
            PlatformEvent(
                type=event_type,
                trace_id=str(current.id),
                aggregate_id=current.id,
                actor=actor,
                resource=f"worker:{current.id}",
                payload={
                    "from": current.status.value,
                    "to": heartbeat.status.value,
                    "active_executions": heartbeat.active_executions,
                },
            ),
        )
        await self._session.commit()
        record = self._record(updated)
        self._cache[record.id] = record
        return record

    async def mark_stale(
        self, *, heartbeat_timeout_seconds: int, now: datetime | None = None
    ) -> tuple[WorkerRecord, ...]:
        observed = now or datetime.now(UTC)
        threshold = observed - timedelta(seconds=heartbeat_timeout_seconds)
        changed: list[WorkerRecord] = []
        for worker in await self.list():
            heartbeat_at = worker.last_heartbeat_at
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
            if heartbeat_at >= threshold or worker.status in {
                WorkerStatus.OFFLINE,
                WorkerStatus.DRAINING,
                WorkerStatus.DEAD,
            }:
                continue
            target = (
                WorkerStatus.DEAD
                if heartbeat_at < threshold - timedelta(seconds=heartbeat_timeout_seconds)
                else WorkerStatus.UNHEALTHY
            )
            try:
                changed.append(
                    await self.heartbeat(
                        WorkerHeartbeat(
                            worker_id=worker.id,
                            observed_at=observed,
                            status=target,
                            active_executions=worker.active_executions,
                        ),
                        actor="health-monitor",
                    )
                )
            except InvalidStateTransition:
                continue
        return tuple(changed)
