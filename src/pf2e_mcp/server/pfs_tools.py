"""Organized Play tools: adventure lookup and chronicle-log validation.

The lookup half exists because Organized Play players speak in codes. "Grip
played 8-02" is how a session gets recorded, and without an index that string is
opaque -- nothing downstream can say whether the character was in tier, whether
the rewards recorded are the right ones for that kind of adventure, or even
whether the code names a real adventure. See ingestion/pfs_adventures.py for
where the index comes from and why PathfinderWiki rather than Paizo directly.

The validation half is in server/chronicle.py; this module is the thin tool
surface over both.
"""

from __future__ import annotations

from typing import Any

from . import chronicle as ch
from .db import get_connection


def get_adventure(code: str) -> dict[str, Any]:
    """Look up one Pathfinder Society adventure by its code.

    Resolves the shorthand a player actually says -- "8-02", "#8-02", "8-2",
    "PFS 8-02" all work -- into the adventure's title, kind, tier, season and
    Paizo product code, along with the standard rewards a chronicle for it
    should record.

    Codes come in three shapes:
      * Scenarios use Paizo's Society code: "1-01" through the current season,
        plus the evergreen intros "99-1" and "99-2".
      * Quests use "Q<series>-<number>", e.g. "Q1-7" or "Q2-26". The series
        matters: Series 2 quests (#14 and up) award double a Series 1 quest.
      * Bounties use "B<number>", e.g. "B21".

    The `standard_award` block is computed from the adventure's kind, not looked
    up per-adventure: a scenario awards 4 XP, 4 Reputation and 8 downtime days;
    a Series 2 quest 2/2/4; a Series 1 quest 1/1/2; a bounty 1 XP, 1 Reputation
    and no downtime.

    Gold is the exception, and it depends on the adventure's vintage. Older
    scenarios awarded gold in Treasure Bundles, and
    `treasure_bundle_value_by_level` gives what one bundle is worth to a
    character of each level in the tier -- note *character* level, so two
    characters at one table earn different gold from the same scenario. Current
    chronicles instead print a flat gold award per level, which is on the sheet
    rather than derivable, so record what the chronicle says. The Guide does not
    date the changeover, so this makes no claim about which system a given
    adventure used; `bundles_are_legacy` flags that the values are advisory.

    Args:
        code: The adventure code, in any of the forms above.

    Returns the adventure with its metadata and standard awards, or a
    `found: false` result naming the closest matches by name if the code does
    not resolve. Index data is from PathfinderWiki under Paizo's Community Use
    Policy and is only as current as the last ingestion run; award rates follow
    the Guide to Organized Play and individual adventures may legitimately
    deviate.
    """
    conn = get_connection()
    try:
        adventure = ch.lookup_adventure(conn, code)
        if adventure is None:
            near = ch.search_adventures(conn, query=code.strip(), limit=5)
            return {
                "found": False,
                "code": ch.normalize_code(code),
                "message": (
                    f"No adventure matches {code!r}. Scenarios are '8-02', quests "
                    f"'Q2-26', bounties 'B21'. If the adventure is very recent, the "
                    f"index may predate it -- re-run ingestion to refresh."
                ),
                "did_you_mean": [
                    {"code": a["code"], "name": a["name"], "kind": a["kind"]} for a in near
                ],
                "index": ch.index_summary(conn),
            }

        award = ch.standard_award(adventure["kind"], adventure["code"])
        slow = ch.standard_award(adventure["kind"], adventure["code"], slow=True)
        bundles = {
            level: ch.format_currency(ch.treasure_bundle_cp(level) or 0)
            for level in range(adventure["tier_low"], adventure["tier_high"] + 1)
            if ch.treasure_bundle_cp(level)
        }
        return {
            "found": True,
            "adventure": adventure,
            "standard_award": award,
            "slow_advancement_award": slow,
            "treasure_bundle_value_by_level": bundles,
            "typical_bundles": (
                ch.TYPICAL_SCENARIO_BUNDLES if adventure["kind"] == "scenario" else None
            ),
            "bundles_are_legacy": True,
            "caveat": (
                "Metadata from PathfinderWiki (Community Use Policy); award rates "
                "from the Guide to Organized Play. Individual adventures vary "
                "treasure bundle counts and may award bonus Reputation. Treasure "
                "Bundles are the older gold system -- current chronicles print a "
                "flat gold award per level, so take gold from the sheet."
            ),
        }
    finally:
        conn.close()


