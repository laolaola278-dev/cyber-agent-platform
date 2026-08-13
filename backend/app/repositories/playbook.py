"""Persistence operations for SOAR Playbooks and durable executions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.playbook import (
    Playbook,
    PlaybookExecution,
    PlaybookStepExecution,
    PlaybookTrigger,
    PlaybookVersion,
)
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class PlaybookRepository(SQLAlchemyRepository[Playbook]):
    model = Playbook

    async def list_with_versions(self, *, page: int, page_size: int) -> PageResult[Playbook]:
        result = await self.list_page(page=page, page_size=page_size)
        items: list[Playbook] = []
        for item in result.items:
            persisted = await self.get_with_latest_version(item.id)
            if persisted is not None:
                items.append(persisted)
        return PageResult(items=items, page=page, page_size=page_size, total=result.total)

    async def get_by_name(self, name: str) -> Playbook | None:
        return await self.session.scalar(select(Playbook).where(Playbook.name == name))

    async def get_with_latest_version(self, playbook_id: UUID) -> Playbook | None:
        return await self.session.scalar(
            select(Playbook)
            .where(Playbook.id == playbook_id)
            .options(selectinload(Playbook.versions), selectinload(Playbook.executions))
        )


class PlaybookVersionRepository(SQLAlchemyRepository[PlaybookVersion]):
    model = PlaybookVersion

    async def latest(self, playbook_id: UUID) -> PlaybookVersion | None:
        return await self.session.scalar(
            select(PlaybookVersion)
            .where(PlaybookVersion.playbook_id == playbook_id)
            .order_by(PlaybookVersion.created_at.desc())
            .limit(1)
            .options(selectinload(PlaybookVersion.triggers))
        )


class PlaybookExecutionRepository(SQLAlchemyRepository[PlaybookExecution]):
    model = PlaybookExecution

    async def get_with_steps(self, execution_id: UUID) -> PlaybookExecution | None:
        return await self.session.scalar(
            select(PlaybookExecution)
            .where(PlaybookExecution.id == execution_id)
            .options(
                selectinload(PlaybookExecution.steps),
                selectinload(PlaybookExecution.version),
                selectinload(PlaybookExecution.playbook),
            )
            .execution_options(populate_existing=True)
        )

    async def get_by_idempotency_key(self, key: str) -> PlaybookExecution | None:
        return await self.session.scalar(
            select(PlaybookExecution).where(PlaybookExecution.idempotency_key == key)
        )

    async def list_with_steps(self, *, page: int, page_size: int) -> PageResult[PlaybookExecution]:
        result = await self.list_page(page=page, page_size=page_size)
        items: list[PlaybookExecution] = []
        for item in result.items:
            persisted = await self.get_with_steps(item.id)
            if persisted is not None:
                items.append(persisted)
        return PageResult(items=items, page=page, page_size=page_size, total=result.total)

    async def add_step(self, step: PlaybookStepExecution) -> PlaybookStepExecution:
        self.session.add(step)
        await self.session.flush()
        await self.session.refresh(step)
        return step

    async def list_steps(self, execution_id: UUID) -> list[PlaybookStepExecution]:
        return list(
            await self.session.scalars(
                select(PlaybookStepExecution)
                .where(PlaybookStepExecution.execution_id == execution_id)
                .order_by(PlaybookStepExecution.created_at, PlaybookStepExecution.step_id)
            )
        )


class PlaybookTriggerRepository(SQLAlchemyRepository[PlaybookTrigger]):
    model = PlaybookTrigger

    async def active_for_type(self, trigger_type: str) -> list[PlaybookTrigger]:
        return list(
            await self.session.scalars(
                select(PlaybookTrigger)
                .where(
                    PlaybookTrigger.trigger_type == trigger_type, PlaybookTrigger.enabled.is_(True)
                )
                .options(
                    selectinload(PlaybookTrigger.version).selectinload(PlaybookVersion.playbook)
                )
            )
        )
