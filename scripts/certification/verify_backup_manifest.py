#!/usr/bin/env python3
"""Phase 28.7 -- verify a backup against its manifest (fail-closed).

Recomputes the pg dump digest and the object manifest digest and compares
them with backup-manifest.json. Any mismatch -> exit 1 BEFORE any restore
step runs, so a corrupted backup can never produce a partial restore that
reports healthy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_backup_manifest.py <BACKUP_DIR>", file=sys.stderr)
        return 2
    backup_dir = Path(sys.argv[1])
    manifest_path = backup_dir / "backup-manifest.json"
    if not manifest_path.is_file():
        print("FATAL: backup-manifest.json missing", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    pg_dump = backup_dir / "postgres" / "cap.sql.gz"
    if not pg_dump.is_file():
        print("FATAL: pg dump missing", file=sys.stderr)
        return 1
    actual_pg = _sha256_file(pg_dump)
    if actual_pg != manifest["pg_backup_digest"]:
        print(
            "FATAL: PostgreSQL backup digest mismatch "
            f"(manifest={manifest['pg_backup_digest']} actual={actual_pg}) -- "
            "restore refused (fail-closed)",
            file=sys.stderr,
        )
        return 1

    objects_dir = backup_dir / "objects"
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
    actual_manifest_digest = hashlib.sha256(
        ("\n".join(lines) + "\n").encode()
    ).hexdigest()
    if actual_manifest_digest != manifest["object_manifest_digest"]:
        print(
            "FATAL: object backup manifest digest mismatch -- restore refused "
            "(fail-closed)",
            file=sys.stderr,
        )
        return 1
    if len(lines) != manifest["object_count"]:
        print(
            f"FATAL: object count changed (manifest={manifest['object_count']} "
            f"actual={len(lines)}) -- restore refused",
            file=sys.stderr,
        )
        return 1

    print(
        f"BACKUP_VERIFIED pg_ok objects={len(lines)} bytes={total_bytes} "
        f"backup_id={manifest['backup_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
