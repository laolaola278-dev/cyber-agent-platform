#!/usr/bin/env python3
"""Phase 28.7 -- generate the machine-readable backup manifest.

The manifest is tamper-evident: ``object_manifest_digest`` is the SHA-256 of
the sorted ``key<TAB>size<TAB>sha256`` lines of every backed-up object, and
``pg_backup_digest`` covers the compressed dump. The restore side recomputes
both and refuses (fail-closed) on any mismatch. No secrets are recorded.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_backup_manifest.py <BACKUP_DIR>", file=sys.stderr)
        return 2
    backup_dir = Path(sys.argv[1])
    pg_dump = backup_dir / "postgres" / "cap.sql.gz"
    objects_dir = backup_dir / "objects"
    if not pg_dump.is_file():
        print(f"FATAL: missing {pg_dump}", file=sys.stderr)
        return 1

    # object manifest: key<TAB>size<TAB>sha256, sorted by key
    lines: list[str] = []
    total_bytes = 0
    for path in sorted(objects_dir.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(objects_dir).as_posix()
        size = path.stat().st_size
        digest = _sha256_file(path)
        total_bytes += size
        lines.append(f"{key}\t{size}\t{digest}")
    if not lines:
        print("FATAL: object backup contains 0 objects", file=sys.stderr)
        return 1
    object_manifest = "\n".join(lines) + "\n"
    object_manifest_digest = hashlib.sha256(object_manifest.encode()).hexdigest()
    (backup_dir / "object-manifest.txt").write_text(object_manifest, encoding="utf-8")

    # sanity: a gzipped dump must decompress and contain the core table
    try:
        with gzip.open(pg_dump, "rt", encoding="utf-8", errors="replace") as handle:
            head = handle.read(1 << 20)
    except OSError as error:
        print(f"FATAL: pg dump is not readable gzip: {error}", file=sys.stderr)
        return 1
    if "acquisition_runs" not in head and "acquisition_runs" not in object_manifest:
        # only the first MB was read; scan the whole file for the core table
        with gzip.open(pg_dump, "rt", encoding="utf-8", errors="replace") as handle:
            found = any("acquisition_runs" in line for line in handle)
        if not found:
            print("FATAL: pg dump missing core tables", file=sys.stderr)
            return 1

    repo_root = Path(__file__).resolve().parents[2]
    version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()

    schema_revision = os.environ.get("CAP_SCHEMA_REVISION", "unknown")
    evidence_ref_count = int(os.environ.get("CAP_EVIDENCE_REF_COUNT", "0"))

    manifest = {
        "backup_id": f"backup-{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cap_version": version,
        "schema_revision": schema_revision,
        "pg_backup_digest": _sha256_file(pg_dump),
        "pg_backup_bytes": pg_dump.stat().st_size,
        "object_manifest_digest": object_manifest_digest,
        "object_count": len(lines),
        "total_bytes": total_bytes,
        "evidence_reference_count": evidence_ref_count,
    }
    out = backup_dir / "backup-manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
