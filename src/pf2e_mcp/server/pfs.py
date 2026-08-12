"""Pathfinder Society (Organized Play) legality helpers.

There's no structured PFS-legality feed in the foundryvtt/pf2e data this
project ingests -- Organized Play legality is Paizo's own "Additional
Resources" policy document, updated on its own quarterly cadence
independent of errata, and it doesn't map cleanly onto rarity (some common
options are PFS-banned for balance; some uncommon ones are unlocked
without a boon). Rather than hand-write specific "X is banned" rulings from
training data that could easily be stale, this uses rarity as an
honest-but-approximate default (common = legal, uncommon/rare/unique =
flagged as restricted, needing a boon or GM/scenario unlock) and layers a
hand-maintained override file on top for known exceptions -- same pattern
as ingestion/prerequisite_overrides.json, but loaded fresh on every call
(not baked into the database at ingestion time) so edits take effect
immediately without a re-ingestion run.

Not authoritative. Always say so in tool output rather than implying a
"pfs: legal" result is a guarantee -- consult the current Additional
Resources document for a real PFS game.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_OVERRIDES_PATH = Path(__file__).parent / "pfs_overrides.json"


def load_overrides() -> dict[str, dict[str, str]]:
    """Keys starting with '_' are treated as comments and skipped, matching
    the convention already used by prerequisite_overrides.json."""
    if not _OVERRIDES_PATH.exists():
        return {}
    raw = json.loads(_OVERRIDES_PATH.read_text())
    return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}


def pfs_status(
    name: str, rarity: str | None, overrides: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Best-effort PFS legality for one named entry. An explicit override
    always wins; otherwise falls back to the rarity heuristic described in
    the module docstring."""
    override = overrides.get(name.lower())
    if override:
        return {
            "status": override.get("status", "restricted"),
            "note": override.get("note"),
            "source": "override",
        }
    if rarity in (None, "common"):
        return {"status": "legal", "note": None, "source": "rarity"}
    if rarity == "uncommon":
        return {
            "status": "restricted",
            "note": "Uncommon -- typically needs a PFS boon or GM/scenario unlock. Not authoritative; check the current Additional Resources document.",
            "source": "rarity",
        }
    return {
        "status": "restricted",
        "note": f"{rarity.capitalize() if rarity else 'Unknown rarity'} -- typically unavailable in PFS without a specific unlock. Not authoritative; check the current Additional Resources document.",
        "source": "rarity",
    }
