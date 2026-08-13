"""Incident and Investigation Case repositories."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import Incident, InvestigationCase
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class IncidentRepository(SQLAlchemyRepository[Incident]):
    model = Incident

    @staticmethod
    def _options() -> tuple[object, ...]:
        return (
            selectinload(Incident.timelines),
            selectinload(Incident.artifacts),
            selectinload(Incident.cases).selectinload(InvestigationCase.comments),
            selectinload(Incident.findings),
            selectinload(Incident.events),
            selectinload(Incident.knowledge),
            selectinload(Incident.assets),
        )

    async def get(self, entity_id: UUID) -> Incident | None:
        return await self.session.scalar(
            select(Incident).where(Incident.id == entity_id).options(*self._options())
        )

    async def search(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
        assignee: str | None = None,
        queue: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[Incident]:
        statement = select(Incident)
        count_statement = select(func.count()).select_from(Incident)
        filters = {
            Incident.severity: severity,
            Incident.status: status,
            Incident.priority: priority,
            Incident.owner: owner,
            Incident.assignee: assignee,
            Incident.queue: queue,
        }
        for column, value in filters.items():
            if value is not None:
                statement = statement.where(column == value)
                count_statement = count_statement.where(column == value)
        total = await self.session.scalar(count_statement)
        items = (
            (
                await self.session.scalars(
                    statement.options(*self._options())
                    .order_by(Incident.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .unique()
            .all()
        )
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def find_duplicate(
        self, correlation_key: str, *, not_before: datetime
    ) -> Incident | None:
        return await self.session.scalar(
            select(Incident)
            .where(
                Incident.correlation_key == correlation_key,
                Incident.created_at >= not_before,
                Incident.duplicate_of_id.is_(None),
                Incident.status != "CLOSED",
            )
            .order_by(Incident.created_at)
            .limit(1)
            .options(*self._options())
        )


class InvestigationCaseRepository(SQLAlchemyRepository[InvestigationCase]):
    model = InvestigationCase

    async def get(self, entity_id: UUID) -> InvestigationCase | None:
        return await self.session.scalar(
            select(InvestigationCase)
            .where(InvestigationCase.id == entity_id)
            .options(selectinload(InvestigationCase.comments))
        )

    async def search(
        self,
        *,
        incident_id: UUID | None = None,
        status: str | None = None,
        assignee: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[InvestigationCase]:
        statement = select(InvestigationCase)
        count_statement = select(func.count()).select_from(InvestigationCase)
        if incident_id is not None:
            statement = statement.where(InvestigationCase.incident_id == incident_id)
            count_statement = count_statement.where(InvestigationCase.incident_id == incident_id)
        if status is not None:
            statement = statement.where(InvestigationCase.status == status)
            count_statement = count_statement.where(InvestigationCase.status == status)
        if assignee is not None:
            statement = statement.where(InvestigationCase.assignee == assignee)
            count_statement = count_statement.where(InvestigationCase.assignee == assignee)
        total = await self.session.scalar(count_statement)
        items = (
            await self.session.scalars(
                statement.options(selectinload(InvestigationCase.comments))
                .order_by(InvestigationCase.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)
