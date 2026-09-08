"""Shared fixtures.

Most of this suite is deliberately database-free: the schema, the YAML I/O and
every semantic check that does not resolve a slug can be tested without the
108 MB SQLite build, which is gitignored and may not exist on a given machine.
The tests that do need it skip rather than fail when it is missing, so a clone
without ingested data still runs a useful suite.
"""

from __future__ import annotations

import sqlite3

import pytest

from pf2e_mcp import paths


@pytest.fixture(scope="session")
def conn() -> sqlite3.Connection:
    """A read-only connection to the rules database, or skip."""
    path = paths.db_path()
    if not path.exists():
        pytest.skip(f"No rules database at {path}; run the ingestion first.")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def minimal() -> dict:
    """The smallest document the schema accepts.

    Every structural test starts from this and breaks one thing, so that a
    failure names the thing that was broken rather than the six fields that
    were missing anyway.
    """
    return {
        "schemaVersion": 1,
        "identity": {"name": "Test Subject", "currentLevel": 1},
        "build": {
            "ancestry": "dwarf",
            "heritage": "death-warden-dwarf",
            "background": "field-medic",
            "class": "animist",
        },
        "plan": [{"level": 1}],
    }
