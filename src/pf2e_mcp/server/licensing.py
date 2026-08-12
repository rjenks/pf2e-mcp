"""Best-effort classification of ingested content by which license regime
applies (OGL 1.0a vs. the ORC License, per the `is_remaster` flag already
captured at ingestion), and whether it's likely to be Paizo's excluded
"Product Identity" (OGL) / "Reserved Material" (ORC) -- proper nouns,
deity lore, narrative content -- rather than the generic game mechanics
those licenses freely permit reproducing. See NOTICE.md for the underlying
license terms this is trying to help a caller respect.

Not authoritative. This is a pack-level heuristic, not a per-entry legal
determination -- e.g. a `deities` entry's mechanical grants (a domain's
initial spell, a favored weapon) are genuine game mechanics even though
the same entry's flavor text (a title, dogma, edicts) is squarely Product
Identity. Flag conservatively (whole pack, not per-field), and always say
so rather than imply a clean split exists.
"""

from __future__ import annotations

from typing import Any

# Bestiary/monster/NPC/adventure packs carrying this same content are
# excluded from ingestion entirely (see ingestion/build.py) rather than
# flagged here, since none of it is needed by this project's tools. What's
# listed below is content that *is* ingested (some of it load-bearing for
# character building, e.g. deities for Champion domain selection) but is
# still predominantly proper nouns/narrative rather than generic mechanics.
_PRODUCT_IDENTITY_LIKELY_PACKS = {
    "deities", "boons-and-curses", "pathfinder-society-boons",
}


def content_regime(is_remaster: bool | int | None) -> str:
    """Which license governs an entry's mechanical content, based on the
    ingested `is_remaster` flag (system.publication.remaster). Some
    ingested content has no publication data at all (e.g. the
    `variant-rules` pack, sourced from a GM Core journal page rather than
    a compendium item with its own `system.publication` block) -- degrade
    to 'unknown' rather than silently guess OGL or ORC for those."""
    if is_remaster is None:
        return "unknown"
    return "ORC License" if is_remaster else "OGL 1.0a"


def classify(pack: str, is_remaster: bool | int | None) -> dict[str, Any]:
    return {
        "regime": content_regime(is_remaster),
        "product_identity_likely": pack in _PRODUCT_IDENTITY_LIKELY_PACKS,
    }


def legacy_filter_sql(include_legacy: bool, column: str = "is_remaster") -> str:
    """SQL fragment excluding legacy (pre-Remaster, OGL-licensed) content
    unless `include_legacy` is True. `is_remaster` does NOT mean "retired"
    or "invalid" when false -- just "not (yet) reprinted under ORC": 54% of
    backgrounds and 38% of ancestries are legacy-flagged and still fully
    playable (see KNOWN_ISSUES.md's "Rules data" section for the full story
    on what this flag does and doesn't mean). NULL (no publication data at
    all, e.g. the `variant-rules` pack) always passes through regardless of
    `include_legacy` -- degrade to "show it" rather than guess, same
    principle as `content_regime` above."""
    if include_legacy:
        return ""
    return f" AND ({column} = 1 OR {column} IS NULL)"
