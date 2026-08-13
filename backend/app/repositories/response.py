"""Response Framework repositories."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.response import ResponsePlan, ResponsePlugin, ResponsePolicyRecord
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class ResponsePlanRepository(SQLAlchemyRepository[ResponsePlan]):
    model = ResponsePlan

    async def get(self, entity_id: UUID) -> ResponsePlan | None:
        return await self.session.scalar(
            select(ResponsePlan)
            .where(ResponsePlan.id == entity_id)
            .options(
                selectinload(ResponsePlan.assets),
                selectinload(ResponsePlan.approvals),
                selectinload(ResponsePlan.executions),
                selectinload(ResponsePlan.rollbacks),
                selectinload(ResponsePlan.evidence),
            )
            .execution_options(populate_existing=True)
        )

    async def search(
        self,
        *,
        incident_id: UUID | None = None,
        approval_state: str | None = None,
        execution_state: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[ResponsePlan]:
        statement = select(ResponsePlan)
        count_statement = select(func.count()).select_from(ResponsePlan)
        for column, value in (
            (ResponsePlan.incident_id, incident_id),
            (ResponsePlan.approval_state, approval_state),
            (ResponsePlan.execution_state, execution_state),
        ):
            if value is not None:
                statement = statement.where(column == value)
                count_statement = count_statement.where(column == value)
        total = await self.session.scalar(count_statement)
        items = (
            await self.session.scalars(
                statement.options(
                    selectinload(ResponsePlan.assets),
                    selectinload(ResponsePlan.approvals),
                    selectinload(ResponsePlan.executions),
                    selectinload(ResponsePlan.rollbacks),
                    selectinload(ResponsePlan.evidence),
                )
                .execution_options(populate_existing=True)
                .order_by(ResponsePlan.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)


class ResponsePluginRepository(SQLAlchemyRepository[ResponsePlugin]):
    model = ResponsePlugin

    async def get_by_identity(self, name: str, version: str) -> ResponsePlugin | None:
        return await self.session.scalar(
            select(ResponsePlugin).where(
                ResponsePlugin.name == name, ResponsePlugin.version == version
            )
        )

    async def list_enabled(self) -> Sequence[ResponsePlugin]:
        return (
            await self.session.scalars(
                select(ResponsePlugin)
                .where(ResponsePlugin.enabled.is_(True))
                .order_by(ResponsePlugin.name)
            )
        ).all()


class ResponsePolicyRepository(SQLAlchemyRepository[ResponsePolicyRecord]):
    model = ResponsePolicyRecord

    async def get_by_identity(self, name: str, version: str) -> ResponsePolicyRecord | None:
        return await self.session.scalar(
            select(ResponsePolicyRecord).where(
                ResponsePolicyRecord.name == name,
                ResponsePolicyRecord.version == version,
            )
        )
