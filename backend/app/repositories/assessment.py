"""Security Assessment Framework repositories."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import AssessmentPlugin, AssessmentReport, AssessmentTask, Finding
from app.repositories.base import SQLAlchemyRepository
from app.repositories.pagination import PageResult


class AssessmentTaskRepository(SQLAlchemyRepository[AssessmentTask]):
    model = AssessmentTask

    async def list_page(self, *, page: int = 1, page_size: int = 100) -> PageResult[AssessmentTask]:
        total = await self.session.scalar(select(func.count()).select_from(AssessmentTask))
        items = (
            await self.session.scalars(
                select(AssessmentTask)
                .options(
                    selectinload(AssessmentTask.plugin),
                    selectinload(AssessmentTask.findings),
                )
                .order_by(AssessmentTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)


class FindingRepository(SQLAlchemyRepository[Finding]):
    model = Finding

    async def get(self, entity_id: UUID) -> Finding | None:
        return await self.session.scalar(
            select(Finding)
            .where(Finding.id == entity_id)
            .options(
                selectinload(Finding.references),
                selectinload(Finding.evidence_links),
                selectinload(Finding.knowledge_links),
                selectinload(Finding.asset_links),
                selectinload(Finding.history),
                selectinload(Finding.comments),
                selectinload(Finding.transitions),
                selectinload(Finding.assessment_task),
            )
        )

    async def search(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        asset_id: UUID | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[Finding]:
        statement = select(Finding)
        count_statement = select(func.count()).select_from(Finding)
        if severity:
            statement = statement.where(Finding.severity == severity)
            count_statement = count_statement.where(Finding.severity == severity)
        if status:
            statement = statement.where(Finding.status == status)
            count_statement = count_statement.where(Finding.status == status)
        if asset_id:
            from app.models import FindingAsset

            statement = statement.join(FindingAsset).where(FindingAsset.asset_id == asset_id)
            count_statement = count_statement.join(FindingAsset).where(
                FindingAsset.asset_id == asset_id
            )
        total = await self.session.scalar(count_statement)
        items = (
            (
                await self.session.scalars(
                    statement.options(
                        selectinload(Finding.references),
                        selectinload(Finding.evidence_links),
                        selectinload(Finding.knowledge_links),
                        selectinload(Finding.asset_links),
                    )
                    .order_by(Finding.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .unique()
            .all()
        )
        return PageResult(items=items, page=page, page_size=page_size, total=total or 0)

    async def originals_for_fingerprints(self, fingerprints: set[str]) -> Sequence[Finding]:
        if not fingerprints:
            return []
        return (
            await self.session.scalars(
                select(Finding)
                .where(Finding.fingerprint.in_(fingerprints), Finding.duplicate_of_id.is_(None))
                .order_by(Finding.created_at)
            )
        ).all()


class AssessmentReportRepository(SQLAlchemyRepository[AssessmentReport]):
    model = AssessmentReport

    async def get(self, entity_id: UUID) -> AssessmentReport | None:
        return await self.session.scalar(
            select(AssessmentReport).where(AssessmentReport.id == entity_id)
        )

    async def get_by_task(self, assessment_task_id: UUID) -> AssessmentReport | None:
        return await self.session.scalar(
            select(AssessmentReport).where(
                AssessmentReport.assessment_task_id == assessment_task_id
            )
        )


class AssessmentPluginRepository(SQLAlchemyRepository[AssessmentPlugin]):
    model = AssessmentPlugin

    async def get_by_identity(self, name: str, version: str) -> AssessmentPlugin | None:
        return await self.session.scalar(
            select(AssessmentPlugin)
            .where(AssessmentPlugin.name == name, AssessmentPlugin.version == version)
            .options(selectinload(AssessmentPlugin.capabilities))
        )

    async def list_enabled(self) -> Sequence[AssessmentPlugin]:
        return (
            await self.session.scalars(
                select(AssessmentPlugin)
                .where(AssessmentPlugin.enabled.is_(True))
                .options(selectinload(AssessmentPlugin.capabilities))
                .order_by(AssessmentPlugin.name)
            )
        ).all()
