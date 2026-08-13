"""Phase 28.1 -- acquisition domain exceptions.

Kept local to the acquisition package so the shared platform exception
namespace is untouched.
"""

from __future__ import annotations


class AcquisitionError(Exception):
    """Base acquisition domain error."""


class AcquisitionNotFound(AcquisitionError):
    """An AcquisitionRun / resource does not exist."""


class AcquisitionConflict(AcquisitionError):
    """State/request conflict (e.g. idempotency key reused differently)."""


class AcquisitionClaimConflict(AcquisitionError):
    """Atomic claim failed: another worker owns / already claimed the run."""


class AcquisitionStaleCommit(AcquisitionError):
    """A worker tried to commit a result after losing its fencing ownership.

    Critical Gate: only the CURRENT fencing owner may commit. A stale worker
    (whose lease expired and whose run was reclaimed) MUST be rejected.
    """
