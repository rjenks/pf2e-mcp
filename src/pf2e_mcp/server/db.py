"""Shared read-only SQLite access for MCP tools.

The DB is built by `pf2e_mcp.ingestion.build` and treated as a static
artifact by the server -- tools only ever read from it."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_DB_PATH = Path(__file__).parent.parent.parent.parent / ".data" / "pf2e.sqlite"


def db_path() -> Path:
    return Path(os.environ.get("PF2E_MCP_DB", _DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise RuntimeError(
            f"No database found at {path}. Run `python -m pf2e_mcp.ingestion.build` first."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
