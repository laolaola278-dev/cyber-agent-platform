"""Asset Center application service and audited source-of-truth operations."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.registry import AssetRegistry
from app.assets.resolver import AssetResolver, ResolvedAsset
from app.core.enums import AssetRelationType, AssetType
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import AssetConflict, AssetNotFound, AssetResolutionError
from app.models import (
    Asset,
    AssetEvidence,
    AssetRelation,
    AssetReport,
    Evidence,
    Report,
)
from app.repositories import AssetRepository, PageResult
from app.schemas.asset import (
    AssetCreate,
    AssetDiscoveryRequest,
    AssetRelationCreate,
    AssetUpdate,
)


class AssetService:
    """Own canonical identity, graph relations, discovery, search, and soft deletion."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AssetRepository,
        publisher: EventPublisher,
        registry: AssetRegistry | None = None,
        resolver: AssetResolver | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._publisher = publisher
        self._registry = registry or AssetRegistry()
        self._resolver = resolver or AssetResolver()

    async def create(self, payload: AssetCreate, *, trace_id: str) -> Asset:
        asset_type = self._registry.require_type(payload.asset_type)
        canonical = self.canonicalize(asset_type, payload.value)
        existing = await self._repository.get_by_identity(asset_type, canonical)
        if existing is not None:
            raise AssetConflict(f"Asset {asset_type.value}:{canonical} already exists")
        asset = await self._repository.add(
            Asset(
                **payload.model_dump(exclude={"asset_type", "tags"}, mode="python"),
                asset_type=asset_type.value,
                canonical_value=canonical,
            )
        )
        await self._repository.replace_tags(asset, payload.tags)
        await self._publish(
            EventType.ASSET_CREATED, asset, trace_id, payload={"type": asset.asset_type}
        )
        await self._session.commit()
        return await self.get(asset.id)

    async def get(self, asset_id: UUID) -> Asset:
        asset = await self._repository.get_active(asset_id)
        if asset is None:
            raise AssetNotFound(f"Asset {asset_id} not found")
        return asset

    async def search(
        self,
        *,
        name: str | None = None,
        asset_type: AssetType | None = None,
        tag: str | None = None,
        owner: str | None = None,
        risk: str | None = None,
        environment: str | None = None,
        capability: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[Asset]:
        return await self._repository.search(
            name=name,
            asset_type=asset_type,
            tag=tag,
            owner=owner,
            risk=risk,
            environment=environment,
            capability=capability,
            page=page,
            page_size=page_size,
        )

    async def update(self, asset_id: UUID, payload: AssetUpdate, *, trace_id: str) -> Asset:
        asset = await self.get(asset_id)
        changes = payload.model_dump(exclude_unset=True, exclude={"tags"})
        if "value" in changes:
            canonical = self.canonicalize(AssetType(asset.asset_type), changes["value"])
            duplicate = await self._repository.get_by_identity(
                AssetType(asset.asset_type), canonical
            )
            if duplicate is not None and duplicate.id != asset.id:
                raise AssetConflict(f"Asset identity {canonical} already exists")
            asset.canonical_value = canonical
        for field, value in changes.items():
            setattr(asset, field, value)
        if payload.tags is not None:
            await self._repository.replace_tags(asset, payload.tags)
        await self._publish(
            EventType.ASSET_UPDATED,
            asset,
            trace_id,
            payload={"changed_fields": sorted(payload.model_fields_set)},
        )
        await self._session.commit()
        return await self.get(asset.id)

    async def soft_delete(self, asset_id: UUID, *, trace_id: str, actor: str) -> Asset:
        asset = await self.get(asset_id)
        asset.deleted_at = datetime.now(UTC)
        asset.deleted_by = actor
        await self._publish(EventType.ASSET_SOFT_DELETED, asset, trace_id)
        await self._session.commit()
        return asset

    async def add_relation(
        self, source_id: UUID, payload: AssetRelationCreate, *, trace_id: str
    ) -> AssetRelation:
        await self.get(source_id)
        await self.get(payload.target_asset_id)
        relation_type = self._registry.require_relation(payload.relation_type)
        if source_id == payload.target_asset_id:
            raise AssetConflict("Asset relation cannot reference itself")
        existing = await self._repository.get_relation(
            source_id, payload.target_asset_id, relation_type.value
        )
        if existing is not None:
            return existing
        relation = AssetRelation(
            source_asset_id=source_id,
            target_asset_id=payload.target_asset_id,
            relation_type=relation_type.value,
            properties=payload.properties,
        )
        self._session.add(relation)
        await self._session.flush()
        await self._publish(
            EventType.ASSET_RELATION_CREATED,
            await self.get(source_id),
            trace_id,
            payload={
                "target_asset_id": str(payload.target_asset_id),
                "relation": relation_type.value,
            },
        )
        await self._session.commit()
        await self._session.refresh(relation)
        return relation

    async def list_relations(self, asset_id: UUID) -> list[AssetRelation]:
        await self.get(asset_id)
        return await self._repository.list_relations(asset_id)

    async def list_evidence(self, asset_id: UUID) -> list[Evidence]:
        await self.get(asset_id)
        return await self._repository.list_evidence(asset_id)

    async def list_reports(self, asset_id: UUID) -> list[Report]:
        await self.get(asset_id)
        return await self._repository.list_reports(asset_id)

    async def discover(
        self, payload: AssetDiscoveryRequest, *, trace_id: str
    ) -> tuple[Asset, Asset, list[Asset], list[AssetRelation]]:
        try:
            resolved = await self._resolver.resolve_url(payload.url)
            common = payload.model_dump(exclude={"url", "tags"})
            website = await self._upsert_resolved(
                resolved.website, payload.tags, common, trace_id=trace_id
            )
            domain = await self._upsert_resolved(
                resolved.domain, payload.tags, common, trace_id=trace_id
            )
            ips = [
                await self._upsert_resolved(item, payload.tags, common, trace_id=trace_id)
                for item in resolved.ips
            ]
            relations = [
                await self._ensure_relation(
                    website.id,
                    domain.id,
                    AssetRelationType.REFERENCES,
                    {"discovery": "url"},
                    trace_id=trace_id,
                )
            ]
            for ip_asset in ips:
                relations.append(
                    await self._ensure_relation(
                        domain.id,
                        ip_asset.id,
                        AssetRelationType.RESOLVES_TO,
                        {"discovery": "dns"},
                        trace_id=trace_id,
                    )
                )
            await self._publish(
                EventType.ASSET_DISCOVERED,
                website,
                trace_id,
                payload={"domain_id": str(domain.id), "ip_count": len(ips)},
            )
            await self._session.commit()
        except (ValueError, OSError) as error:
            await self._session.rollback()
            raise AssetResolutionError(str(error)) from error
        return website, domain, ips, relations

    async def link_evidence(self, asset_id: UUID, evidence_id: UUID, *, trace_id: str) -> None:
        asset = await self.get(asset_id)
        if await self._session.get(Evidence, evidence_id) is None:
            raise AssetNotFound(f"Evidence {evidence_id} not found")
        if not any(
            item.id == evidence_id for item in await self._repository.list_evidence(asset_id)
        ):
            self._session.add(AssetEvidence(asset_id=asset_id, evidence_id=evidence_id))
            await self._session.flush()
        await self._publish(
            EventType.ASSET_EVIDENCE_LINKED,
            asset,
            trace_id,
            payload={"evidence_id": str(evidence_id)},
        )

    async def link_report(self, asset_id: UUID, report_id: UUID, *, trace_id: str) -> None:
        asset = await self.get(asset_id)
        if await self._session.get(Report, report_id) is None:
            raise AssetNotFound(f"Report {report_id} not found")
        if not any(item.id == report_id for item in await self._repository.list_reports(asset_id)):
            self._session.add(AssetReport(asset_id=asset_id, report_id=report_id))
            await self._session.flush()
        await self._publish(
            EventType.ASSET_REPORT_LINKED,
            asset,
            trace_id,
            payload={"report_id": str(report_id)},
        )

    async def _upsert_resolved(
        self,
        resolved: ResolvedAsset,
        tags: list[str],
        common: dict[str, Any],
        *,
        trace_id: str,
    ) -> Asset:
        existing = await self._repository.get_by_identity(
            resolved.asset_type, resolved.canonical_value
        )
        if existing is not None:
            restored = existing.deleted_at is not None
            if restored:
                existing.deleted_at = None
                existing.deleted_by = None
            await self._repository.replace_tags(
                existing, [*(tag.name for tag in existing.tags), *tags]
            )
            await self._publish(
                EventType.ASSET_UPDATED,
                existing,
                trace_id,
                payload={"source": "discovery", "restored": restored},
            )
            return existing
        asset = await self._repository.add(
            Asset(
                asset_type=resolved.asset_type.value,
                name=resolved.name,
                value=resolved.value,
                canonical_value=resolved.canonical_value,
                **common,
            )
        )
        await self._repository.replace_tags(asset, tags)
        await self._publish(
            EventType.ASSET_CREATED,
            asset,
            trace_id,
            payload={"type": asset.asset_type, "source": "discovery"},
        )
        return asset

    async def _ensure_relation(
        self,
        source_id: UUID,
        target_id: UUID,
        relation_type: AssetRelationType,
        properties: dict[str, Any],
        *,
        trace_id: str,
    ) -> AssetRelation:
        existing = await self._repository.get_relation(source_id, target_id, relation_type.value)
        if existing is not None:
            return existing
        relation = AssetRelation(
            source_asset_id=source_id,
            target_asset_id=target_id,
            relation_type=relation_type.value,
            properties=properties,
        )
        self._session.add(relation)
        await self._session.flush()
        await self._publish(
            EventType.ASSET_RELATION_CREATED,
            await self.get(source_id),
            trace_id,
            payload={
                "target_asset_id": str(target_id),
                "relation": relation_type.value,
                "source": "discovery",
            },
        )
        return relation

    @staticmethod
    def canonicalize(asset_type: AssetType, value: str) -> str:
        normalized = value.strip()
        if asset_type in {AssetType.DOMAIN, AssetType.HOST}:
            return normalized.rstrip(".").casefold()
        if asset_type == AssetType.IP:
            import ipaddress

            return str(ipaddress.ip_address(normalized))
        if asset_type in {AssetType.WEBSITE, AssetType.REPOSITORY}:
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit(normalized)
            if not parsed.scheme or not parsed.hostname:
                raise AssetResolutionError(f"{asset_type.value} requires an absolute URL")
            host = parsed.hostname.rstrip(".").casefold()
            port = parsed.port
            if (parsed.scheme.casefold(), port) in {("http", 80), ("https", 443)}:
                port = None
            netloc = host if port is None else f"{host}:{port}"
            return urlunsplit(
                (parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, "")
            )
        return normalized.casefold()

    async def _publish(
        self,
        event_type: EventType,
        asset: Asset,
        trace_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=trace_id,
                aggregate_id=asset.id,
                actor="asset-service",
                resource=f"asset:{asset.id}",
                payload=payload or {},
            )
        )