def find_adventures(
    query: str | None = None,
    kind: str | None = None,
    season: int | None = None,
    level: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search the Pathfinder Society adventure index.

    Use this when the code isn't known -- to find an adventure by title, to list
    a season, or to answer "what can a 4th-level character play?" via `level`,
    which matches adventures whose tier contains that level.

    Args:
        query: Case-insensitive fragment of the adventure's title.
        kind: Restrict to 'scenario', 'quest', or 'bounty'.
        season: Restrict to one scenario season (1-8 and counting). Quests and
            bounties have no season; they are numbered in their own series.
        level: A character level that must fall within the adventure's tier.
        limit: Maximum results (default 25).

    Returns matching adventures ordered by kind, then season and number.
    """
    conn = get_connection()
    try:
        results = ch.search_adventures(
            conn, query=query, kind=kind, season=season, level=level, limit=limit
        )
        return {
            "count": len(results),
            "adventures": results,
            "index": ch.index_summary(conn),
        }
    finally:
        conn.close()


def chronicle_schema() -> dict[str, Any]:
    """Return the JSON Schema for a Pathfinder Society chronicle log.

    A chronicle log records a character's Organized Play history: their
    Organized Play ID and character number, their faction, and every Chronicle
    Sheet earned, in the order applied. It is stored as a
    `characters/<Name>.chronicles.json` sidecar rather than inside the character's
    Pathbuilder JSON, so that export stays a clean, portable Pathbuilder file.

    The shape worth understanding before writing one is that a chronicle is a
    ledger entry, not a snapshot. Every chronicle carries `xp.start` and
    `currency.start`, which must equal the previous chronicle's `end` values;
    the character's current level, wealth and Reputation are what the whole
    chain adds up to. `pfs_validate_chronicle` checks exactly that, and returns
    a `journal`: every movement of money in order, from starting funds through
    each item bought at creation to each adventure's award, with a running
    balance. Itemise purchases in `startingPurchases` and each chronicle's
    `purchases` to get lines in it.

    Call this before authoring or editing a chronicle log by hand.
    """
    return {
        "schema": ch.load_schema(),
        "schema_version": ch.SCHEMA_VERSION,
        "storage": "characters/<Name>.chronicles.json",
        "notes": [
            "Currency is in gold pieces and may be fractional -- 1 sp is 0.1. "
            "Arithmetic is done in integer copper.",
            "`startingLevel` is required reading: PFS characters begin play at "
            "1st, 3rd, 5th or 7th level, always with 0 XP, so XP alone does not "
            "tell you a character's level. Starting funds are 15/75/270/720 gp.",
            "Fame is deliberately absent: it was replaced by Achievement Points "
            "on 31 July 2020, and AcP is account-level, not character-level.",
            "Downtime has no printed box on modern chronicle sheets, but is "
            "recorded here so the running total is derivable.",
        ],
    }


def validate_chronicle(chronicle_log: dict[str, Any]) -> dict[str, Any]:
    """Check a chronicle log and report the totals it implies.

    Two kinds of finding come back. **Errors** are internal contradictions the
    file cannot be right about: a ledger where start + gained does not equal
    end, a chronicle whose starting values do not match the previous one's
    ending values, an adventure code that resolves to nothing, a non-repeatable
    adventure recorded twice without a replay. **Warnings** are deviations from
    the Guide's standard awards -- nonstandard XP, low Reputation, a treasure
    bundle total that does not match the character's level, playing out of tier
    -- which individual adventures are entitled to make, so they are advisory.

    The `derived` block is usually the reason to call this: current total XP and
    the level it implies, currency on hand, Reputation per faction, downtime
    banked, and how many credits were earned running rather than playing.

    Args:
        chronicle_log: A chronicle log object matching the schema returned by
            `pfs_chronicle_schema`.

    Not authoritative -- award rates and Treasure Bundle values are transcribed
    from the Guide to Organized Play. Consult the current Guide and the actual
    chronicle sheets for a real game.
    """
    if not isinstance(chronicle_log, dict) or not chronicle_log:
        raise ValueError("chronicle_log must be a non-empty chronicle log object")
    log = chronicle_log.get("log", chronicle_log)
    conn = get_connection()
    try:
        return ch.validate_chronicle_log(log, conn)
    finally:
        conn.close()


def earn_income(
    level: int,
    proficiency: str = "trained",
    adventure_type: str = "scenario",
    downtime_days: float | None = None,
) -> dict[str, Any]:
    """What one Pathfinder Society Earn Income check pays.

    Downtime in Organized Play works differently from a home game, in ways that
    change both the arithmetic and the strategy:

    - Downtime is granted per Chronicle and **spent when that Chronicle is
      applied, or lost**. It cannot be saved up, so there is no downtime
      "balance" to plan against -- each adventure's days are use-them-now.
    - Downtime is granted at **two days per XP earned**, and spent in **Downtime
      Units of up to 8 days**. You make **one check per unit**, covering the
      whole unit rather than paying per day. A Scenario's 8 days is exactly one
      unit, so one roll; a Series 2 Quest is 4 days and a Series 1 Quest 2, each
      still one unit and one roll. Anything granting more than 8 days splits
      into several units and therefore several checks -- an Adventure Path
      volume is 12 XP, so 24 days, so three units and three rolls.
    - Splitting a unit between activities does not add a check: 7 days
      retraining plus 1 day Earning Income is still one Earn Income check, and
      still pays the unit's amount.
    - The skill must be **Crafting, Performance, or a Lore** -- not whatever the
      character is best at. Some feats and boons (Bargain Hunter, for instance)
      open other skills or higher-level tasks.
    - The default **Task Level is the character's level - 2**, already folded
      into the table, so look up by character level.
    - A **critical success** treats the character as one level higher (minimum
      3); a **critical failure** earns nothing.

    Args:
        level: The character's level.
        proficiency: Rank in the Earn Income skill -- 'trained', 'expert',
            'master' or 'legendary'. Below 9th level the table has no master
            column, so master and legendary pay the expert amount.
        adventure_type: 'scenario' (8 days), 'quest2' (4), 'quest' (2), or
            'bounty' (no downtime). Ignored when downtime_days is given.
        downtime_days: Total downtime granted, for an adventure that is not one
            of the standard types -- an Adventure Path volume, or anything whose
            sanctioning document sets its own. Overrides adventure_type and
            splits into units for you.

    Returns the DC and the value of each degree of success. Levels 1-16 are
    covered; the Guide prints a 17th row only as the critical-success result for
    a 16th-level character.
    """
    if downtime_days is None:
        total = ch.DOWNTIME_UNIT_DAYS.get(adventure_type)
        if total is None:
            raise ValueError(
                f"adventure_type must be one of {sorted(ch.DOWNTIME_UNIT_DAYS)}, "
                f"got {adventure_type!r}"
            )
    else:
        total = downtime_days
    if total <= 0:
        return {
            "adventure_type": adventure_type,
            "downtime_days": 0,
            "checks": 0,
            "message": (
                "No downtime, so no Earn Income check. Bounties grant none -- a "
                "Bounty is itself something a character does during downtime."
            ),
        }

    units = ch.downtime_units(total)
    per_unit = [ch.earn_income(level, proficiency, u) for u in units]
    if any(r is None for r in per_unit):
        raise ValueError(f"no Earn Income row for level {level}; the table covers 1-16")

    result = dict(per_unit[0])
    result["adventure_type"] = adventure_type if downtime_days is None else "custom"
    result["downtime_days"] = total
    result["units"] = [u for u in units]
    result["checks"] = len(units)
    if len(units) > 1:
        result["per_unit"] = per_unit
        result["all_units_success"] = ch.format_currency(
            sum(r["success_cp"] for r in per_unit)
        )
    result["skills"] = "Crafting, Performance, or a Lore skill"
    result["caveat"] = (
        "From the Guide's PFS-modified Earn Income table (Task Level = character "
        "level - 2, one check per Downtime Unit), not the Player Core table. "
        "Feats and boons can change the eligible skills or the task level."
    )
    return result
