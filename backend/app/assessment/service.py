"""Security Assessment Framework application service."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assessment.finding_state import FindingStateMachine
from app.assessment.knowledge_mapper import FindingKnowledgeMapper
from app.assessment.normalizer import ResultNormalizer
from app.assessment.planner import AssessmentPlanner
from app.assessment.registry import AssessmentRegistry
from app.assessment.runtime import AssessmentRuntime
from app.capabilities.service import CapabilityRegistryService
from app.core.enums import AssessmentTaskStatus, FindingStatus, TaskStatus
from app.core.state_machine import TaskStateMachine
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    AssessmentNotFound,
    AssessmentPolicyViolation,
    AssetNotFound,
    FindingNotFound,
)
from app.models import (
    AssessmentCapability,
    AssessmentPlugin,
    AssessmentReport,
    AssessmentTask,
    Asset,
    Evidence,
    Finding,
    FindingHistory,
    FindingTransition,
    Knowledge,
    KnowledgeVersion,
    Task,
)
from app.repositories.assessment import (
    AssessmentPluginRepository,
    AssessmentReportRepository,
    AssessmentTaskRepository,
    FindingRepository,
)
from app.repositories.pagination import PageResult
from app.schemas.assessment import (
    AssessmentCapabilityRead,
    AssessmentPluginRead,
    AssessmentPolicy,
    AssessmentTaskCreate,
    FindingRead,
    FindingTransitionCreate,
    NucleiAssessmentCreate,
    ZapAssessmentCreate,
    ZapPolicy,
    ZapPolicyRead,
    ZapStatusRead,
)
from app.tools.zap import ZapAdapter


class AssessmentService:
    """Own planning, runtime, normalization, persistence and audit transactions."""

    def __init__(
        self,
        session: AsyncSession,
        task_repository: AssessmentTaskRepository,
        finding_repository: FindingRepository,
        plugin_repository: AssessmentPluginRepository,
        report_repository: AssessmentReportRepository,
        capability_service: CapabilityRegistryService,
        registry: AssessmentRegistry,
        planner: AssessmentPlanner,
        runtime: AssessmentRuntime,
        normalizer: ResultNormalizer,
        knowledge_mapper: FindingKnowledgeMapper,
        publisher: EventPublisher,
        default_policy: AssessmentPolicy,
        zap_adapter: ZapAdapter | None = None,
    ) -> None:
        self._session = session
        self._tasks = task_repository
        self._findings = finding_repository
        self._plugins = plugin_repository
        self._reports = report_repository
        self._capabilities = capability_service
        self._registry = registry
        self._planner = planner
        self._runtime = runtime
        self._normalizer = normalizer
        self._knowledge_mapper = knowledge_mapper
        self._publisher = publisher
        self._default_policy = default_policy
        self._zap_adapter = zap_adapter

    async def bootstrap(self) -> None:
        for runtime_plugin in self._registry.plugins:
            plugin = await self._plugins.get_by_identity(
                runtime_plugin.name, runtime_plugin.version
            )
            if plugin is None:
                plugin = await self._plugins.add(
                    AssessmentPlugin(
                        name=runtime_plugin.name,
                        version=runtime_plugin.version,
                        description={
                            "fake-assessment": (
                                "Phase 6 contract validation plugin; performs no scanning"
                            ),
                            "nuclei-assessment": "Sandboxed Nuclei Assessment Plugin",
                            "zap-assessment": "Governed OWASP ZAP Assessment Plugin",
                        }.get(runtime_plugin.name, "Assessment Plugin"),
                        enabled=True,
                        permissions=sorted(runtime_plugin.permissions),
                        configuration={
                            "network_access": runtime_plugin.name
                            in {"nuclei-assessment", "zap-assessment"},
                            "sandbox_required": runtime_plugin.name
                            in {"nuclei-assessment", "zap-assessment"},
                            "active_scan_default": False,
                        },
                    )
                )
            for name in sorted(runtime_plugin.capabilities):
                capability = await self._capabilities.register(
                    name,
                    description=f"Assessment capability {name}",
                    risk_level=(
                        "HIGH"
                        if name in {"web.scan", "web.active_scan", "port.scan", "host.scan"}
                        else "MEDIUM"
                    ),
                )
                if not any(link.capability_id == capability.id for link in plugin.capabilities):
                    self._session.add(
                        AssessmentCapability(
                            plugin_id=plugin.id,
                            capability_id=capability.id,
                            configuration={},
                        )
                    )
            await self._session.flush()

    async def create(self, payload: AssessmentTaskCreate, *, trace_id: str) -> AssessmentTask:
        await self.bootstrap()
        policy = payload.policy or self._default_policy
        asset = await self._require_asset(payload.asset_id)
        plugin = (
            self._registry.require(payload.plugin_name)
            if payload.plugin_name
            else self._registry.resolve(set(payload.capabilities))
        )
        if not set(payload.capabilities) <= set(plugin.capabilities):
            raise AssessmentNotFound("Requested plugin does not provide all capabilities")
        plugin_model = await self._plugins.get_by_identity(plugin.name, plugin.version)
        if plugin_model is None:
            raise AssessmentNotFound("Assessment plugin persistence definition not found")
        task = Task(
            name=payload.name,
            task_type="security-assessment",
            status="CREATED",
            input=payload.input,
            required_permissions=["assessment.execute"],
            required_capabilities=payload.capabilities,
            asset_id=asset.id,
        )
        self._session.add(task)
        await self._session.flush()
        assessment = AssessmentTask(
            task_id=task.id,
            plugin_id=plugin_model.id,
            status=AssessmentTaskStatus.PLANNED.value,
            requested_capabilities=payload.capabilities,
            policy=policy.model_dump(mode="json"),
        )
        self._session.add(assessment)
        await self._session.flush()
        plan, _ = self._planner.plan(
            assessment_task_id=assessment.id,
            task_id=task.id,
            asset_id=asset.id,
            trace_id=trace_id,
            capabilities=payload.capabilities,
            policy=policy,
            input_data=payload.input,
            plugin_name=plugin.name,
        )
        assessment.plan = plan.model_dump(mode="json")
        await self._publish(
            EventType.ASSESSMENT_TASK_CREATED,
            assessment.id,
            task.id,
            trace_id,
            {"asset_id": str(asset.id), "capabilities": payload.capabilities},
        )
        if plugin.name == "zap-assessment":
            await self._publish_zap_plan_events(assessment, task, trace_id, policy)
        if payload.execute:
            await self.execute(assessment, asset, payload, policy=policy, trace_id=trace_id)
        await self._session.commit()
        await self._session.refresh(assessment)
        return assessment

    async def create_nuclei(
        self, payload: NucleiAssessmentCreate, *, trace_id: str
    ) -> AssessmentTask:
        asset = await self._require_asset(payload.asset_id)
        if asset.asset_type not in {"WEBSITE", "DOMAIN", "HOST", "APPLICATION"}:
            raise AssessmentPolicyViolation("Nuclei requires a web-addressable Asset")
        target = self._nuclei_target(asset)
        policy = payload.policy or self._default_policy
        return await self.create(
            AssessmentTaskCreate(
                name=f"Nuclei assessment: {asset.name}",
                asset_id=asset.id,
                capabilities=["template.scan", "web.scan"],
                plugin_name="nuclei-assessment",
                policy=policy,
                input={"target": target, "templates": payload.templates},
                execute=payload.execute,
            ),
            trace_id=trace_id,
        )

    async def create_zap(self, payload: ZapAssessmentCreate, *, trace_id: str) -> AssessmentTask:
        asset = await self._require_asset(payload.asset_id)
        if asset.asset_type not in {"WEBSITE", "APPLICATION"}:
            raise AssessmentPolicyViolation("ZAP requires a WEBSITE or APPLICATION Asset")
        target = self._zap_target(asset)
        policy = payload.policy or ZapPolicy()
        active_authorized = self._active_scan_authorized(asset)
        if policy.active_scan_enabled and not active_authorized:
            raise AssessmentPolicyViolation(
                "ZAP Active Scan requires Asset properties.assessment.active_scan_authorized=true"
            )
        capabilities = ["web.dast", "web.passive_scan"]
        if policy.spider_enabled:
            capabilities.append("web.spider")
        if policy.active_scan_enabled:
            capabilities.append("web.active_scan")
        return await self.create(
            AssessmentTaskCreate(
                name=f"ZAP assessment: {asset.name}",
                asset_id=asset.id,
                capabilities=capabilities,
                plugin_name="zap-assessment",
                policy=policy,
                input={
                    "target": target,
                    "active_scan_authorized": active_authorized,
                    "scan_mode": "active" if policy.active_scan_enabled else "passive",
                },
                execute=payload.execute,
            ),
            trace_id=trace_id,
        )

    @staticmethod
    def list_zap_policies() -> list[ZapPolicyRead]:
        return [
            ZapPolicyRead(
                name="cap-passive-baseline",
                passive_scan_enabled=True,
                active_scan_enabled=False,
                spider_enabled=False,
                description="Default passive-only baseline; no attack payloads.",
            ),
            ZapPolicyRead(
                name="cap-active-controlled",
                passive_scan_enabled=True,
                active_scan_enabled=True,
                spider_enabled=True,
                description="Explicitly authorized active DAST with bounded scope.",
            ),
        ]

    async def get_zap_status(self) -> ZapStatusRead:
        if self._zap_adapter is None:
            return ZapStatusRead(healthy=False, error="ZAP Adapter is not configured")
        return ZapStatusRead.model_validate(await self._zap_adapter.status())

    async def execute(
        self,
        assessment: AssessmentTask,
        asset: Asset,
        payload: AssessmentTaskCreate,
        *,
        policy: AssessmentPolicy,
        trace_id: str,
    ) -> None:
        plan, context = self._planner.plan(
            assessment_task_id=assessment.id,
            task_id=assessment.task_id,
            asset_id=asset.id,
            trace_id=trace_id,
            capabilities=payload.capabilities,
            policy=policy,
            input_data=payload.input,
            plugin_name=payload.plugin_name,
        )
        assessment.status = AssessmentTaskStatus.RUNNING.value
        TaskStateMachine.transition(assessment.task, TaskStatus.QUEUED)
        TaskStateMachine.transition(assessment.task, TaskStatus.RUNNING)
        assessment.started_at = datetime.now(UTC)
        await self._publish(
            EventType.ASSESSMENT_EXECUTION_STARTED,
            assessment.id,
            assessment.task_id,
            trace_id,
            {"plugin": plan.plugin_name},
        )
        try:
            result = await self._runtime.execute(plan, context)
            evidence = await self._evidence(result)
            mapped_knowledge = await self._knowledge_mapper.enrich(result)
            knowledge = {**await self._knowledge(result), **mapped_knowledge}
            findings = self._normalizer.normalize(
                assessment_task_id=assessment.id,
                asset=asset,
                result=result,
                evidence=evidence,
                knowledge=knowledge,
            )
            originals = {
                item.fingerprint: item
                for item in await self._findings.originals_for_fingerprints(
                    {item.fingerprint for item in findings}
                )
            }
            for finding in findings:
                if finding.fingerprint in originals:
                    finding.duplicate_of_id = originals[finding.fingerprint].id
                self._session.add(finding)
            await self._session.flush()
            for finding in findings:
                self._session.add(
                    FindingHistory(
                        finding_id=finding.id,
                        actor="assessment-service",
                        action="CREATED",
                        from_status=None,
                        to_status=finding.status,
                        snapshot=self._finding_snapshot(finding),
                    )
                )
            assessment.status = AssessmentTaskStatus.SUCCESS.value
            TaskStateMachine.transition(assessment.task, TaskStatus.SUCCESS)
            assessment.result_summary = {
                "success": result.success,
                "findings": len(findings),
                "duplicates": sum(item.duplicate_of_id is not None for item in findings),
                "requests_made": result.requests_made,
            }
            report = AssessmentReport(
                assessment_task_id=assessment.id,
                plugin_id=assessment.plugin_id,
                asset_id=asset.id,
                trace_id=trace_id,
                status="FINAL",
                summary=dict(assessment.result_summary),
                content=self._assessment_report_content(
                    assessment, asset, findings, result.metadata
                ),
            )
            self._session.add(report)
            await self._session.flush()
            await self._publish(
                EventType.ASSESSMENT_REPORT_GENERATED,
                report.id,
                assessment.task_id,
                trace_id,
                {"assessment_task_id": str(assessment.id), "findings": len(findings)},
            )
            await self._publish(
                EventType.ASSESSMENT_RESULT_NORMALIZED,
                assessment.id,
                assessment.task_id,
                trace_id,
                assessment.result_summary,
            )
            if plan.plugin_name == "zap-assessment":
                await self._publish(
                    EventType.ZAP_EXECUTION_COMPLETED,
                    assessment.id,
                    assessment.task_id,
                    trace_id,
                    {
                        "mode": result.metadata.get("mode"),
                        "duration": result.metadata.get("scan_duration"),
                        "alerts": len(findings),
                    },
                )
            for finding in findings:
                if plan.plugin_name == "zap-assessment":
                    await self._publish(
                        EventType.ZAP_ALERT_NORMALIZED,
                        finding.id,
                        assessment.task_id,
                        trace_id,
                        {"rule": finding.rule, "severity": finding.severity},
                    )
                await self._publish(
                    EventType.FINDING_CREATED,
                    finding.id,
                    assessment.task_id,
                    trace_id,
                    {
                        "severity": finding.severity,
                        "risk_level": finding.risk_level,
                        "duplicate_of_id": (
                            str(finding.duplicate_of_id) if finding.duplicate_of_id else None
                        ),
                    },
                )
        except Exception as error:
            assessment.status = AssessmentTaskStatus.FAILED.value
            if assessment.task.status == TaskStatus.RUNNING.value:
                TaskStateMachine.transition(assessment.task, TaskStatus.FAILED)
            assessment.error = str(error)
            await self._publish(
                EventType.ASSESSMENT_EXECUTION_FAILED,
                assessment.id,
                assessment.task_id,
                trace_id,
                {},
                error=str(error),
            )
            raise
        finally:
            assessment.finished_at = datetime.now(UTC)

    async def get_task(self, assessment_id: UUID) -> AssessmentTask:
        assessment = await self._tasks.get(assessment_id)
        if assessment is None:
            raise AssessmentNotFound(f"Assessment task {assessment_id} not found")
        return assessment

    async def list_tasks(self, *, page: int, page_size: int) -> PageResult[AssessmentTask]:
        return await self._tasks.list_page(page=page, page_size=page_size)

    async def get_finding(self, finding_id: UUID) -> Finding:
        finding = await self._findings.get(finding_id)
        if finding is None:
            raise FindingNotFound(f"Finding {finding_id} not found")
        return finding

    async def transition_finding(
        self,
        finding_id: UUID,
        payload: FindingTransitionCreate,
        *,
        trace_id: str,
    ) -> FindingTransition:
        finding = await self.get_finding(finding_id)
        current = FindingStatus(finding.status)
        FindingStateMachine.validate(current, payload.status)
        transition = FindingTransition(
            finding_id=finding.id,
            from_status=current.value,
            to_status=payload.status.value,
            actor=payload.actor,
            reason=payload.reason,
            trace_id=trace_id,
        )
        finding.status = payload.status.value
        self._session.add(transition)
        self._session.add(
            FindingHistory(
                finding_id=finding.id,
                actor=payload.actor,
                action="TRANSITION",
                from_status=current.value,
                to_status=payload.status.value,
                reason=payload.reason,
                snapshot=self._finding_snapshot(finding),
            )
        )
        await self._session.flush()
        await self._publish(
            EventType.FINDING_TRANSITIONED,
            finding.id,
            finding.assessment_task.task_id,
            trace_id,
            {
                "from": current.value,
                "to": payload.status.value,
                "actor": payload.actor,
            },
        )
        await self._session.commit()
        await self._session.refresh(transition)
        return transition

    async def get_report(self, report_id: UUID) -> AssessmentReport:
        report = await self._reports.get(report_id)
        if report is None:
            raise AssessmentNotFound(f"Assessment report {report_id} not found")
        return report

    async def list_findings(
        self,
        *,
        severity: str | None,
        status: str | None,
        asset_id: UUID | None,
        page: int,
        page_size: int,
    ) -> PageResult[Finding]:
        return await self._findings.search(
            severity=severity,
            status=status,
            asset_id=asset_id,
            page=page,
            page_size=page_size,
        )

    async def list_plugins(self) -> list[AssessmentPluginRead]:
        await self.bootstrap()
        rows = await self._plugins.list_enabled()
        return [
            AssessmentPluginRead(
                id=row.id,
                name=row.name,
                version=row.version,
                description=row.description,
                enabled=row.enabled,
                permissions=row.permissions,
                capabilities=[link.capability.name for link in row.capabilities],
            )
            for row in rows
        ]

    async def list_capabilities(self) -> list[AssessmentCapabilityRead]:
        await self.bootstrap()
        rows = await self._plugins.list_enabled()
        return [
            AssessmentCapabilityRead(
                id=link.capability.id,
                name=link.capability.name,
                description=link.capability.description,
                risk_level=link.capability.risk_level,
                enabled=link.capability.enabled,
                plugin=row.name,
            )
            for row in rows
            for link in row.capabilities
        ]

    @staticmethod
    def to_finding_read(finding: Finding) -> FindingRead:
        return FindingRead(
            id=finding.id,
            assessment_task_id=finding.assessment_task_id,
            duplicate_of_id=finding.duplicate_of_id,
            fingerprint=finding.fingerprint,
            title=finding.title,
            severity=finding.severity,
            confidence=finding.confidence,
            description=finding.description,
            affected_asset=finding.affected_asset,
            plugin=finding.plugin,
            tool=finding.tool,
            rule=finding.rule,
            risk_level=finding.risk_level,
            risk_score=finding.risk_score,
            status=finding.status,
            attributes=finding.attributes,
            references=[item.url for item in finding.references],
            evidence=[item.evidence_id for item in finding.evidence_links],
            knowledge=[item.knowledge_id for item in finding.knowledge_links],
            assets=[item.asset_id for item in finding.asset_links],
            created_at=finding.created_at,
            updated_at=finding.updated_at,
        )

    async def _require_asset(self, asset_id: UUID) -> Asset:
        asset = await self._session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise AssetNotFound(f"Asset {asset_id} not found")
        return asset

    async def _evidence(self, result: object) -> dict[UUID, Evidence]:
        ids = {item for finding in result.findings for item in finding.evidence_ids}
        if not ids:
            return {}
        rows = await self._session.scalars(select(Evidence).where(Evidence.id.in_(ids)))
        return {item.id: item for item in rows}

    async def _knowledge(self, result: object) -> dict[UUID, tuple[Knowledge, KnowledgeVersion]]:
        ids = {item for finding in result.findings for item in finding.knowledge_ids}
        if not ids:
            return {}
        rows = list(await self._session.scalars(select(Knowledge).where(Knowledge.id.in_(ids))))
        output: dict[UUID, tuple[Knowledge, KnowledgeVersion]] = {}
        for item in rows:
            version = await self._session.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.knowledge_id == item.id,
                    KnowledgeVersion.version == item.current_version,
                    KnowledgeVersion.content_hash == item.current_content_hash,
                )
            )
            if version is not None:
                output[item.id] = (item, version)
        return output

    @staticmethod
    def _nuclei_target(asset: Asset) -> str:
        value = asset.value.strip()
        if asset.asset_type in {"DOMAIN", "HOST"} and "://" not in value:
            return f"https://{value}"
        if asset.asset_type == "APPLICATION":
            configured = asset.properties.get("url")
            if not isinstance(configured, str) or not configured.strip():
                raise AssessmentPolicyViolation(
                    "APPLICATION Asset requires properties.url for Nuclei"
                )
            return configured.strip()
        return value

    @staticmethod
    def _zap_target(asset: Asset) -> str:
        value = asset.value.strip()
        if asset.asset_type == "APPLICATION":
            configured = asset.properties.get("url")
            if not isinstance(configured, str) or not configured.strip():
                raise AssessmentPolicyViolation("APPLICATION Asset requires properties.url for ZAP")
            value = configured.strip()
        if not value.startswith(("http://", "https://")):
            raise AssessmentPolicyViolation("ZAP Asset URL must use HTTP(S)")
        return value

    @staticmethod
    def _active_scan_authorized(asset: Asset) -> bool:
        assessment = asset.properties.get("assessment")
        return bool(
            isinstance(assessment, dict) and assessment.get("active_scan_authorized") is True
        )

    @staticmethod
    def _finding_snapshot(finding: Finding) -> dict[str, object]:
        return {
            "title": finding.title,
            "severity": finding.severity,
            "risk_level": finding.risk_level,
            "risk_score": finding.risk_score,
            "status": finding.status,
            "fingerprint": finding.fingerprint,
        }

    @staticmethod
    def _assessment_report_content(
        assessment: AssessmentTask,
        asset: Asset,
        findings: list[Finding],
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        metadata = metadata or {}
        return {
            "assessment": {
                "id": str(assessment.id),
                "task_id": str(assessment.task_id),
                "plugin": assessment.plugin.name if assessment.plugin else None,
                "plugin_version": assessment.plugin.version if assessment.plugin else None,
                "scan_policy": metadata.get("scan_policy"),
                "scan_scope": metadata.get("scan_scope", []),
                "scan_duration": metadata.get("scan_duration"),
                "tool_version": metadata.get("tool_version"),
                "mode": metadata.get("mode"),
                "alert_summary": metadata.get("alert_summary", {}),
            },
            "asset": {
                "id": str(asset.id),
                "type": asset.asset_type,
                "name": asset.name,
                "value": asset.value,
            },
            "findings": [
                {
                    "id": str(finding.id),
                    "template": finding.rule,
                    "title": finding.title,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "risk": {
                        "level": finding.risk_level,
                        "score": finding.risk_score,
                    },
                    "status": finding.status,
                    "knowledge": [str(item.knowledge_id) for item in finding.knowledge_links],
                    "evidence": finding.attributes.get("evidence", {}),
                    "references": [item.url for item in finding.references],
                }
                for finding in findings
            ],
        }

    async def _publish_zap_plan_events(
        self,
        assessment: AssessmentTask,
        task: Task,
        trace_id: str,
        policy: AssessmentPolicy,
    ) -> None:
        await self._publish(
            EventType.ZAP_SESSION_CREATED,
            assessment.id,
            task.id,
            trace_id,
            {"asset_id": str(task.asset_id), "persistent": False},
        )
        await self._publish(
            EventType.ZAP_POLICY_ENFORCED,
            assessment.id,
            task.id,
            trace_id,
            {
                "active_scan": getattr(policy, "active_scan_enabled", False),
                "scan_policy": getattr(policy, "scan_policy", None),
                "capabilities": task.required_capabilities,
            },
        )

    async def _publish(
        self,
        event_type: EventType,
        aggregate_id: UUID,
        task_id: UUID,
        trace_id: str,
        payload: dict[str, object],
        *,
        error: str | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                trace_id=trace_id,
                aggregate_id=aggregate_id,
                actor="assessment-service",
                resource=f"assessment:{aggregate_id}",
                task_id=task_id,
                payload=payload,
                error=error,
            )
        )
