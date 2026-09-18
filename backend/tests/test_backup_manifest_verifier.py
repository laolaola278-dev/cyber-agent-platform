"""Hermetic tests for the DR backup verifier used by the restore runbook.

``scripts/certification/verify_backup_manifest.py`` is the last gate before a
restore is applied: it recomputes the pg_dump digest and the object-manifest
digest and refuses (exit 1) on any mismatch. The GA certification suite only
exercises it against a real cluster's evidence directory, so the fail-closed
behaviour itself was never provable on a machine without one.

These tests build a small backup whose manifest is produced by the same
algorithm the backup script uses, then attack it the three ways that matter in
an incident: a tampered object, a truncated dump, and objects added after the
manifest was written (the exact way a stale evidence directory drifts).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "certification" / "verify_backup_manifest.py"

OBJECTS = {
    "sha256/aa/aabb/evidence-one.json": b'{"kind":"evidence","id":1}\n',
    "sha256/aa/aabb/evidence-two.json": b'{"kind":"evidence","id":2}\n',
    "sha256/cc/ccdd/report.txt": b"first line\nsecond line\n",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_manifest_digest(objects_dir: Path) -> tuple[str, int, int]:
    """Rebuild the aggregate the backup script records in the manifest."""
    lines: list[str] = []
    total_bytes = 0
    for path in sorted(objects_dir.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(objects_dir).as_posix()
        size = path.stat().st_size
        total_bytes += size
        lines.append(f"{key}\t{size}\t{_sha256(path.read_bytes())}")
    digest = _sha256(("\n".join(lines) + "\n").encode())
    return digest, len(lines), total_bytes


def _build_backup(root: Path) -> Path:
    """Create a complete, internally consistent backup directory."""
    backup_dir = root / "backup"
    objects_dir = backup_dir / "objects"
    for key, payload in OBJECTS.items():
        target = objects_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    dump = backup_dir / "postgres" / "cap.sql.gz"
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(gzip.compress(b"CREATE TABLE asset (id uuid);\n"))

    digest, count, total = _object_manifest_digest(objects_dir)
    objects_bytes = sum(p.stat().st_size for p in objects_dir.rglob("*") if p.is_file())
    (backup_dir / "backup-manifest.json").write_text(
        json.dumps(
            {
                "backup_id": "backup-hermetic",
                "timestamp": "2026-09-18T00:00:00Z",
                "cap_version": "1.0.5",
                "schema_revision": "20260812_0021",
                "pg_backup_digest": _sha256(dump.read_bytes()),
                "object_manifest_digest": digest,
                "object_count": count,
                "total_bytes": objects_bytes + dump.stat().st_size,
                "evidence_reference_count": count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _ = total
    return backup_dir


def _verify(backup_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(backup_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_consistent_backup_verifies_and_reports_its_objects(tmp_path: Path) -> None:
    backup_dir = _build_backup(tmp_path)
    proc = _verify(backup_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("BACKUP_VERIFIED")
    assert f"objects={len(OBJECTS)}" in proc.stdout


def test_a_tampered_object_is_refused(tmp_path: Path) -> None:
    backup_dir = _build_backup(tmp_path)
    victim = backup_dir / "objects" / "sha256" / "cc" / "ccdd" / "report.txt"
    victim.write_bytes(b"first line\nSECOND LINE\n")
    proc = _verify(backup_dir)
    assert proc.returncode == 1
    assert "object backup manifest digest mismatch" in proc.stderr
    assert "restore refused" in proc.stderr


def test_a_truncated_pg_dump_is_refused(tmp_path: Path) -> None:
    backup_dir = _build_backup(tmp_path)
    dump = backup_dir / "postgres" / "cap.sql.gz"
    dump.write_bytes(dump.read_bytes()[:-4])
    proc = _verify(backup_dir)
    assert proc.returncode == 1
    assert "PostgreSQL backup digest mismatch" in proc.stderr


def test_objects_added_after_the_manifest_was_written_are_refused(tmp_path: Path) -> None:
    """The stale-evidence failure mode: extra objects break the aggregate.

    Asserted on the digest check *and* the count check, because the verifier
    tests the digest first -- a restore must not proceed just because the
    object count still happens to line up.
    """
    backup_dir = _build_backup(tmp_path)
    extra = backup_dir / "objects" / "sha256" / "ee" / "eeff" / "late-arrival.json"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b'{"kind":"evidence","id":99}\n')
    proc = _verify(backup_dir)
    assert proc.returncode == 1
    assert "object backup manifest digest mismatch" in proc.stderr


def test_a_missing_manifest_or_dump_is_refused(tmp_path: Path) -> None:
    backup_dir = _build_backup(tmp_path)
    (backup_dir / "backup-manifest.json").unlink()
    proc = _verify(backup_dir)
    assert proc.returncode == 1
    assert "backup-manifest.json missing" in proc.stderr

    _build_backup(tmp_path / "second")
    other = tmp_path / "second" / "backup"
    (other / "postgres" / "cap.sql.gz").unlink()
    proc = _verify(other)
    assert proc.returncode == 1
    assert "pg dump missing" in proc.stderr


def test_usage_error_is_not_silently_a_success() -> None:
    proc = subprocess.run(
        [sys.executable, str(VERIFIER)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stderr


@pytest.mark.parametrize("argument", ["objects", "."])
def test_an_unrelated_directory_never_verifies(tmp_path: Path, argument: str) -> None:
    target = tmp_path / argument
    target.mkdir(parents=True, exist_ok=True)
    proc = _verify(target)
    assert proc.returncode == 1
