"""Phase 28.3 follow-on -- whole-schema migration catalogue on real PostgreSQL.

``test_phase_28_3_migration.py`` certifies that a fresh database reaches a
complete *acquisition* schema. This module certifies the rest of what a release
has to be able to say about migrations, none of which the acquisition-scoped test
can see:

  * the built schema equals the ORM models the application actually uses, so a
    column added to a model without a migration fails here instead of in the next
    deployment that runs ``alembic upgrade head``;
  * every index, named constraint and table that any revision created -- and no
    later revision dropped -- is present by *name*;
  * no index is left invalid (a failed ``CREATE INDEX CONCURRENTLY`` leaves an
    unusable index behind while the upgrade still reports success);
  * a populated database survives a real downgrade/upgrade cycle: rows that were
    there before are unchanged afterwards, and no foreign key gained an orphan.

Everything reaches the server through ``run_sync`` on an async engine: asyncpg is
the only driver the image and the lockfile carry, and a synchronous
``create_engine`` on a ``postgresql+asyncpg://`` URL raises ``MissingGreenlet`` the
moment anything reflects.

Skipped when no PostgreSQL is reachable, which makes it a hard failure under
``CAP_CERTIFICATION_STRICT=1``; the certification workflows provide the server.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [pytest.mark.postgres, pytest.mark.timeout(1800)]

BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"

_ADMIN_DSN = os.environ.get(
    "CAP283_PG_ADMIN_DSN", "postgresql://cap:cap@127.0.0.1:55432/postgres"
)
_admin_parsed = urlparse(_ADMIN_DSN)
_DB_DSN = f"postgresql+asyncpg://{_admin_parsed.netloc}/"

#: A revision mid-chain: downgrading to it exercises several real DDL steps in
#: both directions against tables that hold data, which is what an operator
#: upgrading an existing deployment experiences rather than a pristine install.
MID_REVISION = os.environ.get("CAP_MIGRATION_MID_REVISION", "20260802_0017")

async def _probe_pg() -> bool:
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(_ADMIN_DSN), timeout=3)
        await conn.close()
        return True
    except Exception:  # noqa: BLE001 -- no PG -> skip
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe_pg()), reason="PostgreSQL not reachable")


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=db_url)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
    )


def _heads() -> list[str]:
    proc = _alembic(_DB_DSN + "postgres", "heads")
    assert proc.returncode == 0, proc.stderr[-400:]
    return [line.split()[0] for line in proc.stdout.splitlines() if line.strip()]


class _Database:
    """A throwaway database this module owns, migrates and always drops."""

    def __init__(self) -> None:
        self.name = f"cap283_cat_{uuid4().hex[:8]}"
        self.url = _DB_DSN + self.name

    def _as_admin(self, statements: list[str]) -> None:
        import asyncpg

        async def run() -> None:
            conn = await asyncpg.connect(_ADMIN_DSN)
            try:
                for statement in statements:
                    await conn.execute(statement)
            finally:
                await conn.close()

        asyncio.run(run())

    def __enter__(self) -> _Database:
        self._as_admin([f'CREATE DATABASE "{self.name}"'])
        return self

    def __exit__(self, *exc: object) -> None:
        self._as_admin(
            [
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{self.name}' AND pid <> pg_backend_pid()",
                f'DROP DATABASE "{self.name}"',
            ]
        )

    def upgrade(self, target: str = "head") -> None:
        proc = _alembic(self.url, "upgrade", target)
        assert proc.returncode == 0, f"upgrade {target} failed: {proc.stderr[-800:]}"

    def downgrade(self, target: str) -> None:
        proc = _alembic(self.url, "downgrade", target)
        assert proc.returncode == 0, f"downgrade {target} failed: {proc.stderr[-800:]}"


def _read[T](url: str, work: Callable[[object], T]) -> T:
    """Run `work` (plain synchronous SQLAlchemy) on a live connection."""

    async def go() -> T:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(work)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _write[T](url: str, work: Callable[[object], T]) -> T:
    """Same, inside one transaction that commits when `work` returns."""

    async def go() -> T:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                return await conn.run_sync(work)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _version_num(conn: object) -> object:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()  # type: ignore[attr-defined]


def _table_names(conn: object) -> set[str]:
    return set(inspect(conn).get_table_names())


def _version_rows(conn: object) -> list[str]:
    rows = conn.execute(text("SELECT version_num FROM alembic_version"))  # type: ignore[attr-defined]
    return sorted(row[0] for row in rows)


# -- what the revision scripts promise ---------------------------------------

_CREATE_INDEX = re.compile(r'create_index\(\s*"([^"]+)"')
_DROP_INDEX = re.compile(r'drop_index\(\s*"([^"]+)"')
_CREATE_TABLE = re.compile(r'create_table\(\s*"([^"]+)"')
_DROP_TABLE = re.compile(r'drop_table\(\s*"([^"]+)"')
_CREATE_CONSTRAINT = re.compile(
    r'create_(?:check_constraint|unique_constraint|foreign_key|primary_key_constraint)\(\s*"([^"]+)"'
)
_DROP_CONSTRAINT = re.compile(r'drop_constraint\(\s*"([^"]+)"')


def _upgrade_bodies() -> list[str]:
    """The ``upgrade()`` half of each revision, without its ``downgrade()``.

    A revision drops in ``downgrade()`` exactly what it created in ``upgrade()``,
    so scanning whole files subtracts every name again -- the first version of
    this audit compared an empty set and passed.
    """
    bodies: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        source = path.read_text("utf-8")
        cut = source.find("def downgrade(")
        bodies.append(source if cut < 0 else source[:cut])
    return bodies


def _created_minus_dropped(creator: re.Pattern[str], dropper: re.Pattern[str]) -> set[str]:
    """Names an ``upgrade()`` introduced and no ``upgrade()`` removed.

    Derived from the migration files, so it tracks new revisions without anybody
    remembering to extend an expected list.
    """
    created: set[str] = set()
    dropped: set[str] = set()
    for source in _upgrade_bodies():
        created.update(creator.findall(source))
        dropped.update(dropper.findall(source))
    return created - dropped


@_skip
def test_head_row_matches_the_declared_single_head() -> None:
    heads = _heads()
    assert len(heads) == 1, f"alembic must have exactly one head, found {heads}"
    with _Database() as db:
        db.upgrade()
        claimed = _read(db.url, _version_rows)
    assert claimed == heads, f"the database claims {claimed}, the scripts claim {heads}"


#: Promised constraint names that the migrated database carries under a
#: convention-doubled name instead (``ck_agents_status`` ->
#: ``ck_agents_ck_agents_status``). Verified on PostgreSQL 16 by comparing
#: ``pg_get_constraintdef``: the condition is identical, only the name differs, so
#: enforcement is intact. Pinned rather than ignored, because each new entry means
#: another revision stopped naming its constraints and a later migration that
#: references the promised name will fail.
KNOWN_CONSTRAINT_RENAMES = [
    "ck_agents_status",
    "ck_task_executions_status",
    "ck_tasks_status",
]


@_skip
def test_migrated_objects_from_the_revision_scripts_all_exist() -> None:
    tables = _created_minus_dropped(_CREATE_TABLE, _DROP_TABLE)
    indexes = _created_minus_dropped(_CREATE_INDEX, _DROP_INDEX)
    constraints = _created_minus_dropped(_CREATE_CONSTRAINT, _DROP_CONSTRAINT)
    # Negative control: the parsers have to find something, or this test passes
    # forever on a chain nobody is actually auditing.
    assert len(tables) >= 30, f"only {len(tables)} created tables parsed -- parser drift"
    assert len(indexes) >= 10, f"only {len(indexes)} created indexes parsed -- parser drift"
    assert constraints, "no named constraints parsed -- the pattern no longer matches"

    def work(conn: object) -> tuple[set[str], set[str], set[str]]:
        inspector = inspect(conn)
        present = set(inspector.get_table_names())
        found_indexes = {
            index["name"] for table in present for index in inspector.get_indexes(table)
        }
        found_indexes |= {
            row[0]
            for row in conn.execute(  # type: ignore[attr-defined]
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        }
        found_constraints = {
            row[0]
            for row in conn.execute(  # type: ignore[attr-defined]
                text(
                    "SELECT conname FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE n.nspname = 'public'"
                )
            )
        }
        for table in present:
            found_constraints |= {
                item["name"]
                for item in inspector.get_unique_constraints(table)
                if item.get("name")
            }
        return present, found_indexes, found_constraints

    with _Database() as db:
        db.upgrade()
        db_tables, db_indexes, db_constraints = _read(db.url, work)

    assert not tables - db_tables, f"missing tables: {sorted(tables - db_tables)}"
    assert not indexes - db_indexes, f"missing indexes: {sorted(indexes - db_indexes)}"

    missing = sorted(constraints - db_constraints)
    renamed = {
        promised: sorted(actual for actual in db_constraints if promised in actual)
        for promised in missing
    }
    unexplained = {name: matches for name, matches in renamed.items() if not matches}
    assert not unexplained, f"constraints a migration created are gone: {unexplained}"
    drifted = sorted(renamed)
    assert drifted == KNOWN_CONSTRAINT_RENAMES, (
        "constraint names a revision promised diverged from the database in an "
        f"undocumented way. Documented: {KNOWN_CONSTRAINT_RENAMES}; now: {drifted}. "
        "A new entry means a revision rebuilt a constraint through the metadata "
        "naming convention; a removed entry means the drift was fixed -- delete it "
        "from KNOWN_CONSTRAINT_RENAMES so the list can only shrink."
    )


@_skip
def test_no_invalid_indexes_survive_the_upgrade() -> None:
    def work(conn: object) -> tuple[list[tuple[str, str]], int]:
        invalid = conn.execute(  # type: ignore[attr-defined]
            text(
                "SELECT c.relname, i.indexrelid::regclass::text FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "WHERE NOT i.indisvalid AND c.relnamespace = 'public'::regnamespace"
            )
        ).fetchall()
        total = conn.execute(  # type: ignore[attr-defined]
            text(
                "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
                "WHERE c.relnamespace = 'public'::regnamespace"
            )
        ).scalar()
        return [tuple(row) for row in invalid], int(total or 0)

    with _Database() as db:
        db.upgrade()
        invalid, total = _read(db.url, work)

    assert total > 30, f"only {total} indexes visible -- is this the migrated database?"
    assert not invalid, f"invalid indexes present: {invalid}"


@_skip
def test_migrated_schema_equals_the_orm_models() -> None:
    import app.models  # noqa: F401 -- populates Base.metadata
    from app.database import Base

    expected = Base.metadata
    assert expected.tables, "no ORM models registered -- the test would pass vacuously"

    def work(conn: object) -> dict[str, dict[str, bool]]:
        reflected = MetaData()
        reflected.reflect(bind=conn)  # type: ignore[arg-type]
        return {
            name: {column.name: bool(column.nullable) for column in table.columns}
            for name, table in reflected.tables.items()
        }

    with _Database() as db:
        db.upgrade()
        db_tables = _read(db.url, work)

    missing_tables = sorted(set(expected.tables) - set(db_tables))
    missing_columns: list[str] = []
    nullability: list[str] = []
    for name, table in expected.tables.items():
        if name not in db_tables:
            continue
        for column in table.columns:
            if column.name not in db_tables[name]:
                missing_columns.append(f"{name}.{column.name}")
            elif bool(column.nullable) != db_tables[name][column.name]:
                nullability.append(f"{name}.{column.name}")
    extra = sorted(set(db_tables) - set(expected.tables) - {"alembic_version"})

    assert not missing_tables, f"schema lacks model tables: {missing_tables}"
    assert not missing_columns, f"schema lacks model columns: {missing_columns}"
    assert not nullability, f"nullability differs between models and schema: {nullability}"
    assert not extra, f"the migrated schema carries tables no model declares: {extra}"


# -- data through a real upgrade cycle ---------------------------------------


def _seed(url: str) -> dict[str, dict[str, object]]:
    """Insert one type-shaped row per table, recording which columns went in.

    The values are not business-valid: the question is whether the chain keeps
    what is on disk, so any row the constraints accept makes the point. A table
    whose NOT NULL columns cannot be filled heuristically is recorded as skipped
    and reported, never silently ignored.
    """

    def work(conn: object) -> dict[str, dict[str, object]]:
        metadata = MetaData()
        metadata.reflect(bind=conn)  # type: ignore[arg-type]
        try:
            tables = list(metadata.sorted_tables)  # FK parents first
        except Exception:  # noqa: BLE001 -- a cycle falls back to name order
            tables = [metadata.tables[name] for name in sorted(metadata.tables)]

        result: dict[str, dict[str, object]] = {}
        for table in tables:
            if table.name == "alembic_version":
                continue
            values: dict[str, object] = {}
            reason: str | None = None
            for column in table.columns:
                if column.default is not None or column.server_default is not None:
                    continue
                if column.nullable:
                    continue
                kind = column.type.__class__
                if issubclass(kind, (String, Text)):
                    values[column.name] = f"cert-{table.name}-{uuid4().hex[:8]}"
                elif issubclass(kind, UUID):
                    values[column.name] = str(uuid4())
                elif issubclass(kind, (JSON, JSONB)):
                    values[column.name] = {"cert": True}
                elif issubclass(kind, DateTime):
                    values[column.name] = datetime.now(UTC)
                elif issubclass(kind, (Integer, BigInteger, Numeric, Float)):
                    values[column.name] = 1
                elif issubclass(kind, Boolean):
                    values[column.name] = False
                else:
                    reason = f"no value generator for {column.name} ({column.type})"
                    break
            if reason:
                result[table.name] = {"skipped": reason}
                continue
            try:
                # A savepoint per table: one rejected row (a CHECK constraint the
                # heuristic value cannot satisfy) must cost this table only. On a
                # plain transaction PostgreSQL aborts it, and every later table
                # then reports InFailedSQLTransaction instead of being seeded.
                with conn.begin_nested():  # type: ignore[attr-defined]
                    conn.execute(table.insert().values(**values))  # type: ignore[attr-defined]
            except Exception as error:  # noqa: BLE001 -- a rejection is a finding
                result[table.name] = {"skipped": str(error).splitlines()[0][:160]}
                continue
            result[table.name] = {"columns": sorted(values)}
        return result

    return _write(url, work)


def _fingerprints(url: str, tables: dict[str, list[str]]) -> dict[str, str]:
    """Hash the requested columns of each table, order-normalised."""

    def work(conn: object) -> dict[str, str]:
        metadata = MetaData()
        metadata.reflect(bind=conn)  # type: ignore[arg-type]
        out: dict[str, str] = {}
        for name, columns in sorted(tables.items()):
            if name not in metadata.tables:
                continue
            table = metadata.tables[name]
            usable = [c for c in columns if c in table.columns]
            if not usable or len(usable) != len(columns):
                continue
            rows = conn.execute(  # type: ignore[attr-defined]
                select(*(table.columns[c] for c in usable))
            ).fetchall()
            rendered = sorted(tuple(str(value) for value in row) for row in rows)
            out[name] = hashlib.sha256(repr(rendered).encode()).hexdigest()
        return out

    return _read(url, work)


def _orphans(url: str) -> list[str]:
    def work(conn: object) -> list[str]:
        metadata = MetaData()
        metadata.reflect(bind=conn)  # type: ignore[arg-type]
        found: list[str] = []
        for table in metadata.tables.values():
            for fk in table.foreign_keys:
                parent = fk.column.table
                try:
                    count = conn.execute(  # type: ignore[attr-defined]
                        text(
                            f"SELECT count(*) FROM {table.name} c "
                            f"LEFT JOIN {parent.name} p "
                            f"ON c.{fk.parent.name} = p.{fk.column.name} "
                            f"WHERE c.{fk.parent.name} IS NOT NULL "
                            f"AND p.{fk.column.name} IS NULL"
                        )
                    ).scalar()
                except Exception:  # noqa: BLE001 -- column dropped by the cycle
                    continue
                if count:
                    found.append(f"{table.name}.{fk.parent.name} -> {parent.name}: {count}")
        return found

    return _read(url, work)


@_skip
def test_populated_database_survives_a_downgrade_upgrade_cycle() -> None:
    """Upgrade paths run against databases that already hold data."""
    with _Database() as db:
        db.upgrade()
        seeded = _seed(db.url)
        inserted = {
            name: list(seeded[name]["columns"]) for name in seeded if "columns" in seeded[name]
        }
        assert inserted, f"nothing could be seeded into a migrated schema: {seeded}"
        before = _fingerprints(db.url, inserted)
        assert before, "fingerprinting produced nothing to compare"

        db.downgrade(MID_REVISION)
        mid_version = _read(db.url, _version_num)
        assert mid_version == MID_REVISION, f"downgrade left the schema at {mid_version!r}"

        survivors = _read(db.url, _table_names)
        carried = {name: inserted[name] for name in before if name in survivors}
        after_mid = _fingerprints(db.url, carried)
        changed = sorted(name for name, digest in after_mid.items() if digest != before[name])

        db.upgrade()
        head_version = _read(db.url, _version_num)
        # after_mid is a name -> digest map; the columns have to come from the
        # seeded set, so re-upgrading is compared on exactly what was measured.
        restored = _fingerprints(db.url, {name: carried[name] for name in after_mid})
        orphans = _orphans(db.url)

    assert head_version == _heads()[0], f"the cycle ended at {head_version!r}"
    assert not changed, f"rows changed during the downgrade step: {changed}"
    assert sorted(restored) == sorted(after_mid), (
        f"tables vanished on re-upgrade: {set(after_mid) - set(restored)}"
    )
    changed_back = sorted(
        name for name, digest in restored.items() if after_mid[name] != digest
    )
    assert not changed_back, f"rows changed when the schema was re-upgraded: {changed_back}"
    assert not orphans, f"the cycle produced foreign-key orphans: {orphans}"


# -- the debt, pinned ---------------------------------------------------------

#: Differences between the ORM models and the schema the migrations build that
#: ``alembic check`` reports every time it runs. They are listed here -- with the
#: direction they go in -- so the audit can say "this is known, this is its size,
#: and it has not grown", which is more useful than a gate that is red from the
#: day it is added and gets deleted within a release.
SCHEMA_ONLY_COLUMNS = {"agents.runtime_image"}
SCHEMA_ONLY_INDEXES = {"ix_event_references_url", "ix_finding_references_url", "ix_tools_type"}

#: Index names the models declare that the migrated schema does not carry under
#: that name -- while the *coverage* (same table, same columns, same uniqueness)
#: does exist. Pinned so the divergence is attributed instead of ignored: the
#: assertion below requires the covered set to match this list exactly, so fixing
#: one means deleting its entry, and a new one fails the build.
MODEL_INDEXES_UNDER_OTHER_NAMES = {
    # The models declare a unique Index; revision 20260808_0018 built a UNIQUE
    # CONSTRAINT instead, which is the stronger of the two.
    "ix_playbook_executions_idempotency_key": "uq constraint on the same column",
    # The column was renamed to tool_type; the schema kept the pre-rename name.
    "ix_tools_tool_type": "ix_tools_type covers the same column",
}


@_skip
def test_model_and_schema_differences_are_the_documented_ones() -> None:
    """Nothing the models declare may be missing from the migrated schema.

    The reverse direction (schema carrying what the models dropped) is the debt in
    SCHEMA_ONLY_*; both halves are asserted, so the debt can only shrink.
    """
    import app.models  # noqa: F401
    from app.database import Base

    expected = Base.metadata

    def work(conn: object) -> tuple[dict[str, set[str]], set[str], set[tuple[str, ...]]]:
        inspector = inspect(conn)
        columns = {table: {c["name"] for c in inspector.get_columns(table)}
                   for table in inspector.get_table_names()}
        index_names: set[str] = set()
        covered: set[tuple[str, ...]] = set()
        for table in columns:
            for index in inspector.get_indexes(table):
                index_names.add(index["name"])
                covered.add((table, "unique" if index.get("unique") else "any",
                             *index.get("column_names", [])))
            # A UNIQUE CONSTRAINT is enforced by its own backing index, which the
            # inspector reports separately from get_indexes().
            for constraint in inspector.get_unique_constraints(table):
                covered.add((table, "unique", *constraint.get("column_names", [])))
        return columns, index_names, covered

    with _Database() as db:
        db.upgrade()
        db_columns, db_indexes, db_coverage = _read(db.url, work)

    # (a) every model column exists in the migrated schema.
    absent = sorted(
        f"{table.name}.{column.name}"
        for table in expected.tables.values()
        for column in table.columns
        if table.name in db_columns and column.name not in db_columns[table.name]
    )
    assert not absent, f"the migrated schema is missing model columns: {absent}"

    # (b) extra columns are only the documented ones.
    extra_columns = sorted(
        f"{table}.{column}"
        for table, columns in db_columns.items()
        for column in columns
        if table in expected.tables and column not in expected.tables[table].columns
    )
    assert set(extra_columns) == SCHEMA_ONLY_COLUMNS, (
        f"undocumented schema-only columns: {set(extra_columns) - SCHEMA_ONLY_COLUMNS}; "
        f"documented ones that are gone: {SCHEMA_ONLY_COLUMNS - set(extra_columns)}"
    )

    # (c) every index the models declare is enforced, even where the name differs.
    declared = {index.name for table in expected.tables.values() for index in table.indexes}
    uncovered: list[str] = []
    renamed: list[str] = []
    for table in expected.tables.values():
        for index in table.indexes:
            if not index.name:
                continue
            columns = tuple(column.name for column in index.columns)
            acceptable = ("unique",) if index.unique else ("unique", "any")
            enforced = any(
                (table.name, kind, *columns) in db_coverage for kind in acceptable
            )
            if not enforced:
                uncovered.append(f"{table.name}:{index.name}{columns}")
            elif index.name not in db_indexes:
                renamed.append(index.name)
    assert not uncovered, f"model indexes with no enforcement in the schema: {uncovered}"
    assert sorted(renamed) == sorted(MODEL_INDEXES_UNDER_OTHER_NAMES), (
        f"index-name drift changed: documented {sorted(MODEL_INDEXES_UNDER_OTHER_NAMES)}, "
        f"found {sorted(renamed)}"
    )

    schema_only = sorted(
        name for name in db_indexes if name and name not in declared and name.startswith("ix_")
    )
    assert set(schema_only) == SCHEMA_ONLY_INDEXES, (
        f"undocumented schema-only indexes: {set(schema_only) - SCHEMA_ONLY_INDEXES}; "
        f"documented ones that are gone: {SCHEMA_ONLY_INDEXES - set(schema_only)}"
    )
