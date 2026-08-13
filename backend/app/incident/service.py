"""Incident and Investigation Case application service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import IncidentStatus, IncidentTimelineType, InvestigationStatus
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    IncidentConflict,
    IncidentNotFound,
    IncidentPolicyViolation,
    InvestigationCaseNotFound,
)
from app.incident.planner import IncidentPlanner
from app.incident.registry import IncidentRegistry
from app.incident.runtime import IncidentRuntime
from app.incident.state import IncidentStateMachine
from app.models import (
    Asset,
    CaseComment,
    Evidence,
    Finding,
    Incident,
    IncidentArtifact,
    IncidentAsset,
    IncidentEvent,
    IncidentFinding,
    IncidentKnowledge,
    IncidentTimeline,
    InvestigationCase,
    Knowledge,
    KnowledgeVersion,
    Report,
    SecurityEvent,
)
from app.repositories.incident import IncidentRepository, InvestigationCaseRepository
from app.repositories.pagination import PageResult
from app.schemas.incident import (
    CaseCommentCreate,
    IncidentArtifactCreate,
    IncidentAssignmentCreate,
    IncidentCandidate,
    IncidentCreate,
    IncidentPlan,
    IncidentPolicy,
    IncidentRead,
    IncidentTransitionCreate,
    InvestigationCaseCreate,
)


class IncidentService:
    """Exclusive owner of Incident creation, transition, assignment and merge lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        incidents: IncidentRepository,
        cases: InvestigationCaseRepository,
        registry: IncidentRegistry,
        planner: IncidentPlanner,
        runtime: IncidentRuntime,
        publisher: EventPublisher,
        policy: IncidentPolicy,
    ) -> None:
        self._session = session
        self._incidents = incidents
        self._cases = cases
        self._registry = registry
        self._planner = planner
        self._runtime = runtime
        self._publisher = publisher
        self._policy = policy
        self._trace_id = ""
        self._pending_incident: Incident | None = None

    async def create(self, payload: IncidentCreate, *, trace_id: str) -> Incident:
        source = self._registry.require(payload.source)
        candidate = IncidentCandidate(
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            confidence=payload.confidence,
            source=source,
            correlation_key=str(payload.attributes.get("correlation_key", "")),
            finding_ids=payload.finding_ids,
            event_ids=payload.event_ids,
            asset_ids=payload.asset_ids,
            attributes={
                **payload.attributes,
                "owner": payload.owner,
                "assignee": payload.assignee,
                "queue": payload.queue,
                "classification": payload.classification,
                "risk": payload.risk,
                "priority": payload.priority.value if payload.priority else None,
                "knowledge_ids": [str(item) for item in payload.knowledge_ids],
                "create_case": payload.create_case,
            },
        )
        if source == "MANUAL":
            candidate = candidate.model_copy(
                update={"correlation_key": candidate.correlation_key or f"manual:{trace_id}"}
            )
            policy = self._policy.model_copy(
                update={
                    "minimum_severity": payload.severity,
                    "minimum_confidence": payload.confidence,
                }
            )
        else:
            if source == "ASSESSMENT" and not self._policy.automatic_creation_enabled:
                raise IncidentPolicyViolation("Automatic Incident creation is disabled")
            if source == "DETECTION" and not self._policy.automatic_escalation_enabled:
                raise IncidentPolicyViolation("Automatic Incident escalation is disabled")
            policy = self._policy
        plan = self._planner.plan(candidate, policy)
        if payload.priority is not None:
            plan = plan.model_copy(
                update={
                    "priority": payload.priority,
                    "sla_minutes": policy.sla_targets_minutes[payload.priority],
                }
            )
        self._trace_id = trace_id
        self._pending_incident = None
        result = await self._runtime.execute(candidate, plan, self)
        incident = self._as_incident(result.incident)
        await self._session.commit()
        return await self.get(incident.id)

    async def validate(self, candidate: IncidentCandidate, plan: IncidentPlan) -> None:
        await self._require_ids(Finding, candidate.finding_ids, "Finding")
        await self._require_ids(SecurityEvent, candidate.event_ids, "SecurityEvent")
        await self._require_ids(Asset, candidate.asset_ids, "Asset")
        knowledge_ids = [UUID(item) for item in candidate.attributes.get("knowledge_ids", [])]
        await self._require_ids(Knowledge, knowledge_ids, "Knowledge")

    async def correlate(self, candidate: IncidentCandidate, plan: IncidentPlan) -> None:
        if not self._policy.duplicate_merge_enabled:
            return
        not_before = datetime.now(UTC) - timedelta(seconds=self._policy.duplicate_window_seconds)
        self._pending_incident = await self._incidents.find_duplicate(
            plan.correlation_key, not_before=not_before
        )

    async def create_incident(self, candidate: IncidentCandidate, plan: IncidentPlan) -> Incident:
        if self._pending_incident is not None:
            return self._pending_incident
        attributes = dict(candidate.attributes)
        incident = Incident(
            title=candidate.title,
            description=candidate.description,
            severity=candidate.severity.value,
            priority=plan.priority.value,
            status=IncidentStatus.NEW.value,
            confidence=candidate.confidence.value,
            source=plan.source,
            owner=attributes.pop("owner", None),
            assignee=attributes.pop("assignee", None),
            queue=attributes.pop("queue", None) or plan.queue,
            classification=attributes.pop("classification", None),
            risk=attributes.pop("risk", None),
            correlation_key=plan.correlation_key,
            attributes=attributes,
            sla_due_at=datetime.now(UTC) + timedelta(minutes=plan.sla_minutes),
        )
        self._session.add(incident)
        await self._session.flush()
        self._session.add(
            IncidentTimeline(
                incident_id=incident.id,
                event_type=IncidentTimelineType.CREATED.value,
                actor="incident-service",
                description="Incident created by the platform IncidentService",
                to_status=IncidentStatus.NEW.value,
                details={"source": plan.source, "correlation_key": plan.correlation_key},
            )
        )
        return incident

    async def link(
        self, incident: object, candidate: IncidentCandidate, plan: IncidentPlan
    ) -> None:
        model = self._as_incident(incident)
        if self._pending_incident is None:
            existing_findings: set[UUID] = set()
            existing_events: set[UUID] = set()
            existing_assets: set[UUID] = set()
            existing_knowledge: set[UUID] = set()
            has_case = False
        else:
            existing_findings = {item.finding_id for item in model.findings}
            existing_events = {item.event_id for item in model.events}
            existing_assets = {item.asset_id for item in model.assets}
            existing_knowledge = {item.knowledge_id for item in model.knowledge}
            has_case = bool(model.cases)
        for finding_id in set(candidate.finding_ids) - existing_findings:
            self._session.add(IncidentFinding(incident_id=model.id, finding_id=finding_id))
        for event_id in set(candidate.event_ids) - existing_events:
            self._session.add(IncidentEvent(incident_id=model.id, event_id=event_id))
        for asset_id in set(candidate.asset_ids) - existing_assets:
            self._session.add(IncidentAsset(incident_id=model.id, asset_id=asset_id))
        knowledge_ids = [UUID(item) for item in candidate.attributes.get("knowledge_ids", [])]
        for knowledge_id in set(knowledge_ids) - existing_knowledge:
            version_id = await self._latest_knowledge_version(knowledge_id)
            self._session.add(
                IncidentKnowledge(
                    incident_id=model.id,
                    knowledge_id=knowledge_id,
                    knowledge_version_id=version_id,
                )
            )
        create_case = bool(candidate.attributes.get("create_case", False))
        if create_case and not has_case:
            case = InvestigationCase(
                incident_id=model.id,
                title=f"Investigation: {model.title}",
                status=InvestigationStatus.OPEN.value,
                owner=model.owner,
                assignee=model.assignee,
                queue=model.queue,
                attributes={},
            )
            self._session.add(case)
            await self._session.flush()
            await self._publish(
                EventType.INVESTIGATION_CASE_CREATED,
                model.id,
                self._trace_id,
                {"case_id": str(case.id), "status": case.status},
            )
        await self._session.flush()

    async def audit(
        self, incident: object, candidate: IncidentCandidate, plan: IncidentPlan
    ) -> None:
        model = self._as_incident(incident)
        event_type = (
            EventType.INCIDENT_MERGED if self._pending_incident else EventType.INCIDENT_CREATED
        )
        if self._pending_incident:
            timeline = IncidentTimeline(
                incident_id=model.id,
                event_type=IncidentTimelineType.MERGED.value,
                actor="incident-service",
                description="Duplicate source facts merged into existing Incident",
                details={
                    "finding_ids": [str(item) for item in candidate.finding_ids],
                    "event_ids": [str(item) for item in candidate.event_ids],
                },
            )
            model.timelines.append(timeline)
        await self._publish(
            event_type,
            model.id,
            self._trace_id,
            {
                "source": plan.source,
                "priority": plan.priority.value,
                "finding_count": len(candidate.finding_ids),
                "event_count": len(candidate.event_ids),
            },
        )

    async def get(self, incident_id: UUID) -> Incident:
        incident = await self._incidents.get(incident_id)
        if incident is None:
            raise IncidentNotFound(f"Incident {incident_id} not found")
        return incident

    async def list(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
        assignee: str | None = None,
        queue: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[Incident]:
        return await self._incidents.search(
            severity=severity,
            status=status,
            priority=priority,
            owner=owner,
            assignee=assignee,
            queue=queue,
            page=page,
            page_size=page_size,
        )

    async def transition(
        self,
        incident_id: UUID,
        payload: IncidentTransitionCreate,
        *,
        trace_id: str,
    ) -> Incident:
        incident = await self.get(incident_id)
        current = IncidentStatus(incident.status)
        if payload.status is IncidentStatus.REOPENED and not self._policy.reopen_enabled:
            raise IncidentPolicyViolation("Incident reopen is disabled by policy")
        IncidentStateMachine.transition(incident, payload.status)
        now = datetime.now(UTC)
        if payload.status is IncidentStatus.RESOLVED:
            incident.resolved_at = now
        elif payload.status is IncidentStatus.CLOSED:
            incident.closed_at = now
        elif payload.status is IncidentStatus.REOPENED:
            incident.resolved_at = None
            incident.closed_at = None
        event_type = (
            IncidentTimelineType.REOPENED
            if payload.status is IncidentStatus.REOPENED
            else IncidentTimelineType.STATUS_CHANGED
        )
        self._session.add(
            IncidentTimeline(
                incident_id=incident.id,
                event_type=event_type.value,
                actor=payload.actor,
                description=payload.reason or f"Incident transitioned to {payload.status.value}",
                from_status=current.value,
                to_status=payload.status.value,
                details={},
            )
        )
        await self._publish(
            EventType.INCIDENT_TRANSITIONED,
            incident.id,
            trace_id,
            {"from": current.value, "to": payload.status.value, "actor": payload.actor},
        )
        await self._session.commit()
        return await self.get(incident.id)

    async def assign(
        self,
        incident_id: UUID,
        payload: IncidentAssignmentCreate,
        *,
        trace_id: str,
    ) -> Incident:
        incident = await self.get(incident_id)
        before = {
            "owner": incident.owner,
            "assignee": incident.assignee,
            "queue": incident.queue,
            "priority": incident.priority,
        }
        for field in ("owner", "assignee", "queue"):
            value = getattr(payload, field)
            if value is not None:
                setattr(incident, field, value)
        if payload.priority is not None:
            incident.priority = payload.priority.value
            target = self._policy.sla_targets_minutes[payload.priority]
            incident.sla_due_at = datetime.now(UTC) + timedelta(minutes=target)
        after = {
            "owner": incident.owner,
            "assignee": incident.assignee,
            "queue": incident.queue,
            "priority": incident.priority,
        }
        if before == after:
            raise IncidentConflict("Incident assignment does not change any value")
        self._session.add(
            IncidentTimeline(
                incident_id=incident.id,
                event_type=IncidentTimelineType.ASSIGNMENT_CHANGED.value,
                actor=payload.actor,
                description=payload.reason or "Incident assignment updated",
                details={"before": before, "after": after},
            )
        )
        await self._publish(
            EventType.INCIDENT_ASSIGNED,
            incident.id,
            trace_id,
            {"actor": payload.actor, **after},
        )
        await self._session.commit()
        return await self.get(incident.id)

    async def add_artifact(
        self,
        incident_id: UUID,
        payload: IncidentArtifactCreate,
        *,
        trace_id: str,
    ) -> IncidentArtifact:
        incident = await self.get(incident_id)
        if len(incident.artifacts) >= self._policy.max_artifacts:
            raise IncidentPolicyViolation("Incident artifact limit reached")
        await self._validate_artifact(payload)
        artifact = IncidentArtifact(
            incident_id=incident.id,
            artifact_type=payload.artifact_type.value,
            reference_id=payload.reference_id,
            value=payload.value,
            label=payload.label,
            attributes=payload.attributes,
        )
        self._session.add(artifact)
        await self._session.flush()
        self._session.add(
            IncidentTimeline(
                incident_id=incident.id,
                event_type=IncidentTimelineType.ARTIFACT_LINKED.value,
                actor=payload.actor,
                description=f"Artifact {payload.artifact_type.value} linked",
                details={"artifact_id": str(artifact.id)},
            )
        )
        await self._publish(
            EventType.INCIDENT_ARTIFACT_LINKED,
            incident.id,
            trace_id,
            {"artifact_id": str(artifact.id), "type": payload.artifact_type.value},
        )
        await self._session.commit()
        await self._session.refresh(artifact)
        return artifact

    async def create_case(
        self,
        incident_id: UUID,
        payload: InvestigationCaseCreate,
        *,
        trace_id: str,
    ) -> InvestigationCase:
        incident = await self.get(incident_id)
        case = InvestigationCase(
            incident_id=incident.id,
            title=payload.title,
            status=InvestigationStatus.OPEN.value,
            owner=payload.owner if payload.owner is not None else incident.owner,
            assignee=payload.assignee if payload.assignee is not None else incident.assignee,
            queue=payload.queue if payload.queue is not None else incident.queue,
            attributes=payload.attributes,
        )
        self._session.add(case)
        await self._session.flush()
        self._session.add(
            IncidentTimeline(
                incident_id=incident.id,
                event_type=IncidentTimelineType.INVESTIGATION_ACTION.value,
                actor=payload.actor,
                description="Investigation Case created",
                details={"case_id": str(case.id), "title": case.title},
            )
        )
        await self._publish(
            EventType.INVESTIGATION_CASE_CREATED,
            incident.id,
            trace_id,
            {"case_id": str(case.id), "status": case.status, "actor": payload.actor},
        )
        await self._session.commit()
        return await self.get_case(case.id)

    async def list_cases(
        self,
        *,
        incident_id: UUID | None = None,
        status: str | None = None,
        assignee: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> PageResult[InvestigationCase]:
        return await self._cases.search(
            incident_id=incident_id,
            status=status,
            assignee=assignee,
            page=page,
            page_size=page_size,
        )

    async def get_case(self, case_id: UUID) -> InvestigationCase:
        case = await self._cases.get(case_id)
        if case is None:
            raise InvestigationCaseNotFound(f"Investigation Case {case_id} not found")
        return case

    async def add_case_comment(
        self,
        case_id: UUID,
        payload: CaseCommentCreate,
        *,
        trace_id: str,
    ) -> CaseComment:
        case = await self.get_case(case_id)
        comment = CaseComment(case_id=case.id, author=payload.author, body=payload.body)
        self._session.add(comment)
        await self._session.flush()
        self._session.add(
            IncidentTimeline(
                incident_id=case.incident_id,
                event_type=IncidentTimelineType.COMMENTED.value,
                actor=payload.author,
                description="Investigation Case comment added",
                details={"case_id": str(case.id), "comment_id": str(comment.id)},
            )
        )
        await self._publish(
            EventType.CASE_COMMENT_ADDED,
            case.incident_id,
            trace_id,
            {"case_id": str(case.id), "comment_id": str(comment.id)},
        )
        await self._session.commit()
        await self._session.refresh(comment)
        return comment

    def to_read(self, incident: Incident) -> IncidentRead:
        return IncidentRead.model_validate(
            {
                **incident.__dict__,
                "timelines": incident.timelines,
                "artifacts": incident.artifacts,
                "cases": incident.cases,
                "finding_ids": [item.finding_id for item in incident.findings],
                "event_ids": [item.event_id for item in incident.events],
                "knowledge_ids": [item.knowledge_id for item in incident.knowledge],
                "asset_ids": [item.asset_id for item in incident.assets],
            }
        )

    async def _require_ids(self, model: type[object], ids: list[UUID], label: str) -> None:
        if not ids:
            return
        found = set(
            await self._session.scalars(
                select(model.id).where(model.id.in_(set(ids)))  # type: ignore[attr-defined]
            )
        )
        missing = set(ids) - found
        if missing:
            raise IncidentPolicyViolation(
                f"Referenced {label} objects do not exist",
                details={"ids": sorted(str(item) for item in missing)},
            )

    async def _latest_knowledge_version(self, knowledge_id: UUID) -> UUID:
        version_id = await self._session.scalar(
            select(KnowledgeVersion.id)
            .where(KnowledgeVersion.knowledge_id == knowledge_id)
            .order_by(KnowledgeVersion.imported_at.desc())
            .limit(1)
        )
        if version_id is None:
            raise IncidentPolicyViolation("Knowledge has no immutable version")
        return version_id

    async def _validate_artifact(self, payload: IncidentArtifactCreate) -> None:
        mapping = {
            "ASSET": Asset,
            "EVIDENCE": Evidence,
            "FINDING": Finding,
            "SECURITY_EVENT": SecurityEvent,
            "KNOWLEDGE": Knowledge,
            "REPORT": Report,
        }
        model = mapping.get(payload.artifact_type.value)
        if model is not None and payload.reference_id is not None:
            await self._require_ids(model, [payload.reference_id], payload.artifact_type.value)

    @staticmethod
    def _as_incident(value: object) -> Incident:
        if not isinstance(value, Incident):
            raise TypeError("IncidentRuntime handler returned a non-Incident value")
        return value

    async def _publish(
        self,
        event_type: EventType,
        incident_id: UUID,
        trace_id: str,
        payload: dict[str, object],
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                aggregate_id=incident_id,
                trace_id=trace_id,
                actor="incident-service",
                resource="incident",
                payload=payload,
            )
        )
