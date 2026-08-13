"""Knowledge Center application service and cross-domain provenance links."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import KnowledgeType
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import KnowledgeNotFound
from app.knowledge.importer import ImportResult, KnowledgeImporter
from app.models import (
    Asset,
    AssetKnowledge,
    Evidence,
    EvidenceKnowledge,
    Knowledge,
    Report,
    ReportKnowledge,
)
from app.repositories import KnowledgeRepository, PageResult
from app.schemas.knowledge import KnowledgeRead


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        repository: KnowledgeRepository,
        importer: KnowledgeImporter,
        publisher: EventPublisher,
    ) -> None:
        self._session = session
        self._repository = repository
        self._importer = importer
        self._publisher = publisher

    async def get(self, knowledge_id: UUID) -> Knowledge:
        knowledge = await self._repository.get_with_versions(knowledge_id)
        if knowledge is None:
            raise KnowledgeNotFound(f"Knowledge {knowledge_id} not found")
        return knowledge

    async def get_by_external_id(self, knowledge_type: str, external_id: str) -> Knowledge:
        knowledge = await self._repository.get_by_external_id(knowledge_type.upper(), external_id)
        if knowledge is None:
            raise KnowledgeNotFound(f"Knowledge {knowledge_type}:{external_id} not found")
        return knowledge

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
        return await self._repository.search(
            query=query,
            knowledge_type=knowledge_type,
            source=source,
            status=status,
            page=page,
            page_size=page_size,
        )

    async def import_payload(
        self,
        *,
        source: str,
        provider: str,
        format_name: str,
        payload: object,
        trace_id: str,
    ) -> ImportResult:
        return await self._importer.import_payload(
            source_name=source,
            provider_type=provider,
            format_name=format_name,
            payload=payload,
            trace_id=trace_id,
        )

    async def link_asset(self, asset_id: UUID, knowledge_id: UUID, *, trace_id: str) -> None:
        await self._link(
            owner_type="asset",
            owner_id=asset_id,
            knowledge_id=knowledge_id,
            owner_model=Asset,
            link_model=AssetKnowledge,
            event_type=EventType.ASSET_KNOWLEDGE_LINKED,
            trace_id=trace_id,
        )

    async def link_evidence(self, evidence_id: UUID, knowledge_id: UUID, *, trace_id: str) -> None:
        await self._link(
            owner_type="evidence",
            owner_id=evidence_id,
            knowledge_id=knowledge_id,
            owner_model=Evidence,
            link_model=EvidenceKnowledge,
            event_type=EventType.EVIDENCE_KNOWLEDGE_LINKED,
            trace_id=trace_id,
        )

    async def link_report(self, report_id: UUID, knowledge_id: UUID, *, trace_id: str) -> None:
        await self._link(
            owner_type="report",
            owner_id=report_id,
            knowledge_id=knowledge_id,
            owner_model=Report,
            link_model=ReportKnowledge,
            event_type=EventType.REPORT_KNOWLEDGE_LINKED,
            trace_id=trace_id,
        )

    async def _link(
        self,
        *,
        owner_type: str,
        owner_id: UUID,
        knowledge_id: UUID,
        owner_model: type[Asset] | type[Evidence] | type[Report],
        link_model: type[AssetKnowledge] | type[EvidenceKnowledge] | type[ReportKnowledge],
        event_type: EventType,
        trace_id: str,
    ) -> None:
        if await self._session.get(owner_model, owner_id) is None:
            raise KnowledgeNotFound(f"{owner_type.title()} {owner_id} not found")
        knowledge = await self.get(knowledge_id)
        version = await self._repository.get_current_version(knowledge)
        if version is None:
            raise KnowledgeNotFound(f"Current version for Knowledge {knowledge_id} not found")
        owner_column = getattr(link_model, f"{owner_type}_id")
        existing = await self._session.scalar(
            select(link_model).where(
                owner_column == owner_id,
                link_model.knowledge_id == knowledge_id,
            )
        )
        if existing is None:
            self._session.add(
                link_model(
                    **{
                        f"{owner_type}_id": owner_id,
                        "knowledge_id": knowledge_id,
                        "knowledge_version_id": version.id,
                    }
                )
            )
            await self._session.flush()
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=trace_id,
                aggregate_id=knowledge_id,
                actor="knowledge-service",
                resource=f"{owner_type}:{owner_id}",
                payload={
                    "knowledge_id": str(knowledge_id),
                    "knowledge_version_id": str(version.id),
                },
            )
        )
        await self._session.commit()

    @staticmethod
    def to_read(knowledge: Knowledge) -> KnowledgeRead:
        return KnowledgeRead(
            id=knowledge.id,
            knowledge_type=knowledge.knowledge_type,
            external_id=knowledge.external_id,
            source=knowledge.source.name,
            version=knowledge.current_version,
            title=knowledge.title,
            description=knowledge.description,
            references=knowledge.references,
            status=knowledge.status,
            attributes=knowledge.attributes,
            created_at=knowledge.created_at,
            updated_at=knowledge.updated_at,
        )


LOOKUP_TYPES = {
    "cve": KnowledgeType.CVE.value,
    "cwe": KnowledgeType.CWE.value,
    "attack": KnowledgeType.ATTACK_TECHNIQUE.value,
}
