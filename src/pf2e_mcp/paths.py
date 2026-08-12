"""Where the built database and the download cache live.

Both locations used to be derived from `__file__` relative to the repo root,
which is correct only in a source checkout: once installed, the same
expression resolves inside the environment's `lib/` directory, so an
installed or `uvx`-run server would write its database into site-packages'
parent and lose it whenever that environment was rebuilt or pruned.

These are split across two platform directories on purpose, because the two
artifacts have different worth:

- The **database** is expensive to recreate -- a large download plus a full
  ingestion pass -- so it lives in the user *data* directory, where nothing
  reclaims it behind the user's back.
- The **raw extracted release** is pure derived cache, re-downloadable from
  foundryvtt/pf2e at any time and several hundred MB in practice, so it
  lives in the user *cache* directory where ordinary disk-cleanup tools are
  entitled to delete it. Losing it costs one re-download, nothing more.

Both can be overridden -- `PF2E_MCP_DB` for the database file and
`PF2E_MCP_CACHE` for the cache directory -- which is what to use for a
throwaway build, a shared read-only database, or to keep everything inside a
source checkout during development.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir

APP_NAME = "pf2e-mcp"


def db_path() -> Path:
    """The SQLite database file. `PF2E_MCP_DB` overrides."""
    override = os.environ.get("PF2E_MCP_DB")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(APP_NAME)) / "pf2e.sqlite"


def cache_dir() -> Path:
    """Directory holding one extracted foundryvtt/pf2e release per tag.

    `PF2E_MCP_CACHE` overrides.
    """
    override = os.environ.get("PF2E_MCP_CACHE")
    if override:
        return Path(override).expanduser()
    return Path(user_cache_dir(APP_NAME)) / "raw"
