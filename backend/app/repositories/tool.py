"""Tool Registry persistence operations."""

from uuid import UUID

from sqlalchemy import func, select

from app.models import Tool, ToolVersion
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class ToolRepository(SQLAlchemyRepository[Tool]):
    model = Tool

    async def get_by_name(self, name: str) -> Tool | None:
        return await self.session.scalar(select(Tool).where(Tool.name == name))

    async def add_version(self, version: ToolVersion) -> ToolVersion:
        self.session.add(version)
        await self.session.flush()
        return version

    async def list_versions(
        self, tool_id: UUID, *, page: int, page_size: int
    ) -> PageResult[ToolVersion]:
        criterion = ToolVersion.tool_id == tool_id
        total = await self.session.scalar(
            select(func.count()).select_from(ToolVersion).where(criterion)
        )
        statement = (
            select(ToolVersion)
            .where(criterion)
            .order_by(ToolVersion.created_at.desc(), ToolVersion.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.session.scalars(statement)).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)
