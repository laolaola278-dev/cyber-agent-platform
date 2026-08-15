"""Acquisition production path -- execute runs through the Worker/Sandbox boundary.

Phase 28.1: the API never constructs acquisition adapters or touches the
network directly; execution happens under

    PluginWorkerRuntime -> WorkerRuntime -> SandboxRuntime

Phase 28.2: the API no longer schedules execution at all (no
asyncio.create_task). Runs are enqueued as QUEUED rows; the Worker Claim
Loop atomically claims them (DB = source of truth), executes them here, and
commits results ONLY while the worker still holds fencing ownership
(Critical Gate -- stale writers are rejected).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.acquisition.checkpoint import AcquisitionCheckpoint
from app.acquisition.claim import fencing_hash
from app.acquisition.exceptions import AcquisitionStaleCommit
from app.acquisition.models_db import AcquisitionRun
from app.exceptions import (
    WorkerCancelledError,
    WorkerLeaseConflict,
    WorkerLeaseNotFound,
)
from app.repositories.worker import WorkerLeaseRepository

TERMINAL = ("COMPLETE", "BLOCKED", "CANCELLED", "FAILED")


class AcquisitionRunPayload(BaseModel):
    """Serializable result crossing the worker boundary."""

    status: str = "RUNNING"
    source_type: str = "UNKNOWN"
    strategy: str = ""
    blocked_reason: str = "NONE"
    blocked_detail: str = ""
    replans: int = 0
    retries: int = 0
    total_bytes: int = 0
    total_requests: int = 0
    duration_seconds: float = 0.0
    strategy_history: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    documents_captured: int = 0
    record_count: int = 0
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class AcquisitionWorkerPath:
    """Fencing-validated acquisition execution inside the Worker boundary."""

    PLUGIN_NAME = "acquisition"
    PLUGIN_VERSION = "28.3"
    CAPABILITY = "acquisition.http"

    def __init__(
        self,
        plugin: Any,
        service: Any,
        coordinator: Any = None,
        *,
        lease_ttl_seconds: int = 120,
        lease_renew_interval: float | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._plugin = plugin
        self._service = service
        self._coordinator = coordinator
        self._lease_ttl_seconds = lease_ttl_seconds
        self._metrics = metrics
        # Execution-time lease heartbeat: renew every lease_ttl / 3 seconds
        # (bounded well below the expiry margin, per Phase 28.3). An explicit
        # value overrides; 0 disables renewal entirely.
        if lease_renew_interval is None:
            lease_renew_interval = max(1.0, lease_ttl_seconds / 3.0)
        self._lease_renew_interval = float(lease_renew_interval)

    def _ensure_coordinator(self) -> Any:
        """Lazily build the claim coordinator from the service session."""
        if self._coordinator is None:
            from app.acquisition.claim import AcquisitionClaimCoordinator
            from app.worker.lease import WorkerLeaseManager

            self._coordinator = AcquisitionClaimCoordinator(
                self._service.session,
                WorkerLeaseManager(self._service.session),
                lease_ttl_seconds=self._lease_ttl_seconds,
            )
        return self._coordinator

    # -- legacy direct execution (Phase 28.1 compatibility) ------------------
    # Kept ONLY so the Phase 28.1 certification suite still passes. New code
    # MUST go through the Worker Claim Loop -> run_claimed() path, which adds
    # atomic DB claim + fencing-validated commit. Direct execution does NOT
    # claim and therefore MUST NOT be used by the API in 28.2.

    async def execute(self, run_id: UUID) -> AcquisitionRunPayload:
        """Run (or resume) one AcquisitionRun through the Worker chain (28.1)."""
        run = await self._service.get_run(run_id)
        checkpoint = AcquisitionCheckpoint.from_dict(run.checkpoint or {})
        if checkpoint.status in TERMINAL:
            return self._payload_from_run(run, checkpoint, "already terminal")

        payload = await self._plugin.execute(
            plugin_name=self.PLUGIN_NAME,
            plugin_version=self.PLUGIN_VERSION,
            capability=self.CAPABILITY,
            operation_name="acquire",
            owner=f"acquisition:{run_id}",
            operation=lambda: self._service.run_agent_operation(run, checkpoint),
            result_type=AcquisitionRunPayload,
            timeout_seconds=300,
        )
        await self._record_worker_identity(run, checkpoint)
        await self._apply_payload(run, payload)
        await self._service.commit()
        return payload

    # -- execution (called by the Worker Claim Loop) ------------------------

    async def run_claimed(
        self, run_id: UUID, worker_id: UUID, token: UUID
    ) -> AcquisitionRunPayload:
        """Execute one claimed run; commit ONLY while still the fencing owner.

        This is the worker-side runner invoked by the Claim Loop after a
        successful atomic claim. ``token`` is the fencing token minted at
        claim time; if the lease expired and another worker reclaimed the
        run, the commit is rejected (AcquisitionStaleCommit) and the stale
        result is never applied -- Critical Gate (spec 28.2 #5).
        """
        coordinator = self._ensure_coordinator()
        # verify we are still the current fencing owner (fails fast)
        await coordinator.verify_owner(run_id, worker_id, token)

        run = await self._service.get_run(run_id)
        checkpoint = AcquisitionCheckpoint.from_dict(run.checkpoint or {})
        if checkpoint.status in TERMINAL:
            return self._payload_from_run(run, checkpoint, "already terminal")

        # If the run was already cancel-requested before execution began, do
        # NOT start network work: finalize CANCELLED immediately.
        if run.status == "CANCEL_REQUESTED" and run.cancel_requested_at is not None:
            await self._finalize_cancelled(run, checkpoint)
            return self._payload_from_run(run, checkpoint, "cancelled before execution")

        async def on_start(sandbox_execution_id: UUID) -> None:
            # persist the live sandbox identity so a concurrent cancel request
            # can terminate the running sandbox execution
            live = await self._service.get_run(run_id)
            live.sandbox_execution_id = sandbox_execution_id
            await self._service.commit()

        async def cancel_aware() -> AcquisitionRunPayload:
            """Run the operation while polling the durable cancel flag.

            Production cancellation cannot rely on a shared in-memory sandbox
            handle (workers and the API are separate processes). Instead the
            worker polls the DB queue state: once the run is durably flipped
            to CANCEL_REQUESTED the operation is aborted at the next poll
            boundary (<= poll interval), so no new network / evidence work
            happens after cancellation is observed.

            The poll uses a SEPARATE read-only connection so it never
            interferes with the operation's own session/transaction. On
            cancel, the operation task is cancelled and the worker session is
            rolled back (a torn mid-flush is discarded, never committed).
            """
            from sqlalchemy.ext.asyncio import async_sessionmaker

            # A dedicated session-per-poll factory. Each poll opens a brand
            # new connection + transaction, so it always observes the LATEST
            # committed snapshot -- the API's durable CANCEL_REQUESTED is
            # visible across the process boundary (this is the production
            # cancel channel: DB flag + worker polling).
            poll_factory = async_sessionmaker(self._service.session.bind, expire_on_commit=False)
            operation_task = asyncio.create_task(
                self._service.run_agent_operation(run, checkpoint)
            )
            import time as _t

            _op_start = _t.monotonic()
            last_renew: float = 0.0
            while not operation_task.done():
                try:
                    async with poll_factory() as poll_session:
                        polled = await poll_session.get(AcquisitionRun, run_id)
                    poll_status = polled.status if polled is not None else None
                    poll_cancel_at = polled.cancel_requested_at if polled is not None else None
                    if poll_status == "CANCEL_REQUESTED" or poll_cancel_at is not None:
                        operation_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await operation_task
                        # discard the torn mid-flush, never commit it
                        await self._service.session.rollback()
                        raise WorkerCancelledError(
                            f"acquisition:{run_id} cancelled while executing"
                        )
                    # Phase 28.3 execution-time lease heartbeat: renew while
                    # the operation is alive so a healthy long-running
                    # acquisition never gets falsely reclaimed. Renewal is
                    # fencing-gated (run ownership + lease version CAS).
                    now = asyncio.get_running_loop().time()
                    if self._lease_renew_interval > 0 and (
                        now - last_renew >= self._lease_renew_interval
                    ):
                        await self._renew_lease(
                            poll_factory,
                            polled.lease_id if polled is not None else None,
                            run_id,
                            worker_id,
                            token,
                        )
                        last_renew = now
                except asyncio.CancelledError:
                    raise
                except (WorkerLeaseConflict, WorkerLeaseNotFound) as error:
                    # we lost the lease (expired + reclaimed by another worker,
                    # or released by a concurrent cancel): stop working and
                    # NEVER attach our result. The run now belongs to the new
                    # owner -- a stale CANCELLED/result write must not happen.
                    operation_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await operation_task
                    await self._service.session.rollback()
                    raise AcquisitionStaleCommit(
                        f"acquisition:{run_id} lost lease ownership while executing"
                    ) from error
                except AcquisitionStaleCommit:
                    # we lost fencing ownership (e.g. the lease was reclaimed
                    # and our renewal's verify_owner was rejected): stop the
                    # operation task now -- never let it keep writing side
                    # effects against a lost ownership -- then discard the
                    # torn session and report the lost ownership.
                    operation_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await operation_task
                    await self._service.session.rollback()
                    raise
                except Exception:  # noqa: BLE001 -- transient read failure
                    pass
                await asyncio.sleep(0.05)
            return await operation_task

        try:
            payload = await self._plugin.execute(
                plugin_name=self.PLUGIN_NAME,
                plugin_version=self.PLUGIN_VERSION,
                capability=self.CAPABILITY,
                operation_name="acquire",
                owner=f"acquisition:{run_id}",
                operation=cancel_aware,
                result_type=AcquisitionRunPayload,
                timeout_seconds=300,
                on_execution_start=on_start,
            )
        except WorkerCancelledError:
            # the sandbox execution was terminated (cancel / timeout path):
            # the stale result is dropped. Only finalize CANCELLED while we
            # still own the run (or it is already CANCELLED) -- never over a
            # reclaimed run owned by another worker.
            return await self._finalize_cancelled_if_safe(
                run_id, worker_id, "cancelled during execution"
            )
        except AcquisitionStaleCommit:
            # we lost fencing ownership while executing (lease expired and the
            # run was reclaimed, or our renewal was rejected): stop silently.
            # The run now belongs to the new owner -- a stale CANCELLED /
            # result write must NOT happen.
            await self._service.session.rollback()
            fresh = await self._service.get_run(run_id, fresh=True)
            ck = AcquisitionCheckpoint.from_dict(fresh.checkpoint or {})
            return self._payload_from_run(fresh, ck, "ownership lost during execution")
        except WorkerLeaseConflict:
            # the lease was released/expired while we ran (e.g. a concurrent
            # cancel released it): our sandbox commit was rejected by fencing
            # (Critical Gate) -- never apply stale results; finalize CANCELLED
            # only when we still own the run or cancellation was requested.
            await self._service.session.rollback()
            return await self._finalize_cancelled_if_safe(
                run_id, worker_id, "stale commit rejected"
            )
        except asyncio.CancelledError:
            # the surrounding task was cancelled: finalize CANCELLED too, but
            # only when safe (ownership guard).
            await self._service.session.rollback()
            return await self._finalize_cancelled_if_safe(
                run_id, worker_id, "cancelled during execution"
            )
        except Exception:
            # any other failure while executing: if a cancellation was
            # requested, the run must still land on CANCELLED (never a stale
            # success, never stuck mid-transition). Otherwise the exception is
            # re-raised for the claim loop to record.
            await self._service.session.rollback()
            failed_run = await self._service.get_run(run_id, fresh=True)
            if (
                failed_run.status == "CANCEL_REQUESTED"
                or failed_run.cancel_requested_at is not None
            ):
                return await self._finalize_cancelled_if_safe(
                    run_id, worker_id, "cancelled on failure"
                )
            raise

        # Critical Gate: re-verify fencing ownership BEFORE applying the
        # result. If the lease expired mid-run and another worker reclaimed,
        # verify_owner raises AcquisitionStaleCommit and the stale result is
        # dropped. NOTE: this gate sits outside the execute try/except, so the
        # stale path must be handled HERE -- a lost-ownership exception must
        # become a clean payload, never an escape (the new owner's run must
        # not be disturbed, and no terminal write may happen).
        try:
            await coordinator.verify_owner(run_id, worker_id, token)
        except AcquisitionStaleCommit:
            await self._service.session.rollback()
            fresh = await self._service.get_run(run_id, fresh=True)
            ck = AcquisitionCheckpoint.from_dict(fresh.checkpoint or {})
            return self._payload_from_run(fresh, ck, "ownership lost during execution")

        # Critical Gate: atomically apply the terminal result via a DB
        # conditional UPDATE guarded by a non-terminal pre-state + fencing
        # ownership. Cancel and complete race at the DB layer (single
        # linearization point), NOT on a 50ms polling boundary: if a concurrent
        # cancel already durably flipped the run to CANCEL_REQUESTED (or a
        # reclaim swapped ownership), this UPDATE matches 0 rows and the stale
        # completion is discarded (its pending evidence rolls back with it).
        await self._record_worker_identity(run, checkpoint)
        applied = await self._finalize_terminal_atomic(run_id, worker_id, token, payload)
        if applied:
            return payload

        # The conditional transition lost. Read the durable state and converge
        # to a terminal outcome (never leave CANCEL_REQUESTED lingering).
        current = await self._service.get_run(run_id, fresh=True)
        ck = AcquisitionCheckpoint.from_dict(current.checkpoint or {})
        if current.status == "CANCEL_REQUESTED" or current.cancel_requested_at is not None:
            # cancel won the race -> finalize CANCELLED (idempotent conditional)
            await self._finalize_cancelled_if_safe(run_id, worker_id, "cancelled before commit")
            return self._payload_from_run(current, ck, "cancelled before commit")
        # reclaimed (ownership lost) or already terminal -> never touch it
        return self._payload_from_run(current, ck, "ownership lost during execution")

    # -- lease heartbeat -----------------------------------------------------

    async def _renew_lease(
        self,
        factory: Any,
        lease_id: Any,
        run_id: UUID,
        worker_id: UUID,
        token: UUID,
    ) -> None:
        """Renew the run's lease while executing (fencing-gated heartbeat).

        Phase 28.3: a healthy long-running acquisition must NOT be falsely
        reclaimed when its operation outlives the lease TTL. Renewal runs on
        a SEPARATE connection (never disturbing the operation's session) and
        is gated by run-level ownership: the caller must still be the fencing
        owner (worker_id + claim-token hash). A worker whose run was reclaimed
        raises AcquisitionStaleCommit -- its renewal is rejected and any later
        commit is also rejected by the same gate.
        """
        from app.worker.lease import WorkerLeaseManager

        if lease_id is None:
            return
        try:
            async with factory() as session:
                current = await session.get(AcquisitionRun, run_id)
                if (
                    current is None
                    or current.worker_id != worker_id
                    or current.claim_token_hash != fencing_hash(token)
                ):
                    raise AcquisitionStaleCommit(
                        f"acquisition:{run_id}: ownership lost before lease renewal"
                    )
                leases = WorkerLeaseManager(session)
                lease = await leases.require(lease_id)
                await leases.renew(
                    lease.id,
                    owner=f"acquisition:{run_id}",
                    fencing_token=lease.fencing_token,
                    expected_version=lease.version,
                    ttl_seconds=self._lease_ttl_seconds,
                )
        except Exception:
            if self._metrics is not None:
                self._metrics.inc("worker_lease_renew_failure_total")
            raise
        if self._metrics is not None:
            self._metrics.inc("worker_lease_renew_total")

    # -- cancellation -------------------------------------------------------

    async def cancel(self, run_id: UUID) -> AcquisitionRunPayload:
        """Cancel a run: CANCEL_REQUESTED -> terminate -> CANCELLED.

        Phase 28.2 semantics:
          * a run that was NEVER claimed (QUEUED, no fencing token) is
            finalized CANCELLED immediately (no background work exists);
          * a run that IS claimed (RUNNING/PARTIAL/CANCEL_REQUESTED) is
            durably flipped to CANCEL_REQUESTED and the live sandbox (if any)
            is terminated; the worker-side runner -- the only party that owns
            the execution -- finalizes CANCELLED. The run is NEVER marked
            CANCELLED while background work keeps running.
        """
        run = await self._service.get_run(run_id)
        checkpoint = AcquisitionCheckpoint.from_dict(run.checkpoint or {})
        if checkpoint.status in TERMINAL:
            return self._payload_from_run(run, checkpoint, "already terminal")

        if run.claim_token_hash is None:
            # never claimed: normally nothing is executing -- safe to cancel
            # immediately. Best-effort terminate in case a sandbox identity
            # was recorded (tests / partial setups).
            if run.sandbox_execution_id is not None:
                try:
                    await self._plugin.terminate(run.sandbox_execution_id)
                except Exception:  # noqa: BLE001 -- best-effort
                    pass
            await self._finalize_cancelled(run, checkpoint)
            return self._payload_from_run(run, checkpoint, "cancelled")

        # claimed (RUNNING/PARTIAL): durable request state first. The flip to
        # CANCEL_REQUESTED is a conditional UPDATE guarded on a non-terminal
        # pre-state, so a completion that already durably landed COMPLETE/
        # BLOCKED/FAILED is never overwritten (cancel loses cleanly to a
        # committed terminal result, instead of clobbering it).
        from sqlalchemy import update

        now = datetime.now(UTC)
        checkpoint.status = "CANCEL_REQUESTED"
        stmt = (
            update(AcquisitionRun)
            .where(
                AcquisitionRun.id == run_id,
                AcquisitionRun.status.in_(("RUNNING", "PARTIAL")),
            )
            .values(
                status="CANCEL_REQUESTED",
                cancel_requested_at=now,
                checkpoint=checkpoint.to_dict(),
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._service.session.execute(stmt)
        if result.rowcount != 1:
            # run became terminal between the read and this UPDATE -> no-op
            await self._service.session.rollback()
            final = await self._service.get_run(run_id, fresh=True)
            ck = AcquisitionCheckpoint.from_dict(final.checkpoint or {})
            return self._payload_from_run(final, ck, "already terminal")
        await self._service.commit()
        # keep the in-memory objects in sync for the terminate/return below
        run.status = "CANCEL_REQUESTED"
        run.cancel_requested_at = now
        run.checkpoint = checkpoint.to_dict()

        # terminate the live sandbox execution (if any) -- resources closed.
        # If the plugin can actually terminate it (real worker runtime /
        # shared provider), the execution is fully unwound NOW, so it is safe
        # to finalize CANCELLED immediately. Otherwise (synthetic control
        # plane, or the execution is not registered with this provider) the
        # durable CANCEL_REQUESTED flag remains for the worker to observe.
        terminated = False
        if run.sandbox_execution_id is not None:
            try:
                terminated = bool(await self._plugin.terminate(run.sandbox_execution_id))
            except Exception:  # noqa: BLE001 -- best-effort
                terminated = False
        if terminated:
            final = await self._service.get_run(run_id, fresh=True)
            ck = AcquisitionCheckpoint.from_dict(final.checkpoint or {})
            await self._finalize_cancelled(final, ck)
            return self._payload_from_run(final, ck, "cancelled")
        # the executing worker (or the claim loop) finalizes CANCELLED after
        # the sandbox unwinds; we never flip CANCELLED while work may run.
        return self._payload_from_run(run, checkpoint, "cancel requested")

    async def _finalize_terminal_atomic(
        self, run_id: UUID, worker_id: UUID, token: UUID, payload: AcquisitionRunPayload
    ) -> bool:
        """Atomically apply the operation's terminal result.

        Single DB linearization point: a conditional UPDATE guarded by a
        non-terminal pre-state (RUNNING/PARTIAL) AND fencing ownership
        (worker_id + claim_token_hash). If a concurrent cancel already flipped
        the run to CANCEL_REQUESTED, or a reclaim swapped ownership, this
        UPDATE matches 0 rows and the pending evidence/result is rolled back
        (never attached post-cancel). Returns True when this worker's result
        won the transition.
        """
        from sqlalchemy import update

        now = datetime.now(UTC)
        terminal = payload.status in TERMINAL
        stmt = (
            update(AcquisitionRun)
            .where(
                AcquisitionRun.id == run_id,
                AcquisitionRun.status.in_(("RUNNING", "PARTIAL")),
                AcquisitionRun.worker_id == worker_id,
                AcquisitionRun.claim_token_hash == fencing_hash(token),
            )
            .values(
                status=payload.status,
                finished_at=now if terminal else None,
                checkpoint=payload.checkpoint,
            )
            .execution_options(synchronize_session=False)
        )
        # Disable autoflush: the worker session holds pending result/evidence
        # writes (including run.status from _persist_result). Auto-flushing
        # those BEFORE the guarded UPDATE would flip the DB status to COMPLETE
        # first, so the UPDATE's `status IN (RUNNING, PARTIAL)` guard would
        # match 0 rows. Execute the guarded UPDATE against the committed state
        # instead; the pending writes commit atomically right after.
        with self._service.session.no_autoflush:
            result = await self._service.session.execute(stmt)
        if result.rowcount != 1:
            # lost the race (cancelled or reclaimed): discard pending writes
            await self._service.session.rollback()
            return False
        await self._service.commit()
        return True

    async def _finalize_cancelled_if_safe(
        self, run_id: UUID, worker_id: UUID, note: str
    ) -> AcquisitionRunPayload:
        """Finalize CANCELLED only when it is safe to do so.

        Phase 28.3 side-effect fencing rule: a worker may write the terminal
        CANCELLED state only while it is STILL the recorded fencing owner of
        the run (or the run is already CANCELLED -- idempotent). The ownership
        check is folded into a conditional UPDATE (DB-side), so a reclaim
        between the read and the write is caught atomically; a stale worker
        never clobbers the new owner's execution.
        """
        fresh = await self._service.get_run(run_id, fresh=True)
        ck = AcquisitionCheckpoint.from_dict(fresh.checkpoint or {})
        if fresh.status == "CANCELLED":
            # already cancelled -> idempotent
            return self._payload_from_run(fresh, ck, note)
        finalized = await self._finalize_cancelled(fresh, ck, worker_id=worker_id)
        if finalized:
            return self._payload_from_run(fresh, ck, note)
        # stale: never write over the new owner's run
        return self._payload_from_run(fresh, ck, f"{note} (ownership lost)")

    async def _finalize_cancelled(
        self, run: Any, checkpoint: AcquisitionCheckpoint, *, worker_id: UUID | None = None
    ) -> bool:
        """Release lease + close state; only then mark CANCELLED.

        The terminal write is a conditional UPDATE in a DEDICATED session (the
        worker's own session may hold a stale snapshot or be mid-rollback),
        guarded by ``status NOT IN TERMINAL`` (no double terminal transition)
        and, when ``worker_id`` is given, by run ownership so a stale worker
        cannot clobber a reclaimed run. When ``worker_id`` is None (the
        never-claimed cancel path), the run must still be unclaimed
        (claim_token_hash NULL) to avoid racing a concurrent claim.
        Returns True when this call performed the CANCELLED transition.
        """
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import async_sessionmaker

        try:
            bind = self._service.session.bind
        except Exception:  # noqa: BLE001 -- session access may fail in tests
            bind = None
        now = datetime.now(UTC)
        checkpoint.status = "CANCELLED"
        ck_dict = checkpoint.to_dict()
        if bind is None:
            # no usable session -> best-effort in-memory finalization only
            run.status = "CANCELLED"
            run.cancelled_at = now
            run.checkpoint = ck_dict
            run.finished_at = now
            return True

        final_factory = async_sessionmaker(bind, expire_on_commit=False)
        async with final_factory() as final_session:
            # release the lease held for this run (owner-agnostic best effort)
            if run.lease_id is not None:
                try:
                    leases = WorkerLeaseRepository(final_session)
                    lease = await leases.get(run.lease_id)
                    if lease is not None:
                        await leases.update(
                            lease,
                            {
                                "status": "RELEASED",
                                "released_at": None,
                                "version": lease.version + 1,
                            },
                        )
                except Exception:  # noqa: BLE001 -- best-effort
                    pass
            where = [
                AcquisitionRun.id == run.id,
                AcquisitionRun.status.not_in(TERMINAL),
            ]
            if worker_id is not None:
                where.append(AcquisitionRun.worker_id == worker_id)
            else:
                # never-claimed cancel: refuse to clobber a concurrently claimed run
                where.append(AcquisitionRun.claim_token_hash.is_(None))
            stmt = (
                update(AcquisitionRun)
                .where(*where)
                .values(
                    status="CANCELLED",
                    cancelled_at=now,
                    finished_at=now,
                    checkpoint=ck_dict,
                )
                .execution_options(synchronize_session=False)
            )
            result = await final_session.execute(stmt)
            await final_session.commit()
            if result.rowcount == 1:
                # refresh the in-memory run object for the caller's payload
                run.status = "CANCELLED"
                run.cancelled_at = now
                run.checkpoint = ck_dict
                run.finished_at = now
                return True
            return False

    # -- internals -----------------------------------------------------------

    async def _record_worker_identity(self, run: Any, checkpoint: AcquisitionCheckpoint) -> None:
        execution = getattr(self._plugin, "last_execution", None)
        if execution is None:
            return
        run.worker_id = execution.worker_id
        run.sandbox_execution_id = execution.sandbox_execution_id
        run.worker_execution_id = execution.execution_id
        try:
            leases = WorkerLeaseRepository(self._service.session)
            lease = await leases.get_by_execution_id(execution.execution_id)
            if lease is not None:
                run.lease_id = lease.id
        except Exception:  # noqa: BLE001 -- identity is best-effort
            pass

    async def _apply_payload(self, run: Any, payload: AcquisitionRunPayload) -> None:
        run.status = payload.status
        run.source_type = payload.source_type
        run.strategy = payload.strategy
        run.blocked_reason = payload.blocked_reason
        run.blocked_detail = payload.blocked_detail
        run.replans = payload.replans
        run.retries = payload.retries
        run.total_bytes = payload.total_bytes
        run.total_requests = payload.total_requests
        run.duration_seconds = payload.duration_seconds
        run.strategy_history = payload.strategy_history
        run.checkpoint = payload.checkpoint
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        if payload.status in TERMINAL:
            run.finished_at = datetime.now(UTC)

    @staticmethod
    def _payload_from_run(
        run: Any, checkpoint: AcquisitionCheckpoint, note: str
    ) -> AcquisitionRunPayload:
        return AcquisitionRunPayload(
            status=run.status,
            source_type=run.source_type,
            strategy=run.strategy,
            blocked_reason=run.blocked_reason,
            blocked_detail=run.blocked_detail or "",
            replans=run.replans,
            retries=run.retries,
            total_bytes=run.total_bytes,
            total_requests=run.total_requests,
            duration_seconds=run.duration_seconds,
            strategy_history=list(run.strategy_history or []),
            checkpoint=checkpoint.to_dict(),
            error=note,
        )
