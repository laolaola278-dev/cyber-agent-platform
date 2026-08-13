"""Detection Framework repositories."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import DetectionPlugin, DetectionTask, EventAsset, SecurityEvent
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class DetectionTaskRepository(SQLAlchemyRepository[DetectionTask]):
    model = DetectionTask

    async def list_page(self, *, page: int = 1, page_size: int = 100) -> PageResult[DetectionTask]:
        total = await self.session.scalar(select(func.count()).select_from(DetectionTask))
        items = (
            await self.session.scalars(
                select(DetectionTask)
                .options(
                    selectinload(DetectionTask.plugin),
                    selectinload(DetectionTask.events),
                )
                .order_by(DetectionTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)


class SecurityEventRepository(SQLAlchemyRepository[SecurityEvent]):
    model = SecurityEvent

    async def get(self, entity_id: UUID) -> SecurityEvent | None:
        return await self.session.scalar(
            select(SecurityEvent)
            .where(SecurityEvent.id == entity_id)
            .options(
                selectinload(SecurityEvent.references),
                selectinload(SecurityEvent.evidence_links),
                selectinload(SecurityEvent.knowledge_links),
                selectinload(SecurityEvent.asset_links),
            )
        )

    async def search(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        asset_id: UUID | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[SecurityEvent]:
        statement = select(SecurityEvent)
        count_statement = select(func.count()).select_from(SecurityEvent)
        if severity:
            statement = statement.where(SecurityEvent.severity == severity)
            count_statement = count_statement.where(SecurityEvent.severity == severity)
        if status:
            statement = statement.where(SecurityEvent.status == status)
            count_statement = count_statement.where(SecurityEvent.status == status)
        if asset_id:
            statement = statement.join(EventAsset).where(EventAsset.asset_id == asset_id)
            count_statement = count_statement.join(EventAsset).where(
                EventAsset.asset_id == asset_id
            )
        total = await self.session.scalar(count_statement)
        items = (
            (
                await self.session.scalars(
                    statement.options(
                        selectinload(SecurityEvent.references),
                        selectinload(SecurityEvent.evidence_links),
                        selectinload(SecurityEvent.knowledge_links),
                        selectinload(SecurityEvent.asset_links),
                    )
                    .order_by(SecurityEvent.timestamp.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .unique()
            .all()
        )
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)


class DetectionPluginRepository(SQLAlchemyRepository[DetectionPlugin]):
    model = DetectionPlugin

    async def get_by_identity(self, name: str, version: str) -> DetectionPlugin | None:
        return await self.session.scalar(
            select(DetectionPlugin)
            .where(DetectionPlugin.name == name, DetectionPlugin.version == version)
            .options(selectinload(DetectionPlugin.capabilities))
        )

    async def list_enabled(self) -> Sequence[DetectionPlugin]:
        return (
            await self.session.scalars(
                select(DetectionPlugin)
                .where(DetectionPlugin.enabled.is_(True))
                .options(selectinload(DetectionPlugin.capabilities))
                .order_by(DetectionPlugin.name)
            )
        ).all()
