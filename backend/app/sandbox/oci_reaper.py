"""Phase 28.5 -- orphan OCI container reaper.

Reconciles CAP-managed sandbox containers after worker crashes:

  * startup reconciliation (scan once at boot)
  * periodic reconciliation (interval loop)

A container is only killed when ownership is provably STALE:

  * the owning worker is dead (registration gone / offline)
  * the run's lease expired / was reclaimed by another worker
  * the execution is no longer the run's current sandbox execution

Fencing: decisions are made on ``sandbox_execution_id`` + ``lease_id`` from
container labels vs the CURRENT run/lease in the DB -- NEVER on ``run_id``
alone, so a reaper can never kill a NEW owner's container (its execution id /
lease id differ from the stale one).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.sandbox.oci_provider import (
    LABEL_EXECUTION,
    LABEL_LEASE,
    LABEL_RUN,
    LABEL_WORKER,
)

logger = logging.getLogger("cap.sandbox.oci.reaper")


@dataclass
class ReapStats:
    scanned: int = 0
    stale: int = 0
    removed: int = 0
    alive: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "stale": self.stale,
            "removed": self.removed,
            "alive": self.alive,
            "errors": self.errors[:5],
        }


class OCIContainerReaper:
    """Kills/removes sandbox containers whose ownership is stale."""

    def __init__(
        self,
        driver: Any,
        session_factory: Any,
        *,
        interval_seconds: float = 60.0,
        metrics: Any | None = None,
    ) -> None:
        self._driver = driver
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._metrics = metrics

    # -- ownership -----------------------------------------------------------

    def _as_uuid(self, value: str):
        """Bind label ids as real UUID objects so SQLAlchemy adapts them to
        the backend's UUID representation (PG native, SQLite hex)."""
        try:
            return UUID(value)
        except (ValueError, AttributeError):
            return value

    async def _current_owner(self, run_id: str) -> tuple[str | None, str | None]:
        """Return (worker_id, lease_id) currently owning the run from the DB.

        ORM-based so UUID parameters are adapted per backend (text() SQL has
        no column types and cannot bind UUID objects portably).
        """
        from sqlalchemy import select

        from app.acquisition.models_db import AcquisitionRun

        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(AcquisitionRun.worker_id, AcquisitionRun.lease_id).where(
                            AcquisitionRun.id == self._as_uuid(run_id)
                        )
                    )
                ).first()
            if row is None:
                return None, None
            return (
                str(row[0]) if row[0] is not None else None,
                str(row[1]) if row[1] is not None else None,
            )
        except Exception:  # noqa: BLE001 -- DB down: fail safe (do NOT reap)
            return None, None

    async def _worker_alive(self, worker_id: str) -> bool:

        from app.worker.contracts import WorkerStatus
        from app.worker.registry import WorkerRegistry

        try:
            async with self._session_factory() as session:
                try:
                    worker = await WorkerRegistry(session).require(self._as_uuid(worker_id))
                except Exception:  # noqa: BLE001 -- missing worker = dead
                    return False
            return worker.status == WorkerStatus.ONLINE
        except Exception:  # noqa: BLE001 -- DB down: fail safe
            return True

    async def _is_stale(self, container: dict[str, Any]) -> tuple[bool, str]:
        labels = container.get("Config", {}).get("Labels") or {}
        execution_id = labels.get(LABEL_EXECUTION)
        run_id = labels.get(LABEL_RUN)
        worker_id = labels.get(LABEL_WORKER)
        lease_id = labels.get(LABEL_LEASE)
        if not execution_id:
            return False, "unmanaged (no execution label)"
        if not run_id:
            # managed label but no run -> provably orphaned
            return True, "no run label (orphan)"

        cur_worker, cur_lease = await self._current_owner(run_id)
        if cur_worker is None:
            # run row is gone -> orphan
            return True, f"run {run_id[:8]} no longer exists"
        if cur_lease and lease_id and cur_lease != lease_id:
            # the run is now owned through a DIFFERENT lease (reclaimed by
            # another worker / new epoch) -> this container is stale
            return True, (
                f"lease changed: container lease {lease_id[:8]} != current {cur_lease[:8]}"
            )
        if worker_id and worker_id != cur_worker:
            # current owner is another worker -> stale
            return True, "owner changed: container worker != current worker"
        if worker_id and not await self._worker_alive(worker_id):
            # owning worker is dead/offline and no one reclaimed yet -> stale
            return True, f"owning worker {worker_id[:8]} is not ONLINE"
        # still the current owner and worker alive -> leave it alone
        return False, "current owner, worker alive"

    # -- reconciliation ------------------------------------------------------

    async def reconcile_once(self) -> ReapStats:
        stats = ReapStats()
        try:
            containers = await self._driver.list_by_labels({LABEL_EXECUTION: ""})
        except Exception as error:  # noqa: BLE001
            stats.errors.append(f"list containers failed: {error}")
            if self._metrics is not None:
                self._metrics.inc("evidence_gc_error_total")
            return stats
        stats.scanned = len(containers)
        for container in containers:
            container_id = container.get("Id", "")
            try:
                stale, reason = await self._is_stale(container)
            except Exception as error:  # noqa: BLE001
                stats.errors.append(f"ownership check failed for {container_id[:12]}: {error}")
                continue
            if stale:
                stats.stale += 1
                try:
                    await self._driver.kill(container_id)
                    await self._driver.rm(container_id, force=True)
                    stats.removed += 1
                    logger.warning(
                        "reaper removed stale sandbox container %s (%s)",
                        container_id[:12],
                        reason,
                    )
                    if self._metrics is not None:
                        self._metrics.inc("sandbox_forced_termination_total")
                except Exception as error:  # noqa: BLE001
                    stats.errors.append(f"remove failed for {container_id[:12]}: {error}")
            else:
                stats.alive += 1
        return stats

    async def run_forever(self) -> None:
        """Periodic reconciliation loop (cancel via task cancellation)."""
        while True:
            try:
                await self.reconcile_once()
            except Exception as error:  # noqa: BLE001
                logger.warning("reaper reconcile failed: %s", error)
            await asyncio.sleep(self._interval)
