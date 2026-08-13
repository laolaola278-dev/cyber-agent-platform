"""Knowledge Center repositories and portable full-text-like search."""

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import selectinload

from app.models.knowledge import (
    Knowledge,
    KnowledgeRelation,
    KnowledgeSource,
    KnowledgeVersion,
)
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class KnowledgeSourceRepository(SQLAlchemyRepository[KnowledgeSource]):
    model = KnowledgeSource

    async def get_by_name(self, name: str) -> KnowledgeSource | None:
        return await self.session.scalar(
            select(KnowledgeSource).where(func.lower(KnowledgeSource.name) == name.casefold())
        )


class KnowledgeRepository(SQLAlchemyRepository[Knowledge]):
    model = Knowledge

    async def get_with_versions(self, knowledge_id: UUID) -> Knowledge | None:
        return await self.session.scalar(
            select(Knowledge)
            .where(Knowledge.id == knowledge_id)
            .options(selectinload(Knowledge.versions))
        )

    async def get_by_identity(
        self, source_id: UUID, knowledge_type: str, external_id: str
    ) -> Knowledge | None:
        return await self.session.scalar(
            select(Knowledge)
            .where(
                Knowledge.source_id == source_id,
                Knowledge.knowledge_type == knowledge_type,
                func.lower(Knowledge.external_id) == external_id.casefold(),
            )
            .options(selectinload(Knowledge.versions))
        )

    async def get_by_external_id(self, knowledge_type: str, external_id: str) -> Knowledge | None:
        return await self.session.scalar(
            select(Knowledge)
            .where(
                Knowledge.knowledge_type == knowledge_type,
                func.lower(Knowledge.external_id) == external_id.casefold(),
            )
            .order_by(Knowledge.updated_at.desc(), Knowledge.id)
            .limit(1)
        )

    async def get_version_snapshot(
        self, knowledge_id: UUID, version: str, content_hash: str
    ) -> KnowledgeVersion | None:
        return await self.session.scalar(
            select(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_id == knowledge_id,
                KnowledgeVersion.version == version,
                KnowledgeVersion.content_hash == content_hash,
            )
        )

    async def get_current_version(self, knowledge: Knowledge) -> KnowledgeVersion | None:
        return await self.session.scalar(
            select(KnowledgeVersion)
            .where(
                KnowledgeVersion.knowledge_id == knowledge.id,
                KnowledgeVersion.version == knowledge.current_version,
                KnowledgeVersion.content_hash == knowledge.current_content_hash,
            )
            .order_by(KnowledgeVersion.imported_at.desc(), KnowledgeVersion.id.desc())
            .limit(1)
        )

    async def search(
        self,
        *,
        query: str | None = None,
        knowledge_type: str | None = None,
        source: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[Knowledge]:
        statement: Select[tuple[Knowledge]] = select(Knowledge).join(KnowledgeSource)
        if query:
            pattern = f"%{query.strip().casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(Knowledge.external_id).like(pattern),
                    func.lower(Knowledge.title).like(pattern),
                    func.lower(Knowledge.description).like(pattern),
                )
            )
        if knowledge_type:
            statement = statement.where(Knowledge.knowledge_type == knowledge_type.upper())
        if source:
            statement = statement.where(func.lower(KnowledgeSource.name) == source.casefold())
        if status:
            statement = statement.where(Knowledge.status == status.upper())
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        items = list(
            await self.session.scalars(
                statement.order_by(Knowledge.external_id, Knowledge.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def list_relations(self, knowledge_id: UUID) -> list[KnowledgeRelation]:
        return list(
            await self.session.scalars(
                select(KnowledgeRelation)
                .where(
                    or_(
                        KnowledgeRelation.source_knowledge_id == knowledge_id,
                        KnowledgeRelation.target_knowledge_id == knowledge_id,
                    )
                )
                .order_by(KnowledgeRelation.created_at, KnowledgeRelation.id)
            )
        )

    async def get_relation(
        self, source_id: UUID, target_id: UUID, relation_type: str
    ) -> KnowledgeRelation | None:
        return await self.session.scalar(
            select(KnowledgeRelation).where(
                KnowledgeRelation.source_knowledge_id == source_id,
                KnowledgeRelation.target_knowledge_id == target_id,
                KnowledgeRelation.relation_type == relation_type,
            )
        )
