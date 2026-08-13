"""Generic asynchronous repository primitives."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base
from app.repositories.pagination import PageResult


class SQLAlchemyRepository[ModelT: Base]:
    """Repository base with one uniform page/page_size/total contract."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_page(self, *, page: int = 1, page_size: int = 100) -> PageResult[ModelT]:
        total = await self.session.scalar(select(func.count()).select_from(self.model))
        statement = select(self.model).offset((page - 1) * page_size).limit(page_size)
        items = (await self.session.scalars(statement)).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def get(self, entity_id: UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity
