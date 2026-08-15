"""Phase 28.2 -- bounded Acquisition Worker Claim Loop.

The API only enqueues runs (QUEUED, 202). Execution happens HERE: the loop
polls the durable DB queue (the database is the source of truth -- there is
no in-memory queue), atomically claims runs, executes them through the
Worker/Sandbox boundary, and records observability fields.

Loop properties (spec 28.2 #12):
  * bounded polling: poll_interval (never a busy loop);
  * batch_size: claims at most N runs per tick;
  * shutdown: cooperative stop signal; draining: finish in-flight, stop
    claiming new work, then exit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.exceptions import AcquisitionClaimConflict, AcquisitionNotFound
from app.acquisition.models_db import AcquisitionRun
from app.worker.contracts import LeaseStatus, WorkerHeartbeat, WorkerStatus
from app.worker.registry import WorkerRegistry

logger = logging.getLogger("cap.acquisition.claim_loop")

# terminal run states: only these release the run lease after execution
TERMINAL = ("COMPLETE", "BLOCKED", "CANCELLED", "FAILED")

ClaimRunner = Callable[[UUID, UUID], Awaitable[Any]]


@dataclass
class LoopStats:
    claimed: int = 0
    reclaimed: int = 0
    completed: int = 0
    cancelled: int = 0
    skipped_terminal: int = 0
    stale_rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "reclaimed": self.reclaimed,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "skipped_terminal": self.skipped_terminal,
            "stale_rejected": self.stale_rejected,
            "errors": self.errors[:20],
        }


class AcquisitionWorkerLoop:
    """Bounded DB-queue worker that claims and executes acquisition runs."""

    def __init__(
        self,
        session: AsyncSession,
        coordinator: AcquisitionClaimCoordinator,
        worker_id: UUID,
        runner: ClaimRunner,
        *,
        poll_interval: float = 0.05,
        batch_size: int = 5,
        registry: WorkerRegistry | None = None,
        metrics: Any | None = None,
        readiness: Any | None = None,
    ) -> None:
        self._session = session
        self._coordinator = coordinator
        self._worker_id = worker_id
        self._runner = runner
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._registry = registry
        self._metrics = metrics
        self._readiness = readiness
        self._shutdown = asyncio.Event()
        self._draining = False
        self._in_flight: set[UUID] = set()
        self.stats = LoopStats()

    # -- lifecycle -----------------------------------------------------------

    def request_shutdown(self) -> None:
        """Cooperative stop: stop claiming new work, finish in-flight runs."""
        self._draining = True
        self._shutdown.set()

    async def drain(self, timeout: float | None = None) -> LoopStats:
        """Wait until all in-flight runs finish (or timeout), then return stats."""
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        while self._in_flight:
            if deadline is not None and asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(self._poll_interval)
        return self.stats

    # -- main loop -----------------------------------------------------------

    async def run_forever(self) -> LoopStats:
        """Poll the durable queue until shutdown is requested (bounded loop)."""
        while not self._shutdown.is_set():
            await self.heartbeat()
            await self.tick()
            if self._draining and not self._in_flight:
                break
            await asyncio.sleep(self._poll_interval)
        await self.drain()
        return self.stats

    async def tick(self) -> LoopStats:
        """One bounded poll cycle: expire stale leases, claim, execute, recover.

        Recovery (Phase 28.3): after expiring leases and claiming fresh
        QUEUED work, RUNNING/PARTIAL runs whose lease is no longer ACTIVE are
        atomically reclaimed through the coordinator (new fencing epoch,
        recovery_count += 1) and executed here. While DRAINING no new
        recovery begins (only in-flight work is finished).
        """
        # Phase 28.4 (GATE 15): a worker whose critical dependency is down must
        # stop claiming new work (readiness=false). Existing in-flight runs
        # are untouched.
        if self._readiness is not None:
            try:
                ready = await self._readiness()
            except Exception:  # noqa: BLE001
                ready = False
            if not ready:
                if self._metrics is not None:
                    self._metrics.inc("worker_claim_skipped_unhealthy")
                await asyncio.sleep(self._poll_interval)
                return self.stats
        await self._expire_stale()
        if self._metrics is not None:
            from app.acquisition.models_db import AcquisitionRun

            self._metrics.set_gauge("acquisition_running", float(len(self._in_flight)))
            try:
                depth = int(
                    await self._session.scalar(
                        __import__("sqlalchemy")
                        .select(__import__("sqlalchemy").func.count())
                        .select_from(AcquisitionRun)
                    )
                    or 0
                )
                self._metrics.set_gauge("acquisition_queue_depth", float(depth))
            except Exception:  # noqa: BLE001
                pass
        batch = await self._next_batch()
        for run in batch:
            if self._shutdown.is_set() and not self._in_flight:
                break
            await self._claim_and_run(run)
        if not self._draining:
            for run in await self._next_recoverable():
                if self._shutdown.is_set() and not self._in_flight:
                    break
                await self._recover_and_run(run)
        return self.stats

    # -- internals -----------------------------------------------------------

    async def _expire_stale(self) -> None:
        """Expire leases whose TTL elapsed (crash recovery enabler)."""
        from app.worker.lease import WorkerLeaseManager

        await WorkerLeaseManager(self._session).expire()

    def _available_slots(self) -> int | None:
        """Remaining execution capacity for this worker (registry-aware).

        When a registry is attached the loop must not claim more runs than
        the worker's registered max_concurrency minus active executions --
        ownership must stay close to actual execution slots (Phase 28.3 #11).
        Returns None when no registry is attached (no limit). The registry
        cache is refreshed by the loop's own heartbeat in run_forever().
        """
        if self._registry is None:
            return None
        record = self._registry._cache.get(self._worker_id)  # type: ignore[attr-defined]
        if record is None:
            return None
        return max(0, record.max_concurrency - record.active_executions)

    async def _next_batch(self) -> list[AcquisitionRun]:
        """Read the next claimable batch directly from the DB queue."""
        slots = self._available_slots()
        limit = self._batch_size if slots is None else min(self._batch_size, slots)
        if limit <= 0:
            return []
        rows = (
            await self._session.scalars(
                select(AcquisitionRun.id)
                .where(AcquisitionRun.status.in_(("QUEUED", "CANCEL_REQUESTED")))
                .order_by(AcquisitionRun.created_at.asc())
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        ).all()
        return list(rows)

    async def _next_recoverable(self) -> list[AcquisitionRun]:
        """RUNNING/PARTIAL runs whose worker lease is no longer ACTIVE.

        Detection is read-only; the actual ownership handover happens through
        ``coordinator.reclaim_expired`` (atomic CAS), so a run observed here
        may legitimately fail to reclaim (already recovered / still active) --
        that is safe and expected.
        """
        slots = self._available_slots()
        limit = self._batch_size if slots is None else min(self._batch_size, slots)
        if limit <= 0:
            return []
        from app.worker.lease import WorkerLeaseManager

        rows = (
            await self._session.execute(
                select(AcquisitionRun.id, AcquisitionRun.lease_id)
                .where(AcquisitionRun.status.in_(("RUNNING", "PARTIAL")))
                .order_by(AcquisitionRun.claimed_at.asc().nulls_first())
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        ).all()
        leases = WorkerLeaseManager(self._session)
        recoverable: list[UUID] = []
        for run_id, lease_id in rows:
            if lease_id is None:
                # no lease ever bound -> provably abandoned (anomaly)
                recoverable.append(run_id)
                continue
            try:
                lease = await leases.require(lease_id)
            except Exception:  # noqa: BLE001 -- missing lease row = abandoned
                recoverable.append(run_id)
                continue
            # ONLY an EXPIRED lease means the previous owner crashed: a
            # RELEASED lease means it finished normally (the run must not be
            # auto-reclaimed), and ACTIVE means it is still executing.
            if lease.status == LeaseStatus.EXPIRED.value:
                recoverable.append(run_id)
        return recoverable

    async def _recover_and_run(self, run_id: UUID) -> None:
        """Atomically reclaim an expired RUNNING run and execute it here."""
        token = uuid4()
        try:
            claimed = await self._coordinator.reclaim_expired(run_id, self._worker_id, token=token)
        except (AcquisitionClaimConflict, AcquisitionNotFound):
            await self._session.rollback()
            self.stats.skipped_terminal += 1
            return
        if claimed is None:
            # still actively owned (lease became ACTIVE again) -- skip
            return
        self.stats.reclaimed += 1
        if self._metrics is not None:
            self._metrics.inc("acquisition_reclaim_total")
        self._in_flight.add(run_id)
        reached_terminal = False
        try:
            # the runner may be sync (test double) or async (production):
            # tolerate both at the boundary
            result = self._runner(run_id, token)
            if __import__("inspect").isawaitable(result):
                result = await result
            status = getattr(result, "status", "RUNNING")
            reached_terminal = status in TERMINAL
            if status == "CANCELLED":
                self.stats.cancelled += 1
                if self._metrics is not None:
                    self._metrics.inc("acquisition_cancel_total")
            elif status in ("COMPLETE", "BLOCKED", "PARTIAL", "FAILED"):
                self.stats.completed += 1
                if self._metrics is not None:
                    self._metrics.inc("acquisition_complete_total")
        except Exception as error:  # noqa: BLE001 -- record and continue
            self.stats.errors.append(f"{run_id}: {error}")
            if self._metrics is not None:
                self._metrics.inc("acquisition_failed_total")
            logger.warning("run %s failed: %s", run_id, error)
            await self._session.rollback()
        finally:
            self._in_flight.discard(run_id)
            # Phase 28.4 audit (GATE 11): a run that did NOT reach a terminal
            # state must keep its lease -- releasing it (RELEASED) makes the
            # run permanently unrecoverable (recovery only reclaims EXPIRED
            # leases). Failed executions stay recoverable instead.
            if reached_terminal:
                await self._release_after(run_id, token)

    async def _claim_and_run(self, run_id: UUID) -> None:
        # fresh load: never touch an ORM object that may have been expired by
        # an intermediate commit/rollback (async lazy load is unsupported)
        run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            return
        # CANCEL_REQUESTED that was never claimed -> cancel directly (no work)
        if run.status == "CANCEL_REQUESTED" and run.claim_token_hash is None:
            run.status = "CANCELLED"
            run.cancelled_at = datetime.now(UTC)
            await self._session.commit()
            self.stats.cancelled += 1
            if self._metrics is not None:
                self._metrics.inc("acquisition_cancel_total")
            return
        token = uuid4()
        try:
            await self._coordinator.claim(run_id, self._worker_id, token=token)
        except Exception:  # noqa: BLE001 -- claim lost or not claimable
            await self._session.rollback()
            self.stats.skipped_terminal += 1
            return
        self.stats.claimed += 1
        if self._metrics is not None:
            self._metrics.inc("acquisition_claim_total")
        self._in_flight.add(run_id)
        reached_terminal = False
        try:
            # the runner may be sync (test double) or async (production):
            # tolerate both at the boundary
            result = self._runner(run_id, token)
            if __import__("inspect").isawaitable(result):
                result = await result
            status = getattr(result, "status", "RUNNING")
            reached_terminal = status in TERMINAL
            if status == "CANCELLED":
                self.stats.cancelled += 1
                if self._metrics is not None:
                    self._metrics.inc("acquisition_cancel_total")
            elif status in ("COMPLETE", "BLOCKED", "PARTIAL", "FAILED"):
                self.stats.completed += 1
                if self._metrics is not None:
                    self._metrics.inc("acquisition_complete_total")
        except Exception as error:  # noqa: BLE001 -- record and continue
            self.stats.errors.append(f"{run_id}: {error}")
            if self._metrics is not None:
                self._metrics.inc("acquisition_failed_total")
            logger.warning("run %s failed: %s", run_id, error)
            await self._session.rollback()
        finally:
            self._in_flight.discard(run_id)
            # Phase 28.4 audit (GATE 11): only terminal runs release their
            # lease; failed executions stay recoverable (see recover path)
            if reached_terminal:
                await self._release_after(run_id, token)

    async def _release_after(self, run_id: UUID, token: UUID) -> None:
        """Best-effort owner-only lease release after execution."""
        try:
            await self._coordinator.release_claim(run_id, self._worker_id, token)
        except Exception:  # noqa: BLE001 -- best-effort
            await self._session.rollback()

    async def heartbeat(self) -> None:
        """Keep the worker online while the loop lives (bounded by registry)."""
        if self._registry is None:
            return
        await self._registry.heartbeat(
            WorkerHeartbeat(
                worker_id=self._worker_id,
                status=WorkerStatus.DRAINING if self._draining else WorkerStatus.ONLINE,
                active_executions=len(self._in_flight),
            )
        )
