"""Shared read-only SQLite access for MCP tools.

The DB is built by `pf2e_mcp.ingestion.build` and treated as a static
artifact by the server -- tools only ever read from it."""

from __future__ import annotations

import sqlite3

from ..paths import db_path

__all__ = ["db_path", "get_connection"]


def get_connection() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise RuntimeError(
            f"No database found at {path}. Run `python -m pf2e_mcp.ingestion.build` first."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
