"""Pathfinder Society chronicle logs: the schema, the arithmetic, the checks.

A Chronicle Sheet is what an Organized Play player walks away from a session
with, and the thing worth understanding about it is that it is a **ledger, not
a receipt**. Each sheet records starting XP and starting gold, adds what the
session awarded, and prints the new totals -- which the next sheet then takes as
*its* starting values. A character's current level, wealth and Reputation are
therefore only correct if the whole chain is applied in order, and a single
transcription slip halfway down a stack of a dozen PDFs quietly corrupts
everything after it. That chain is what this module models and checks.

Where the numbers come from
---------------------------
Almost nothing about a chronicle's rewards is per-adventure data to be looked
up; it is computed from two things:

- **Adventure type** fixes XP, Reputation and downtime (see `ADVENTURE_AWARDS`).
  A scenario is 4/4/8 whatever else is true about it.
- **Character level** fixes what a Treasure Bundle is worth (`TREASURE_BUNDLE_CP`).
  Note *character* level, not adventure tier: two characters at the same table
  playing the same scenario earn different gold.

So the adventure index (`pfs_adventures`, see ingestion/pfs_adventures.py) only
has to supply the kind and the tier; everything else follows arithmetically.

Money is handled in copper
--------------------------
Chronicle sheets print gold with a decimal part -- a 1st-level Treasure Bundle
is 1 gp 4 sp, Earn Income routinely pays out in silver -- and the JSON stores gp
as a plain number because that is what a person hand-editing the file expects to
type. All arithmetic converts to integer copper first. Validating a ledger means
asserting exact equality across a chain of a dozen additions, and floating-point
gold fails that for reasons that have nothing to do with the player's
bookkeeping.

What is deliberately absent
---------------------------
**Fame.** It was replaced by Achievement Points on 31 July 2020, and AcP is
account-level, not character-level -- it belongs to the player, not to any one
character, and is spent through Paizo's own system. A chronicle sheet with a
Fame box is a pre-Year-2 printing and the box is meant to be crossed off.

Nothing here is authoritative. The award rates and bundle values are transcribed
from the Guide to Organized Play, and scenarios do legitimately deviate --
varying bundle counts, awarding bonus Reputation for a faction objective. Checks
that fail a "standard" comparison are reported as advisories, not errors; only
internal contradictions (a ledger that does not add up) are reported as errors.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).parent / "chronicle_schema.json"

#: Current `schemaVersion` of a chronicle log file.
SCHEMA_VERSION = 2

#: XP per character level. A character levels the moment it crosses a multiple
#: of this, and the Guide requires the level be taken immediately.
XP_PER_LEVEL = 12

#: Standard awards by adventure kind: (XP, Reputation, downtime days).
#: Quests split by series -- Series 2 (#14 and up) doubled the Series 1 award.
ADVENTURE_AWARDS: dict[str, tuple[int, int, int]] = {
    "scenario": (4, 4, 8),
    "quest": (1, 1, 2),
    "quest2": (2, 2, 4),
    "bounty": (1, 1, 0),
}

#: Value of one Treasure Bundle at each character level, in copper pieces.
#: Transcribed from the Guide's Treasure Bundles table; a scenario is normally
#: 8 bundles, so level 1's 140 cp per bundle is the familiar 11 gp 2 sp total.
#:
#: **This is the older system.** The Guide now describes Treasure Bundles as
#: what "older Pathfinder Society Scenarios used", reproduced for reference;
#: current chronicles print a flat gold award per level instead. The Guide does
#: not name the season the change landed in, so no cutoff is hard-coded here --
#: instead, bundle checks only run when a chronicle actually records a
#: `treasureBundles` count, which is exactly when the paper sheet had the boxes.
TREASURE_BUNDLE_CP: dict[int, int] = {
    1: 140, 2: 220, 3: 380, 4: 640, 5: 1000,
    6: 1500, 7: 2200, 8: 3000, 9: 4400, 10: 6000,
    11: 8600, 12: 12400, 13: 18800, 14: 27400, 15: 40800,
    16: 62000, 17: 96000, 18: 156000, 19: 266000, 20: 368000,
}

#: A scenario's default bundle count. Individual scenarios vary this, so it is
#: only ever used to *compare against* a recorded value, never to supply one.
TYPICAL_SCENARIO_BUNDLES = 8

#: The six factions a character can represent. Reputation is tracked per
#: faction, and an adventure tagged for a faction can award a second block of
#: Reputation on top of the one the player's own faction earns.
FACTIONS = (
    "Envoy's Alliance",
    "Grand Archive",
    "Horizon Hunters",
    "Radiant Oath",
    "Vigilant Seal",
    "Verdant Wheel",
)

#: Where Reputation goes when the player represented no faction.
DEFAULT_FACTION = "Horizon Hunters"

#: Starting funds in copper for a character created above 1st level, from the
#: Guide's character-creation table. These are the "credits-only" lump sums; a
#: player may instead take permanent items of set levels plus a smaller purse
#: (25 gp at 3rd, 50 gp at 5th, 125 gp at 7th), in which case the purse is what
#: goes in the ledger and the items are recorded as granted rather than bought.
#:
#: The levels are not arbitrary: PFS lets a character be created at 1st, 3rd,
#: 5th or 7th and nothing else.
STARTING_FUNDS_CP: dict[int, int] = {1: 1500, 3: 7500, 5: 27000, 7: 72000}

#: Levels a PFS character may be created at.
STARTING_LEVELS = tuple(STARTING_FUNDS_CP)


def load_schema() -> dict[str, Any]:
    """The JSON Schema describing a chronicle log file."""
    return json.loads(SCHEMA_PATH.read_text())


# ---------------------------------------------------------------- money


def to_cp(gp: float | None) -> int:
    """Convert a gold value as written on a chronicle sheet into copper.

    Rounds to the nearest copper: sheets are never denominated finer than that,
    so a fractional copper is a typo or a float artefact either way.
    """
    if gp is None:
        return 0
    return round(float(gp) * 100)


def to_gp(cp: int) -> float:
    """Copper back to gold, at chronicle-sheet precision (2 decimal places)."""
    return round(cp / 100, 2)


def format_currency(cp: int) -> str:
    """Render copper the way a chronicle sheet writes it: '11 gp, 2 sp'."""
    sign = "-" if cp < 0 else ""
    cp = abs(cp)
    gold, rest = divmod(cp, 100)
    silver, copper = divmod(rest, 10)
    parts = []
    if gold or not rest:
        parts.append(f"{gold} gp")
    if silver:
        parts.append(f"{silver} sp")
    if copper:
        parts.append(f"{copper} cp")
    return sign + ", ".join(parts)


# ---------------------------------------------------------------- awards


def halve(value: int) -> float:
    """Slow advancement halves rewards 'without rounding' (Guide's wording),
    so a scenario's 4 XP becomes a genuine 2, but a bounty's 1 XP becomes 0.5
    and is carried as a half."""
    return value / 2


def standard_award(kind: str, code: str | None = None, slow: bool = False) -> dict[str, Any]:
    """Standard XP / Reputation / downtime for one adventure.

    `code` is only consulted to tell the two quest series apart -- a Series 2
    quest (coded `Q2-*` here) awards double a Series 1 quest.
    """
    key = kind
    if kind == "quest" and code and code.upper().startswith("Q2"):
        key = "quest2"
    xp, reputation, downtime = ADVENTURE_AWARDS.get(key, ADVENTURE_AWARDS["scenario"])
    if slow:
        return {
            "xp": halve(xp),
            "reputation": halve(reputation),
            "downtime_days": halve(downtime),
            "advancement": "slow",
        }
    return {
        "xp": xp,
        "reputation": reputation,
        "downtime_days": downtime,
        "advancement": "standard",
    }


def treasure_bundle_cp(level: int) -> int | None:
    """Copper value of one Treasure Bundle for a character of this level."""
    return TREASURE_BUNDLE_CP.get(level)


def level_for_xp(total_xp: float, starting_level: int = 1) -> int:
    """Character level from XP earned, counting up from the starting level.

    The subtlety is that XP does not encode the level. PFS characters "begin
    play with 0 XP" *regardless of their starting level* -- a character created
    at 3rd level starts at 0 XP and reaches 4th at 12, exactly like a 1st-level
    character reaches 2nd. So the starting level is a separate input, not
    something derivable from the total.

    Capped at 20: PFS characters retire at 20th and stop accruing levels, and
    an uncapped formula would silently report a level that cannot exist.
    """
    return min(20, starting_level + int(total_xp // XP_PER_LEVEL))


# ---------------------------------------------------------------- lookup


_ADVENTURE_COLUMNS = (
    "code, name, kind, full_title, season, number, tier_low, tier_high, series, "
    "tags, factions, metaplot, location, author, sanctioned, pubcode, "
    "release_date, wiki_page"
)


_SCENARIO_IN_TEXT_RE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})")
# Quests need three separate patterns rather than one permissive alternation,
# because a bare "Q26" is genuinely ambiguous: read greedily it looks like
# series 2 quest #6. The series is only accepted when it is unmistakable --
# spelled out, or separated by the canonical dash.
_QUEST_CANONICAL_RE = re.compile(r"^Q(\d)-(\d{1,2})$")
_QUEST_SERIES_RE = re.compile(r"^Q(?:UEST)?\s*\(?SERIES\s*(\d)\)?\s*#?\s*(\d{1,2})$")
_QUEST_BARE_RE = re.compile(r"^Q(?:UEST)?\s*#?\s*(\d{1,2})$")
_BOUNTY_IN_TEXT_RE = re.compile(r"^B(?:OUNTY)?\s*#?\s*(\d{1,3})$")


def normalize_code(code: str) -> str:
    """Accept the many ways a player writes an adventure code.

    The whole point of this lookup is to take what someone says out loud, so it
    accepts '#8-02', '8-2', 'PFS 8-02', '8‑02' with a Unicode hyphen, 'Bounty
    21', 'quest 26' and 'b21', and it tolerates a title trailing the code
    ('8-02 The Fey Reclamation') because that is how chronicle sheets print it.

    Quests are the awkward case: their numbering runs continuously across both
    series (Series 1 is #1-13, Series 2 is #14-27) but the series determines the
    XP award, so the canonical code carries it -- 'Q2-26'. A bare 'Quest 26'
    normalises to the partial form 'Q?-26', which `lookup_adventure` resolves
    against the index rather than guessing here.
    """
    text = " ".join(code.strip().upper().split())
    for dash in ("‐", "‑", "‒", "–", "—", "−"):
        text = text.replace(dash, "-")
    for prefix in ("PFS2", "PFS", "SCENARIO", "#"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    text = text.lstrip("#").strip()

    bounty = _BOUNTY_IN_TEXT_RE.match(text)
    if bounty:
        return f"B{int(bounty.group(1))}"

    for pattern in (_QUEST_CANONICAL_RE, _QUEST_SERIES_RE):
        quest = pattern.match(text)
        if quest:
            return f"Q{quest.group(1)}-{int(quest.group(2))}"
    bare = _QUEST_BARE_RE.match(text)
    if bare:
        return f"Q?-{int(bare.group(1))}"

    # The evergreen intros are numbered 99-1 and 99-2, with no zero padding --
    # they are not a real season, so the season rule below does not apply.
    if text.startswith("99-"):
        tail = text[3:].strip()
        if tail.isdigit():
            return f"99-{int(tail)}"

    # A scenario code anywhere in the string, so a pasted full title works.
    scenario = _SCENARIO_IN_TEXT_RE.search(text)
    if scenario:
        # Zero-pad the sequence number: players write 8-2 for 8-02.
        return f"{int(scenario.group(1))}-{int(scenario.group(2)):02d}"
    return text


def _row_to_adventure(row: sqlite3.Row) -> dict[str, Any]:
    adventure = dict(row)
    for key in ("tags", "factions", "metaplot"):
        adventure[key] = json.loads(adventure[key] or "[]")
    adventure["sanctioned"] = bool(adventure["sanctioned"])
    adventure["repeatable"] = "Repeatable" in adventure["tags"]
    adventure["tier"] = (
        f"{adventure['tier_low']}-{adventure['tier_high']}"
        if adventure["tier_low"] != adventure["tier_high"]
        else str(adventure["tier_low"])
    )
    return adventure


def lookup_adventure(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    """Resolve one adventure code.

    Handles the partial quest form `Q?-<n>` that `normalize_code` produces for a
    bare "Quest 26": quest numbers are unique across both series, so the index
    itself settles which series it belongs to.
    """
    normalized = normalize_code(code)
    if normalized.startswith("Q?-"):
        row = conn.execute(
            f"SELECT {_ADVENTURE_COLUMNS} FROM pfs_adventures "
            "WHERE kind = 'quest' AND code LIKE ?",
            (f"Q_-{normalized[3:]}",),
        ).fetchone()
        return _row_to_adventure(row) if row else None
    row = conn.execute(
        f"SELECT {_ADVENTURE_COLUMNS} FROM pfs_adventures WHERE code = ?",
        (normalized,),
    ).fetchone()
    return _row_to_adventure(row) if row else None


def search_adventures(
    conn: sqlite3.Connection,
    query: str | None = None,
    kind: str | None = None,
    season: int | None = None,
    level: int | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Find adventures by name fragment, kind, season, or a character level
    that must fall inside the tier."""
    clauses, params = [], []
    if query:
        clauses.append("(name LIKE ? OR full_title LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if season is not None:
        clauses.append("season = ?")
        params.append(season)
    if level is not None:
        clauses.append("tier_low <= ? AND tier_high >= ?")
        params += [level, level]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT {_ADVENTURE_COLUMNS} FROM pfs_adventures {where} "
        "ORDER BY kind, season, number, code LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [_row_to_adventure(r) for r in rows]


def index_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Coverage of the adventure index, for tool output and diagnostics."""
    rows = conn.execute(
        "SELECT kind, COUNT(*) n, MAX(release_date) latest FROM pfs_adventures GROUP BY kind"
    ).fetchall()
    seasons = conn.execute(
        "SELECT MIN(season), MAX(season) FROM pfs_adventures WHERE season IS NOT NULL "
        "AND season < 90"
    ).fetchone()
    return {
        "counts": {r["kind"]: r["n"] for r in rows},
        "total": sum(r["n"] for r in rows),
        "latest_release": max((r["latest"] for r in rows if r["latest"]), default=None),
        "seasons": (
            f"{seasons[0]}-{seasons[1]}" if seasons and seasons[0] is not None else None
        ),
        "source": "PathfinderWiki Facts namespace (Community Use Policy)",
    }


# ---------------------------------------------------------------- validation


def _issue(level: str, code: str, message: str, chronicle: int | None = None) -> dict[str, Any]:
    """One finding. `level` is 'error' for an internal contradiction the file
    cannot be right about (a ledger that does not add up, an unknown adventure)
    and 'warning' for a deviation from the standard that a specific adventure is
    allowed to make."""
    return {
        "level": level,
        "code": code,
        "message": message,
        "chronicle": chronicle,
    }


def _check_ledger_arithmetic(entry: dict[str, Any], index: int) -> list[dict[str, Any]]:
    """start + gained == end, for both ledgers, in integer copper."""
    issues = []
    xp = entry.get("xp") or {}
    start, gained, end = xp.get("start", 0), xp.get("gained", 0), xp.get("end", 0)
    if start + gained != end:
        issues.append(_issue(
            "error", "xp_does_not_add_up",
            f"XP ledger does not add up: {start} + {gained} = {start + gained}, "
            f"but end is recorded as {end}.",
            index,
        ))

    money = entry.get("currency") or {}
    start_cp = to_cp(money.get("start"))
    gained_cp = to_cp(money.get("gained"))
    spent_cp = to_cp(money.get("spent"))
    end_cp = to_cp(money.get("end"))
    if start_cp + gained_cp - spent_cp != end_cp:
        issues.append(_issue(
            "error", "currency_does_not_add_up",
            f"Currency ledger does not add up: {format_currency(start_cp)} + "
            f"{format_currency(gained_cp)} - {format_currency(spent_cp)} = "
            f"{format_currency(start_cp + gained_cp - spent_cp)}, but end is "
            f"recorded as {format_currency(end_cp)}.",
            index,
        ))

    # Itemised purchases are the journal's raw material, so they have to agree
    # with the lump `spent` the ledger reports. Under-itemising is fine (the
    # journal shows the remainder as an unlabelled line); claiming to have
    # bought more than was spent is not.
    itemized_cp = purchases_cp(entry.get("purchases"))
    if entry.get("purchases") and itemized_cp > to_cp(money.get("spent")):
        issues.append(_issue(
            "error", "purchases_exceed_spent",
            f"Itemised purchases come to {format_currency(itemized_cp)}, more than "
            f"the {format_currency(to_cp(money.get('spent')))} recorded as spent.",
            index,
        ))

    # The named components of `gained` should not exceed it. They can be less --
    # a chronicle can award gold that is neither treasure nor income -- so only
    # an overshoot is a contradiction.
    components_cp = to_cp(money.get("treasureBundleValue")) + to_cp(money.get("incomeEarned"))
    if components_cp > gained_cp:
        issues.append(_issue(
            "error", "currency_components_exceed_total",
            f"Treasure bundles plus income come to {format_currency(components_cp)}, "
            f"more than the {format_currency(gained_cp)} recorded as gained.",
            index,
        ))
    return issues


def _check_against_adventure(
    entry: dict[str, Any], adventure: dict[str, Any] | None, index: int, slow: bool
) -> list[dict[str, Any]]:
    """Compare a chronicle against what its adventure should have awarded."""
    issues = []
    code = entry.get("adventure", "?")
    if adventure is None:
        issues.append(_issue(
            "error", "unknown_adventure",
            f"No adventure in the index matches code {code!r}. Check the code, or "
            "the index may predate the adventure -- re-run ingestion to refresh it.",
            index,
        ))
        return issues

    recorded_name = entry.get("adventureName")
    if recorded_name and recorded_name.strip().lower() != adventure["name"].lower():
        issues.append(_issue(
            "warning", "adventure_name_mismatch",
            f"Recorded as {recorded_name!r}, but {code} is {adventure['name']!r}.",
            index,
        ))

    level = entry.get("characterLevel")
    if level is not None and not (adventure["tier_low"] <= level <= adventure["tier_high"]):
        issues.append(_issue(
            "warning", "out_of_tier",
            f"Played at level {level}, but {code} is tier {adventure['tier']}.",
            index,
        ))

    award = standard_award(adventure["kind"], adventure["code"], slow=slow)

    gained_xp = (entry.get("xp") or {}).get("gained")
    if gained_xp is not None and gained_xp != award["xp"]:
        issues.append(_issue(
            "warning", "nonstandard_xp",
            f"Awarded {gained_xp} XP; a {adventure['kind']} on "
            f"{award['advancement']} advancement awards {award['xp']}.",
            index,
        ))

    total_reputation = sum(r.get("amount", 0) for r in entry.get("reputation") or [])
    if total_reputation < award["reputation"]:
        issues.append(_issue(
            "warning", "low_reputation",
            f"Recorded {total_reputation} Reputation; a {adventure['kind']} awards at "
            f"least {award['reputation']}"
            + (f" (plus a bonus block for {', '.join(adventure['factions'])})"
               if adventure["factions"] else "")
            + ".",
            index,
        ))

    downtime = entry.get("downtimeDays")
    if downtime is not None and downtime != award["downtime_days"]:
        issues.append(_issue(
            "warning", "nonstandard_downtime",
            f"Recorded {downtime} downtime days; a {adventure['kind']} grants "
            f"{award['downtime_days']}.",
            index,
        ))

    # Treasure bundles: value must match count x the per-level rate exactly.
    # This is the check that catches a chronicle applied at the wrong level.
    money = entry.get("currency") or {}
    bundles, level = money.get("treasureBundles"), entry.get("characterLevel")
    if bundles is not None and level is not None:
        per_bundle = treasure_bundle_cp(level)
        if per_bundle is None:
            issues.append(_issue(
                "error", "level_out_of_range",
                f"Level {level} has no Treasure Bundle value; levels run 1-20.",
                index,
            ))
        else:
            expected_cp = round(per_bundle * bundles / (2 if slow else 1))
            recorded_cp = to_cp(money.get("treasureBundleValue"))
            if money.get("treasureBundleValue") is not None and recorded_cp != expected_cp:
                issues.append(_issue(
                    "warning", "treasure_bundle_mismatch",
                    f"{bundles} bundles at level {level} come to "
                    f"{format_currency(expected_cp)}, but "
                    f"{format_currency(recorded_cp)} is recorded.",
                    index,
                ))
    return issues


def validate_chronicle_log(
    log: dict[str, Any], conn: sqlite3.Connection
) -> dict[str, Any]:
    """Check a chronicle log for internal contradictions and deviations.

    Returns both the findings and the derived state the log implies -- current
    XP and level, gold on hand, Reputation per faction, downtime banked -- since
    the reason to validate a ledger is almost always to trust its totals.
    """
    issues: list[dict[str, Any]] = []

    version = log.get("schemaVersion")
    if version != SCHEMA_VERSION:
        issues.append(_issue(
            "warning", "schema_version",
            f"File declares schemaVersion {version!r}; this server writes and "
            f"validates version {SCHEMA_VERSION}.",
        ))

    slow = log.get("advancement", "standard") == "slow"
    entries = log.get("chronicles") or []

    resolved: list[dict[str, Any] | None] = []
    for i, entry in enumerate(entries):
        adventure = lookup_adventure(conn, entry.get("adventure", ""))
        resolved.append(adventure)
        issues += _check_ledger_arithmetic(entry, i)
        issues += _check_against_adventure(entry, adventure, i, slow)

    # The chain: each chronicle picks up where the last left off. The opening
    # balance is starting funds less whatever was spent equipping the character,
    # so the first chronicle's `currency.start` is checked against money actually
    # on hand rather than against the gross purse.
    starting_level = int(log.get("startingLevel") or 1)
    if starting_level not in STARTING_LEVELS:
        issues.append(_issue(
            "warning", "unusual_starting_level",
            f"Created at level {starting_level}; Pathfinder Society characters "
            f"begin play at 1st, 3rd, 5th or 7th level only.",
        ))
    opening = log.get("startingCurrency")
    opening_cp = (
        to_cp(opening) if opening is not None
        else STARTING_FUNDS_CP.get(starting_level, STARTING_FUNDS_CP[1])
    )
    expected_opening = STARTING_FUNDS_CP.get(starting_level)
    if expected_opening is not None and opening is not None and opening_cp != expected_opening:
        issues.append(_issue(
            "warning", "nonstandard_starting_funds",
            f"Opened with {format_currency(opening_cp)}; the credits-only starting "
            f"funds for a level-{starting_level} character are "
            f"{format_currency(expected_opening)}. Taking the permanent-items "
            f"option instead leaves a smaller purse, which is a legitimate reason "
            f"to differ.",
        ))

    creation_spend = purchases_cp(log.get("startingPurchases"))
    if creation_spend > opening_cp:
        issues.append(_issue(
            "error", "creation_overspend",
            f"Starting equipment costs {format_currency(creation_spend)}, more than "
            f"the {format_currency(opening_cp)} available at character creation.",
        ))

    previous_xp = 0
    previous_cp = opening_cp - creation_spend
    for i, entry in enumerate(entries):
        xp, money = entry.get("xp") or {}, entry.get("currency") or {}
        if xp.get("start", 0) != previous_xp:
            issues.append(_issue(
                "error", "xp_chain_broken",
                f"Starts at {xp.get('start', 0)} XP, but the previous chronicle "
                f"ended at {previous_xp}.",
                i,
            ))
        if to_cp(money.get("start")) != previous_cp:
            issues.append(_issue(
                "error", "currency_chain_broken",
                f"Starts with {format_currency(to_cp(money.get('start')))}, but the "
                f"previous chronicle ended with {format_currency(previous_cp)}.",
                i,
            ))
        previous_xp = xp.get("end", previous_xp)
        previous_cp = to_cp(money.get("end")) if money.get("end") is not None else previous_cp

    # Replays. A non-repeatable adventure appearing twice needs an explanation,
    # and GM credit for something already played is itself a replay.
    seen: dict[str, int] = {}
    for i, (entry, adventure) in enumerate(zip(entries, resolved)):
        code = normalize_code(entry.get("adventure", ""))
        if code in seen and not entry.get("replay"):
            repeatable = adventure["repeatable"] if adventure else False
            issues.append(_issue(
                "warning" if repeatable else "error",
                "duplicate_adventure",
                f"{code} also appears as chronicle {seen[code] + 1}"
                + (" and is Repeatable, so mark this one `replay: true`."
                   if repeatable
                   else ", is not tagged Repeatable, and is not marked as a replay -- "
                        "this needs a replay boon."),
                i,
            ))
        seen.setdefault(code, i)

    # Level consistency: a character must take a level the moment it earns one,
    # so the level recorded on a later chronicle has to keep up with its XP.
    running_xp = 0
    for i, entry in enumerate(entries):
        expected = level_for_xp(running_xp, starting_level)
        recorded = entry.get("characterLevel")
        if recorded is not None and recorded != expected:
            issues.append(_issue(
                "warning", "level_mismatch",
                f"Played at level {recorded}, but {running_xp} XP at that point "
                f"means level {expected}.",
                i,
            ))
        running_xp = (entry.get("xp") or {}).get("end", running_xp)

    reputation: dict[str, float] = {}
    # Downtime is deliberately NOT summed. The Guide is explicit that it "is
    # spent after each Chronicle is applied, or it is lost" and "cannot be saved
    # up (accrued)" -- so a running total is not a resource the character has,
    # it is a number with no referent. What is meaningful is the size of each
    # Downtime Unit, since that is what one Earn Income check covers.
    downtime_granted = 0.0
    for entry in entries:
        for award in entry.get("reputation") or []:
            faction = award.get("faction") or log.get("faction") or DEFAULT_FACTION
            reputation[faction] = reputation.get(faction, 0) + award.get("amount", 0)
        downtime_granted += entry.get("downtimeDays") or 0
    last_downtime = (entries[-1].get("downtimeDays") if entries else None)
    income_cp = sum(to_cp((e.get("currency") or {}).get("incomeEarned")) for e in entries)

    journal = build_journal(log, conn)
    if journal and journal[-1]["balance_cp"] != previous_cp:
        issues.append(_issue(
            "error", "journal_disagrees_with_ledger",
            f"The gold journal ends at {journal[-1]['balance']}, but the chronicle "
            f"ledger ends at {format_currency(previous_cp)}.",
        ))

    errors = [i for i in issues if i["level"] == "error"]
    level = level_for_xp(previous_xp, starting_level)
    return {
        "valid": not errors,
        "issues": issues,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "journal": journal,
        "derived": {
            "chronicles": len(entries),
            "starting_level": starting_level,
            "total_xp": previous_xp,
            "level": level,
            "xp_to_next_level": (
                None if level >= 20
                else XP_PER_LEVEL - (previous_xp % XP_PER_LEVEL)
            ),
            "currency_gp": to_gp(previous_cp),
            "currency": format_currency(previous_cp),
            "reputation": dict(sorted(reputation.items())),
            "downtime_days_granted": downtime_granted,
            "downtime_days_last_unit": last_downtime,
            "downtime_note": (
                "Downtime is spent when its Chronicle is applied, or lost -- it "
                "cannot be accrued, so the granted total is a history, not a "
                "balance."
            ),
            "income_earned": format_currency(income_cp),
            "advancement": "slow" if slow else "standard",
            "gm_credits": sum(1 for e in entries if e.get("playedAs") == "gm"),
        },
        "caveat": (
            "Award rates and Treasure Bundle values are transcribed from the Guide to "
            "Organized Play; individual adventures legitimately vary bundle counts and "
            "award bonus Reputation, so warnings flag deviations rather than mistakes. "
            "Errors mark internal contradictions the file cannot be right about."
        ),
    }


# ---------------------------------------------------------------- gold journal


def _purchase_cp(purchase: dict[str, Any]) -> int:
    """Cost of one purchase line in copper.

    `price` is the line total when given. Otherwise it is `unitPrice` times
    `quantity`, which is how gear is usually written down -- "torch x5" is one
    line, not five.
    """
    if purchase.get("price") is not None:
        return to_cp(purchase["price"])
    return to_cp(purchase.get("unitPrice")) * int(purchase.get("quantity") or 1)


def purchases_cp(purchases: list[dict[str, Any]] | None) -> int:
    return sum(_purchase_cp(p) for p in purchases or [])


def build_journal(log: dict[str, Any], conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every movement of money, in order, with a running balance.

    This is the document a player actually audits against: a chronicle's
    `currency` block says what a session did, but it cannot answer "where did my
    money go", because a stack of chronicles hides every purchase inside a
    single `spent` figure. The journal flattens all of it into one column of
    transactions -- opening funds, each item bought at character creation, each
    adventure's award, each purchase made against a chronicle's item access --
    so the balance can be checked line by line.

    Each row carries `in_cp`/`out_cp` and the `balance_cp` after it. Rows are
    derived, never stored; the log holds the facts and this composes them.
    """
    rows: list[dict[str, Any]] = []
    balance = 0

    def add(kind: str, description: str, delta: int, **extra: Any) -> None:
        nonlocal balance
        balance += delta
        rows.append({
            "kind": kind,
            "description": description,
            "in_cp": max(0, delta),
            "out_cp": max(0, -delta),
            "balance_cp": balance,
            "balance": format_currency(balance),
            **extra,
        })

    starting_level = int(log.get("startingLevel") or 1)
    opening = log.get("startingCurrency")
    opening_cp = (
        to_cp(opening) if opening is not None
        else STARTING_FUNDS_CP.get(starting_level, STARTING_FUNDS_CP[1])
    )
    add(
        "opening",
        f"Character created at level {starting_level} — Pathfinder Society starting funds",
        opening_cp,
        level=starting_level,
    )

    for purchase in log.get("startingPurchases") or []:
        add(
            "purchase",
            _purchase_label(purchase),
            -_purchase_cp(purchase),
            level=starting_level,
            note=purchase.get("notes"),
        )

    for i, entry in enumerate(log.get("chronicles") or []):
        adventure = lookup_adventure(conn, entry.get("adventure", ""))
        code = normalize_code(entry.get("adventure", ""))
        name = (adventure or {}).get("name") or entry.get("adventureName") or "Unknown adventure"
        money = entry.get("currency") or {}
        gained_cp = to_cp(money.get("gained"))
        # Earn Income is its own transaction, not part of the adventure's award --
        # it is a downtime activity the player rolled for, and burying it inside
        # the award line hides both the roll and the fact that it happened.
        income_cp = to_cp(money.get("incomeEarned"))
        award_cp = gained_cp - income_cp
        if award_cp:
            label = f"{code} {name}"
            if entry.get("playedAs") == "gm":
                label += " (GM credit)"
            add(
                "award", label, award_cp,
                chronicle=i, date=entry.get("date"),
                level=entry.get("characterLevel"),
            )
        if income_cp:
            add(
                "income", f"Earn Income ({code} downtime)", income_cp,
                chronicle=i, date=entry.get("date"),
                level=entry.get("characterLevel"),
                note=entry.get("earnIncomeNote"),
            )

        itemized = entry.get("purchases") or []
        for purchase in itemized:
            add(
                "purchase", _purchase_label(purchase), -_purchase_cp(purchase),
                chronicle=i, date=entry.get("date"),
                level=entry.get("characterLevel"), note=purchase.get("notes"),
            )
        # A chronicle may record only a lump `spent` with no itemisation. Show
        # the remainder so the journal still reconciles to the ledger.
        remainder = to_cp(money.get("spent")) - purchases_cp(itemized)
        if remainder:
            add(
                "purchase",
                f"Spent against {code}" if itemized else f"Purchases against {code}",
                -remainder,
                chronicle=i, date=entry.get("date"),
                level=entry.get("characterLevel"),
            )

    return rows


def _purchase_label(purchase: dict[str, Any]) -> str:
    quantity = purchase.get("quantity")
    item = purchase.get("item") or "Purchase"
    return f"{item} ×{quantity}" if quantity and quantity != 1 else item


# ---------------------------------------------------------------- earn income


#: The Guide's Earn Income table for an 8-day Downtime Unit (a Scenario), in
#: copper: level -> (DC, failure, trained, expert, master). Master only appears
#: from 9th; below that the column doesn't exist and expert is the ceiling.
#:
#: This is *not* the Player Core table. PFS modifies it in two ways that matter:
#: the default Task Level is the character's level - 2, and one check covers the
#: whole Downtime Unit rather than paying per day. Level 17 is the last row the
#: Guide prints, and it exists only as the critical-success result for a
#: 16th-level character.
_EARN_INCOME_8_DAY: dict[int, tuple[int, int, int, int, int | None]] = {
    1:  (14,    8,    40,    40, None),
    2:  (14,    8,    40,    40, None),
    3:  (15,   16,   160,   160, None),
    4:  (16,   32,   240,   240, None),
    5:  (18,   64,   400,   400, None),
    6:  (19,   80,   560,   640, None),
    7:  (20,  160,   720,   800, None),
    8:  (22,  240,  1200,  1600, None),
    9:  (23,  320,  1600,  2000,  2000),
    10: (24,  400,  2000,  2400,  2400),
    11: (26,  480,  2400,  3200,  3200),
    12: (27,  560,  3200,  4000,  4800),
    13: (28,  640,  4000,  4800,  6400),
    14: (30,  720,  4800,  6400,  8000),
    15: (31,  800,  5600,  8000, 12000),
    16: (32, 1200,  6400, 12000, 16000),
    17: (32, 1200,  8000, 16000, 22400),
}

#: Downtime granted per adventure type. The underlying rule is "two days of
#: Downtime per XP earned", so these follow from the XP awards rather than being
#: independent facts -- which matters for anything granting more XP than a
#: Scenario (an Adventure Path volume is 12 XP, so 24 days). Bounties grant none
#: at all, because a Bounty is itself something you do during downtime.
DOWNTIME_UNIT_DAYS = {"scenario": 8, "quest2": 4, "quest": 2, "bounty": 0}

#: A Downtime Unit is at most this many days, and one Earn Income check covers
#: one unit.
MAX_UNIT_DAYS = 8


def downtime_days_for_xp(xp: float) -> float:
    """Two days of downtime per XP earned."""
    return xp * 2


def downtime_units(days: float) -> list[float]:
    """Split a downtime award into the units it is actually spent in.

    Eight days or fewer is a single unit. More than that is spent as 8-day units
    "one at a time, until 8 or fewer days remain, then... the remaining days as
    a single unit" -- so a 24-day award is three units and therefore three Earn
    Income checks, not one check for 24 days' worth of income.
    """
    if days <= 0:
        return []
    units = []
    remaining = float(days)
    while remaining > MAX_UNIT_DAYS:
        units.append(float(MAX_UNIT_DAYS))
        remaining -= MAX_UNIT_DAYS
    units.append(remaining)
    return units


def earn_income(
    level: int, proficiency: str = "trained", unit_days: int = 8
) -> dict[str, Any] | None:
    """What one Earn Income check pays for a whole Downtime Unit.

    PFS Earn Income is not the Player Core activity. The differences that change
    the arithmetic:

    - The check is **Crafting, Performance, or a Lore skill** -- not any skill
      you happen to be good at. A martial character with expert Athletics and no
      Crafting earns on their Lore, at whatever rank that is.
    - The default **Task Level is the character's level - 2**, which is already
      baked into the table; look up by character level, not task level.
    - You make **one check per Downtime Unit**, and the payout is for the whole
      unit, not per day -- so it is one check per *unit*, not per adventure. A
      Scenario's 8 days is exactly one unit, but anything granting more than 8
      days splits into several (see `downtime_units`), each with its own check.
      Splitting a unit between activities does not add a check either: 7 days
      retraining plus 1 day Earning Income is still one Earn Income check.
    - A **critical success** treats the character as one level higher (minimum
      level 3); a **critical failure** earns nothing at all.

    Returns the DC and the copper value of each degree of success, or None if
    the level is outside the table the Guide publishes (1-16, plus 17 as the
    critical-success row for a 16th-level character).
    """
    row = _EARN_INCOME_8_DAY.get(level)
    if row is None or unit_days <= 0:
        return None
    dc, failed, trained, expert, master = row
    scale = unit_days / 8

    def at(rank: str) -> int | None:
        value = {"trained": trained, "expert": expert, "master": master,
                 "legendary": master}.get(rank)
        return None if value is None else int(round(value * scale))

    success = at(proficiency)
    if success is None:  # master/legendary below 9th tops out at expert
        success = at("expert")

    crit_row = _EARN_INCOME_8_DAY.get(max(3, level + 1))
    crit = None
    if crit_row is not None:
        _, _, c_tr, c_ex, c_ma = crit_row
        crit = {"trained": c_tr, "expert": c_ex, "master": c_ma or c_ex,
                "legendary": c_ma or c_ex}.get(proficiency, c_ex)
        crit = int(round(crit * scale))

    return {
        "level": level,
        "proficiency": proficiency,
        "unit_days": unit_days,
        "dc": dc,
        "critical_success_cp": crit,
        "success_cp": success,
        "failure_cp": int(round(failed * scale)),
        "critical_failure_cp": 0,
        "critical_success": format_currency(crit) if crit is not None else None,
        "success": format_currency(success),
        "failure": format_currency(int(round(failed * scale))),
        "critical_failure": format_currency(0),
    }
