"""Database-authoritative Worker, lease and Sandbox execution repositories."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.worker import (
    SandboxExecution,
    SandboxProfileRecord,
    Worker,
    WorkerLease,
)
from app.repositories.base import SQLAlchemyRepository


class WorkerRepository(SQLAlchemyRepository[Worker]):
    model = Worker

    async def list(self) -> Sequence[Worker]:
        return (
            await self.session.scalars(select(Worker).order_by(Worker.name, Worker.registered_at))
        ).all()

    async def get_by_name(self, name: str) -> Worker | None:
        return await self.session.scalar(select(Worker).where(Worker.name == name))

    async def add_unique(self, worker: Worker) -> Worker:
        try:
            return await self.add(worker)
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_name(worker.name)
            if existing is None:
                raise
            return existing

    async def update_state(
        self,
        *,
        worker_id: UUID,
        expected_version: int,
        status: str,
        active_executions: int,
        observed_at: datetime,
        metadata: dict[str, object] | None = None,
    ) -> Worker | None:
        values: dict[str, object] = {
            "status": status,
            "active_executions": active_executions,
            "last_heartbeat_at": observed_at,
            "state_version": expected_version + 1,
        }
        if metadata:
            values["metadata_"] = metadata
        statement = (
            update(Worker)
            .where(Worker.id == worker_id, Worker.state_version == expected_version)
            .values(**values)
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get(worker_id)


class WorkerLeaseRepository(SQLAlchemyRepository[WorkerLease]):
    model = WorkerLease

    async def get_by_execution_id(self, execution_id: UUID) -> WorkerLease | None:
        return await self.session.scalar(
            select(WorkerLease).where(WorkerLease.execution_id == execution_id)
        )

    async def update_active(
        self,
        *,
        lease_id: UUID,
        owner: str,
        expected_version: int,
        expected_token: UUID,
        values: dict[str, object],
    ) -> WorkerLease | None:
        statement = (
            update(WorkerLease)
            .where(
                WorkerLease.id == lease_id,
                WorkerLease.owner == owner,
                WorkerLease.status == "ACTIVE",
                WorkerLease.version == expected_version,
                WorkerLease.fencing_token == expected_token,
            )
            .values(**values)
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get(lease_id)

    async def expire_active(self, *, now: datetime) -> Sequence[WorkerLease]:
        # Atomic conditional UPDATE (guarded by ACTIVE + expires_at) instead of
        # SELECT-then-flush: a lease renewed concurrently (expires_at pushed
        # forward) is never overwritten by a stale expire. This closes the
        # renew/reclaim split-brain where expire's unguarded ORM flush lost an
        # UPDATE race to renew.
        statement = (
            update(WorkerLease)
            .where(
                WorkerLease.status == "ACTIVE",
                WorkerLease.expires_at <= now,
            )
            .values(
                status="EXPIRED",
                version=WorkerLease.version + 1,
            )
            .returning(WorkerLease)
            .execution_options(synchronize_session=False)
        )
        rows = list((await self.session.scalars(statement)).all())
        return rows


class SandboxExecutionRepository(SQLAlchemyRepository[SandboxExecution]):
    model = SandboxExecution

    async def list(self) -> Sequence[SandboxExecution]:
        return (
            await self.session.scalars(
                select(SandboxExecution).order_by(SandboxExecution.started_at.desc())
            )
        ).all()

    async def get_by_execution_id(self, execution_id: UUID) -> SandboxExecution | None:
        return await self.session.scalar(
            select(SandboxExecution).where(SandboxExecution.execution_id == execution_id)
        )

    async def commit_result(
        self,
        *,
        execution_id: UUID,
        lease_id: UUID,
        owner: str,
        fencing_token: UUID,
        expected_lease_version: int,
        now: datetime,
        values: dict[str, object],
    ) -> SandboxExecution | None:
        valid_lease = await self.session.scalar(
            select(WorkerLease.id).where(
                WorkerLease.id == lease_id,
                WorkerLease.owner == owner,
                WorkerLease.status == "ACTIVE",
                WorkerLease.fencing_token == fencing_token,
                WorkerLease.version == expected_lease_version,
                WorkerLease.expires_at > now,
            )
        )
        if valid_lease is None:
            return None
        statement = (
            update(SandboxExecution)
            .where(
                SandboxExecution.execution_id == execution_id,
                SandboxExecution.status == "RUNNING",
                SandboxExecution.lease_id == lease_id,
            )
            .values(**values)
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_by_execution_id(execution_id)


class SandboxProfileRepository(SQLAlchemyRepository[SandboxProfileRecord]):
    model = SandboxProfileRecord

    async def list_enabled(self) -> Sequence[SandboxProfileRecord]:
        return (
            await self.session.scalars(
                select(SandboxProfileRecord)
                .where(SandboxProfileRecord.enabled.is_(True))
                .order_by(SandboxProfileRecord.name, SandboxProfileRecord.version)
            )
        ).all()
