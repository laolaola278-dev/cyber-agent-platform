"""Notification and Ticket Framework repositories."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.notification import (
    NotificationPlan,
    NotificationPlugin,
    NotificationTemplate,
    Ticket,
)
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class NotificationPlanRepository(SQLAlchemyRepository[NotificationPlan]):
    model = NotificationPlan

    async def get(self, entity_id: UUID) -> NotificationPlan | None:
        return await self.session.scalar(
            select(NotificationPlan)
            .where(NotificationPlan.id == entity_id)
            .options(
                selectinload(NotificationPlan.executions),
                selectinload(NotificationPlan.evidence),
            )
            .execution_options(populate_existing=True)
        )

    async def search(
        self,
        *,
        incident_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[NotificationPlan]:
        statement = select(NotificationPlan)
        count_statement = select(func.count()).select_from(NotificationPlan)
        for column, value in (
            (NotificationPlan.incident_id, incident_id),
            (NotificationPlan.status, status),
        ):
            if value is not None:
                statement = statement.where(column == value)
                count_statement = count_statement.where(column == value)
        total = await self.session.scalar(count_statement)
        items = (
            await self.session.scalars(
                statement.options(
                    selectinload(NotificationPlan.executions),
                    selectinload(NotificationPlan.evidence),
                )
                .execution_options(populate_existing=True)
                .order_by(NotificationPlan.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def latest_duplicate(self, key: str) -> NotificationPlan | None:
        return await self.session.scalar(
            select(NotificationPlan)
            .where(
                NotificationPlan.deduplication_key == key,
                NotificationPlan.status.in_(["SENT", "VERIFIED"]),
            )
            .order_by(NotificationPlan.created_at.desc())
            .limit(1)
        )

    async def count_sent_since(self, since: datetime) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(NotificationPlan)
            .where(
                NotificationPlan.created_at >= since,
                NotificationPlan.status.in_(["SENT", "VERIFIED"]),
            )
        )
        return count or 0


class NotificationPluginRepository(SQLAlchemyRepository[NotificationPlugin]):
    model = NotificationPlugin

    async def get_by_identity(self, name: str, version: str) -> NotificationPlugin | None:
        return await self.session.scalar(
            select(NotificationPlugin).where(
                NotificationPlugin.name == name, NotificationPlugin.version == version
            )
        )

    async def list_enabled(self) -> Sequence[NotificationPlugin]:
        return (
            await self.session.scalars(
                select(NotificationPlugin)
                .where(NotificationPlugin.enabled.is_(True))
                .order_by(NotificationPlugin.name)
            )
        ).all()


class NotificationTemplateRepository(SQLAlchemyRepository[NotificationTemplate]):
    model = NotificationTemplate

    async def get_by_identity(self, name: str, version: str) -> NotificationTemplate | None:
        return await self.session.scalar(
            select(NotificationTemplate).where(
                NotificationTemplate.name == name,
                NotificationTemplate.version == version,
            )
        )


class TicketRepository(SQLAlchemyRepository[Ticket]):
    model = Ticket

    async def search(self, *, status: str | None, page: int, page_size: int) -> PageResult[Ticket]:
        statement = select(Ticket)
        count_statement = select(func.count()).select_from(Ticket)
        if status is not None:
            statement = statement.where(Ticket.status == status)
            count_statement = count_statement.where(Ticket.status == status)
        total = await self.session.scalar(count_statement)
        items = (
            await self.session.scalars(
                statement.order_by(Ticket.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)
