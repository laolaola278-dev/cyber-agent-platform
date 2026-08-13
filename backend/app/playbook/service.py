"""SOAR Playbook application service and event trigger boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import PlaybookConflict, PlaybookNotFound, PlaybookValidationError
from app.models.playbook import Playbook, PlaybookExecution, PlaybookTrigger, PlaybookVersion
from app.playbook.contracts import (
    PlaybookCreate,
    PlaybookDSL,
    PlaybookExecutionStatus,
    PlaybookRead,
    PlaybookRunRequest,
    PlaybookTriggerType,
)
from app.playbook.planner import PlaybookPlanner
from app.playbook.policy import PlaybookPolicy
from app.playbook.registry import PlaybookRegistry
from app.playbook.runtime import PlaybookRuntime
from app.repositories.pagination import PageResult
from app.repositories.playbook import (
    PlaybookExecutionRepository,
    PlaybookRepository,
    PlaybookTriggerRepository,
    PlaybookVersionRepository,
)


class PlaybookService:
    def __init__(
        self,
        session: AsyncSession,
        playbooks: PlaybookRepository,
        versions: PlaybookVersionRepository,
        executions: PlaybookExecutionRepository,
        triggers: PlaybookTriggerRepository,
        registry: PlaybookRegistry,
        planner: PlaybookPlanner,
        runtime: PlaybookRuntime,
        policy: PlaybookPolicy,
        publisher: EventPublisher,
    ) -> None:
        self._session = session
        self._playbooks = playbooks
        self._versions = versions
        self._executions = executions
        self._triggers = triggers
        self._registry = registry
        self._planner = planner
        self._runtime = runtime
        self._policy = policy
        self._publisher = publisher

    async def create(self, payload: PlaybookCreate, *, trace_id: str, actor: str) -> Playbook:
        try:
            document = PlaybookDSL.load(payload.yaml)
        except (ValueError, PydanticValidationError) as error:
            raise PlaybookValidationError(str(error)) from error
        self._policy.validate_document(document)
        if await self._playbooks.get_by_name(document.name) is not None:
            raise PlaybookConflict(f"Playbook name already exists: {document.name}")
        playbook = Playbook(
            name=document.name,
            description=document.description,
            enabled=payload.enabled,
        )
        await self._playbooks.add(playbook)
        canonical = document.model_dump(mode="json")
        version = PlaybookVersion(
            playbook_id=playbook.id,
            version="1.0.0",
            dsl_version=document.dsl_version,
            source_yaml=payload.yaml,
            document=canonical,
            checksum=self._checksum(canonical),
        )
        await self._versions.add(version)
        trigger = PlaybookTrigger(
            playbook_version_id=version.id,
            trigger_type=document.trigger.type.value,
            filters=document.trigger.filters,
            enabled=payload.enabled,
        )
        await self._triggers.add(trigger)
        await self._registry.register(version)
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.PLAYBOOK_CREATED,
                aggregate_id=playbook.id,
                trace_id=trace_id,
                actor=actor,
                resource="playbook",
                payload={
                    "version": version.version,
                    "dsl_version": version.dsl_version,
                    "trigger": trigger.trigger_type,
                },
            )
        )
        await self._session.commit()
        return await self.get(playbook.id)

    async def list(self, *, page: int, page_size: int) -> PageResult[Playbook]:
        return await self._playbooks.list_with_versions(page=page, page_size=page_size)

    async def get(self, playbook_id: UUID) -> Playbook:
        playbook = await self._playbooks.get_with_latest_version(playbook_id)
        if playbook is None:
            raise PlaybookNotFound(f"Playbook {playbook_id} not found")
        return playbook

    async def run(
        self,
        playbook_id: UUID,
        payload: PlaybookRunRequest,
        *,
        trace_id: str,
        trigger_type: PlaybookTriggerType = PlaybookTriggerType.MANUAL,
        trigger_id: UUID | None = None,
    ) -> PlaybookExecution:
        if payload.idempotency_key:
            existing = await self._executions.get_by_idempotency_key(payload.idempotency_key)
            if existing is not None:
                if existing.playbook_id != playbook_id:
                    raise PlaybookConflict("Idempotency key belongs to another Playbook")
                return await self.get_execution(existing.id)
        playbook = await self.get(playbook_id)
        if not playbook.enabled:
            raise PlaybookConflict("Disabled Playbooks cannot run")
        version, document = await self._registry.latest(playbook_id)
        if trigger_type is not document.trigger.type:
            raise PlaybookValidationError("Playbook trigger does not match this execution")
        plan = self._planner.plan(document, actor=payload.actor)
        for approval in payload.approvals.values():
            self._policy.authorize_approver(approval.approver)
            if approval.approver == payload.actor:
                raise PlaybookValidationError("Runner and approver must be distinct")
        execution = PlaybookExecution(
            playbook_id=playbook.id,
            playbook_version_id=version.id,
            trigger_id=trigger_id,
            trigger_type=trigger_type.value,
            status=PlaybookExecutionStatus.PENDING.value,
            actor=payload.actor,
            input=payload.input,
            context={},
            trace_id=trace_id,
            idempotency_key=payload.idempotency_key,
        )
        await self._executions.add(execution)
        await self._session.commit()
        return await self._runtime.execute(execution, plan, approvals=payload.approvals)

    async def resume(
        self,
        execution_id: UUID,
        payload: PlaybookRunRequest,
        *,
        trace_id: str,
    ) -> PlaybookExecution:
        execution = await self.get_execution(execution_id)
        if execution.status != PlaybookExecutionStatus.WAITING_APPROVAL.value:
            raise PlaybookConflict("Only executions waiting for approval can be resumed")
        if payload.input:
            raise PlaybookValidationError("Resume cannot replace execution input")
        if payload.actor != execution.actor:
            raise PlaybookValidationError("Resume actor must match the original runner")
        for approval in payload.approvals.values():
            self._policy.authorize_approver(approval.approver)
            if approval.approver == execution.actor:
                raise PlaybookValidationError("Runner and approver must be distinct")
        version, document = await self._registry.latest(execution.playbook_id)
        if version.id != execution.playbook_version_id:
            raise PlaybookConflict("Execution version is no longer the current Playbook version")
        plan = self._planner.plan(document, actor=execution.actor)
        execution.trace_id = trace_id
        await self._session.commit()
        return await self._runtime.execute(execution, plan, approvals=payload.approvals)

    async def list_executions(self, *, page: int, page_size: int) -> PageResult[PlaybookExecution]:
        return await self._executions.list_with_steps(page=page, page_size=page_size)

    async def get_execution(self, execution_id: UUID) -> PlaybookExecution:
        execution = await self._executions.get_with_steps(execution_id)
        if execution is None:
            raise PlaybookNotFound(f"Playbook Execution {execution_id} not found")
        return execution

    async def handle_incident_created(self, event: PlatformEvent) -> list[PlaybookExecution]:
        if event.type is not EventType.INCIDENT_CREATED:
            return []
        payload = {**event.payload, "incident_id": str(event.aggregate_id)}
        results: list[PlaybookExecution] = []
        for trigger in await self._triggers.active_for_type(
            PlaybookTriggerType.INCIDENT_CREATED.value
        ):
            if not trigger.version.playbook.enabled:
                continue
            if not self._policy.matches_filters(trigger.filters, payload):
                continue
            results.append(
                await self.run(
                    trigger.version.playbook_id,
                    PlaybookRunRequest(
                        input=payload,
                        actor="playbook-event",
                        idempotency_key=f"incident:{event.id}:{trigger.id}",
                    ),
                    trace_id=event.trace_id,
                    trigger_type=PlaybookTriggerType.INCIDENT_CREATED,
                    trigger_id=trigger.id,
                )
            )
        return results

    @staticmethod
    def to_read(playbook: Playbook) -> PlaybookRead:
        version = max(playbook.versions, key=lambda item: item.created_at)
        return PlaybookRead(
            id=playbook.id,
            name=playbook.name,
            version=version.version,
            description=playbook.description,
            enabled=playbook.enabled,
            document=version.document,
            created_at=playbook.created_at,
            updated_at=playbook.updated_at,
        )

    @staticmethod
    def _checksum(document: dict[str, Any]) -> str:
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
