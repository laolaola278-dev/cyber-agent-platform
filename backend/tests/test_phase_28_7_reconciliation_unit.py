"""Phase 28.7 -- offline unit tests for the reconciliation engine.

Runs against a LocalFilesystemEvidenceStore + in-memory SQLite so the four
reference states (referenced_and_present / missing_referenced / orphan /
digest_mismatch) are covered without a cluster.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.acquisition.reconciliation import (
    EvidenceReconciler,
)
from app.acquisition.store import LocalFilesystemEvidenceStore, sha256_hex


class _FakeReader:
    """Referenced digests injected by the test."""

    def __init__(self, digests: set[str]) -> None:
        self.digests = digests

    async def referenced_digests(self, session) -> set[str]:  # noqa: ANN001
        return self.digests


def _session_factory():
    """Minimal async context manager standing in for a session factory."""

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    return _Session


@pytest.mark.asyncio
async def test_all_states_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFilesystemEvidenceStore(Path(tmp))
        corrupted = b"corrupted payload"
        # referenced-and-present: stored via put(), digest == content address
        digest_ok = (await store.put(b"referenced ok", metadata={})).key
        digest_corrupt = sha256_hex(corrupted)
        # write bytes that do NOT match their content address: put() derives
        # the digest from the bytes, so simulate bit-rot by writing directly
        # into the shard layout (<root>/objects/<d0:2>/<d2:>) under a stale
        # content address.
        rot_path = Path(tmp) / "objects" / digest_corrupt[:2] / digest_corrupt[2:]
        rot_path.parent.mkdir(parents=True, exist_ok=True)
        rot_path.write_bytes(corrupted + b"bit-rot")

        missing = "f" * 64  # referenced but never stored

        reconciler = EvidenceReconciler(
            store,
            _session_factory(),
            reference_reader=_FakeReader({digest_ok, digest_corrupt, missing}),
        )
        report = await reconciler.run()

        assert not report.integrity_ok
        assert missing in report.missing_referenced
        assert digest_corrupt in report.digest_mismatch
        assert digest_ok in report.referenced_and_present
        assert report.missing_referenced  # STATUS_MISSING_REFERENCED category non-empty
        assert report.digest_mismatch    # STATUS_DIGEST_MISMATCH category non-empty
        # nothing unreferenced was stored -> no orphans
        assert report.orphan == []


@pytest.mark.asyncio
async def test_orphan_and_healthy_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFilesystemEvidenceStore(Path(tmp))
        obj = await store.put(b"referenced", metadata={})
        await store.put(b"orphan bytes", metadata={})
        digest = sha256_hex(b"referenced")
        _ = obj

        reconciler = EvidenceReconciler(
            store,
            _session_factory(),
            reference_reader=_FakeReader({digest}),
        )
        report = await reconciler.run()

        assert report.integrity_ok
        assert len(report.referenced_and_present) == 1
        assert len(report.orphan) == 1
        payload = report.to_dict()
        assert payload["integrity_ok"] is True
        assert payload["orphan_count"] == 1
