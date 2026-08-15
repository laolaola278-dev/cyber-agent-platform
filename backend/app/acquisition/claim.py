"""Phase 28.2 -- durable run claim + run-level fencing coordination.

The DB row is the single source of truth for WHO owns a QUEUED AcquisitionRun:

    QUEUED --(atomic CAS)--> RUNNING (claimed_by worker, fencing token hash)

Only the CURRENT fencing owner may commit a result. A worker whose lease
expired -- and whose run was reclaimed by another worker -- is a STALE
writer and its commit MUST be rejected (Critical Gate).

Design rules (spec 28.2):
  * reuses the existing WorkerLeaseManager / fencing-token / version-CAS
    semantics; does NOT create a second lease system;
  * raw fencing tokens are NEVER persisted -- only their sha256;
  * the atomic claim is a single UPDATE ... WHERE status='QUEUED' statement
    (CAS) whose rowcount decides the winner under concurrency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.exceptions import (
    AcquisitionClaimConflict,
    AcquisitionNotFound,
    AcquisitionStaleCommit,
)
from app.acquisition.models_db import AcquisitionRun
from app.exceptions import WorkerLeaseConflict
from app.models.worker import WorkerLease as WorkerLeaseModel
from app.worker.contracts import LeaseStatus, WorkerLease
from app.worker.lease import WorkerLeaseManager

_CLAIMABLE = ("QUEUED", "CANCEL_REQUESTED")
_RUNNABLE = ("QUEUED", "RUNNING", "PARTIAL", "CANCEL_REQUESTED")


def fencing_hash(token: UUID) -> str:
    """Hash of the fencing token -- NEVER store the plaintext token."""
    return sha256(str(token).encode()).hexdigest()


class AcquisitionClaimCoordinator:
    """Atomic DB claim and fencing-validated commit for acquisition runs."""

    def __init__(
        self,
        session: AsyncSession,
        leases: WorkerLeaseManager,
        *,
        lease_ttl_seconds: int = 120,
        metrics: Any | None = None,
    ) -> None:
        self._session = session
        self._leases = leases
        self._metrics = metrics
        self._lease_ttl_seconds = lease_ttl_seconds

    @property
    def lease_ttl_seconds(self) -> int:
        """Expose the lease TTL used for claim/reclaim/renewal."""
        return self._lease_ttl_seconds

    # -- claim --------------------------------------------------------------

    async def claim(
        self,
        run_id: UUID,
        worker_id: UUID,
        *,
        token: UUID | None = None,
    ) -> tuple[AcquisitionRun, WorkerLease]:
        """Atomically claim a QUEUED run and bind a worker lease.

        Returns (run, lease) on success. Raises AcquisitionClaimConflict when
        the run is already claimed / not claimable / not found.
        """
        run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        if run.status not in _CLAIMABLE:
            raise AcquisitionClaimConflict(f"run {run_id} is not claimable (status={run.status})")
        fencing = token or uuid4()
        observed = datetime.now(UTC)
        statement = (
            update(AcquisitionRun)
            .where(
                AcquisitionRun.id == run_id,
                AcquisitionRun.status.in_(list(_CLAIMABLE)),
            )
            .values(
                status="RUNNING",
                worker_id=worker_id,
                claim_token_hash=fencing_hash(fencing),
                claim_attempts=AcquisitionRun.claim_attempts + 1,
                claimed_at=observed,
                cancel_requested_at=(
                    AcquisitionRun.cancel_requested_at if run.status == "CANCEL_REQUESTED" else None
                ),
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:
            await self._session.rollback()
            raise AcquisitionClaimConflict(
                f"run {run_id} claim lost: another worker claimed it first"
            )
        await self._session.flush()
        lease = await self._leases.acquire(
            worker_id=worker_id,
            execution_id=uuid4(),
            owner=f"acquisition:{run_id}",
            ttl_seconds=self._lease_ttl_seconds,
        )
        run.lease_id = lease.id
        await self._session.commit()
        await self._session.refresh(run)
        return run, lease

    # -- fencing-validated commit ------------------------------------------

    async def _reject_stale(self, run_id: UUID, reason: str) -> None:
        """Fencing rejection path.

        Phase 28.4 audit (GATE 9): the stale branch must NEVER commit on the
        CALLER's session -- the caller (a stale worker) may hold pending
        evidence/artifact rows in that session, and a commit here would
        silently attach them, bypassing the fencing gate. We also must NOT
        roll back the caller's session from here: a rollback expires every
        loaded ORM object, and AsyncSession does not support the subsequent
        lazy load (MissingGreenlet). Discarding the caller's pending rows is
        the caller's responsibility -- every caller in the worker path rolls
        back in its stale-commit except branch. Here we only persist the
        rejection counter through an isolated one-shot session.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        if self._metrics is not None:
            self._metrics.inc("acquisition_stale_reject_total")
        temp = async_sessionmaker(self._session.bind, expire_on_commit=False)()
        try:
            row = (
                await temp.execute(
                    select(AcquisitionRun)
                    .where(AcquisitionRun.id == run_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is not None:
                row.stale_result_rejected += 1
                await temp.commit()
        except Exception:  # noqa: BLE001 -- counter is observational only
            await temp.rollback()
        finally:
            await temp.close()

    async def verify_owner(
        self,
        run_id: UUID,
        worker_id: UUID,
        token: UUID,
    ) -> AcquisitionRun:
        """Return the run ONLY if ``worker_id`` is still the fencing owner.

        Raises AcquisitionStaleCommit otherwise (Critical Gate). On rejection
        the caller's pending rows are rolled back -- a stale worker can never
        attach evidence/artifacts through the fencing gate.
        """
        run = (
            await self._session.execute(
                select(AcquisitionRun)
                .where(AcquisitionRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        if run.worker_id != worker_id or run.claim_token_hash != fencing_hash(token):
            await self._reject_stale(
                run_id,
                f"run {run_id}: stale worker {worker_id} attempted a commit after "
                "losing fencing ownership",
            )
            raise AcquisitionStaleCommit(
                f"run {run_id}: stale worker {worker_id} attempted a commit after "
                "losing fencing ownership"
            )
        lease = await self._leases.require(run.lease_id) if run.lease_id else None
        if lease is None or lease.status != LeaseStatus.ACTIVE.value:
            await self._reject_stale(
                run_id,
                f"run {run_id}: worker lease is no longer active (fencing expired)",
            )
            raise AcquisitionStaleCommit(
                f"run {run_id}: worker lease is no longer active (fencing expired)"
            )
        return run

    async def release_claim(self, run_id: UUID, worker_id: UUID, token: UUID) -> None:
        """Best-effort release of the run claim lease (owner-only)."""
        try:
            run = await self.verify_owner(run_id, worker_id, token)
            if run.lease_id is not None:
                lease = await self._leases.require(run.lease_id)
                try:
                    await self._leases.release(
                        lease.id,
                        owner=lease.owner,
                        fencing_token=lease.fencing_token,
                        expected_version=lease.version,
                    )
                except Exception:  # noqa: BLE001 -- best-effort release
                    pass
        except (AcquisitionStaleCommit, AcquisitionNotFound):
            pass

    # -- execution-time lease heartbeat --------------------------------------

    async def renew(
        self,
        run_id: UUID,
        worker_id: UUID,
        token: UUID,
        *,
        ttl_seconds: int | None = None,
    ) -> WorkerLease | None:
        """Renew the current owner's lease while its operation is executing.

        Phase 28.5-RC atomic ownership-aware semantics: the renewal is a SINGLE
        conditional UPDATE that verifies (a) the lease is still ACTIVE with the
        expected version + fencing token, AND (b) the referenced run still
        points at THIS lease AND is owned by ``worker_id`` with
        ``claim_token_hash`` (an EXISTS subquery). This closes the
        verify-owner-then-renew TOCTOU: a reclaim that swapped the run to a new
        worker/lease makes the EXISTS guard fail, so renew(A) can never report
        success while the run is owned by B.

        Returns the renewed lease (or None when the run has no lease yet).
        Raises AcquisitionStaleCommit when ownership was lost (reclaimed or
        lease expired), so the executing worker's later commit is also rejected.
        """
        run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        if run.lease_id is None:
            return None
        lease = await self._leases.require(run.lease_id)
        if lease.worker_id != worker_id:
            raise AcquisitionStaleCommit(
                f"run {run_id}: lease {lease.id} is owned by "
                f"{lease.worker_id}, not {worker_id} -- renewal rejected"
            )
        # ownership guard folded into the atomic lease UPDATE: the run must
        # STILL reference this exact lease and be owned by this worker.
        ownership_guard = exists(
            select(1).where(
                AcquisitionRun.id == run_id,
                AcquisitionRun.lease_id == WorkerLeaseModel.id,
                AcquisitionRun.worker_id == worker_id,
                AcquisitionRun.claim_token_hash == fencing_hash(token),
            )
        )
        try:
            return await self._leases.renew(
                lease.id,
                owner=lease.owner,
                fencing_token=lease.fencing_token,
                expected_version=lease.version,
                ttl_seconds=ttl_seconds or self._lease_ttl_seconds,
                extra_guard=ownership_guard,
            )
        except WorkerLeaseConflict as error:
            # the lease was expired/reclaimed concurrently -> ownership lost
            await self._session.rollback()
            raise AcquisitionStaleCommit(
                f"run {run_id}: lease {lease.id} renewal lost ownership (reclaimed or expired)"
            ) from error

    # -- crash recovery ------------------------------------------------------

    async def reclaim_expired(
        self,
        run_id: UUID,
        worker_id: UUID,
        *,
        token: UUID | None = None,
    ) -> tuple[AcquisitionRun, WorkerLease] | None:
        """Reclaim a RUNNING run whose worker lease has EXPIRED.

        Returns (run, lease) when the previous owner is provably gone
        (lease EXPIRED). Returns None when the run is still actively owned.
        Raises AcquisitionClaimConflict when the run is terminal or the
        lease is neither expired nor missing.
        """
        run = await self._session.get(AcquisitionRun, run_id)
        if run is None:
            raise AcquisitionNotFound(f"AcquisitionRun {run_id} not found")
        if run.status not in ("RUNNING", "PARTIAL"):
            return None
        previous_lease = await self._leases.require(run.lease_id) if run.lease_id else None
        if previous_lease is not None and previous_lease.status != LeaseStatus.EXPIRED.value:
            # ACTIVE   -> the previous owner still holds a live lease; the run
            #             is NOT reclaimable.
            # RELEASED -> the owner finished NORMALLY (e.g. committed PARTIAL);
            #             the run must NOT be auto-reclaimed -- explicit
            #             resume/requeue is the only re-entry path.
            return None
        fencing = token or uuid4()
        observed = datetime.now(UTC)
        # The CAS is conditional on the bound lease being EXPIRED (or missing)
        # at UPDATE time (atomic recheck). Without this subquery, two
        # recovering workers could both pass a plain `status IN (RUNNING,
        # PARTIAL)` UPDATE -- the second writer would overwrite the first and
        # double-increment recovery_count. The subquery makes the reclaim
        # atomic: exactly one concurrent recovery winner per expired run, and
        # a normally-finished (RELEASED) run is never reclaimed.
        statement = (
            update(AcquisitionRun)
            .where(
                AcquisitionRun.id == run_id,
                AcquisitionRun.status.in_(("RUNNING", "PARTIAL")),
                (
                    AcquisitionRun.lease_id.is_(None)
                    | ~AcquisitionRun.lease_id.in_(
                        select(WorkerLeaseModel.id).where(
                            WorkerLeaseModel.id == AcquisitionRun.lease_id,
                            WorkerLeaseModel.status.in_(
                                (
                                    LeaseStatus.ACTIVE.value,
                                    LeaseStatus.RELEASED.value,
                                )
                            ),
                        )
                    )
                ),
            )
            .values(
                status="RUNNING",
                worker_id=worker_id,
                claim_token_hash=fencing_hash(fencing),
                claim_attempts=AcquisitionRun.claim_attempts + 1,
                recovery_count=AcquisitionRun.recovery_count + 1,
                claimed_at=observed,
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:
            await self._session.rollback()
            raise AcquisitionClaimConflict(f"run {run_id} reclaim lost to a concurrent worker")
        await self._session.flush()
        lease = await self._leases.acquire(
            worker_id=worker_id,
            execution_id=uuid4(),
            owner=f"acquisition:{run_id}",
            ttl_seconds=self._lease_ttl_seconds,
        )
        run.lease_id = lease.id
        await self._session.commit()
        await self._session.refresh(run)
        return run, lease

    # -- queue helpers --------------------------------------------------------

    @staticmethod
    async def pending_count(session: AsyncSession) -> int:
        """Number of runs waiting in the durable queue (for backpressure)."""
        from sqlalchemy import func, select

        total = await session.scalar(
            select(func.count())
            .select_from(AcquisitionRun)
            .where(AcquisitionRun.status.in_(("QUEUED", "RUNNING", "CANCEL_REQUESTED")))
        )
        return int(total or 0)
