"""Phase 28.7 -- Postgres/ObjectStore consistency reconciliation.

PostgreSQL and the S3/MinIO object store have NO cross-system distributed
transaction, so a backup/restore (or any crash window between the blob put
and the durable row commit) can legitimately produce four states:

- ``referenced_and_present`` : DB row references the digest AND the object
  exists with matching content   -> healthy
- ``missing_referenced``     : DB row references the digest but the object
  is absent                      -> CRITICAL integrity failure
- ``orphan``                 : object exists but no durable row references
  it                             -> GC candidate (grace policy applies)
- ``digest_mismatch``        : object exists but its bytes no longer hash
  to its content address         -> CRITICAL integrity failure

The first category may NEVER be silently treated as healthy when non-empty;
the reconciler is the machine-readable arbiter used by the Phase 28.7 DR
gates and by operators after any restore.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.acquisition.gc import EvidenceReferenceReader, ReferenceReader
from app.acquisition.store import EvidenceObjectStoreProvider, S3EvidenceStore, sha256_hex

logger = logging.getLogger("cap.acquisition.reconciliation")

STATUS_REFERENCED_PRESENT = "referenced_and_present"
STATUS_MISSING_REFERENCED = "missing_referenced"
STATUS_ORPHAN = "orphan"
STATUS_DIGEST_MISMATCH = "digest_mismatch"


@dataclass
class ReconciliationReport:
    """Machine-readable outcome of one reconciliation sweep."""

    scanned_objects: int = 0
    referenced_digests: int = 0
    referenced_and_present: list[str] = field(default_factory=list)
    missing_referenced: list[str] = field(default_factory=list)
    orphan: list[str] = field(default_factory=list)
    digest_mismatch: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def integrity_ok(self) -> bool:
        """True only when NOTHING referenced is missing or corrupted.

        Orphans do NOT violate integrity (they are a GC concern); missing or
        corrupted referenced objects always do.
        """
        return not self.missing_referenced and not self.digest_mismatch and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_objects": self.scanned_objects,
            "referenced_digests": self.referenced_digests,
            "referenced_and_present_count": len(self.referenced_and_present),
            "missing_referenced": sorted(self.missing_referenced),
            "missing_referenced_count": len(self.missing_referenced),
            "orphan": sorted(self.orphan),
            "orphan_count": len(self.orphan),
            "digest_mismatch": sorted(self.digest_mismatch),
            "digest_mismatch_count": len(self.digest_mismatch),
            "errors": self.errors[:20],
            "integrity_ok": self.integrity_ok,
        }


class EvidenceReconciler:
    """Compare durable DB references against the object store, with digest
    verification of every referenced object."""

    def __init__(
        self,
        store: EvidenceObjectStoreProvider,
        session_factory: Any,
        *,
        reference_reader: ReferenceReader | None = None,
        # full digest verification of every referenced object (the DR gates
        # demand exhaustive verification; production sweeps may bound this)
        max_verify: int = 10_000,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._reader = reference_reader or EvidenceReferenceReader()
        self._max_verify = max(1, int(max_verify))

    async def run(self) -> ReconciliationReport:
        report = ReconciliationReport()

        try:
            keys = await self._store.list_keys()
        except Exception as error:  # noqa: BLE001 -- storage unavailable
            report.errors.append(f"list_keys failed: {error}")
            return report
        report.scanned_objects = len(keys)
        present: dict[str, str] = {}
        for key in keys:
            if isinstance(self._store, S3EvidenceStore):
                digest = S3EvidenceStore.digest_from_key(key)
            else:
                digest = key.rsplit("/", 1)[-1]
            present[digest] = key

        async with self._session_factory() as session:
            try:
                referenced = await self._reader.referenced_digests(session)
            except Exception as error:  # noqa: BLE001 -- DB unavailable
                report.errors.append(f"reference scan failed: {error}")
                return report
        report.referenced_digests = len(referenced)

        for digest in sorted(referenced):
            key = present.get(digest)
            if key is None:
                report.missing_referenced.append(digest)
                continue
            report.referenced_and_present.append(digest)

        for digest in sorted(present):
            if digest not in referenced:
                report.orphan.append(digest)

        # digest verification (bounded): a content-addressed object whose
        # bytes no longer hash to its key is a silent-corruption hazard
        verified = 0
        for digest in report.referenced_and_present:
            if verified >= self._max_verify:
                break
            key = present[digest]
            try:
                data = await self._store.get(key)
            except Exception as error:  # noqa: BLE001
                # get() itself verifies digests and raises on mismatch --
                # surface both flavours uniformly
                report.digest_mismatch.append(digest)
                report.errors.append(f"verify {key} failed: {error}")
                continue
            verified += 1
            if sha256_hex(data) != digest:
                report.digest_mismatch.append(digest)

        # anything we did not get to verify is reported, not hidden
        unverified = len(report.referenced_and_present) - verified
        if unverified > 0:
            report.errors.append(f"digest verification skipped for {unverified} objects")

        logger.info(
            "reconciliation scanned=%d referenced=%d ok=%d missing=%d "
            "orphan=%d mismatch=%d verified=%d",
            report.scanned_objects,
            report.referenced_digests,
            len(report.referenced_and_present),
            len(report.missing_referenced),
            len(report.orphan),
            len(report.digest_mismatch),
            verified,
        )
        return report
