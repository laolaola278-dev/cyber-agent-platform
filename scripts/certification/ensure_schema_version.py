#!/usr/bin/env python3
"""Ensure the alembic_version table exists on the HA certification database.

Phase 28.5-CI -- GATE 15 readiness. The production AcquisitionWorker's
health._check_schema() runs ``SELECT version_num FROM alembic_version`` and
returns ready only when that table has a row.  The security/cert test suites
build tables with ``Base.metadata.create_all`` (no alembic_version), so a
worker started against that schema would report readiness=False forever and
the durable queue would never drain (runs stuck in QUEUED).

This idempotent helper makes the real cap283 schema satisfy the worker's
check by ensuring the version table exists with an initial migration row.  It
connects with the password-carrying DSN exported by run_ha.sh (CAP283_PG_DSN
/ CAP283_PG_SYNC / CAP_CERT_PG_DSN) and is safe to run repeatedly.

Usage (from backend/):  uv run python ../scripts/certification/ensure_schema_version.py
"""

from __future__ import annotations

import asyncio
import os
import sys

_INITIAL_VERSION = "20260729_0001_initial_schema"


def _resolve_dsn() -> str:
    # Preference: asyncpg-style DSNs first (they carry the password); fall
    # back to the sync DSN, then the generic cert DSN.
    for key in ("CAP283_PG_SYNC", "CAP283_PG_DSN", "CAP_CERT_PG_DSN"):
        val = os.environ.get(key)
        if val:
            # Sync DSNs are postgresql://; asyncpg accepts either scheme.
            return val.replace("postgresql://", "postgresql://", 1)
    sys.stderr.write(
        "ensure_schema_version: no CAP283_PG_SYNC/CAP283_PG_DSN/CAP_CERT_PG_DSN "
        "in environment\n"
    )
    raise SystemExit(1)


async def _main() -> int:
    import asyncpg  # local import: only needed when running

    dsn = _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO alembic_version (version_num)
            SELECT $1
            WHERE NOT EXISTS (SELECT 1 FROM alembic_version)
            """,
            _INITIAL_VERSION,
        )
        row = await conn.fetchrow("SELECT version_num FROM alembic_version")
        sys.stderr.write(
            f"ensure_schema_version: alembic_version ready version_num={row['version_num']}\n"
        )
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))