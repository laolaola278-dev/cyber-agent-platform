"""Asset search must stay executable on PostgreSQL, the documented durable store.

Found by running the shipped application against a real PostgreSQL server, not
by the unit suite: ``AssetRepository.search`` applied ``statement.distinct()``
unconditionally, so its count query became

    SELECT count(*) FROM (SELECT DISTINCT assets.risk, assets.capabilities,
                                          assets.properties, ... FROM assets)

and PostgreSQL answered

    UndefinedFunctionError: could not identify an equality operator for type json

Every console read of /assets returned 500. SQLite -- which the unit suite runs
on, because CI has no database service -- accepts DISTINCT over json, so the
defect was invisible there and this guard has to assert on the SQL compiled for
the PostgreSQL dialect rather than on a query that actually executes.
"""

from typing import Any

from sqlalchemy.dialects import postgresql

from app.models.asset import Asset
from app.repositories.asset import AssetRepository
from tests.conftest import TestSessionFactory


class _RecordingSession:
    """Captures the statements search() builds without executing them."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return 0

    async def scalars(self, statement: Any) -> list[Any]:
        self.statements.append(statement)
        return []

    async def execute(self, statement: Any) -> Any:  # pragma: no cover
        self.statements.append(statement)
        raise AssertionError("search() must not issue a third statement")


async def _compiled_statements(**filters: Any) -> list[str]:
    session = _RecordingSession()
    repository = AssetRepository(session)  # type: ignore[arg-type]
    await repository.search(**filters)
    assert session.statements, "search() built no statement"
    dialect = postgresql.dialect()
    return [str(statement.compile(dialect=dialect)) for statement in session.statements]


async def test_asset_search_compiles_without_distinct_on_postgresql() -> None:
    """A DISTINCT over the asset row is fatal here, so the query must not use one."""
    for filters in ({}, {"tag": "managed"}, {"tag": "managed", "capability": "crawl.html"}):
        for sql in await _compiled_statements(**filters):
            assert "DISTINCT" not in sql.upper(), f"{filters} produced a DISTINCT:\n{sql}"


async def test_tag_filter_survives_the_distinct_removal() -> None:
    """Dropping DISTINCT must not quietly drop the tag constraint it replaced."""
    unfiltered = await _compiled_statements()
    filtered = await _compiled_statements(tag="Managed")

    for sql in unfiltered:
        assert "asset_tags" not in sql, "an unfiltered search must not touch tags"

    joined = [sql for sql in filtered if "asset_tags" in sql]
    assert joined, f"the tag filter vanished from the compiled SQL:\n{filtered}"
    # Case folding is part of the contract: the console sends "Managed", the
    # stored tag may be "managed".
    assert any("lower(" in sql for sql in joined), joined


def test_asset_json_columns_are_the_reason() -> None:
    """Pin the premise: json (not jsonb) columns make DISTINCT unusable.

    If these columns ever move to JSONB, PostgreSQL gains an equality operator
    for them -- and this test says so, instead of leaving a guard whose reason
    for existing has silently disappeared.
    """
    dialect = postgresql.dialect()
    json_columns = [
        column.name
        for column in Asset.__table__.columns
        if column.type.compile(dialect).upper() == "JSON"
    ]
    assert json_columns, (
        "Asset no longer has plain json columns; the DISTINCT guard in "
        "AssetRepository.search needs re-evaluating against the new types."
    )


async def test_search_still_pages_and_reports_a_total() -> None:
    """Guard against "fixing" the crash by removing the count query."""
    async with TestSessionFactory() as session:
        result = await AssetRepository(session).search(page=1, page_size=10)
    assert result.page == 1
    assert result.page_size == 10
    assert isinstance(result.total, int)
