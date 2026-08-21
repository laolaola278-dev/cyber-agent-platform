"""Phase 28 -- AcquisitionService (DB persistence + platform integration).

Bridges the AdaptiveDataAcquisitionAgent to the platform: persists runs /
plans / steps / artifacts / documents / completeness, and hands raw
artifacts to the existing EvidenceService so lineage stays unbroken
(Source -> Raw Artifact -> Evidence -> ExtractedDocument -> candidates).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from warnings import deprecated

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.models import (
    AcquisitionPolicy,
    AcquisitionResult,
    RawArtifact,
)
from app.acquisition.models_db import (
    AcquisitionArtifactRecord,
    AcquisitionPlanRecord,
    AcquisitionRun,
    CompletenessReportRecord,
    ExtractedDocumentRecord,
    PublicEndpointCandidateRecord,
)
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.store import EvidenceObjectStoreProvider, LocalFilesystemEvidenceStore
from app.acquisition.urlpolicy import URLPolicyValidator
from app.evidence.service import EvidenceService

# Fixed identity of the platform's acquisition system agent (FK target for
# runs created without an explicit agent). Phase 28.3 PostgreSQL
# certification: acquisition_runs.agent_id references agents.id.
DEFAULT_ACQUISITION_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000ac")


class AcquisitionService:
    """Platform-facing acquisition orchestration with persistence."""

    def __init__(
        self,
        session: AsyncSession,
        evidence_service: EvidenceService,
        *,
        store_root: Path | None = None,
        policy: AcquisitionPolicy | None = None,
        validator: URLPolicyValidator | None = None,
        store: EvidenceObjectStoreProvider | None = None,
        sandbox_runtime: Any | None = None,
        sandbox_profile: Any | None = None,
    ) -> None:
        self._session = session
        self._evidence = evidence_service
        self._store = store or LocalFilesystemEvidenceStore(
            store_root or Path("outputs/acquisition-objects")
        )
        self._policy = policy or AcquisitionPolicy()
        self._validator = validator or URLPolicyValidator()
        self._planner = AcquisitionPlanner(policy=self._policy)
        self._sandbox_runtime = sandbox_runtime
        self._sandbox_profile = sandbox_profile

    @property
    def session(self) -> AsyncSession:
        """Expose the session to worker-path coordination (lease lookups)."""
        return self._session

    @property
    def policy(self) -> AcquisitionPolicy:
        """Expose the active acquisition policy (backpressure reads it)."""
        return self._policy

    def _build_agent(self, run_id: str, trace_id: str) -> AdaptiveDataAcquisitionAgent:
        fetch_executor = None
        if self._sandbox_runtime is not None:
            from app.acquisition.sandboxed_fetch import SandboxedFetchExecutor
            from app.sandbox.profile import SandboxProfile

            profile = self._sandbox_profile or SandboxProfile(
                name="acquisition-fetch", timeout_seconds=self._policy.timeout_seconds * 2
            )
            fetch_executor = SandboxedFetchExecutor(
                self._sandbox_runtime,
                profile=profile,
                policy=self._policy,
                validator=self._validator,
            )
        http = HTTPAdapter(
            policy=self._policy,
            validator=self._validator,
            fetch_executor=fetch_executor,
        )
        evidence_sink = _EvidenceSink(self._evidence)

        return AdaptiveDataAcquisitionAgent(
            http=http,
            store=self._store,
            planner=self._planner,
            document=None,  # default DocumentAdapter
            evidence_sink=evidence_sink,  # type: ignore[arg-type]
            config=AgentConfig(task_id=run_id, trace_id=trace_id),
        )

    # -- Phase 28.1: async worker path --------------------------------------

    async def create(
        self,
        *,
        goal: str,
        url: str,
        target_asset: str = "",
        expected_fields: list[str] | None = None,
        expected_time_range: list[str] | None = None,
        expected_record_count: int | None = None,
        agent_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[AcquisitionRun, bool]:
        """Create an AcquisitionRun in QUEUED state (202 semantics).

        Returns (run, created). When ``idempotency_key`` matches an existing
        run with the same request fingerprint the existing run is returned
        (created=False); a conflicting payload raises 409 (ConflictError).
        Execution is NOT started here -- the caller submits the run to the
        Worker path.
        """
        run_id = uuid4()
        # Phase 28.3 PostgreSQL certification: acquisition_runs.agent_id is a
        # NOT NULL FK to agents. When no agent is supplied, ensure the fixed
        # system agent row exists so the enqueue can never fail on the FK.
        if agent_id is None:
            agent_id = DEFAULT_ACQUISITION_AGENT_ID
        from app.models.agent import Agent

        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            try:
                agent = Agent(
                    id=agent_id,
                    name="acquisition-system",
                    version="28.3",
                    status="ONLINE",
                    health_status="HEALTHY",
                )
                self._session.add(agent)
                await self._session.flush()
            except IntegrityError:
                # concurrent create provisioned the system agent first; the
                # row is now committed and visible to a fresh read
                await self._session.rollback()
                agent = await self._session.get(Agent, agent_id)
        # acquisition_runs.task_id is a NOT NULL FK to tasks: provision the
        # owning task row (task id == run id) so enqueue never fails on FK.
        from app.models.task import Task

        task = await self._session.get(Task, run_id)
        if task is None:
            self._session.add(
                Task(
                    id=run_id,
                    name=f"acquisition-{run_id.hex[:8]}",
                    task_type="acquisition",
                    status="QUEUED",
                    input={"goal": goal, "url": url},
                    required_permissions=[],
                    required_capabilities=["acquisition.http"],
                )
            )
            await self._session.flush()
        trace_id = uuid4().hex[:16]
        agent_id = agent_id or uuid4()
        fingerprint = _request_fingerprint(
            goal=goal,
            url=url,
            target_asset=target_asset,
            expected_fields=expected_fields or [],
            expected_time_range=expected_time_range,
            expected_record_count=expected_record_count,
        )

        if idempotency_key:
            from sqlalchemy import select

            existing = (
                await self._session.execute(
                    select(AcquisitionRun).where(AcquisitionRun.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.request_fingerprint == fingerprint:
                    return existing, False
                from app.acquisition.exceptions import AcquisitionConflict

                raise AcquisitionConflict("idempotency key reused with a different request")

        request = PlannerRequest(
            goal=goal,
            url=url,
            target_asset=target_asset,
            expected_fields=expected_fields or [],
            expected_time_range=(
                (expected_time_range[0], expected_time_range[1])
                if expected_time_range and len(expected_time_range) >= 2
                else None
            ),
            expected_record_count=expected_record_count,
            expected_record_type="records",
        )
        plan = self._planner.plan(request)

        run = AcquisitionRun(
            id=run_id,
            task_id=run_id,
            agent_id=agent_id,
            trace_id=trace_id,
            goal=goal,
            target_asset=target_asset or None,
            status="QUEUED",
            source_type=plan.source_type.value,
            strategy=plan.strategy,
            started_at=None,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            # durable scalar state for worker execution -- avoids touching the
            # lazy plan relationship inside the async worker operation
            checkpoint={
                "current_url": plan.urls[0] if plan.urls else plan.target,
                "page_number": 1,
                "status": "QUEUED",
                "expected_fields": list(request.expected_fields),
                "expected_time_range": (
                    list(request.expected_time_range) if request.expected_time_range else None
                ),
                "expected_record_count": request.expected_record_count,
            },
        )
        self._session.add(run)
        try:
            await self._session.flush()

            plan_record = AcquisitionPlanRecord(
                run_id=run_id,
                target=plan.target,
                source_type=plan.source_type.value,
                strategy=plan.strategy,
                steps=[s.__dict__ for s in plan.steps],
                expected_outputs=plan.expected_outputs,
                completeness_conditions=plan.completeness_conditions,
                budgets=plan.budgets,
                fallback_strategy=plan.fallback_strategy,
            )
            self._session.add(plan_record)
            await self._session.flush()
        except IntegrityError:
            # Phase 28.3 concurrent idempotency: two creates with the same
            # idempotency_key raced -- the loser hits the UNIQUE constraint.
            # Roll back and resolve deterministically against the committed
            # winner: same fingerprint -> return the existing run; different
            # fingerprint -> explicit 409 (never a leaked IntegrityError).
            await self._session.rollback()
            from sqlalchemy import select as _select

            existing = (
                await self._session.execute(
                    _select(AcquisitionRun).where(AcquisitionRun.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is None:
                # the constraint was not about idempotency_key -- re-raise
                raise
            if existing.request_fingerprint == fingerprint:
                return existing, False
            from app.acquisition.exceptions import AcquisitionConflict

            raise AcquisitionConflict("idempotency key reused with a different request") from None
        return run, True

    async def get_run(self, run_id: UUID, *, fresh: bool = False) -> AcquisitionRun:
        from sqlalchemy import select

        from app.acquisition.exceptions import AcquisitionNotFound

        if fresh:
            # force a fresh DB read (bypasses the session identity map) so a
            # worker sees state written by ANOTHER session (e.g. a concurrent
            # API cancel flipping the run to CANCEL_REQUESTED).
            run = (
                await self._session.execute(
                    select(AcquisitionRun)
                    .where(AcquisitionRun.id == run_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        else:
            run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        return run

    async def run_agent_operation(self, run: AcquisitionRun, checkpoint: Any) -> Any:
        """Execute the acquisition INSIDE the worker operation.

        Adapters (HTTP/Document/Store/Evidence sink) are constructed here --
        i.e. within the Worker/Sandbox boundary -- never by the API layer.
        All plan metadata is read from the durable checkpoint column so no
        lazy relationship is touched inside the async worker operation.

        Returns the AcquisitionRunPayload model; the Worker boundary performs
        the serialization (model_dump) when it crosses the sandbox.
        """
        from app.acquisition.checkpoint import AcquisitionCheckpoint
        from app.acquisition.worker_path import AcquisitionRunPayload

        state = dict(run.checkpoint or {})
        agent = self._build_agent(str(run.id), run.trace_id)
        request = self._planner_request_from_state(run, state)
        result = await agent.acquire(request, checkpoint=checkpoint)
        # Defer the terminal status: the worker path applies it atomically via
        # a conditional UPDATE (single linearization point). Writing status here
        # would auto-flush COMPLETE before the guarded UPDATE (see
        # _persist_result docstring). The legacy execute() path re-applies the
        # status via _apply_payload afterwards, so this is safe for both.
        await self._persist_result(run, result, run.id, apply_terminal=False)

        resumed = AcquisitionCheckpoint(run_id=str(run.id))
        resumed.snapshot(result)
        resumed.current_url = str(state.get("current_url") or "")
        if result.plan is not None:
            resumed.strategy = result.plan.strategy
        payload = AcquisitionRunPayload(
            status=result.status.value,
            source_type=result.plan.source_type.value if result.plan else "UNKNOWN",
            strategy=result.plan.strategy if result.plan else "",
            blocked_reason=result.blocked_reason.value,
            blocked_detail=result.blocked_detail,
            replans=result.replans,
            retries=result.retries,
            total_bytes=result.total_bytes,
            total_requests=len(result.visited_urls),
            duration_seconds=result.duration_seconds,
            strategy_history=list(result.strategy_history),
            visited_urls=list(result.visited_urls),
            evidence_ids=list(result.evidence_ids),
            documents_captured=len(result.documents),
            record_count=len(result.records),
            checkpoint=resumed.to_dict(),
        )
        return payload

    def _planner_request_from_state(
        self, run: AcquisitionRun, state: dict[str, Any]
    ) -> PlannerRequest:
        """Rebuild the PlannerRequest from the durable checkpoint state."""
        return PlannerRequest(
            goal=run.goal,
            url=str(state.get("current_url") or ""),
            target_asset=run.target_asset or "",
            expected_fields=list(state.get("expected_fields") or []),
            expected_time_range=(
                tuple(state["expected_time_range"]) if state.get("expected_time_range") else None
            ),
            expected_record_count=state.get("expected_record_count"),
            expected_record_type="records",
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def requeue(self, run_id: UUID) -> AcquisitionRun:
        """Durably reset a non-terminal run to QUEUED for the claim loop.

        The persisted checkpoint is preserved; resume continues the SAME run
        from the checkpoint cursor (never restarting from page 1).
        """
        from app.acquisition.exceptions import AcquisitionNotFound

        run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        checkpoint = dict(run.checkpoint or {})
        checkpoint["status"] = "QUEUED"
        run.checkpoint = checkpoint
        run.status = "QUEUED"
        run.cancel_requested_at = None
        await self._session.commit()
        return run

    @deprecated(
        "create_and_run executes synchronously inside the request path and is "
        "not durable. Use create() (enqueue) + the Worker Claim Loop instead. "
        "Scheduled for removal in v2.0."
    )
    async def create_and_run(
        self,
        *,
        goal: str,
        url: str,
        target_asset: str = "",
        expected_fields: list[str] | None = None,
        expected_time_range: list[str] | None = None,
        expected_record_count: int | None = None,
        agent_id: UUID | None = None,
    ) -> AcquisitionRun:
        run_id = uuid4()
        trace_id = uuid4().hex[:16]
        agent_id = agent_id or uuid4()

        request = PlannerRequest(
            goal=goal,
            url=url,
            target_asset=target_asset,
            expected_fields=expected_fields or [],
            expected_time_range=(
                (expected_time_range[0], expected_time_range[1])
                if expected_time_range and len(expected_time_range) >= 2
                else None
            ),
            expected_record_count=expected_record_count,
            expected_record_type="records",
        )
        plan = self._planner.plan(request)

        run = AcquisitionRun(
            id=run_id,
            task_id=run_id,
            agent_id=agent_id,
            trace_id=trace_id,
            goal=goal,
            target_asset=target_asset or None,
            status="PENDING",
            source_type=plan.source_type.value,
            strategy=plan.strategy,
            started_at=None,
        )
        self._session.add(run)
        await self._session.flush()

        plan_record = AcquisitionPlanRecord(
            run_id=run_id,
            target=plan.target,
            source_type=plan.source_type.value,
            strategy=plan.strategy,
            steps=[s.__dict__ for s in plan.steps],
            expected_outputs=plan.expected_outputs,
            completeness_conditions=plan.completeness_conditions,
            budgets=plan.budgets,
            fallback_strategy=plan.fallback_strategy,
        )
        self._session.add(plan_record)
        await self._session.flush()

        agent = self._build_agent(str(run_id), trace_id)
        result = await agent.acquire(request)

        await self._persist_result(run, result, run_id)
        await self._session.commit()
        return run

    async def _persist_result(
        self,
        run: AcquisitionRun,
        result: AcquisitionResult,
        run_id: UUID,
        *,
        apply_terminal: bool = True,
    ) -> None:
        # idempotent: resume re-runs the agent, so drop this run's previous
        # detail rows before re-inserting the accumulated result
        from sqlalchemy import delete

        for model in (
            AcquisitionArtifactRecord,
            ExtractedDocumentRecord,
            CompletenessReportRecord,
            PublicEndpointCandidateRecord,
        ):
            await self._session.execute(delete(model).where(model.run_id == run_id))
        # The terminal status/finished_at is applied ONLY via the atomic
        # conditional UPDATE in AcquisitionWorkerPath._finalize_terminal_atomic
        # when apply_terminal=False. Writing it here (as a dirty ORM attribute)
        # would be auto-flushed at the next SELECT -- flipping the DB status to
        # COMPLETE BEFORE the guarded UPDATE, defeating its
        # `status IN (RUNNING, PARTIAL)` pre-state check (the §7 ORM-writeback
        # hazard). The atomic UPDATE is the SINGLE writer of the terminal
        # transition; everything else (detail rows, plan metadata) is data.
        if apply_terminal:
            run.status = result.status.value
            run.finished_at = result.finished_at
        run.source_type = result.plan.source_type.value if result.plan else "UNKNOWN"
        run.strategy = result.plan.strategy if result.plan else ""
        run.blocked_reason = result.blocked_reason.value
        run.blocked_detail = result.blocked_detail or None
        run.replans = result.replans
        run.retries = result.retries
        run.total_bytes = result.total_bytes
        run.total_requests = len(result.visited_urls)
        run.duration_seconds = result.duration_seconds
        run.strategy_history = list(result.strategy_history)
        run.started_at = result.started_at

        for artifact in result.artifacts:
            self._session.add(
                AcquisitionArtifactRecord(
                    run_id=run_id,
                    object_key=artifact.object_key,
                    sha256=artifact.sha256,
                    size=artifact.size,
                    content_type=artifact.content_type,
                    source_url=artifact.source_url,
                    final_url=artifact.final_url,
                    http_status=artifact.http_status,
                    etag=artifact.etag,
                    last_modified=artifact.last_modified,
                    method=artifact.method,
                    tool=artifact.tool,
                    tool_version=artifact.tool_version,
                )
            )
        for document in result.documents:
            self._session.add(
                ExtractedDocumentRecord(
                    run_id=run_id,
                    title=document.title,
                    source_url=document.source_url,
                    evidence_id=None,
                    artifact_sha256=document.artifact_sha256,
                    extraction_backend=document.extraction_backend,
                    text_length=len(document.text),
                    doc_metadata=document.metadata,
                    published_at=document.published_at,
                    author=document.author,
                    language=document.language,
                )
            )
        if result.completeness is not None:
            self._session.add(
                CompletenessReportRecord(
                    run_id=run_id,
                    coverage_score=result.completeness.coverage_score,
                    field_completeness=result.completeness.field_completeness,
                    time_coverage=result.completeness.time_coverage,
                    pagination_complete=result.completeness.pagination_complete,
                    duplicates=result.completeness.duplicates,
                    gaps=list(result.completeness.gaps),
                    errors=list(result.completeness.errors),
                    confidence=result.completeness.confidence,
                    verdict=result.completeness.verdict.value,
                )
            )
        for candidate in result.endpoint_candidates:
            self._session.add(
                PublicEndpointCandidateRecord(
                    run_id=run_id,
                    url=candidate.url,
                    method=candidate.method,
                    state=candidate.state.value,
                    observed_from=candidate.observed_from,
                    content_type=candidate.content_type,
                    status=candidate.status,
                    reason=candidate.reason,
                )
            )


class _EvidenceSink:
    """Persist a raw artifact through the platform EvidenceService."""

    def __init__(self, evidence_service: EvidenceService) -> None:
        self._evidence = evidence_service

    async def commit(self) -> None:
        """Release the write lock between pagination pages (see EvidenceService.commit)."""
        await self._evidence.commit()

    async def save_evidence(
        self, artifact: RawArtifact, object_key: str, content: bytes = b""
    ) -> str:
        """Save the RAW artifact bytes as evidence.

        The evidence SHA-256 is the exact hash of the artifact bytes, so
        object-store key == Evidence.sha256 == Artifact.sha256 and the
        lineage is integrity-checkable end to end.
        """
        evidence = await self._evidence.save_object(
            task_id=_safe_uuid(artifact.task_id),
            # Phase 28.4 (PG FK): use the system acquisition agent that
            # service.create() guarantees exists; nil agent_id (00000000-0000-
            # 0000-0000-000000000000) violates the agents FK on PostgreSQL
            # (SQLite had FK enforcement off and never surfaced this).
            agent_id=DEFAULT_ACQUISITION_AGENT_ID,
            trace_id=artifact.trace_id or uuid4().hex[:16],
            url=artifact.source_url or artifact.final_url,
            http_status=artifact.http_status,
            title=artifact.final_url,
            content=content or artifact.sha256.encode(),
            content_type=artifact.content_type or "",
            object_storage_path=object_key,
        )
        return str(evidence.id)


def _safe_uuid(value: str | None) -> UUID:
    """Parse a task/run id string into a UUID; fall back to a fresh one."""
    if value:
        try:
            return UUID(value)
        except (ValueError, AttributeError):
            pass
    return uuid4()


def _request_fingerprint(
    *,
    goal: str,
    url: str,
    target_asset: str,
    expected_fields: list[str],
    expected_time_range: list[str] | None,
    expected_record_count: int | None,
) -> str:
    """Deterministic fingerprint of a create request for idempotency checks."""
    import hashlib
    import json

    payload = json.dumps(
        {
            "goal": goal,
            "url": url,
            "target_asset": target_asset,
            "expected_fields": expected_fields,
            "expected_time_range": expected_time_range,
            "expected_record_count": expected_record_count,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
