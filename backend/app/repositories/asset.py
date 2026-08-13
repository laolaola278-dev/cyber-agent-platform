"""Asset Center repositories with soft-delete-aware search."""

from uuid import UUID

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.core.enums import AssetType
from app.models.asset import Asset, AssetEvidence, AssetRelation, AssetReport, AssetTag
from app.models.runtime import Evidence, Report
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class AssetRepository(SQLAlchemyRepository[Asset]):
    model = Asset

    async def get_active(self, asset_id: UUID) -> Asset | None:
        return await self.session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.deleted_at.is_(None))
            .options(selectinload(Asset.tags))
        )

    async def get_by_identity(self, asset_type: AssetType, canonical_value: str) -> Asset | None:
        return await self.session.scalar(
            select(Asset)
            .where(
                Asset.asset_type == asset_type.value,
                Asset.canonical_value == canonical_value,
            )
            .options(selectinload(Asset.tags))
        )

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
        statement: Select[tuple[Asset]] = select(Asset).where(Asset.deleted_at.is_(None))
        if tag:
            statement = statement.join(AssetTag).where(func.lower(AssetTag.name) == tag.casefold())
        if name:
            pattern = f"%{name.casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(Asset.name).like(pattern),
                    func.lower(Asset.value).like(pattern),
                )
            )
        if asset_type:
            statement = statement.where(Asset.asset_type == asset_type.value)
        if owner:
            statement = statement.where(func.lower(Asset.owner) == owner.casefold())
        if risk:
            statement = statement.where(func.lower(Asset.risk) == risk.casefold())
        if environment:
            statement = statement.where(func.lower(Asset.environment) == environment.casefold())
        if capability:
            escaped = (
                capability.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                .replace('"', '\\"')
            )
            statement = statement.where(
                cast(Asset.capabilities, String).like(f'%"{escaped}"%', escape="\\")
            )
        statement = statement.distinct()
        total_statement = select(func.count()).select_from(statement.subquery())
        total = await self.session.scalar(total_statement)
        items = list(
            await self.session.scalars(
                statement.options(selectinload(Asset.tags))
                .order_by(Asset.name, Asset.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def replace_tags(self, asset: Asset, names: list[str]) -> None:
        existing = {tag.name: tag for tag in asset.tags}
        normalized = {name.strip().casefold() for name in names if name.strip()}
        for name, tag in existing.items():
            if name not in normalized:
                await self.session.delete(tag)
        for name in normalized - existing.keys():
            self.session.add(AssetTag(asset_id=asset.id, name=name))
        await self.session.flush()
        await self.session.refresh(asset, attribute_names=["tags"])

    async def list_relations(self, asset_id: UUID) -> list[AssetRelation]:
        return list(
            await self.session.scalars(
                select(AssetRelation)
                .where(
                    or_(
                        AssetRelation.source_asset_id == asset_id,
                        AssetRelation.target_asset_id == asset_id,
                    )
                )
                .order_by(AssetRelation.created_at, AssetRelation.id)
            )
        )

    async def get_relation(
        self, source_id: UUID, target_id: UUID, relation_type: str
    ) -> AssetRelation | None:
        return await self.session.scalar(
            select(AssetRelation).where(
                AssetRelation.source_asset_id == source_id,
                AssetRelation.target_asset_id == target_id,
                AssetRelation.relation_type == relation_type,
            )
        )

    async def list_evidence(self, asset_id: UUID) -> list[Evidence]:
        return list(
            await self.session.scalars(
                select(Evidence)
                .join(AssetEvidence, AssetEvidence.evidence_id == Evidence.id)
                .where(AssetEvidence.asset_id == asset_id)
                .order_by(Evidence.captured_at)
            )
        )

    async def list_reports(self, asset_id: UUID) -> list[Report]:
        return list(
            await self.session.scalars(
                select(Report)
                .join(AssetReport, AssetReport.report_id == Report.id)
                .where(AssetReport.asset_id == asset_id)
                .order_by(Report.created_at)
            )
        )
