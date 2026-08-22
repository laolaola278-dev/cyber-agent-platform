"""Phase 28.7 -- reconciliation CLI (machine-readable JSON to stdout).

Runs :class:`~app.acquisition.reconciliation.EvidenceReconciler` against a
live CAP deployment. Used by the DR gates on a restored cluster and by
operators after any restore.

Environment:
    DATABASE_URL            async SQLAlchemy URL of the target database
    OBJECT_STORE_ENDPOINT   S3/MinIO host:port
    OBJECT_STORE_ACCESS_KEY / OBJECT_STORE_SECRET_KEY / OBJECT_STORE_BUCKET

Usage:
    python -m app.acquisition.reconcile_cli [--output report.json]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def _run(output_path: str | None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.acquisition.reconciliation import EvidenceReconciler
    from app.acquisition.store import S3EvidenceStore

    database_url = os.environ["DATABASE_URL"]
    store = S3EvidenceStore(
        endpoint=os.environ["OBJECT_STORE_ENDPOINT"],
        access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
        secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
        bucket=os.environ.get("OBJECT_STORE_BUCKET", "cap-evidence"),
        secure=os.environ.get("OBJECT_STORE_SECURE", "0") == "1",
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        report = await EvidenceReconciler(store, session_factory).run()
    finally:
        await engine.dispose()

    payload = json.dumps(report.to_dict(), indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload)
    # exit code reflects INTEGRITY only (orphans are a GC policy concern):
    # missing or corrupted referenced objects are critical failures
    return 0 if report.integrity_ok else 1


def main() -> int:
    output = None
    args = sys.argv[1:]
    if "--output" in args:
        output = args[args.index("--output") + 1]
    try:
        return asyncio.run(_run(output))
    except KeyError as error:
        print(f"missing required environment variable: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
