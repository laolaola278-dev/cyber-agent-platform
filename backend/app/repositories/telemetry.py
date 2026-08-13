"""Telemetry persistence repositories and database checkpoint provider."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import TelemetryConflict
from app.models import TelemetryCheckpoint, TelemetryRuntimeState, TelemetryTask
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult
from app.telemetry.checkpoint import Checkpoint


class TelemetryRepository(SQLAlchemyRepository[TelemetryTask]):
    model = TelemetryTask

    async def list_tasks(self, *, page: int, page_size: int) -> PageResult[TelemetryTask]:
        total = await self.session.scalar(select(func.count()).select_from(TelemetryTask))
        items = (
            await self.session.scalars(
                select(TelemetryTask)
                .options(selectinload(TelemetryTask.pipeline))
                .order_by(TelemetryTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def list_runtime_states(self) -> list[TelemetryRuntimeState]:
        return list(
            await self.session.scalars(
                select(TelemetryRuntimeState).order_by(TelemetryRuntimeState.worker_id)
            )
        )

    async def upsert_runtime_state(
        self,
        *,
        worker_id: str,
        pipeline_id: UUID | None,
        status: str,
        stream: str | None,
        partition: str | None,
        consumer: str | None,
        current_offset: int | None,
        queue_depth: int,
        backpressure_action: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryRuntimeState:
        state = await self.session.scalar(
            select(TelemetryRuntimeState).where(TelemetryRuntimeState.worker_id == worker_id)
        )
        if state is None:
            state = TelemetryRuntimeState(
                worker_id=worker_id,
                heartbeat_at=datetime.now(UTC),
            )
            self.session.add(state)
        state.pipeline_id = pipeline_id
        state.status = status
        state.stream = stream
        state.partition = partition
        state.consumer = consumer
        state.current_offset = current_offset
        state.queue_depth = queue_depth
        state.backpressure_action = backpressure_action
        state.metadata_ = dict(metadata or {})
        state.heartbeat_at = datetime.now(UTC)
        await self.session.flush()
        return state


class SQLAlchemyCheckpointProvider:
    """SQLite/PostgreSQL-compatible provider through the active AsyncSession."""

    name = "database"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, stream: str, partition: str, consumer: str) -> Checkpoint | None:
        row = await self._find(stream, partition, consumer)
        return self._to_contract(row) if row else None

    async def commit(self, checkpoint: Checkpoint) -> Checkpoint:
        row = await self._find(checkpoint.stream, checkpoint.partition, checkpoint.consumer)
        if row is not None:
            if checkpoint.offset < row.offset or checkpoint.sequence < row.sequence:
                raise TelemetryConflict("Telemetry checkpoint cannot move backwards")
            row.offset = checkpoint.offset
            row.sequence = checkpoint.sequence
            row.checksum = checkpoint.checksum
            row.metadata_ = dict(checkpoint.metadata)
            row.committed_at = datetime.now(UTC)
        else:
            row = TelemetryCheckpoint(
                provider=self.name,
                stream=checkpoint.stream,
                partition=checkpoint.partition,
                consumer=checkpoint.consumer,
                offset=checkpoint.offset,
                sequence=checkpoint.sequence,
                checksum=checkpoint.checksum,
                metadata_=dict(checkpoint.metadata),
                committed_at=datetime.now(UTC),
            )
            self._session.add(row)
        await self._session.flush()
        return self._to_contract(row)

    async def list(self) -> list[Checkpoint]:
        rows = await self._session.scalars(
            select(TelemetryCheckpoint).order_by(
                TelemetryCheckpoint.stream,
                TelemetryCheckpoint.partition,
                TelemetryCheckpoint.consumer,
            )
        )
        return [self._to_contract(row) for row in rows]

    async def _find(self, stream: str, partition: str, consumer: str) -> TelemetryCheckpoint | None:
        return await self._session.scalar(
            select(TelemetryCheckpoint).where(
                TelemetryCheckpoint.provider == self.name,
                TelemetryCheckpoint.stream == stream,
                TelemetryCheckpoint.partition == partition,
                TelemetryCheckpoint.consumer == consumer,
            )
        )

    @staticmethod
    def _to_contract(row: TelemetryCheckpoint) -> Checkpoint:
        return Checkpoint(
            provider=row.provider,
            stream=row.stream,
            partition=row.partition,
            consumer=row.consumer,
            offset=row.offset,
            sequence=row.sequence,
            checksum=row.checksum,
            metadata=dict(row.metadata_),
            committed_at=row.committed_at,
        )
