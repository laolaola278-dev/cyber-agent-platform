"""Knowledge import orchestration with immutable source snapshots."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import KnowledgeValidationError
from app.knowledge.registry import KnowledgeRegistry
from app.knowledge.resolver import KnowledgeResolver
from app.models import Knowledge, KnowledgeRelation, KnowledgeSource, KnowledgeVersion
from app.repositories import KnowledgeRepository, KnowledgeSourceRepository
from app.schemas.knowledge import KnowledgeRecord


@dataclass(frozen=True, slots=True)
class ImportResult:
    source: str
    imported: int
    unchanged: int
    relations: int
    knowledge_ids: list[UUID]


class KnowledgeImporter:
    """Validate, canonicalize, version, relate, and audit imported knowledge."""

    def __init__(
        self,
        session: AsyncSession,
        repository: KnowledgeRepository,
        source_repository: KnowledgeSourceRepository,
        publisher: EventPublisher,
        registry: KnowledgeRegistry,
        resolver: KnowledgeResolver,
    ) -> None:
        self._session = session
        self._repository = repository
        self._sources = source_repository
        self._publisher = publisher
        self._registry = registry
        self._resolver = resolver

    async def import_payload(
        self,
        *,
        source_name: str,
        provider_type: str,
        format_name: str,
        payload: Any,
        trace_id: str,
    ) -> ImportResult:
        importer = self._registry.require_importer(format_name)
        records = importer.parse(payload, source=source_name)
        source = await self._ensure_source(source_name, provider_type)
        imported = 0
        unchanged = 0
        identities: dict[tuple[str, str, str], Knowledge] = {}
        for record in records:
            knowledge, changed = await self._import_record(source, record, trace_id=trace_id)
            identity = (
                source.name.casefold(),
                knowledge.knowledge_type,
                knowledge.external_id.casefold(),
            )
            identities[identity] = knowledge
            imported += int(changed)
            unchanged += int(not changed)
        relations = await self._import_relations(records, identities, trace_id=trace_id)
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.KNOWLEDGE_IMPORTED,
                trace_id=trace_id,
                actor="knowledge-importer",
                resource=f"knowledge-source:{source.id}",
                aggregate_id=source.id,
                payload={
                    "source": source.name,
                    "format": format_name,
                    "record_count": len(records),
                },
                result={"imported": imported, "unchanged": unchanged, "relations": relations},
            )
        )
        await self._session.commit()
        return ImportResult(
            source=source.name,
            imported=imported,
            unchanged=unchanged,
            relations=relations,
            knowledge_ids=[item.id for item in identities.values()],
        )

    async def _ensure_source(self, name: str, provider_type: str) -> KnowledgeSource:
        existing = await self._sources.get_by_name(name)
        if existing is not None:
            if not existing.enabled:
                raise KnowledgeValidationError(f"Knowledge source {name} is disabled")
            return existing
        return await self._sources.add(
            KnowledgeSource(
                name=name.strip(), provider_type=provider_type.strip(), configuration={}
            )
        )

    async def _import_record(
        self, source: KnowledgeSource, record: KnowledgeRecord, *, trace_id: str
    ) -> tuple[Knowledge, bool]:
        knowledge_type = self._registry.require_type(record.knowledge_type)
        external_id = self._resolver.canonical_external_id(knowledge_type, record.external_id)
        existing = await self._repository.get_by_identity(source.id, knowledge_type, external_id)
        snapshot = self._snapshot(record, knowledge_type=knowledge_type, external_id=external_id)
        content_hash = sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if existing is None:
            existing = await self._repository.add(
                Knowledge(
                    source_id=source.id,
                    knowledge_type=knowledge_type,
                    external_id=external_id,
                    current_version=record.version,
                    current_content_hash=content_hash,
                    title=record.title,
                    description=record.description,
                    references=record.references,
                    status=record.status.value,
                    attributes=record.attributes,
                )
            )
        duplicate = await self._repository.get_version_snapshot(
            existing.id, record.version, content_hash
        )
        if duplicate is not None:
            return existing, False
        version = KnowledgeVersion(
            knowledge_id=existing.id,
            version=record.version,
            content_hash=content_hash,
            payload=snapshot,
            source_updated_at=record.source_updated_at,
        )
        self._session.add(version)
        existing.current_version = record.version
        existing.current_content_hash = content_hash
        existing.title = record.title
        existing.description = record.description
        existing.references = record.references
        existing.status = record.status.value
        existing.attributes = record.attributes
        await self._session.flush()
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.KNOWLEDGE_VERSION_CREATED,
                trace_id=trace_id,
                aggregate_id=existing.id,
                actor="knowledge-importer",
                resource=f"knowledge:{existing.id}",
                payload={
                    "source": source.name,
                    "external_id": external_id,
                    "version": record.version,
                    "content_hash": content_hash,
                },
            )
        )
        return existing, True

    async def _import_relations(
        self,
        records: list[KnowledgeRecord],
        identities: dict[tuple[str, str, str], Knowledge],
        *,
        trace_id: str,
    ) -> int:
        created = 0
        for record in records:
            source_key = (
                record.source.casefold(),
                self._registry.require_type(record.knowledge_type),
                self._resolver.canonical_external_id(
                    record.knowledge_type, record.external_id
                ).casefold(),
            )
            source = identities[source_key]
            for relation in record.relations:
                target_source = relation.target_source or record.source
                target_type = self._registry.require_type(relation.target_type)
                target_id = self._resolver.canonical_external_id(
                    target_type, relation.target_external_id
                )
                target = identities.get(
                    (target_source.casefold(), target_type, target_id.casefold())
                )
                if target is None:
                    source_row = await self._sources.get_by_name(target_source)
                    if source_row is not None:
                        target = await self._repository.get_by_identity(
                            source_row.id, target_type, target_id
                        )
                if target is None:
                    raise KnowledgeValidationError(
                        f"Relation target not found: {target_type}:{target_id}"
                    )
                relation_type = self._resolver.relation_type(relation.relation_type)
                if source.id == target.id:
                    raise KnowledgeValidationError("Knowledge relation cannot reference itself")
                if await self._repository.get_relation(source.id, target.id, relation_type):
                    continue
                self._session.add(
                    KnowledgeRelation(
                        source_knowledge_id=source.id,
                        target_knowledge_id=target.id,
                        relation_type=relation_type,
                        source_name=record.source,
                        properties=relation.properties,
                    )
                )
                await self._session.flush()
                await self._publisher.publish(
                    PlatformEvent(
                        type=EventType.KNOWLEDGE_RELATION_CREATED,
                        trace_id=trace_id,
                        aggregate_id=source.id,
                        actor="knowledge-importer",
                        resource=f"knowledge:{source.id}",
                        payload={
                            "target_knowledge_id": str(target.id),
                            "relation_type": relation_type,
                        },
                    )
                )
                created += 1
        return created

    @staticmethod
    def _snapshot(
        record: KnowledgeRecord, *, knowledge_type: str, external_id: str
    ) -> dict[str, Any]:
        data = record.model_dump(mode="json")
        data["knowledge_type"] = knowledge_type
        data["external_id"] = external_id
        return data
