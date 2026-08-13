"""Detection Framework application service."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.service import CapabilityRegistryService
from app.core.enums import DetectionTaskStatus, SecurityEventStatus, TaskStatus
from app.core.state_machine import TaskStateMachine
from app.detection.correlation import RuleBasedCorrelationEngine
from app.detection.normalizer import DetectionResultNormalizer
from app.detection.planner import DetectionPlanner
from app.detection.registry import DetectionRegistry
from app.detection.runtime import DetectionRuntime
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    AssetNotFound,
    DetectionNotFound,
    DetectionValidationError,
    SecurityEventNotFound,
)
from app.models import (
    Asset,
    DetectionCapability,
    DetectionPlugin,
    DetectionTask,
    EventAsset,
    EventEvidence,
    EventKnowledge,
    EventReference,
    Evidence,
    Knowledge,
    KnowledgeVersion,
    SecurityEvent,
    Task,
)
from app.repositories.detection import (
    DetectionPluginRepository,
    DetectionTaskRepository,
    SecurityEventRepository,
)
from app.repositories.pagination import PageResult
from app.schemas.detection import (
    DetectionCapabilityRead,
    DetectionPluginRead,
    DetectionPolicy,
    DetectionResult,
    DetectionTaskCreate,
    SecurityEventRead,
)


class DetectionService:
    """Own planning, execution, normalization, persistence, correlation and audit."""

    def __init__(
        self,
        session: AsyncSession,
        task_repository: DetectionTaskRepository,
        event_repository: SecurityEventRepository,
        plugin_repository: DetectionPluginRepository,
        capability_service: CapabilityRegistryService,
        registry: DetectionRegistry,
        planner: DetectionPlanner,
        runtime: DetectionRuntime,
        normalizer: DetectionResultNormalizer,
        correlation: RuleBasedCorrelationEngine,
        publisher: EventPublisher,
        default_policy: DetectionPolicy,
    ) -> None:
        self._session = session
        self._tasks = task_repository
        self._events = event_repository
        self._plugins = plugin_repository
        self._capabilities = capability_service
        self._registry = registry
        self._planner = planner
        self._runtime = runtime
        self._normalizer = normalizer
        self._correlation = correlation
        self._publisher = publisher
        self._default_policy = default_policy

    async def bootstrap(self) -> None:
        for runtime_plugin in self._registry.plugins:
            plugin = await self._plugins.get_by_identity(
                runtime_plugin.name, runtime_plugin.version
            )
            if plugin is None:
                plugin = await self._plugins.add(
                    DetectionPlugin(
                        name=runtime_plugin.name,
                        version=runtime_plugin.version,
                        description="Synthetic framework validation plugin; invokes no real tool",
                        enabled=True,
                        permissions=sorted(runtime_plugin.permissions),
                        configuration={"network_access": False, "real_tool": False},
                    )
                )
            for name in sorted(runtime_plugin.capabilities):
                capability = await self._capabilities.register(
                    name,
                    description=f"Detection capability {name}",
                    risk_level="MEDIUM",
                )
                if not any(link.capability_id == capability.id for link in plugin.capabilities):
                    self._session.add(
                        DetectionCapability(
                            plugin_id=plugin.id,
                            capability_id=capability.id,
                            configuration={},
                        )
                    )
            await self._session.flush()

    async def create(self, payload: DetectionTaskCreate, *, trace_id: str) -> DetectionTask:
        await self.bootstrap()
        policy = payload.policy or self._default_policy
        asset = await self._require_asset(payload.asset_id)
        runtime_plugin = (
            self._registry.require(payload.plugin_name)
            if payload.plugin_name
            else self._registry.resolve(set(payload.capabilities))
        )
        plugin = await self._plugins.get_by_identity(runtime_plugin.name, runtime_plugin.version)
        if plugin is None:
            raise DetectionNotFound("Detection plugin persistence definition not found")
        task = Task(
            name=payload.name,
            task_type="security-detection",
            status=TaskStatus.CREATED.value,
            input=payload.input,
            required_permissions=["detection.execute"],
            required_capabilities=payload.capabilities,
            asset_id=asset.id,
        )
        self._session.add(task)
        await self._session.flush()
        detection = DetectionTask(
            task_id=task.id,
            plugin_id=plugin.id,
            status=DetectionTaskStatus.PLANNED.value,
            requested_capabilities=payload.capabilities,
            policy=policy.model_dump(mode="json"),
        )
        self._session.add(detection)
        await self._session.flush()
        plan, _ = self._planner.plan(
            detection_task_id=detection.id,
            task_id=task.id,
            asset_id=asset.id,
            trace_id=trace_id,
            capabilities=payload.capabilities,
            log_source=payload.log_source,
            parser=payload.parser,
            policy=policy,
            input_data=payload.input,
            plugin_name=runtime_plugin.name,
        )
        detection.plan = plan.model_dump(mode="json")
        await self._publish(
            EventType.DETECTION_TASK_CREATED,
            detection.id,
            task.id,
            trace_id,
            {"asset_id": str(asset.id), "capabilities": payload.capabilities},
        )
        if payload.execute:
            await self.execute(detection, asset, payload, policy=policy, trace_id=trace_id)
        await self._session.commit()
        await self._session.refresh(detection)
        return detection

    async def execute(
        self,
        detection: DetectionTask,
        asset: Asset,
        payload: DetectionTaskCreate,
        *,
        policy: DetectionPolicy,
        trace_id: str,
    ) -> None:
        plan, context = self._planner.plan(
            detection_task_id=detection.id,
            task_id=detection.task_id,
            asset_id=asset.id,
            trace_id=trace_id,
            capabilities=payload.capabilities,
            log_source=payload.log_source,
            parser=payload.parser,
            policy=policy,
            input_data=payload.input,
            plugin_name=payload.plugin_name,
        )
        detection.status = DetectionTaskStatus.RUNNING.value
        TaskStateMachine.transition(detection.task, TaskStatus.QUEUED)
        TaskStateMachine.transition(detection.task, TaskStatus.RUNNING)
        detection.started_at = datetime.now(UTC)
        await self._publish(
            EventType.DETECTION_EXECUTION_STARTED,
            detection.id,
            detection.task_id,
            trace_id,
            {"plugin": plan.plugin_name},
        )
        try:
            result = self._normalizer.normalize_result(await self._runtime.execute(plan, context))
            events = await self._persist_events(detection, asset, result)
            groups = self._correlation.correlate(
                events,
                window_seconds=policy.correlation_window_seconds,
                asset_ids={
                    item.id: [link.asset_id for link in item.asset_links] for item in events
                },
            )
            correlated_ids = {event_id for group in groups for event_id in group.event_ids}
            for event in events:
                if event.id in correlated_ids:
                    event.status = SecurityEventStatus.CORRELATED.value
            detection.status = DetectionTaskStatus.SUCCESS.value
            TaskStateMachine.transition(detection.task, TaskStatus.SUCCESS)
            detection.result_summary = {
                "success": result.success,
                "events": len(events),
                "correlation_groups": len(groups),
                "records_collected": result.records_collected,
            }
            await self._publish(
                EventType.DETECTION_RESULT_NORMALIZED,
                detection.id,
                detection.task_id,
                trace_id,
                detection.result_summary,
            )
            for event in events:
                await self._publish(
                    EventType.SECURITY_EVENT_CREATED,
                    event.id,
                    detection.task_id,
                    trace_id,
                    {"event_type": event.event_type, "severity": event.severity},
                )
            for group in groups:
                await self._publish(
                    EventType.SECURITY_EVENTS_CORRELATED,
                    detection.id,
                    detection.task_id,
                    trace_id,
                    group.model_dump(mode="json"),
                )
        except Exception as error:
            detection.status = DetectionTaskStatus.FAILED.value
            if detection.task.status == TaskStatus.RUNNING.value:
                TaskStateMachine.transition(detection.task, TaskStatus.FAILED)
            detection.error = str(error)
            await self._publish(
                EventType.DETECTION_EXECUTION_FAILED,
                detection.id,
                detection.task_id,
                trace_id,
                {},
                error=str(error),
            )
            raise
        finally:
            detection.finished_at = datetime.now(UTC)

    async def _persist_events(
        self, detection: DetectionTask, asset: Asset, result: DetectionResult
    ) -> list[SecurityEvent]:
        events: list[SecurityEvent] = []
        for raw in result.events:
            evidence = await self._evidence(raw.evidence_ids)
            knowledge = await self._knowledge(raw.knowledge_ids)
            asset_ids = set(raw.asset_ids) or {asset.id}
            if asset.id not in asset_ids:
                raise DetectionValidationError("Plugin event omitted the authorized primary Asset")
            await self._validate_assets(asset_ids)
            event = SecurityEvent(
                detection_task_id=detection.id,
                fingerprint=self._normalizer.fingerprint(raw, result.plugin_name, asset.id),
                event_type=raw.event_type,
                source=raw.source,
                severity=raw.severity.value,
                confidence=raw.confidence.value,
                timestamp=raw.timestamp,
                plugin=result.plugin_name,
                tool=raw.tool,
                rule=raw.rule,
                status=SecurityEventStatus.NEW.value,
                attributes={**raw.attributes, "iocs": raw.iocs},
                references=[EventReference(url=url) for url in raw.references],
                evidence_links=[EventEvidence(evidence_id=item.id) for item in evidence.values()],
                knowledge_links=[
                    EventKnowledge(
                        knowledge_id=item.id,
                        knowledge_version_id=version.id,
                    )
                    for item, version in knowledge.values()
                ],
                asset_links=[EventAsset(asset_id=item) for item in sorted(asset_ids, key=str)],
            )
            self._session.add(event)
            events.append(event)
        await self._session.flush()
        return events

    async def get_task(self, detection_id: UUID) -> DetectionTask:
        item = await self._tasks.get(detection_id)
        if item is None:
            raise DetectionNotFound(f"Detection task {detection_id} not found")
        return item

    async def list_tasks(self, *, page: int, page_size: int) -> PageResult[DetectionTask]:
        return await self._tasks.list_page(page=page, page_size=page_size)

    async def get_event(self, event_id: UUID) -> SecurityEvent:
        event = await self._events.get(event_id)
        if event is None:
            raise SecurityEventNotFound(f"Security event {event_id} not found")
        return event

    async def list_events(
        self,
        *,
        severity: str | None,
        status: str | None,
        asset_id: UUID | None,
        page: int,
        page_size: int,
    ) -> PageResult[SecurityEvent]:
        return await self._events.search(
            severity=severity,
            status=status,
            asset_id=asset_id,
            page=page,
            page_size=page_size,
        )

    async def list_plugins(self) -> list[DetectionPluginRead]:
        await self.bootstrap()
        rows = await self._plugins.list_enabled()
        return [
            DetectionPluginRead(
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

    async def list_capabilities(self) -> list[DetectionCapabilityRead]:
        await self.bootstrap()
        rows = await self._plugins.list_enabled()
        return [
            DetectionCapabilityRead(
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
    def to_event_read(event: SecurityEvent) -> SecurityEventRead:
        return SecurityEventRead(
            id=event.id,
            detection_task_id=event.detection_task_id,
            fingerprint=event.fingerprint,
            event_type=event.event_type,
            source=event.source,
            severity=event.severity,
            confidence=event.confidence,
            timestamp=event.timestamp,
            plugin=event.plugin,
            tool=event.tool,
            rule=event.rule,
            status=event.status,
            attributes=event.attributes,
            references=[item.url for item in event.references],
            evidence=[item.evidence_id for item in event.evidence_links],
            knowledge=[item.knowledge_id for item in event.knowledge_links],
            assets=[item.asset_id for item in event.asset_links],
            created_at=event.created_at,
            updated_at=event.updated_at,
        )

    async def _require_asset(self, asset_id: UUID) -> Asset:
        asset = await self._session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise AssetNotFound(f"Asset {asset_id} not found")
        return asset

    async def _validate_assets(self, asset_ids: set[UUID]) -> None:
        rows = list(await self._session.scalars(select(Asset).where(Asset.id.in_(asset_ids))))
        if len(rows) != len(asset_ids) or any(item.deleted_at is not None for item in rows):
            raise DetectionValidationError("Plugin referenced unknown or deleted Assets")

    async def _evidence(self, ids: list[UUID]) -> dict[UUID, Evidence]:
        if not ids:
            return {}
        rows = list(await self._session.scalars(select(Evidence).where(Evidence.id.in_(ids))))
        if len(rows) != len(set(ids)):
            raise DetectionValidationError("Plugin referenced unknown Evidence")
        return {item.id: item for item in rows}

    async def _knowledge(self, ids: list[UUID]) -> dict[UUID, tuple[Knowledge, KnowledgeVersion]]:
        if not ids:
            return {}
        rows = list(await self._session.scalars(select(Knowledge).where(Knowledge.id.in_(ids))))
        if len(rows) != len(set(ids)):
            raise DetectionValidationError("Plugin referenced unknown Knowledge")
        output: dict[UUID, tuple[Knowledge, KnowledgeVersion]] = {}
        for item in rows:
            version = await self._session.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.knowledge_id == item.id,
                    KnowledgeVersion.version == item.current_version,
                    KnowledgeVersion.content_hash == item.current_content_hash,
                )
            )
            if version is None:
                raise DetectionValidationError("Knowledge current version is unavailable")
            output[item.id] = (item, version)
        return output

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
                aggregate_id=aggregate_id,
                task_id=task_id,
                trace_id=trace_id,
                resource="detection",
                payload=payload,
                error=error,
            )
        )
