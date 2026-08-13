"""Task report application service."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventPublisher, EventType, PlatformEvent
from app.models import (
    AssetReport,
    Evidence,
    EvidenceKnowledge,
    Finding,
    Knowledge,
    Report,
    ReportKnowledge,
    Task,
)
from app.report.templates import ReportTemplateRegistry


class ReportService:
    """Generate consistent JSON and Markdown reports from task evidence."""

    def __init__(
        self,
        session: AsyncSession,
        publisher: EventPublisher,
        templates: ReportTemplateRegistry | None = None,
    ) -> None:
        self._session = session
        self._publisher = publisher
        self._templates = templates or ReportTemplateRegistry.with_platform_defaults()

    async def generate(
        self,
        *,
        task: Task,
        agent_id: UUID,
        trace_id: str,
        status: str,
        error: str | None = None,
    ) -> Report:
        """Build and persist one report for a task from its evidence set."""

        evidence = list(
            await self._session.scalars(
                select(Evidence).where(Evidence.task_id == task.id).order_by(Evidence.captured_at)
            )
        )
        payload: dict[str, Any] = {
            "task": {"id": str(task.id), "name": task.name, "type": task.task_type},
            "agent_id": str(agent_id),
            "trace_id": trace_id,
            "status": status,
            "generated_at": datetime.now(UTC).isoformat(),
            "evidence": [
                {
                    "id": str(item.id),
                    "url": item.url,
                    "http_status": item.http_status,
                    "title": item.title,
                    "evidence_type": item.evidence_type,
                    "sha256": item.sha256,
                    "content_type": item.content_type,
                    "object_storage_path": item.object_storage_path,
                    "html_hash": item.html_hash,
                    "content_hash": item.content_hash,
                    "screenshot_path": item.screenshot_path,
                    "captured_at": item.captured_at.isoformat(),
                }
                for item in evidence
            ],
            "statistics": {
                "evidence_count": len(evidence),
                "error_count": int(error is not None),
            },
            "error": error,
        }
        findings = list(
            await self._session.scalars(
                select(Finding)
                .join(Finding.assessment_task)
                .where(Finding.assessment_task.has(task_id=task.id))
                .order_by(Finding.created_at)
            )
        )
        if findings:
            payload["findings"] = [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "severity": item.severity,
                    "confidence": item.confidence,
                    "risk_level": item.risk_level,
                    "risk_score": item.risk_score,
                    "status": item.status,
                    "plugin": item.plugin,
                    "tool": item.tool,
                    "rule": item.rule,
                }
                for item in findings
            ]
            payload["statistics"]["finding_count"] = len(findings)
        json_content = self._templates.render("json", payload)
        markdown_content = self._templates.render("markdown", payload)
        html_content = self._templates.render("html", payload)
        if not isinstance(json_content, dict):
            raise TypeError("JSON report template must return a dictionary")
        if not isinstance(markdown_content, str) or not isinstance(html_content, str):
            raise TypeError("Text report templates must return strings")
        report = Report(
            task_id=task.id,
            agent_id=agent_id,
            trace_id=trace_id,
            status=status,
            json_content=json_content,
            markdown_content=markdown_content,
            html_content=html_content,
        )
        self._session.add(report)
        await self._session.flush()
        knowledge_links = list(
            await self._session.execute(
                select(
                    EvidenceKnowledge.knowledge_id,
                    EvidenceKnowledge.knowledge_version_id,
                )
                .join(Evidence, Evidence.id == EvidenceKnowledge.evidence_id)
                .where(Evidence.task_id == task.id)
                .distinct()
            )
        )
        for knowledge_id, knowledge_version_id in knowledge_links:
            self._session.add(
                ReportKnowledge(
                    report_id=report.id,
                    knowledge_id=knowledge_id,
                    knowledge_version_id=knowledge_version_id,
                )
            )
            await self._publisher.publish(
                PlatformEvent(
                    type=EventType.REPORT_KNOWLEDGE_LINKED,
                    trace_id=trace_id,
                    aggregate_id=knowledge_id,
                    actor="report-service",
                    resource=f"report:{report.id}",
                    agent_id=agent_id,
                    task_id=task.id,
                    payload={
                        "knowledge_version_id": str(knowledge_version_id),
                        "source": "evidence",
                    },
                )
            )
        if knowledge_links:
            await self._session.flush()
            knowledge_rows = list(
                await self._session.scalars(
                    select(Knowledge).where(Knowledge.id.in_([item[0] for item in knowledge_links]))
                )
            )
            payload["knowledge"] = [
                {
                    "id": str(item.id),
                    "type": item.knowledge_type,
                    "external_id": item.external_id,
                    "version": item.current_version,
                    "title": item.title,
                }
                for item in knowledge_rows
            ]
            json_content = self._templates.render("json", payload)
            markdown_content = self._templates.render("markdown", payload)
            html_content = self._templates.render("html", payload)
            if not isinstance(json_content, dict):
                raise TypeError("JSON report template must return a dictionary")
            if not isinstance(markdown_content, str) or not isinstance(html_content, str):
                raise TypeError("Text report templates must return strings")
            report.json_content = json_content
            report.markdown_content = markdown_content
            report.html_content = html_content
        if task.asset_id is not None:
            self._session.add(AssetReport(asset_id=task.asset_id, report_id=report.id))
            await self._session.flush()
            await self._publisher.publish(
                PlatformEvent(
                    type=EventType.ASSET_REPORT_LINKED,
                    trace_id=trace_id,
                    aggregate_id=task.asset_id,
                    actor="report-service",
                    resource=f"asset:{task.asset_id}",
                    agent_id=agent_id,
                    task_id=task.id,
                    payload={"report_id": str(report.id), "source": "generation"},
                )
            )
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.REPORT_GENERATED,
                trace_id=trace_id,
                aggregate_id=report.id,
                actor="report-service",
                resource=f"report:{report.id}",
                agent_id=agent_id,
                task_id=task.id,
                result={"status": status, "evidence_count": len(evidence)},
                error=error,
            )
        )
        return report
