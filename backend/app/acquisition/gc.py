"""Phase 28.4 -- Orphan blob garbage collection.

Content-addressed immutable objects written by stale / cancelled / crashed
attempts may never be attached to a durable evidence/artifact row. GC removes
those orphans WITHOUT ever deleting a blob that any durable row references.

Safety rules (Phase 28.4):
- only objects whose age exceeds ``grace_period`` are even considered
  (write -> DB attach has a legitimate transaction window);
- an object referenced by ANY durable evidence.content_hash or
  artifact.sha256 is never deleted (shared digests across runs are safe);
- GC is idempotent and restart-safe (no in-memory state; each sweep re-scans);
- deletions are observable (counters + structured log), never silent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.store import EvidenceObjectStoreProvider

logger = logging.getLogger("cap.acquisition.gc")


@dataclass
class GCRunStats:
    scanned: int = 0
    referenced: int = 0
    too_young: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "referenced": self.referenced,
            "too_young": self.too_young,
            "deleted": self.deleted,
            "errors": self.errors[:10],
        }


class ReferenceReader(Protocol):
    """Reads the set of digests durably referenced by the database."""

    async def referenced_digests(self, session: AsyncSession) -> set[str]: ...


class EvidenceReferenceReader:
    """Digests referenced by evidence.content_hash or artifact.sha256 rows."""

    async def referenced_digests(self, session: AsyncSession) -> set[str]:
        from sqlalchemy import select

        from app.acquisition.models_db import AcquisitionArtifactRecord
        from app.models import Evidence

        referenced: set[str] = set()
        rows = (
            await session.execute(
                select(Evidence.content_hash).where(Evidence.content_hash.is_not(None))
            )
        ).all()
        referenced.update(row[0] for row in rows if row[0])
        rows = (
            await session.execute(
                select(AcquisitionArtifactRecord.sha256).where(
                    AcquisitionArtifactRecord.sha256.is_not(None)
                )
            )
        ).all()
        referenced.update(row[0] for row in rows if row[0])
        return referenced


def _age_seconds(metadata: dict[str, Any], now: datetime) -> float | None:
    raw = metadata.get("stored_at")
    if raw is None:
        return None
    try:
        numeric = float(str(raw))
        stored = datetime.fromtimestamp(numeric, tz=UTC)
    except ValueError:
        try:
            stored = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
    return (now - stored).total_seconds()


class EvidenceOrphanGC:
    """Mark/sweep orphan collector over a content-addressed store."""

    def __init__(
        self,
        store: EvidenceObjectStoreProvider,
        session_factory: Any,
        *,
        grace_period_seconds: float = 3600.0,
        reference_reader: ReferenceReader | None = None,
        max_delete_per_run: int = 1000,
        metrics: Any | None = None,
    ) -> None:
        self._metrics = metrics
        self._store = store
        self._session_factory = session_factory
        self._grace = max(0.0, float(grace_period_seconds))
        self._reader = reference_reader or EvidenceReferenceReader()
        self._max_delete = max_delete_per_run

    async def run(self) -> GCRunStats:
        stats = GCRunStats()
        now = datetime.now(UTC)
        try:
            keys = await self._store.list_keys()
        except Exception as error:  # noqa: BLE001 -- storage unavailable
            stats.errors.append(f"list_keys failed: {error}")
            return stats
        stats.scanned = len(keys)
        if self._metrics is not None:
            self._metrics.set_gauge("evidence_orphan_candidates", float(len(keys)))
        if not keys:
            return stats

        async with self._session_factory() as session:
            try:
                referenced = await self._reader.referenced_digests(session)
            except Exception as error:  # noqa: BLE001
                stats.errors.append(f"reference scan failed: {error}")
                return stats
        stats.referenced = len(referenced)

        deleted = 0
        for key in keys:
            if deleted >= self._max_delete:
                break
            if key in referenced:
                continue
            try:
                meta = await self._store.metadata(key)
            except Exception:  # noqa: BLE001 -- vanished mid-scan
                continue
            age = _age_seconds(meta, now)
            if age is None:
                continue
            if age < self._grace:
                stats.too_young += 1
                continue
            # eligible: unreferenced and older than the grace period
            try:
                await self._store.delete(key)
                deleted += 1
                stats.deleted += 1
                if self._metrics is not None:
                    self._metrics.inc("evidence_orphan_deleted_total")
            except Exception as error:  # noqa: BLE001
                stats.errors.append(f"delete {key} failed: {error}")
                if self._metrics is not None:
                    self._metrics.inc("evidence_gc_error_total")
        if self._metrics is not None and stats.errors:
            self._metrics.inc("evidence_gc_error_total")
        logger.info(
            "orphan gc sweep scanned=%d referenced=%d too_young=%d deleted=%d",
            stats.scanned,
            stats.referenced,
            stats.too_young,
            stats.deleted,
        )
        return stats
