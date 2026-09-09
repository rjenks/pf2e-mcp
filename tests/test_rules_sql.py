"""The read-only SQL escape hatch.

The guards matter more than the happy path here: this tool exists so that an
agent hitting an unforeseen gap stays inside the MCP instead of opening the
database directly, which only works if it is safe to hand a model.
"""

from __future__ import annotations

import pytest

from pf2e_mcp import paths
from pf2e_mcp.server import rules_tools


@pytest.fixture(autouse=True)
def _needs_db() -> None:
    if not paths.db_path().exists():
        pytest.skip("No rules database; run the ingestion first.")


def test_schema_lists_tables_without_a_query() -> None:
    """`schema=True` is the discovery step, so it must not need valid SQL."""
    result = rules_tools.rules_sql(schema=True)
    tables = {entry["table"] for entry in result["schema"]}
    assert {"entries", "class_progression", "prerequisites"} <= tables
    entries = next(e for e in result["schema"] if e["table"] == "entries")
    assert {"name", "type", "raw_json"} <= {c["name"] for c in entries["columns"]}
    assert entries["rows"] > 0


def test_select_returns_rows_and_columns() -> None:
    result = rules_tools.rules_sql(
        "select name, level from entries where type = ? and name = ?",
        params=["feat", "Dazing Blow"],
    )
    assert result["columns"] == ["name", "level"]
    assert result["rows"] == [{"name": "Dazing Blow", "level": 6}]
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "sql",
    [
        "delete from entries",
        "drop table entries",
        "  /* sneaky */ update entries set name = 'x'",
        "-- select 1\ndelete from entries",
        "insert into entries (id) values ('x')",
    ],
)
def test_writes_are_refused(sql: str) -> None:
    """Comments must not disguise the leading verb."""
    assert "Only SELECT/WITH" in rules_tools.rules_sql(sql)["error"]


def test_second_statement_is_refused() -> None:
    """Silently running only the first half would be worse than an error."""
    result = rules_tools.rules_sql("select 1; drop table entries")
    assert "single statement" in result["error"]


def test_trailing_semicolon_is_fine() -> None:
    assert rules_tools.rules_sql("select 1 as n;")["rows"] == [{"n": 1}]


def test_with_clause_is_allowed() -> None:
    result = rules_tools.rules_sql(
        "with f as (select name from entries where type = 'feat' limit 3) select * from f"
    )
    assert result["row_count"] == 3


def test_limit_flags_that_more_matched() -> None:
    result = rules_tools.rules_sql("select name from entries where type = 'feat'", limit=5)
    assert result["row_count"] == 5
    assert result["truncated"] is True


def test_oversized_cells_are_clipped_and_named() -> None:
    """`raw_json` is whole Foundry documents; unclipped it would swamp a caller."""
    result = rules_tools.rules_sql(
        "select raw_json from entries where type = 'feat' limit 1", max_cell=100
    )
    assert result["truncated_cells"] == ["raw_json"]
    assert len(result["rows"][0]["raw_json"]) <= 100 + len("... [truncated]")


def test_empty_query_points_at_schema() -> None:
    assert "schema=True" in rules_tools.rules_sql("")["error"]


def test_sql_error_is_reported_not_raised() -> None:
    result = rules_tools.rules_sql("select nope from entries")
    assert "SQL error" in result["error"]
