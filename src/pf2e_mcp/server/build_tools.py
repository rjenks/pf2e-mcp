"""Character-building tools, covering level-1 creation through full 1-20
leveling across all ancestries/backgrounds/classes, per the
character-building layer plan.

All tools are pure functions over a `character` dict shaped like
Pathbuilder 2e's `build` export object (confirmed against the pathmuncher
and foundry-pathbuilder2e-import importer source, not a bespoke schema --
see the plan for why). The server holds no character state between calls;
the caller (the agent) passes the character JSON each time and keeps it in
conversation.

Still deferred: multiclass feat-count-tracking flags (e.g.
flags.system.barbarian.archetypeFeatCount), retraining/respec as a distinct
mechanism (handled today by the agent editing feats/proficiencies directly
and re-running validate_build), and non-standard spellcasting progressions
(Magus/Summoner) -- see list_available_spells' docstring.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from . import class_skills
from . import licensing
from . import pf2e_math as m
from . import pfs
from . import character as _native
from .db import get_connection

ABILITY_BOOST_LEVELS = (5, 10, 15, 20)


def _real_slug(pack: str, name: str) -> str:
    """Look up an entry's actual `slug` column by exact (case-insensitive)
    name within the given pack, rather than naively deriving one from
    display name. The naive `.lower().replace(" ", "-")` pattern this
    replaces silently fails to match anything for a name with an apostrophe
    or other punctuation Foundry's real slugs strip -- e.g. the background
    "Battle's Spark" naively derives to "battle's-spark" but its real slug
    is "battles-spark". Falls back to the naive derivation if no exact name
    match is found, so an unrecognized name still produces *a* slug (which
    then simply won't match anything downstream, same behavior as before)
    rather than raising."""
    if not name:
        return ""
    rows = _fetchall(
        "SELECT slug FROM entries WHERE pack = ? AND name = ? COLLATE NOCASE",
        (pack, name),
    )
    if rows and rows[0]["slug"]:
        return rows[0]["slug"]
    return name.lower().replace(" ", "-")


def _fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()




def list_ancestries(filter: str | None = None, include_legacy: bool = False) -> list[dict[str, Any]]:
    """List playable ancestries, optionally filtered by name substring.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    ancestries unless set -- but 38% of ancestries are legacy-flagged and
    still fully playable (not reprinted under ORC yet isn't the same as
    retired), so set this True whenever the user wants the full catalog,
    not just what's been individually remastered. See `_legacy_filter_sql`."""
    sql = (
        "SELECT e.id, e.name, e.slug, a.hp, a.size, a.boosts, a.flaws "
        "FROM entries e JOIN ancestry_boosts a ON a.ancestry_slug = e.slug "
        "WHERE e.pack = 'ancestries'" + licensing.legacy_filter_sql(include_legacy, "e.is_remaster")
    )
    params: tuple = ()
    if filter:
        sql += " AND e.name LIKE ?"
        params = (f"%{filter}%",)
    rows = _fetchall(sql, params)
    for r in rows:
        r["boosts"] = json.loads(r["boosts"])
        r["flaws"] = json.loads(r["flaws"])
    return rows


def list_backgrounds(filter: str | None = None, include_legacy: bool = False) -> list[dict[str, Any]]:
    """List backgrounds, optionally filtered by name substring.

    `trained_skills` (`{"fixed": [skill slugs], "lore": [Lore skill names]}`)
    and `granted_items` (`[{level, name, uuid}]`, `level` always null for a
    background -- granted at character creation) come straight from the
    background item's own structured `system.trainedSkills`/`system.items`
    fields, the same mechanism `build_list_classes` already exposes for
    classes -- e.g. Field Medic: trained_skills `{"fixed": ["medicine"],
    "lore": ["Warfare Lore"]}`, granted_items `[{"name": "Battle
    Medicine", ...}]`.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    backgrounds unless set -- 54% of all backgrounds are legacy-flagged and
    still fully playable, the single largest exclusion of any pack this
    filter touches. See `_legacy_filter_sql`."""
    sql = (
        "SELECT e.id, e.name, e.slug, e.description, b.boosts, b.trained_skills, b.granted_items "
        "FROM entries e JOIN background_boosts b ON b.background_slug = e.slug "
        "WHERE e.pack = 'backgrounds'" + licensing.legacy_filter_sql(include_legacy, "e.is_remaster")
    )
    params: tuple = ()
    if filter:
        sql += " AND e.name LIKE ?"
        params = (f"%{filter}%",)
    rows = _fetchall(sql, params)
    for r in rows:
        r["boosts"] = json.loads(r["boosts"])
        r["trained_skills"] = json.loads(r["trained_skills"]) if r["trained_skills"] else {"fixed": [], "lore": []}
        r["granted_items"] = json.loads(r["granted_items"]) if r["granted_items"] else []
    return rows


def _foundry_rank_to_project(rank: int | None) -> int | None:
    """Foundry stores proficiency ranks as 0-4 (untrained/trained/expert/
    master/legendary). This project's own convention, used throughout every
    character's `proficiencies` dict, is 0/2/4/6/8 (matching Pathbuilder's
    export format). Convert at the read boundary so a caller can copy a
    tool result straight into a draft character without doing this math
    themselves."""
    return None if rank is None else rank * 2


def list_classes(filter: str | None = None, include_legacy: bool = False) -> list[dict[str, Any]]:
    """List classes, optionally filtered by name substring. Includes each
    class's level-1 initial proficiency baseline (HP, Perception, saves,
    trained skills formula, weapon/armor proficiencies) -- sourced directly
    from the class item's own data rather than requiring the caller to
    recall or look this up externally. Proficiency ranks in the returned
    `perception`/`fortitude`/`reflex`/`will`/`class_dc`/`attacks`/`defenses`
    fields use this project's 0/2/4/6/8 convention (untrained/trained/
    expert/master/legendary), the same as a character's `proficiencies`
    dict elsewhere -- ready to drop into a draft character directly.

    Note this is the class's own *baseline* only -- doctrine/subclass
    choices (e.g. Cleric's Warpriest doctrine granting expert Fortitude and
    martial weapons) and later automatic class features (e.g. Champion's
    Weapon Expertise at level 5) can raise these further; this call doesn't
    know about either, only what every member of the class starts with at
    level 1.

    **`trained_skills.additional` is the class's own flat baseline ONLY --
    it does NOT include the Intelligence-modifier bonus to additional
    trained skills that every class gets at character creation** ("...
    becomes trained in a number of skills equal to [class value] plus your
    Intelligence modifier" -- Player Core, Skills step of character
    creation). This is a genuinely easy value to miscount: confirmed live
    building a level-10 Magus with Int 14 (+2) where only 2 additional
    skills were picked instead of the correct 4. The caller is responsible
    for adding `max(0, ability_mod(character's Int score))` to this value
    themselves before presenting a skill count to the user; `validate_build`
    now includes a floor check for this specifically (see
    `_validate_trained_skill_count`) but it's a warning after the fact, not
    a substitute for getting the count right when first proposing it.

    `trained_skills.choice`, when present, is a class's "trained in X **or**
    Y" pattern (e.g. Fighter's Acrobatics-or-Athletics) that `fixed` alone
    can't represent -- sourced from the class item's own ChoiceSet rule
    element (see `item_choice_sets`), not from `fixed` being silently
    incomplete.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    classes unless set -- currently a no-op (every class chassis has been
    reprinted under ORC, so all 29 are is_remaster:1), kept for API
    consistency with the other `list_*` tools and in case a future release
    adds a class before its own remaster reprint. See `_legacy_filter_sql`."""
    sql = (
        "SELECT e.id, e.name, e.slug, c.key_ability, c.class_feat_levels, "
        "c.ancestry_feat_levels, c.general_feat_levels, c.skill_feat_levels, "
        "c.skill_increase_levels, c.hp, c.perception_rank, c.fortitude_rank, "
        "c.reflex_rank, c.will_rank, c.class_dc_rank, c.trained_skills, "
        "c.attacks, c.defenses "
        "FROM entries e JOIN class_progression c ON c.class_slug = e.slug "
        "WHERE e.pack = 'classes'" + licensing.legacy_filter_sql(include_legacy, "e.is_remaster")
    )
    params: tuple = ()
    if filter:
        sql += " AND e.name LIKE ?"
        params = (f"%{filter}%",)
    rows = _fetchall(sql, params)
    for r in rows:
        for key in ("key_ability", "class_feat_levels", "ancestry_feat_levels",
                    "general_feat_levels", "skill_feat_levels", "skill_increase_levels"):
            r[key] = json.loads(r[key])

        r["perception"] = _foundry_rank_to_project(r.pop("perception_rank"))
        r["fortitude"] = _foundry_rank_to_project(r.pop("fortitude_rank"))
        r["reflex"] = _foundry_rank_to_project(r.pop("reflex_rank"))
        r["will"] = _foundry_rank_to_project(r.pop("will_rank"))
        r["class_dc"] = _foundry_rank_to_project(r.pop("class_dc_rank"))

        attacks = json.loads(r["attacks"])
        for key in ("simple", "martial", "unarmed", "advanced"):
            if key in attacks:
                attacks[key] = _foundry_rank_to_project(attacks[key])
        if attacks.get("other", {}).get("rank") is not None:
            attacks["other"]["rank"] = _foundry_rank_to_project(attacks["other"]["rank"])
        r["attacks"] = attacks

        defenses = json.loads(r["defenses"])
        r["defenses"] = {k: _foundry_rank_to_project(v) for k, v in defenses.items()}

        r["trained_skills"] = class_skills.apply(
            r["slug"], json.loads(r["trained_skills"])
        )

        choice_rows = _fetchall(
            "SELECT flag, choices FROM item_choice_sets WHERE entry_id = ?", (r["id"],)
        )
        if choice_rows:
            # A class item can carry more than one ChoiceSet (rare) -- surface
            # all of them rather than guessing which one is "the" skill choice.
            r["trained_skills"]["choice"] = [
                {"flag": cr["flag"], "options": json.loads(cr["choices"])} for cr in choice_rows
            ]
    return rows


# Fixed core rule (PF2e Player Core): starting wealth for a level-1
# character using the "starting money" method rather than a fixed
# equipment package.
STARTING_WEALTH_GP = 15


def list_equipment(
    filter: str | None = None,
    item_type: Literal["weapon", "armor", "shield", "equipment", "consumable", "ammo", "treasure"] | None = None,
    max_price_gp: float | None = None,
    include_legacy: bool = False,
) -> dict[str, Any]:
    """Browse the equipment pack (weapons, armor, shields, gear) by name
    substring, item type, and/or maximum price. A fresh level-1 character
    has STARTING_WEALTH_GP (15) gold pieces to spend using the standard
    "starting money" method -- returned alongside results as a reminder,
    not a per-call lookup, since it's a fixed constant.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    equipment unless set. See `_legacy_filter_sql`."""
    sql = "SELECT id, name, type, raw_json FROM entries WHERE pack = 'equipment'" + licensing.legacy_filter_sql(include_legacy)
    params: list[Any] = []
    if filter:
        sql += " AND name LIKE ?"
        params.append(f"%{filter}%")
    if item_type:
        sql += " AND type = ?"
        params.append(item_type)
    rows = _fetchall(sql, tuple(params))

    items = []
    for r in rows:
        system = json.loads(r["raw_json"])["system"]
        price = system.get("price", {}).get("value", {})
        price_gp = price.get("pp", 0) * 10 + price.get("gp", 0) + price.get("sp", 0) / 10 + price.get("cp", 0) / 100
        if max_price_gp is not None and price_gp > max_price_gp:
            continue
        items.append({"id": r["id"], "name": r["name"], "type": r["type"], "price_gp": round(price_gp, 2)})

    return {"starting_wealth_gp": STARTING_WEALTH_GP, "items": items}


def list_available_feats(
    character: dict[str, Any],
    feat_category: Literal["ancestry", "class", "general", "skill", "archetype"],
    level: int | None = None,
    include_legacy: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """The main token-saving discovery tool: returns only feats of the
    requested category at or below the target level (character's current
    level if unspecified) whose prerequisites the character already meets,
    instead of the caller reasoning over the full feat list or raw rules
    text. Split into two buckets:

    - "available": prerequisites confirmed satisfied (or none required).
    - "unconfirmed": at least one prerequisite couldn't be automatically
      verified (freeform text the parser can't structure -- roughly 16% of
      all feats per the Phase 0 parsing spike). Never silently included in
      "available" -- surface these to the user for manual confirmation.

    Feats that definitely fail a prerequisite, or that the character
    already has, are omitted entirely.

    "class" and "ancestry" feats are further restricted to the character's
    own class/ancestry (matched via the feat's trait list, e.g. a Fighter
    class feat carries the "fighter" trait) -- category alone only narrows
    to "any class feat from any class".

    "archetype" is a special case: the source data has no "archetype"
    category value at all (confirmed against the real data -- every
    archetype/dedication feat is stored as category "class" or "skill",
    with "archetype" appearing only as a trait). This matches actual PF2e
    rules: archetype feats are taken using ordinary class or skill feat
    slots, not a separate slot type. This function queries for the
    "archetype" trait within those two categories instead. It does not
    filter further to one specific chosen archetype (e.g. "only Alchemist
    archetype feats") -- that gating already happens naturally through each
    feat's own prerequisite (e.g. "Alchemist Dedication"), checked the same
    way as any other prerequisite.

    Every returned entry also carries `rarity` and a best-effort `pfs`
    legality status (not authoritative -- see pfs.py module docstring),
    so a PFS-bound build can be steered away from restricted picks without
    a separate lookup per feat.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    feats unless set. See `_legacy_filter_sql`.
    """
    character = _native.as_legacy(character, level)
    target_level = level if level is not None else character.get("level", 1)
    legacy_sql = licensing.legacy_filter_sql(include_legacy)
    if feat_category == "archetype":
        candidate_rows = _fetchall(
            "SELECT id, name, level, traits, rarity, access_text FROM entries "
            "WHERE pack = 'feats' AND category IN ('class', 'skill') AND level <= ?" + legacy_sql,
            (target_level,),
        )
        rows = [r for r in candidate_rows if "archetype" in json.loads(r["traits"] or "[]")]
    else:
        rows = _fetchall(
            "SELECT id, name, level, traits, rarity, access_text FROM entries "
            "WHERE pack = 'feats' AND category = ? AND level <= ?" + legacy_sql,
            (feat_category, target_level),
        )

    required_traits: set[str] = set()
    if feat_category == "class":
        class_name = character.get("class", "").strip().lower()
        if class_name:
            required_traits.add(class_name)
    elif feat_category == "ancestry":
        ancestry = character.get("ancestry", "").strip().lower()
        if ancestry:
            required_traits.add(ancestry)
        # A heritage's own feat list is gated on a trait matching the
        # *heritage's* slug, not the base ancestry's -- e.g. Duskwalker
        # feats carry the "duskwalker" trait, absent from every Dwarf feat.
        # Matching only `ancestry` silently hid an entire heritage's feats
        # for any versatile heritage (confirmed live: a Dwarf/Duskwalker
        # character got 27 Dwarf feats and 0 Duskwalker ones from this
        # browse, despite each Duskwalker feat individually checking out as
        # eligible via `build_check_prerequisite`). Harmless no-op for a
        # native (non-versatile) heritage like Death Warden Dwarf, which
        # doesn't have its own separate feat list to miss.
        heritage = character.get("heritage")
        if heritage:
            heritage_slug = _real_slug("heritages", heritage)
            if heritage_slug:
                required_traits.add(heritage_slug)

    overrides = pfs.load_overrides()
    derived = _build_derived(character)
    committed_below_threshold = _committed_dedications_below_threshold(character)
    conn = get_connection()
    try:
        available, unconfirmed = [], []
        for feat in rows:
            if m.has_feat(character, feat["name"]):
                continue
            feat_traits = json.loads(feat["traits"] or "[]")
            if required_traits and required_traits.isdisjoint(feat_traits):
                continue
            prereq_rows = conn.execute(
                "SELECT raw_text, kind, structured FROM prerequisites WHERE entry_id = ?",
                (feat["id"],),
            ).fetchall()
            status, checks = m.evaluate_prerequisites(character, prereq_rows, derived)
            if "dedication" in feat_traits and committed_below_threshold:
                # A second (different) dedication feat, but at least one
                # already-taken archetype hasn't met its own "two other
                # feats" threshold yet -- see _committed_dedications_
                # below_threshold's docstring for why this is checked here
                # at all (Foundry's own reference implementation doesn't).
                status = "ineligible"
                checks = checks + [{
                    "raw_text": f"can't select another dedication feat until "
                                f"{' and '.join(committed_below_threshold)} "
                                "gain(s) two other feats from their archetype",
                    "satisfied": False,
                }]
            entry = {
                "id": feat["id"],
                "name": feat["name"],
                "level": feat["level"],
                "checks": checks,
                "rarity": feat["rarity"],
                # Best-effort, not authoritative -- see pfs.py module docstring.
                "pfs": pfs.pfs_status(feat["name"], feat["rarity"], overrides),
            }
            if feat["access_text"]:
                # Narrative gate (org membership, background, region) --
                # never affects `status`/`checks` above, since it's
                # GM-adjudicated, not a mechanical prerequisite this
                # project can evaluate true/false. Surfaced so it isn't
                # silently invisible the way it used to be (a feat with an
                # Access line the character plainly doesn't meet used to
                # show as fully eligible with zero checks).
                entry["access"] = feat["access_text"]
            if status == "eligible":
                available.append(entry)
            elif status == "unconfirmed":
                unconfirmed.append(entry)
        return {"available": available, "unconfirmed": unconfirmed}
    finally:
        conn.close()


def check_prerequisite(character: dict[str, Any], feat_id: str) -> dict[str, Any]:
    """Narrow single-feat eligibility check -- use when sanity-checking one
    user-proposed choice rather than re-running full discovery."""
    character = _native.as_legacy(character)
    conn = get_connection()
    try:
        entry = conn.execute(
            "SELECT id, name, traits, access_text FROM entries WHERE id = ?", (feat_id,)
        ).fetchone()
        if not entry:
            return {"error": f"No feat with id {feat_id}"}
        prereq_rows = conn.execute(
            "SELECT raw_text, kind, structured FROM prerequisites WHERE entry_id = ?",
            (feat_id,),
        ).fetchall()
        derived = _build_derived(character)
        status, checks = m.evaluate_prerequisites(character, prereq_rows, derived)
        if "dedication" in json.loads(entry["traits"] or "[]"):
            committed_below_threshold = _committed_dedications_below_threshold(character)
            if committed_below_threshold:
                status = "ineligible"
                checks = checks + [{
                    "raw_text": f"can't select another dedication feat until "
                                f"{' and '.join(committed_below_threshold)} "
                                "gain(s) two other feats from their archetype",
                    "satisfied": False,
                }]
        result = {"id": feat_id, "name": entry["name"], "status": status, "checks": checks}
        if entry["access_text"]:
            # Narrative gate, not a mechanical check -- see list_available_feats'
            # matching comment for why this doesn't affect `status` above.
            result["access"] = entry["access_text"]
        return result
    finally:
        conn.close()


def list_ability_boost_options(
    character: dict[str, Any],
    source: Literal["ancestry", "background", "class", "free"],
) -> dict[str, Any]:
    """Eligible ability score boosts for the given source, respecting
    ancestry flaws and boosts already recorded in
    character.abilities.breakdown (Pathbuilder's own per-level boost
    history field -- see the plan's Pathbuilder-schema findings). Does not
    yet cross-check the "can't take two free boosts in the same ability at
    the same tier" rule beyond what's already recorded in breakdown for
    *this* source; full multi-source interaction (e.g. voluntary ancestry
    flaws freeing up a boost) is left to validate_build.
    """
    character = _native.as_legacy(character)
    breakdown = character.get("abilities", {}).get("breakdown", {})
    already_chosen = {
        "ancestry": breakdown.get("ancestryBoosts", []) + breakdown.get("ancestryFree", []),
        "background": breakdown.get("backgroundBoosts", []),
        "class": breakdown.get("classBoosts", []),
        "free": [b for boosts in breakdown.get("mapLevelledBoosts", {}).values() for b in boosts],
    }[source]

    if source == "ancestry":
        row = _fetchall(
            "SELECT boosts, flaws FROM ancestry_boosts WHERE ancestry_slug = ?",
            (_real_slug("ancestries", character.get("ancestry", "")),),
        )
        if not row:
            return {"error": "Character has no recognized ancestry set yet"}
        boosts, flaws = json.loads(row[0]["boosts"]), json.loads(row[0]["flaws"])
        flawed = {a for tier in flaws.values() for a in tier.get("value", [])}
        options = []
        for tier_idx, tier in sorted(boosts.items()):
            eligible = [a for a in tier["value"] if a not in flawed and a not in already_chosen]
            if eligible or not tier["value"]:
                options.append({"tier": tier_idx, "options": eligible or ["free choice"]})
        return {"source": "ancestry", "tiers": options}

    if source == "background":
        row = _fetchall(
            "SELECT boosts FROM background_boosts WHERE background_slug = ?",
            (_real_slug("backgrounds", character.get("background", "")),),
        )
        if not row:
            return {"error": "Character has no recognized background set yet"}
        boosts = json.loads(row[0]["boosts"])
        return {
            "source": "background",
            "tiers": [{"tier": k, "options": v["value"]} for k, v in sorted(boosts.items())],
        }

    if source == "class":
        row = _fetchall(
            "SELECT key_ability FROM class_progression WHERE class_slug = ?",
            (_real_slug("classes", character.get("class", "")),),
        )
        if not row:
            return {"error": "Character has no recognized class set yet"}
        return {"source": "class", "options": json.loads(row[0]["key_ability"])}

    # "free": at level 1, four free boosts to four different abilities.
    all_abilities = ["str", "dex", "con", "int", "wis", "cha"]
    return {"source": "free", "options": [a for a in all_abilities if a not in already_chosen]}


def list_skill_increase_options(character: dict[str, Any]) -> dict[str, Any]:
    """Which of the 16 core skills are legal to raise one rank right now,
    given the character's current level and current rank in each (the
    trained/expert/master/legendary level-3/7/15 caps are a fixed core rule,
    independent of class). Does not consult skillIncreaseLevels itself --
    call build_get_level_up_choices to confirm a skill-increase choice is
    actually available at the target level before offering these."""
    character = _native.as_legacy(character)
    level = character.get("level", 1)
    prof = character.get("proficiencies", {})
    options = []
    for skill in sorted(m.KNOWN_SKILLS):
        current = prof.get(skill, 0)
        target_rank = m.next_rank(current)
        if target_rank is None:
            continue
        if m.skill_rank_legal_at_level(target_rank, level):
            options.append({"skill": skill, "current_rank": m.RANK_BY_VALUE[current], "next_rank": target_rank})
    return {"options": options}


def _is_skill_row(feat: Any) -> bool:
    """True for a Skill Increase / Skill Training entry in a character's feat
    list. These are mechanical gains recorded alongside feats so the sheet can
    place them on the right level, not feats in their own right -- they have no
    rules entry, no prerequisites, and may legitimately repeat."""
    if not (isinstance(feat, (list, tuple)) and len(feat) > 2):
        return False
    return str(feat[2] or "").strip().lower() in ("skill increase", "skill training")


def validate_build(
    character: dict[str, Any],
    variant_rules: list[str] | None = None,
    pfs_legal_only: bool = False,
) -> dict[str, Any]:
    """Structured validation, not prose -- returns errors (definite rule
    violations) and warnings (things that couldn't be auto-verified and
    need human confirmation). Covers: duplicate feats, prerequisites of
    every taken feat still being satisfied (important after e.g. a
    hypothetical ancestry swap mid-build), skill ranks not exceeding the
    fixed level-3/7/15 expert/master/legendary caps, whether the number of
    ancestry-category feats taken fits the character's actual budget for
    their level, and (new) the archetype "one dedication feat at a time"
    rule -- a level-ordered check that no dedication feat was taken while
    an earlier, different dedication still needed two other archetype
    feats first (see `_validate_dedication_exclusivity`; this is validation
    this project does that Foundry's own reference implementation doesn't
    attempt either -- confirmed nothing in its source enforces it), and
    (new) a warning for any *fixed* (non-player-choice) trained skill or
    Lore the character's background/class grants but that isn't actually
    trained on the character (see `_validate_fixed_trained_skills` --
    deliberately scoped to just the fixed/unconditional grants, not the
    player's free "additional" choices, since those can't be cleanly told
    apart from later Skill Increases without risking a false positive at
    higher levels), (new) a warning if the character's *total* count of
    trained-or-better skills/lores is below the level-1 minimum implied by
    class fixed+additional skills, background fixed/lore/choice skills, and
    the character's Intelligence modifier if positive -- a floor check, not
    an identity check, so it stays valid at any level, and (new) two checks
    on the attribute array (see `_validate_attribute_boosts`): a **half-step**
    warning naming any attribute left on an odd score at a boost milestone,
    where the last point spent buys no modifier at all, and a **derivability**
    warning when the recorded scores don't match the character's own recorded
    boosts. The second reads both breakdown schemas found in real files
    (`backgroundBoosts`/`mapLevelledBoosts` and `backgroundAbilities`/`lvl1`
    ...`lvl20`) and folds case, since exports disagree on both; a single
    attribute exactly 2 high is called out as the signature of an invested
    apex item rather than an illegal array (see
    `_validate_trained_skill_count`; this exists specifically because the
    Intelligence-modifier bonus to a class's additional trained-skill count
    is a universal core rule not baked into `list_classes`' `additional`
    field, and is easy to silently drop), and (new) a warning for any
    save/Perception/weapon/
    armor/class-DC proficiency rank that's *lower* than what the
    character's class baseline plus its granted class features/taken feats
    up to their level should produce (see `_validate_proficiency_ranks`).
    Does NOT yet check ability-boost-tier double-ups across sources, or
    spellcasting proficiency specifically (tradition-dependent -- see
    `_validate_proficiency_ranks`'s docstring for why that one key is
    skipped).

    `variant_rules` (see rules_list_variant_rules / get_level_up_choices)
    changes the ancestry-feat budget when `'ancestry-paragon'` is included
    (2 feats at level 1, one more at every odd level, instead of the
    class's normal 1/5/9/13/17 schedule), and adds a soft, non-blocking
    note about archetype-trait feat count when `'free-archetype'` is
    included -- that one is a warning rather than an error because this
    server doesn't track class/skill-feat-slot usage at all, so it can't
    tell a normal-slot archetype feat apart from a free-archetype bonus
    slot with certainty; only ancestry feats get a hard budget check, since
    'ancestry' is otherwise never used as a normal class/skill/general feat
    slot. The **Ancestral Paragon** general feat (a real, always-available
    pick independent of any variant rule) adds +1 to the ancestry budget on
    its own if taken.

    `pfs_legal_only=True` adds a `pfs` key to the result: a structured report
    (see `_validate_pfs`) of exactly which options would need a boon, rather
    than a verdict. A yes/no isn't actionable -- a build is usually one choice
    away from legal, and the player needs to know *which* choice -- so it
    carries `requires_boon` (name, kind, status, rarity, source, note per
    entry), `unchecked` (options this project couldn't resolve, which are not
    evidence of legality), and `legal`, true only when `requires_boon` is
    empty. The same findings are mirrored into `warnings` so callers that
    read only that keep working.

    Coverage is class, ancestry, heritage, background, every feat and every
    piece of equipment. Until this took its own function it checked *feats
    alone*, so a character could come back clean while their class or their
    gear was restricted. Still a rarity-plus-overrides heuristic and still
    not authoritative -- see pfs.py.

    Also warns about **unfilled feat slots** (`_validate_feat_slots`): a
    scheduled class/skill/general/ancestry feat at or below the character's
    level with nothing recorded in it. The class's own level arrays are
    ingested, so this was always answerable and simply wasn't asked -- it
    found a level-10 animist missing both 10th-level feats and a rogue
    missing their 5th-level ancestry feat, both of which had been validating
    clean for months."""
    character = _native.as_legacy(character)
    variant_rules = variant_rules or []
    errors: list[str] = []
    warnings: list[str] = []
    derived = _build_derived(character)

    level = character.get("level", 1)
    prof = character.get("proficiencies", {})
    for skill in m.KNOWN_SKILLS:
        value = prof.get(skill, 0)
        rank = m.RANK_BY_VALUE.get(value)
        if rank and not m.skill_rank_legal_at_level(rank, level):
            errors.append(
                f"'{skill}' is {rank} but that requires character level "
                f"{m.SKILL_RANK_MIN_LEVEL[rank]} (currently level {level})"
            )

    feats = character.get("feats", [])
    seen = set()
    for f in feats:
        name = f[0] if f else None
        if not name:
            continue
        # Skill Increases and feat-granted Skill Trainings ride in the feat
        # list because that is where the sheet's Advancement page reads a
        # level's gains from, but they are not feats and repeating one is not
        # only legal, it is the norm: Athletics goes trained -> expert ->
        # master -> legendary through four separate increases naming the same
        # skill. Counting those as duplicate feats reported hard errors on a
        # correct build.
        if _is_skill_row(f):
            continue
        key = name.strip().lower()
        if key in seen:
            errors.append(f"Duplicate feat: {name}")
        seen.add(key)

    overrides = pfs.load_overrides()
    ancestry_taken = 0
    archetype_trait_taken = 0
    has_ancestral_paragon = False
    conn = get_connection()
    try:
        for f in feats:
            name = f[0] if f else None
            if not name:
                continue
            if _is_skill_row(f):
                # A skill name, not a feat name. Looking it up would report
                # every skill increase on the sheet as an unverifiable feat.
                continue
            entry = conn.execute(
                "SELECT id, category, rarity, traits FROM entries "
                "WHERE pack = 'feats' AND name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if not entry:
                warnings.append(f"Feat '{name}' not found in rules database -- cannot verify prerequisites")
                continue
            if entry["category"] == "ancestry":
                ancestry_taken += 1
            if "archetype" in json.loads(entry["traits"] or "[]"):
                archetype_trait_taken += 1
            if name.strip().lower() == "ancestral paragon":
                has_ancestral_paragon = True
            prereq_rows = conn.execute(
                "SELECT raw_text, kind, structured FROM prerequisites WHERE entry_id = ?",
                (entry["id"],),
            ).fetchall()
            status, checks = m.evaluate_prerequisites(character, prereq_rows, derived)
            if status == "ineligible":
                unmet = [c["raw_text"] for c in checks if c["satisfied"] is False]
                errors.append(f"'{name}' prerequisites no longer met: {', '.join(unmet)}")
            elif status == "unconfirmed":
                unclear = [c["raw_text"] for c in checks if c["satisfied"] is None]
                warnings.append(f"'{name}' prerequisites could not be auto-verified: {', '.join(unclear)}")

        class_row = conn.execute(
            "SELECT ancestry_feat_levels FROM class_progression WHERE class_slug = ?",
            (_real_slug("classes", character.get("class", "")),),
        ).fetchone()
    finally:
        conn.close()

    if class_row:
        base_levels = json.loads(class_row["ancestry_feat_levels"])
        if "ancestry-paragon" in variant_rules:
            ancestry_budget = (2 if level >= 1 else 0) + len(
                [lvl for lvl in range(3, level + 1, 2)]
            )
        else:
            ancestry_budget = len([lvl for lvl in base_levels if lvl <= level])
        if has_ancestral_paragon:
            ancestry_budget += 1
        if ancestry_taken > ancestry_budget:
            variant_hint = (
                "" if "ancestry-paragon" in variant_rules
                else " -- pass variant_rules=['ancestry-paragon'] if this table uses that "
                     "variant rule (GM Core p.194 grants 2 ancestry feats at level 1 and one "
                     "more at every odd level)"
            )
            errors.append(
                f"{ancestry_taken} ancestry-category feats taken but only {ancestry_budget} "
                f"slot(s) available at level {level}{variant_hint}"
            )

    if "free-archetype" in variant_rules:
        expected_min = len([lvl for lvl in range(2, level + 1, 2)])
        if archetype_trait_taken < expected_min:
            warnings.append(
                f"Free Archetype variant active: {archetype_trait_taken} archetype-trait "
                f"feat(s) taken, expected roughly {expected_min} (one per even level up to "
                f"level {level}) -- worth double-checking whether any were skipped "
                "intentionally."
            )

    errors.extend(_validate_dedication_exclusivity(character))
    warnings.extend(_validate_fixed_trained_skills(character))
    warnings.extend(_validate_trained_skill_count(character))
    warnings.extend(_validate_proficiency_ranks(character))
    warnings.extend(_validate_attribute_boosts(character))
    warnings.extend(_validate_feat_slots(character, variant_rules))

    result: dict[str, Any] = {"errors": errors, "warnings": warnings}
    if pfs_legal_only:
        report = _validate_pfs(character)
        result["pfs"] = report
        # Keep the flat warnings usable on their own -- a caller reading only
        # `warnings` (as every caller did before `pfs` existed) still sees
        # what needs a boon.
        for entry in report["requires_boon"]:
            warnings.append(
                f"PFS: {entry['kind']} '{entry['name']}' is {entry['status']} "
                f"({entry['rarity']}, {entry['source']})")
        for entry in report["unchecked"]:
            warnings.append(
                f"PFS: {entry['kind']} '{entry['name']}' could not be checked "
                f"-- {entry['reason']}")
    return result


_BOOST_MILESTONES = (1, 5, 10, 15, 20)
_ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def _feat_slot_bucket(category: str, free_archetype: bool) -> str | None:
    """Which feat-slot schedule a recorded feat's category counts against, or
    None when it fills no scheduled slot.

    Character files disagree wildly on this label -- Pathbuilder writes bare
    slugs ('class', 'skill', 'classfeature'), the hand-written files write
    prose ('Class Feat', 'Awarded Feat'), and some exports name a class feat
    after its class instead of the generic label ('Fighter Feat', 'Champion
    Feat') -- so match on substrings, with traps to avoid:

    * `classfeature` contains "class" but is an automatic class feature, not a
      feat filling a class slot.
    * `Skill Increase` contains "skill" but is a proficiency-rank bump, not a
      feat -- counting it as one would mask a genuinely missing Skill Feat.
      Confirmed live: real exports use both categories, and PF2e's own
      skill-feat and skill-increase level lists overlap almost entirely, so
      this collision fires often, not just in theory.
    * An archetype feat normally *does* consume a class feat slot, so it
      counts as one -- unless Free Archetype is running, where it has a slot
      of its own and the class slot is still owed.
    * A `'<ClassName> Feat'` label (confirmed live) matches none of the four
      generic bucket words, so it needs its own fallback rather than being
      silently dropped as "fills no slot" -- which would report an owed
      class feat as still missing even though one was taken.
    """
    text = (category or "").strip().lower()
    if not text or "classfeature" in text.replace(" ", ""):
        return None
    # Bonus feats from a background, boon or GM award sit outside the
    # schedule entirely; counting them would mask a genuinely empty slot.
    if any(word in text for word in ("awarded", "bonus", "heritage", "granted", "increase")):
        return None
    if "archetype" in text or "dedication" in text:
        return None if free_archetype else "class"
    for bucket in ("class", "skill", "general", "ancestry"):
        if bucket in text:
            return bucket
    if text.rsplit(" ", 1)[-1] == "feat":
        return "class"
    return None


def _validate_feat_slots(character: dict[str, Any],
                         variant_rules: list[str]) -> list[str]:
    """Scheduled feat slots at or below the character's level with no feat
    recorded in them.

    Every class's own level arrays are ingested (`class_progression`), so
    "you are owed a 10th-level class feat" is a fact this project holds and
    was simply never checking -- confirmed live on a real level-10 build
    with both its 10th-level class and skill feats unfilled and validating
    clean.

    A warning rather than an error: a character can be mid-level-up, and the
    category labels are free text that no schema enforces, so a
    false positive here should never block a build.

    Ancestry is skipped under Ancestry Paragon, whose schedule this doesn't
    model -- the existing budget check above covers over-spending there.
    """
    level = int(character.get("level") or 1)
    slug = _real_slug("classes", character.get("class", ""))
    rows = _fetchall(
        "SELECT class_feat_levels, skill_feat_levels, general_feat_levels, "
        "ancestry_feat_levels FROM class_progression WHERE class_slug = ?",
        (slug,))
    if not rows:
        return []
    schedule = rows[0]
    free_archetype = "free-archetype" in variant_rules

    taken: dict[str, set[int]] = {}
    for feat in character.get("feats") or []:
        if not (isinstance(feat, (list, tuple)) and feat):
            continue
        bucket = _feat_slot_bucket(
            str(feat[2]) if len(feat) > 2 and feat[2] else "", free_archetype)
        if not bucket:
            continue
        try:
            taken.setdefault(bucket, set()).add(int(feat[3]) if len(feat) > 3 and feat[3] else 1)
        except (TypeError, ValueError):
            continue

    warnings = []
    for bucket, column in (("class", "class_feat_levels"),
                           ("skill", "skill_feat_levels"),
                           ("general", "general_feat_levels"),
                           ("ancestry", "ancestry_feat_levels")):
        if bucket == "ancestry" and "ancestry-paragon" in variant_rules:
            continue
        due = [lvl for lvl in json.loads(schedule[column] or "[]") if lvl <= level]
        missing = [lvl for lvl in due if lvl not in taken.get(bucket, set())]
        if missing:
            levels = ", ".join(str(lvl) for lvl in missing)
            warnings.append(
                f"No {bucket} feat recorded for level {levels} -- the class "
                f"grants one at each of those levels and the character is "
                f"level {level}.")
    return warnings

# Where to look each part of a character up, in the order a player would read
# them off a sheet. `pack` is checked first so a name that exists in several
# packs resolves to the right one -- "Death Warden Dwarf" is both a heritage
# and a heritage feat, and "Battle Medicine" is a feat and (as a grant) a
# background line.
_PFS_SUBJECTS = (
    ("class", "classes"),
    ("ancestry", "ancestries"),
    ("heritage", "heritages"),
    ("background", "backgrounds"),
)


def _validate_pfs(character: dict[str, Any]) -> dict[str, Any]:
    """Every option on the character checked against the PFS heuristic, as a
    structured report rather than a yes/no.

    A verdict alone is not actionable: a player told "not legal" still has to
    work out *which* choice needs the boon, and a build is usually one option
    away from legal. So this returns the offending options with their rarity
    and source, plus the ones that couldn't be checked at all, which matter
    just as much -- a name this project can't resolve is not evidence of
    legality.

    Coverage is class, ancestry, heritage, background, every feat, and every
    piece of equipment. An earlier version checked feats alone, which meant a
    character could be reported clean while their *class* was restricted.

    The heuristic itself remains rarity plus a curated override list, and is
    not authoritative -- see pfs.py. Notably it cannot see that the same feat
    can be Standard in one printing and Limited in another (Defy the Darkness
    is Limited in the Advanced Player's Guide and Standard in its Player Core
    reprint), because rarity is identical in both.
    """
    overrides = pfs.load_overrides()
    requires_boon: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []

    def check(kind: str, name: str, pack: str | None) -> None:
        name = str(name or "").strip()
        if not name or name.lower() in ("not set", "none", "-"):
            return
        base = "SELECT name, rarity, source_book FROM entries WHERE "
        rows: list[dict[str, Any]] = []
        if pack:
            rows = _fetchall(base + "name = ? COLLATE NOCASE AND pack = ? LIMIT 1",
                             (name, pack))
        if not rows:
            # The character's own label for a thing often disagrees with the
            # pack it lives in -- a heritage recorded as a feat, say -- so a
            # miss in the expected pack is not a miss.
            rows = _fetchall(base + "name = ? COLLATE NOCASE LIMIT 1", (name,))
        if not rows:
            # Several entries are stored under a qualified name the character
            # records bare: "Spirit Familiar" is "Spirit Familiar (Animist)".
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = _fetchall(
                base + "name LIKE ? ESCAPE '\\' COLLATE NOCASE", (f"{escaped} (%",))
            if len({r["rarity"] for r in rows}) > 1:
                unchecked.append({
                    "name": name, "kind": kind,
                    "reason": "several entries share this name and disagree on "
                              "rarity; check by hand"})
                return
        if not rows:
            unchecked.append({"name": name, "kind": kind,
                              "reason": "no entry of that name in the rules data"})
            return
        row = rows[0]
        status = pfs.pfs_status(row["name"], row["rarity"], overrides)
        if status["status"] != "legal":
            requires_boon.append({
                "name": row["name"], "kind": kind,
                "status": status["status"], "rarity": row["rarity"],
                "source": row["source_book"], "note": status["note"],
            })

    for kind, pack in _PFS_SUBJECTS:
        check(kind, character.get(kind), pack)
    for feat in character.get("feats") or []:
        if isinstance(feat, (list, tuple)) and feat:
            check("feat", feat[0], "feats")
    for weapon in character.get("weapons") or []:
        if isinstance(weapon, dict):
            check("weapon", weapon.get("name"), "equipment")
    for armor in character.get("armor") or []:
        if isinstance(armor, dict):
            check("armor", armor.get("name"), "equipment")
    for item in character.get("equipment") or []:
        if isinstance(item, (list, tuple)) and item:
            check("item", item[0], "equipment")
        elif isinstance(item, dict):
            check("item", item.get("name"), "equipment")

    return {
        "legal": not requires_boon,
        "requires_boon": requires_boon,
        "unchecked": unchecked,
        "authoritative": False,
        "note": ("Rarity-based heuristic plus a curated override list, not a "
                 "read of Paizo's Additional Resources. 'legal': true means "
                 "nothing was flagged, not that the build is sanctioned -- "
                 "check anything that matters against the current Additional "
                 "Resources document."),
    }


def _validate_attribute_boosts(character: dict[str, Any]) -> list[str]:
    """Two checks on a character's attribute array.

    **Half-steps, at 20th only.** An attribute modifier is
    `(score - 10) // 2`, so an odd score holds a point that isn't buying a
    modifier -- but below 20th that is usually the *opposite* of waste. An odd
    score is half-way to its next modifier and completes at the following
    milestone, arriving one milestone sooner than an even score would reach
    it: a level-10 character with Con 19 has +5 from 15th, where Con 18
    would wait until 20th. Only at 20th is there nothing left to complete the
    step, so only 20th is reported. An earlier version warned at every
    milestone and was simply wrong about a level-10 character.

    Not an error even then: a player may want a specific score for an item
    requirement, a story reason, or a planned apex.

    **Derivability.** When the character carries Pathbuilder's `breakdown`
    (which boosts were applied where), the recorded boosts are replayed --
    ancestry boosts and flaw, background, class key attribute, then four
    distinct boosts at each milestone -- and the result compared with the
    recorded scores. A mismatch is a warning rather than an error because an
    invested apex item legitimately adds 2 to one attribute *after* the
    boosts, and this project's character files record the played score
    (confirmed live on a real build carrying an apex item), so the honest
    reading of a +2 discrepancy on a single attribute is "probably an apex
    item", not "illegal array".
    """
    warnings: list[str] = []
    abilities = character.get("abilities") or {}
    scores = {k: v for k, v in abilities.items()
              if k in _ABILITIES and isinstance(v, int)}
    if not scores:
        return warnings
    level = int(character.get("level") or 1)

    if level >= 20:
        odd = sorted(k.upper() for k, v in scores.items() if v % 2)
        if odd:
            warnings.append(
                f"Attribute half-steps at level {level}: {', '.join(odd)} "
                f"{'is' if len(odd) == 1 else 'are'} odd, and no further "
                f"boosts are coming, so that point buys no modifier "
                f"((score - 10) // 2). Redirect it to an attribute that lands "
                f"even -- and redirect the 20th-level boost specifically, "
                f"since that is the one with no later milestone to complete "
                f"it; an earlier boost that leaves a score odd is only half a "
                f"step, and finishes at the next milestone.")

    breakdown = abilities.get("breakdown")
    if not isinstance(breakdown, dict):
        return warnings

    replay = {k: 10 for k in _ABILITIES}
    for attr in breakdown.get("ancestryFlaws") or []:
        # Exports capitalise attribute names inconsistently -- Pathbuilder
        # writes "Wis" in `ancestryFlaws` and "wis" elsewhere in the same
        # file. Everything here folds case.
        if str(attr).lower() in replay:
            replay[str(attr).lower()] -= 2

    def apply(attrs: Any) -> None:
        for attr in attrs or []:
            attr = str(attr).lower()
            if attr in replay:
                replay[attr] += 2 if replay[attr] < 18 else 1

    apply(breakdown.get("ancestryBoosts"))
    apply(breakdown.get("ancestryFree"))
    # Two spellings in the wild for the same thing.
    apply(breakdown.get("backgroundBoosts") or breakdown.get("backgroundAbilities"))
    apply(breakdown.get("classBoosts"))
    # Likewise the milestones: a dict keyed by level, or one key per level.
    milestones = dict(breakdown.get("mapLevelledBoosts") or {})
    for level_key in _BOOST_MILESTONES:
        flat = breakdown.get(f"lvl{level_key}")
        if flat is not None:
            milestones.setdefault(str(level_key), flat)
    for milestone, attrs in sorted(milestones.items(), key=lambda kv: int(kv[0])):
        if int(milestone) > level:
            continue
        chosen = [str(a).lower() for a in attrs or []]
        if len(set(chosen)) != len(chosen):
            warnings.append(
                f"Level-{milestone} attribute boosts repeat an attribute "
                f"({', '.join(chosen)}); boosts gained at the same time must "
                f"each go to a different attribute.")
        if len(chosen) > 4:
            warnings.append(
                f"Level-{milestone} grants 4 attribute boosts, but "
                f"{len(chosen)} are recorded.")
        apply(chosen)

    off = {k: scores[k] - replay[k] for k in scores
           if k in replay and scores[k] != replay[k]}
    if off:
        detail = ", ".join(f"{k.upper()} {scores[k]} vs {replay[k]}"
                           for k in sorted(off))
        apex = [k for k, v in off.items() if v == 2]
        note = (" A single attribute 2 higher than its boosts is what an "
                "invested apex item looks like." if len(off) == 1 and apex
                else "")
        warnings.append(
            f"Recorded attributes don't match the recorded boosts: {detail}."
            f"{note}")
    return warnings


def _worn_armor_stats(character: dict[str, Any]) -> dict[str, Any] | None:
    """Looks up the character's worn armor (Pathbuilder's `armor` list --
    objects with `name`/`worn`, confirmed against pathmuncher source) in the
    equipment pack for its acBonus/dexCap/category. Returns None if nothing
    is worn or the name isn't found, in which case the caller should fall
    back to the unarmored baseline. `potency` (the armor's potency rune
    bonus) and `resilient` (its resilient rune's item bonus to saves) come
    from the character's own armor entry (Pathbuilder's `pot`/`res` fields),
    not the equipment pack -- they're a property of the specific physical
    item the player has, not the base armor type."""
    worn = next((a for a in character.get("armor", []) if a.get("worn")), None)
    if not worn:
        return None
    rows = _fetchall(
        "SELECT raw_json FROM entries WHERE pack = 'equipment' AND type = 'armor' AND name = ? COLLATE NOCASE",
        (worn["name"],),
    )
    if not rows:
        return None
    system = json.loads(rows[0]["raw_json"])["system"]
    return {
        "ac_bonus": system.get("acBonus", 0),
        "dex_cap": system.get("dexCap"),
        "category": system.get("category", "unarmored"),
        "potency": worn.get("pot", 0) or 0,
        "resilient": m.resilient_tier(worn.get("res")),
    }


def _feat_prerequisite_names(feat_name: str) -> list[str]:
    """All named-reference targets in one feat's own prerequisites (both
    plain `named_reference` and `compound_named` any-of lists), by exact
    display name lookup. Used to answer 'does feat X require dedication D'
    without re-deriving prerequisite structure the ingestion already
    parsed."""
    entry_rows = _fetchall("SELECT id FROM entries WHERE name = ? COLLATE NOCASE", (feat_name,))
    if not entry_rows:
        return []
    prereq_rows = _fetchall(
        "SELECT kind, structured FROM prerequisites WHERE entry_id = ?", (entry_rows[0]["id"],)
    )
    names: list[str] = []
    for p in prereq_rows:
        if p["kind"] not in ("named_reference", "compound_named"):
            continue
        structured = json.loads(p["structured"]) if p["structured"] else {}
        names.extend(n for n in (structured.get("any_of") or [structured.get("name")]) if n)
    return names


def _archetype_member_count(character: dict[str, Any], dedication_name: str) -> int:
    """How many of the character's *other* taken feats belong to the
    archetype anchored by `dedication_name` -- i.e. feats whose own
    prerequisites name-reference it (the same relationship `rules_related`
    computes as `required_by` for a single entry, reused here in bulk).
    This is the quantity every dedication feat's freeform Special text
    gates a second, different dedication behind ("...until you've gained
    two other feats from this archetype"), but which lives nowhere as a
    structured field -- confirmed live that Foundry's own reference
    implementation doesn't enforce this rule in code either
    (`src/module/item/feat/document.ts` only normalizes the `dedication`
    trait to imply `archetype`, nothing checks a count anywhere in
    `src/module`), so this is genuinely additive validation this project
    does that the upstream system doesn't, not a gap being closed to match
    existing behavior."""
    taken_names = [f[0].strip() for f in character.get("feats", []) if f and f[0]]
    target = dedication_name.strip().lower()
    if target not in {t.lower() for t in taken_names}:
        return 0
    count = 0
    for name in taken_names:
        if name.lower() == target:
            continue
        if any(n.strip().lower() == target for n in _feat_prerequisite_names(name)):
            count += 1
    return count


def _committed_dedications_below_threshold(character: dict[str, Any]) -> list[str]:
    """Names of dedication feats the character has already taken that
    haven't yet met the two-other-archetype-feats threshold -- i.e.
    archetypes a new, different dedication currently can't be added on top
    of. Empty for a character with zero or one dedication (no restriction
    can apply with fewer than two archetypes in play)."""
    taken_names = [f[0].strip() for f in character.get("feats", []) if f and f[0]]
    if not taken_names:
        return []
    placeholders = ",".join("?" for _ in taken_names)
    rows = _fetchall(
        f"SELECT name, traits FROM entries WHERE name IN ({placeholders}) COLLATE NOCASE",
        tuple(taken_names),
    )
    dedications = [r["name"] for r in rows if "dedication" in json.loads(r["traits"] or "[]")]
    return [d for d in dedications if _archetype_member_count(character, d) < 2]


_LORE_PREFIX_RE = re.compile(r"^lore\s*:\s*")
_LORE_SUFFIX_RE = re.compile(r"\s*lore$")


def _normalize_lore(name: str) -> str:
    """Folds the four spellings of a Lore skill name that occur in real
    data down to a bare, lowercased topic ("undead") for comparison:

    * ``"Undead"`` -- Pathbuilder's own JSON export, which strips the
      suffix. Confirmed against several real exports in this project, all
      storing e.g. ``["Warfare", 2]``.
    * ``"Undead Lore"`` -- the game's real skill name, used by this
      project's ingested background/class data and by the hand-authored
      character files in ``characters/``.
    * ``"UndeadLore "`` -- an upstream ``foundryvtt/pf2e`` typo in Once
      Bitten's ``system.trainedSkills.lore`` (no space before "Lore", one
      trailing space). Ingestion mirrors upstream verbatim so the database
      stays diffable against the source, which means the typo returns with
      every release and has to be absorbed at comparison time instead.
    * ``"Lore: Undead"`` -- how Pathbuilder *displays* the skill in its own
      skills list. Not an export form, but the one someone hand-copying
      from the app's UI writes, and ``characters/`` already contains
      hand-authored files.

    A name carrying no "lore" marker at all comes back unchanged (bar
    lowercasing), which is what lets :func:`_is_choice_lore` separate a
    named grant from a player-choice one.
    """
    text = _LORE_PREFIX_RE.sub("", name.strip().lower())
    return _LORE_SUFFIX_RE.sub("", text).strip()


def _unwrap_lore_grant(name: str) -> str:
    """Strips editorial decoration off an ingested lore grant, leaving the
    skill name. Exists for one row: Returned's
    ``'**Boneyard Lore (with Additional Lore perks)'``, where upstream
    markdown and a parenthetical aside leaked into the data around an
    otherwise ordinary ``"Boneyard Lore"``. Leading non-word characters and
    anything from a ``" ("`` onward come off; every other grant passes
    through untouched.
    """
    text = re.sub(r"^\W+", "", name.strip())
    return text.split(" (", 1)[0].strip()


def _is_choice_lore(name: str) -> bool:
    """True if a lore grant describes a Lore the *player* names rather than
    a fixed one, e.g. Foreign Aid's ``'Lore skill pertaining to your place
    of origin'`` or Energy Scarred's ``'Lore associated with the chosen
    energy'``. Pathbuilder models exactly these with a free-text box for
    the player to fill in, and the filled-in value lands in the character's
    ordinary ``lores`` list -- so the grant is real and worth validating,
    it just can't be validated *by name*. See
    :func:`_validate_fixed_trained_skills`, which checks presence instead
    of identity for these.

    Detection is by shape, not by pattern-matching the prose: a real grant
    is a skill name ending in "Lore" (428 of 430 rows once
    :func:`_unwrap_lore_grant` has run, which recovers Returned's
    markdown-wrapped ``"Boneyard Lore"``); the two choice grants are
    sentences that merely *begin* with the word, ending in "origin" and
    "energy". Requiring the suffix separates them without encoding either
    sentence.
    """
    return not _LORE_SUFFIX_RE.search(name.strip().lower())


def _validate_fixed_trained_skills(character: dict[str, Any]) -> list[str]:
    """Checks the character has at least trained rank in every skill their
    background and class *unconditionally* grant (their `trained_skills.
    fixed`/`.lore` -- the deterministic subset, not the player's free
    "additional" choices, which can't be cleanly distinguished from later
    Skill Increases without risking a false positive: by level 10 a
    character has picked up several increases beyond their initial free
    choices, and this project doesn't track which specific skill each
    individual increase went into, only the total count unlocked per
    level). Warnings, not errors -- this is a narrower, more conservative
    check than a full count/identity validation would be, kept to the
    part that's unambiguous at any level."""
    warnings: list[str] = []
    prof = character.get("proficiencies", {})
    # Character-side and grant-side lore names use different conventions
    # (and more than one convention each) -- see `_normalize_lore` for the
    # four spellings and where each comes from. Fold both sides through it
    # rather than comparing raw strings.
    lore_names = {
        _normalize_lore(l[0])
        for l in character.get("lores", []) if l and l[0]
    }

    bg_rows = _fetchall(
        "SELECT trained_skills FROM background_boosts WHERE background_slug = ?",
        (_real_slug("backgrounds", character.get("background", "")),),
    )
    class_rows = _fetchall(
        "SELECT trained_skills FROM class_progression WHERE class_slug = ?",
        (_real_slug("classes", character.get("class", "")),),
    )
    named_grants: list[tuple[str, str]] = []
    choice_grants: list[str] = []
    for label, rows in (("background", bg_rows), ("class", class_rows)):
        if not rows or not rows[0]["trained_skills"]:
            continue
        trained = json.loads(rows[0]["trained_skills"])
        if label == "class":
            trained = class_skills.apply(
                _real_slug("classes", character.get("class", "")), trained
            )
        for skill in trained.get("fixed", []):
            if prof.get(skill, 0) <= 0:
                warnings.append(
                    f"{label.capitalize()} grants training in '{skill}' but it's not "
                    "trained (or higher) in this character's proficiencies"
                )
        for lore in trained.get("lore", []):
            grant = _unwrap_lore_grant(lore)
            if _is_choice_lore(grant):
                choice_grants.append(label)
            else:
                named_grants.append((label, grant))

    matched: set[str] = set()
    for label, grant in named_grants:
        key = _normalize_lore(grant)
        if key in lore_names:
            matched.add(key)
        else:
            warnings.append(
                f"{label.capitalize()} grants training in the '{grant}' Lore "
                "skill but it's not present in this character's lores"
            )

    # A player-choice grant has no name to match against, so check presence
    # rather than identity: each one needs a recorded Lore left spare once
    # the named grants have claimed theirs. Catches the case that actually
    # goes wrong -- a character with such a background and no Lore recorded
    # at all -- without inventing an identity check that can't exist.
    if choice_grants:
        spare = len(lore_names - matched)
        if spare < len(choice_grants):
            source = (
                choice_grants[0].capitalize()
                if len(set(choice_grants)) == 1
                else "Background/class"
            )
            warnings.append(
                f"{source} grants {len(choice_grants)} Lore skill(s) of the "
                f"player's own choosing, but the character records {spare} "
                "Lore not already accounted for by a named grant"
            )
    return warnings


def _level1_ability_score(character: dict[str, Any], ability: str) -> int | None:
    """Reconstructs an ability's score as it stood at the end of 1st-level
    character creation, from Pathbuilder's own `abilities.breakdown` boost
    history -- NOT the character's current/final score. This matters
    because ability boosts gained at later milestone levels (5, 10, 15, 20)
    don't retroactively change anything decided during character creation
    (e.g. how many bonus trained skills Intelligence granted); only the
    boosts actually applied *during* 1st-level creation do. Applies flaws
    first, then ancestry free/fixed, background, class, and the level-1
    entry of `mapLevelledBoosts` in that order (the real chargen sequence),
    using the core +2-if-below-18-else-+1 rule per boost. Returns None if
    `breakdown` is missing or malformed, since guessing would risk a wrong
    floor in either direction -- callers should treat None as "can't
    determine, don't check" rather than falling back to the current score,
    which is only coincidentally correct for a level-1 character."""
    breakdown = character.get("abilities", {}).get("breakdown")
    if not isinstance(breakdown, dict):
        return None
    score = 10
    # Real Pathbuilder exports capitalize ability names in `breakdown`
    # ("Str", "Con") where this project's hand-authored character files use
    # lowercase -- confirmed against a live export. Both sides get folded,
    # here and in the boost loop below; comparing raw would silently drop a
    # capitalized flaw and overstate the score by 2.
    flaws = breakdown.get("ancestryFlaws", [])
    if not isinstance(flaws, list):
        return None
    if any(isinstance(f, str) and f.lower() == ability for f in flaws):
        score -= 2
    boost_sources = [
        breakdown.get("ancestryFree", []),
        breakdown.get("ancestryBoosts", []),
        breakdown.get("backgroundBoosts", []),
        breakdown.get("classBoosts", []),
        breakdown.get("mapLevelledBoosts", {}).get("1", []),
    ]
    for source in boost_sources:
        if not isinstance(source, list):
            return None
        for boosted in source:
            if isinstance(boosted, str) and boosted.lower() == ability:
                score += 1 if score >= 18 else 2
    return score


def _validate_trained_skill_count(character: dict[str, Any]) -> list[str]:
    """Checks the character has at least as many trained-or-better skills
    (skills at rank >= trained, plus Lores) as the level-1 minimum: class
    `fixed` skills + class `additional` free picks + the character's
    *level-1* Intelligence modifier (if positive) + background `fixed`/
    `lore` skills + one pick for a background `ChoiceSet` skill, if it has
    one.

    The Intelligence-modifier term is the point of this check: it's a core
    rule applying to *every* class's additional-trained-skills count
    ("...becomes trained in a number of skills equal to [class value] plus
    your Intelligence modifier" -- Player Core, Skills step of character
    creation), but `list_classes`' `trained_skills.additional` field is
    deliberately just the class's own flat baseline (see its docstring) --
    a caller who treats that number as the *total* free skill picks, without
    separately adding Intelligence modifier, undercounts. Confirmed as a
    real, previously-hit mistake building a level-10 Magus with Int 14 (+2)
    at 1st level: 2 additional trained skills were picked instead of 4.

    Uses `_level1_ability_score` for Intelligence specifically -- not the
    character's current score -- since a later ability boost (at level 5,
    10, ...) doesn't retroactively grant more starting trained skills; only
    the Intelligence score as it stood at the end of 1st-level creation
    does. If `abilities.breakdown` isn't present/well-formed, this check is
    skipped entirely for a character above level 1 (an unreconstructable
    level-1 Intelligence score means no safe floor can be computed), and
    for a level-1 character it falls back to the current score, which is
    exactly the level-1 score by definition.

    This is otherwise a floor check, safe at any character level: a Skill
    Increase can train a brand-new skill instead of raising an
    already-trained one, so the actual count only ever grows from the
    level-1 baseline -- never a false positive for a higher-level character
    who has spent increases on new skills rather than upgrades."""
    warnings: list[str] = []
    prof = character.get("proficiencies", {})
    actual = sum(1 for skill in m.KNOWN_SKILLS if prof.get(skill, 0) >= 2)
    actual += len(character.get("lores", []))

    class_rows = _fetchall(
        "SELECT trained_skills FROM class_progression WHERE class_slug = ?",
        (_real_slug("classes", character.get("class", "")),),
    )
    if not class_rows or not class_rows[0]["trained_skills"]:
        return warnings
    class_trained = json.loads(class_rows[0]["trained_skills"])
    expected = len(class_trained.get("fixed", [])) + class_trained.get("additional", 0)

    background_slug = _real_slug("backgrounds", character.get("background", ""))
    bg_rows = _fetchall(
        "SELECT trained_skills FROM background_boosts WHERE background_slug = ?",
        (background_slug,),
    )
    if bg_rows and bg_rows[0]["trained_skills"]:
        bg_trained = json.loads(bg_rows[0]["trained_skills"])
        expected += len(bg_trained.get("fixed", [])) + len(bg_trained.get("lore", []))
        bg_choice_rows = _fetchall(
            "SELECT cs.choices FROM item_choice_sets cs "
            "JOIN entries e ON e.id = cs.entry_id "
            "WHERE e.pack = 'backgrounds' AND e.slug = ?",
            (background_slug,),
        )
        expected += len(bg_choice_rows)  # each ChoiceSet is one skill pick

    level1_int = _level1_ability_score(character, "int")
    if level1_int is None:
        if character.get("level", 1) != 1:
            return warnings  # can't reconstruct level-1 Int; don't guess a floor
        level1_int = m.default_character_abilities(character).get("int", 10)
    expected += max(0, m.ability_mod(level1_int))

    if actual < expected:
        warnings.append(
            f"Character has {actual} trained-or-better skill(s)/lore(s), but the "
            f"level-1 minimum is {expected} (class fixed + additional skill picks, "
            "background fixed/lore/choice skills, and the character's level-1 "
            "Intelligence modifier if positive -- the Int-mod bonus applies to every "
            "class's trained-skill count; double-check it wasn't left out)."
        )
    return warnings


_PROFICIENCY_RANK_NAMES = ["untrained", "trained", "expert", "master", "legendary"]

# Direct-mapped subfeature keys -> the same-named key this project's
# `proficiencies` dict already uses. Everything else in
# `item_proficiency_grants.key` is either 'spellcasting' (skipped -- see
# `_validate_proficiency_ranks`) or a class slug (classDC, handled
# separately since it depends on the character's own class).
_PROFICIENCY_GRANT_KEYS = {
    "perception", "fortitude", "reflex", "will",
    "simple", "martial", "advanced", "unarmed",
    "unarmored", "light", "medium", "heavy",
}


def _validate_proficiency_ranks(character: dict[str, Any]) -> list[str]:
    """Checks the character's save/Perception/weapon/armor/class-DC
    proficiency ranks are at least as high as what their class's level-1
    baseline plus every class feature/feat granting a rank increase up to
    their level should produce.

    This closes a gap once tracked as genuinely blocked on unstructured
    data: class features like Juggernaut (Fortitude to master), Weapon
    Legend, and Perception Mastery don't carry a rule element for their
    rank bump at all (confirmed live -- their own `system.rules` is empty)
    -- Foundry's actor-preparation code reads a separate structured field
    instead, `system.subfeatures.proficiencies` (see
    `item_proficiency_grants`), which this project's ingestion previously
    never read either. It's real structured data, not flavor text, so it's
    safe to validate against.

    Deliberately a floor check, not an exact-match one: warns only when the
    character's actual rank is *lower* than the computed minimum, never
    when it's higher (magic items, ABP, and any other rank source this
    project doesn't track could legitimately push it higher without this
    being a real problem). Deliberately skips the 'spellcasting' subfeature
    key entirely -- unlike every other key here it doesn't map onto a
    fixed `proficiencies`-dict key; Pathbuilder's convention names that key
    after the character's actual magic tradition (e.g. 'divine'), and nothing
    ingested ties a class to its tradition reliably enough to guess right
    for every caster archetype/dedication combination without risking a
    wrong assertion."""
    warnings: list[str] = []
    class_slug = _real_slug("classes", character.get("class", ""))
    class_rows = _fetchall(
        "SELECT perception_rank, fortitude_rank, reflex_rank, will_rank, class_dc_rank, "
        "attacks, defenses, granted_items FROM class_progression WHERE class_slug = ?",
        (class_slug,),
    )
    if not class_rows:
        return warnings
    c = class_rows[0]
    level = character.get("level") or 1

    expected: dict[str, int] = {}

    def _bump(key: str, rank: int | None) -> None:
        if rank is None:
            return
        expected[key] = max(expected.get(key, 0), rank)

    _bump("perception", c["perception_rank"])
    _bump("fortitude", c["fortitude_rank"])
    _bump("reflex", c["reflex_rank"])
    _bump("will", c["will_rank"])
    _bump("classDC", c["class_dc_rank"])
    attacks = json.loads(c["attacks"]) if c["attacks"] else {}
    for atk_key in ("simple", "martial", "advanced", "unarmed"):
        _bump(atk_key, attacks.get(atk_key))
    defenses = json.loads(c["defenses"]) if c["defenses"] else {}
    for def_key in ("unarmored", "light", "medium", "heavy"):
        _bump(def_key, defenses.get(def_key))

    granted_names = [
        g["name"] for g in json.loads(c["granted_items"])
        if g.get("level") is not None and g["level"] <= level
    ]
    taken_names = [f[0].strip() for f in character.get("feats", []) if f and f[0]]
    all_names = granted_names + taken_names
    if all_names:
        placeholders = ",".join("?" for _ in all_names)
        rows = _fetchall(
            "SELECT ipg.key AS grant_key, ipg.rank AS grant_rank FROM item_proficiency_grants ipg "
            f"JOIN entries e ON e.id = ipg.entry_id WHERE e.name IN ({placeholders}) COLLATE NOCASE",
            tuple(all_names),
        )
        for row in rows:
            grant_key = row["grant_key"]
            if grant_key == "spellcasting":
                continue
            if grant_key == class_slug:
                grant_key = "classDC"
            elif grant_key not in _PROFICIENCY_GRANT_KEYS:
                continue  # a different class's classDC key -- doesn't apply here
            _bump(grant_key, row["grant_rank"])

    prof = character.get("proficiencies", {})
    for key, rank in expected.items():
        expected_scale = rank * 2
        if prof.get(key, 0) < expected_scale:
            warnings.append(
                f"'{key}' proficiency should be at least "
                f"{_PROFICIENCY_RANK_NAMES[rank]} (character's class progression "
                f"grants this by level {level}), but it's lower on this character"
            )
    return warnings


def _validate_dedication_exclusivity(character: dict[str, Any]) -> list[str]:
    """Level-ordered simulation of the archetype "one dedication feat at a
    time" rule (see `_archetype_member_count`'s docstring for why this is
    checked at all -- Foundry's own system doesn't). Unlike the point-in-
    time check in `list_available_feats`/`check_prerequisite` (which only
    needs "is a committed archetype currently below threshold"), a
    finished build needs to know it was *never* violated at any point
    along the way -- e.g. two archetype feats both taken at the same level
    a dedication was gained can't retroactively justify that dedication.
    Feats without a recognized integer level (position 3 of the Pathbuilder
    tuple) are skipped rather than guessed into an order."""
    leveled_feats = sorted(
        (
            (f[3], f[0].strip())
            for f in character.get("feats", [])
            if f and f[0] and len(f) > 3 and isinstance(f[3], int)
        ),
        key=lambda pair: pair[0],
    )
    if not leveled_feats:
        return []

    names = [name for _, name in leveled_feats]
    placeholders = ",".join("?" for _ in names)
    trait_rows = _fetchall(
        f"SELECT name, traits FROM entries WHERE name IN ({placeholders}) COLLATE NOCASE", tuple(names)
    )
    dedication_names = {r["name"].lower() for r in trait_rows if "dedication" in json.loads(r["traits"] or "[]")}

    errors: list[str] = []
    committed: list[str] = []  # lowercased, for matching
    display_names: dict[str, str] = {}  # lowercased -> original casing, for messages
    member_counts: dict[str, int] = {}
    for level, name in leveled_feats:
        name_lower = name.lower()
        if name_lower in dedication_names:
            if name_lower not in committed:
                unfulfilled = [d for d in committed if member_counts.get(d, 0) < 2]
                if unfulfilled:
                    errors.append(
                        f"'{name}' (level {level}) taken as a new archetype dedication while "
                        f"{' and '.join(display_names[d] for d in unfulfilled)} still needed two "
                        "other feats from their own archetype first"
                    )
                committed.append(name_lower)
                display_names[name_lower] = name
                member_counts[name_lower] = 0
        else:
            for ref in _feat_prerequisite_names(name):
                ref_lower = ref.strip().lower()
                if ref_lower in committed:
                    member_counts[ref_lower] = member_counts.get(ref_lower, 0) + 1
    return errors


def _character_has_familiar(character: dict[str, Any]) -> bool:
    """Checks whether the character has taken any feat whose `GrantItem`
    target (see `item_grants`) is the feat literally named "Pet" -- the
    shared underlying mechanism every familiar-granting path checked in
    this codebase turned out to use (Familiar, Spirit Familiar (Animist),
    and Leshy Familiar all `GrantItem` that same target, each customizing
    it further with their own additional rule elements for flavor/traits).

    Also handles a real-world naming wrinkle hit live against an actual
    Pathbuilder export: Pathbuilder sometimes stores a disambiguated feat's
    *short* name ("Spirit Familiar") where this project's ingested data
    has the full disambiguated name ("Spirit Familiar (Animist)", to
    distinguish it from "Spirit Familiar (Witch)") -- an exact-name match
    alone would miss this and silently fall back to "no familiar" for a
    character who genuinely has one. Falls back to a "does a real granter
    name start with `<taken name> (`" check for exactly this shape.

    **Known gap, not fixed here:** Familiar Master Dedication's ingested
    rules don't include an unconditional grant of this item at all --
    only a conditional grant of Enhanced Familiar gated on a `self:
    has-familiar` predicate (a roll-option flag Foundry computes at
    runtime, which this project has no equivalent for). A character whose
    *only* route to a familiar is Familiar Master Dedication won't be
    detected by this check. Confirmed this isn't an ingestion omission on
    this project's end -- the feat's own `system.rules` in the raw source
    genuinely doesn't grant a base familiar item unconditionally."""
    taken_names = [f[0].strip() for f in character.get("feats", []) if f and f[0]]
    if not taken_names:
        return False
    granter_rows = _fetchall(
        "SELECT granter.name AS name FROM item_grants g "
        "JOIN entries granter ON granter.id = g.granter_id "
        "JOIN entries granted ON granted.id = g.granted_id "
        "WHERE granted.name = 'Pet'"
    )
    granter_names = {r["name"].lower() for r in granter_rows}
    for taken in taken_names:
        taken_lower = taken.lower()
        if taken_lower in granter_names:
            return True
        if any(g.startswith(f"{taken_lower} (") for g in granter_names):
            return True
    return False


def _character_class_hp(character: dict[str, Any]) -> int | None:
    """The character's class's base HP-per-level (before Constitution),
    straight from `class_progression.hp` -- what a handful of feat
    prerequisites gate on directly (e.g. "class granting no more Hit
    Points per level than 8 + your Constitution modifier", a real
    prerequisite on several low-HP-class-exclusive feats). None if the
    character's class isn't recognized."""
    rows = _fetchall(
        "SELECT hp FROM class_progression WHERE class_slug = ?",
        (_real_slug("classes", character.get("class", "")),),
    )
    return rows[0]["hp"] if rows else None


def _build_derived(character: dict[str, Any]) -> dict[str, Any]:
    """Facts about the character that `pf2e_math.check_single_prerequisite`
    needs but can't compute itself (it's deliberately DB-free) -- resolved
    here once and threaded through every prerequisite-checking call site
    instead of each one re-deriving the same three facts inline."""
    return {
        "vision": _character_vision_level(character),
        "has_familiar": _character_has_familiar(character),
        "class_hp": _character_class_hp(character),
    }


def _character_vision_level(character: dict[str, Any]) -> str | None:
    """Resolve a character's effective vision level ('normal' /
    'low-light-vision' / 'darkvision') from their ancestry's own
    `system.vision` field plus any heritage-level `Sense` rule element
    grants layered on top (e.g. Duskwalker's "low-light vision, or
    darkvision if your ancestry already has low-light vision").

    Deliberately narrow predicate handling: only the specific
    self-referential `"self:<level>:from-ancestry"` shape (confirmed
    against Duskwalker's real rule elements) is resolved -- an unconditional
    Sense grant always applies, a conditional one only applies if it
    matches that exact pattern and the ancestry's own vision is at that
    level. Any other predicate shape is skipped rather than guessed at.
    Returns None if the character's ancestry isn't recognized at all (vs.
    'normal', a real value meaning "recognized, but no low-light vision or
    better")."""
    ancestry_slug = _real_slug("ancestries", character.get("ancestry", ""))
    ancestry_rows = _fetchall("SELECT vision FROM ancestry_boosts WHERE ancestry_slug = ?", (ancestry_slug,))
    if not ancestry_rows:
        return None
    ancestry_vision = ancestry_rows[0]["vision"] or "normal"
    best = m.VISION_RANK.get(ancestry_vision, 0)

    heritage_name = character.get("heritage")
    if heritage_name:
        heritage_rows = _fetchall(
            "SELECT id FROM entries WHERE pack = 'heritages' AND name = ? COLLATE NOCASE", (heritage_name,)
        )
        if heritage_rows:
            sense_rows = _fetchall(
                "SELECT selector, predicate FROM item_senses WHERE entry_id = ?", (heritage_rows[0]["id"],)
            )
            for row in sense_rows:
                rank = m.VISION_RANK.get(row["selector"])
                if rank is None:
                    continue
                predicate = json.loads(row["predicate"]) if row["predicate"] else None
                if predicate is None:
                    best = max(best, rank)
                    continue
                if (
                    isinstance(predicate, list)
                    and len(predicate) == 1
                    and isinstance(predicate[0], str)
                ):
                    match = re.match(r"^self:(.+):from-ancestry$", predicate[0])
                    if match and match.group(1) == ancestry_vision:
                        best = max(best, rank)

    for level, rank in m.VISION_RANK.items():
        if rank == best:
            return level
    return "normal"


def _fallback_ancestry_hp(ancestry_name: str) -> int:
    slug = _real_slug("ancestries", ancestry_name)
    rows = _fetchall("SELECT hp FROM ancestry_boosts WHERE ancestry_slug = ?", (slug,))
    return rows[0]["hp"] if rows and rows[0]["hp"] is not None else 0


def _fallback_class_baseline(class_name: str) -> tuple[int, int | None]:
    """Returns (classhp, unarmored_rank) -- unarmored_rank already converted
    to this project's 0/2/4/6/8 convention, None if the class has no
    unarmored-defense entry at all (shouldn't happen for a real class, but
    matches this project's general "don't invent a default" stance)."""
    slug = _real_slug("classes", class_name)
    rows = _fetchall("SELECT hp, defenses FROM class_progression WHERE class_slug = ?", (slug,))
    if not rows:
        return 0, None
    classhp = rows[0]["hp"] or 0
    defenses = json.loads(rows[0]["defenses"] or "{}")
    unarmored = defenses.get("unarmored")
    return classhp, _foundry_rank_to_project(unarmored) if unarmored is not None else None


def calculate_derived_stats(character: dict[str, Any]) -> dict[str, Any]:
    """Deterministic PF2e math: AC, saves, Perception, skill totals, HP,
    class DC, and spell DC/attack per casting tradition the character has
    trained. Never left to model arithmetic -- proficiency stacking
    (untrained = ability mod only, no level) is easy to get wrong.

    AC uses the character's worn armor (Pathbuilder's `armor` list) if
    present, else the unarmored baseline, and includes the armor's potency
    rune if any. Does not yet include a raised shield's bonus -- see
    pf2e_math.armor_ac docstring. All three saves include the armor's
    resilient rune's item bonus, if any -- previously omitted entirely, so
    a character wearing resilient armor had their printed saves understate
    what they actually have.

    HP's `attributes.ancestryhp`/`attributes.classhp` and AC's
    `proficiencies.unarmored` fall back to a lookup against this project's
    own ingested `ancestry_boosts`/`class_progression` data (via the
    character's `ancestry`/`class` fields) when the caller doesn't supply
    them explicitly -- previously a caller-side omission here silently
    produced a plausible-looking-but-wrong result (confirmed live: HP
    understated by ~90 points, AC by 12, for a real level-10 character
    built without these three fields) rather than an error, since Python's
    dict.get default masked the difference between "explicitly 0" and
    "never set." The explicit-value case is preserved -- these fallbacks
    only apply when the key is genuinely absent, not when it's present and
    zero (e.g. a real Wizard's `classhp` isn't 0, but nothing in this
    system produces a *correct* 0 for these three fields either, so
    treating "key absent" as the signal to fall back is safe in practice).
    """
    character = _native.as_legacy(character)
    level = character.get("level", 1)
    abilities = m.default_character_abilities(character)
    prof = character.get("proficiencies", {})
    attrs = character.get("attributes", {})

    if "ancestryhp" not in attrs or "classhp" not in attrs or "unarmored" not in prof:
        fallback_classhp, fallback_unarmored = _fallback_class_baseline(character.get("class", ""))
        if "ancestryhp" not in attrs or "classhp" not in attrs:
            attrs = {
                **attrs,
                "ancestryhp": attrs.get("ancestryhp", _fallback_ancestry_hp(character.get("ancestry", ""))),
                "classhp": attrs.get("classhp", fallback_classhp),
            }
        if "unarmored" not in prof and fallback_unarmored is not None:
            prof = {**prof, "unarmored": fallback_unarmored}

    armor = _worn_armor_stats(character)
    resilient = armor["resilient"] if armor else 0

    saves = {
        "fortitude": m.total_bonus(abilities["con"], prof.get("fortitude", 0), level) + resilient,
        "reflex": m.total_bonus(abilities["dex"], prof.get("reflex", 0), level) + resilient,
        "will": m.total_bonus(abilities["wis"], prof.get("will", 0), level) + resilient,
    }
    perception = m.total_bonus(abilities["wis"], prof.get("perception", 0), level)

    if armor:
        ac = m.armor_ac(
            abilities["dex"], prof.get(armor["category"], 0), level,
            armor["ac_bonus"], armor["dex_cap"], armor["potency"],
        )
    else:
        ac = 10 + m.total_bonus(abilities["dex"], prof.get("unarmored", 0), level)

    skill_keys = [
        "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
        "intimidation", "medicine", "nature", "occultism", "performance", "religion",
        "society", "stealth", "survival", "thievery",
    ]
    key_ability_by_skill = {
        "acrobatics": "dex", "arcana": "int", "athletics": "str", "crafting": "int",
        "deception": "cha", "diplomacy": "cha", "intimidation": "cha", "medicine": "wis",
        "nature": "wis", "occultism": "int", "performance": "cha", "religion": "wis",
        "society": "int", "stealth": "dex", "survival": "wis", "thievery": "dex",
    }
    skills = {
        s: m.total_bonus(abilities[key_ability_by_skill[s]], prof.get(s, 0), level)
        for s in skill_keys
    }

    key_ability = character.get("keyability", "str")
    class_dc = 10 + m.total_bonus(abilities.get(key_ability, 10), prof.get("classDC", 0), level)

    hp = (
        attrs.get("ancestryhp", 0)
        + level * (attrs.get("classhp", 0) + m.ability_mod(abilities["con"]))
        + attrs.get("bonushp", 0)
        + level * attrs.get("bonushpPerLevel", 0)
    )

    spellcasting = {}
    for tradition in ("Arcane", "Divine", "Occult", "Primal"):
        # Real Pathbuilder exports have been observed storing casting
        # proficiency two different ways depending on class/version -- e.g.
        # a Cleric under the bare tradition key "divine", a Thaumaturge
        # under the always-present "castingDivine" -- so both are checked,
        # matching pf2e_math._spellcasting_trained's prerequisite evaluator.
        value = max(prof.get(f"casting{tradition}", 0), prof.get(tradition.lower(), 0))
        if value > 0:
            key = abilities.get(character.get("keyability", "int"), 10)
            spellcasting[tradition.lower()] = {
                "dc": 10 + m.total_bonus(key, value, level),
                "attack": m.total_bonus(key, value, level),
            }

    return {
        "ac": ac, "saves": saves, "perception": perception, "skills": skills,
        "class_dc": class_dc, "hp": hp, "spellcasting": spellcasting,
    }


# Standard full-caster spell rank progression (Player Core): the highest
# spell rank a prepared/spontaneous full caster (Wizard, Cleric, Druid,
# Bard, Sorcerer, Oracle, Witch, etc.) can access at a given level.
# Deliberately not used for classes with a delayed/hybrid progression
# (Magus, Summoner) -- see list_available_spells' docstring.
def _max_spell_rank(level: int) -> int:
    return min((level + 1) // 2, 10)


def _class_spell_slots(character: dict[str, Any]) -> dict[str, int] | None:
    """The character class's real per-level spell slot table row (see
    `class_spell_slots`, sourced from the "Spells per Day" table on each
    caster class's own page in foundryvtt/pf2e's "Classes" journal --
    genuine per-class data, not the `ceil(level/2)` full-caster heuristic
    `_max_spell_rank` falls back to). `{"cantrips": count, "1": slots, ...}`
    -- a numbered rank key is present only if the class actually has slots
    of that rank at this level (e.g. Magus/Summoner only ever hold 2 active
    ranks at once and *lose* their lowest one as they gain a new one, which
    this table reflects exactly since it's the real source, not a formula).
    None if the character's class isn't in the table at all (not a
    caster class, or a caster archetype/dedication where `character.class`
    itself isn't the caster identity -- e.g. a Fighter with Wizard
    Dedication)."""
    class_slug = _real_slug("classes", character.get("class", ""))
    level = character.get("level", 1)
    rows = _fetchall(
        "SELECT slots FROM class_spell_slots WHERE class_slug = ? AND level = ?",
        (class_slug, level),
    )
    return json.loads(rows[0]["slots"]) if rows else None


_HEIGHTEN_INTERVAL_RE = re.compile(r"Heightened \(\+(\d+)\)")
_HEIGHTEN_FIXED_RE = re.compile(r"Heightened \((\d+)(?:st|nd|rd|th)\)")


def _heightening(system: dict[str, Any], description: str,
                 base_rank: int) -> dict[str, Any] | None:
    """How a spell heightens, as `{"type": "interval", "interval": n}` or
    `{"type": "fixed", "ranks": [...]}`, or None if it gains nothing from
    being cast in a higher slot.

    Read from `system.heightening` where it exists and from the description's
    "Heightened (+2)" / "Heightened (5th)" lines otherwise, because the
    structured field is only present where Foundry needs it to automate
    damage: **528 spells state their heightening in prose alone**, against
    597 carrying the structured field. Using one source would miss almost
    half of them, and the half it missed would be exactly the spells whose
    heightening changes duration, targets or conditions rather than dice --
    which is most of what makes heightening a low-rank spell worthwhile.
    """
    structured = system.get("heightening") or {}
    kind = structured.get("type")
    if kind == "interval" and structured.get("interval"):
        return {"type": "interval", "interval": int(structured["interval"])}
    if kind == "fixed" and structured.get("levels"):
        ranks = sorted(int(r) for r in structured["levels"])
        if ranks:
            return {"type": "fixed", "ranks": ranks}

    interval = _HEIGHTEN_INTERVAL_RE.search(description or "")
    if interval:
        return {"type": "interval", "interval": int(interval.group(1))}
    fixed = sorted({int(m) for m in _HEIGHTEN_FIXED_RE.findall(description or "")
                    if int(m) > base_rank})
    if fixed:
        return {"type": "fixed", "ranks": fixed}
    return None


def _useful_ranks(base_rank: int, heightening: dict[str, Any] | None,
                  cap: int) -> list[int]:
    """Every rank up to `cap` at which casting this spell gains something over
    casting it at the rank below.

    A spell can always be cast from a higher slot -- and doing so does raise
    its rank for counteracting and for effects that care about spell rank --
    but its *effects* only improve at its own heightening steps. Putting a
    "Heightened (+2)" spell in a slot one rank up buys nothing but a
    counteract level, which is why this returns the steps rather than the
    whole range: it answers "which slots is this spell worth preparing in".
    """
    if base_rank > cap:
        return []
    if not heightening:
        return [base_rank]
    if heightening["type"] == "interval":
        step = max(1, heightening["interval"])
        return list(range(base_rank, cap + 1, step))
    return [base_rank] + [r for r in heightening["ranks"] if base_rank < r <= cap]


def list_available_spells(
    character: dict[str, Any],
    tradition: Literal["arcane", "divine", "occult", "primal"],
    max_rank: int | None = None,
    include_legacy: bool = False,
    slot_rank: int | None = None,
) -> dict[str, Any]:
    """Spells of the given tradition at or below the highest rank the
    character can currently access, plus (new) the character's actual spell
    slot counts by rank for their level, when their class has one.

    Rank cap resolution, in order: an explicitly-passed `max_rank` always
    wins (needed for a caster archetype/dedication, where `character.class`
    itself isn't the caster and so has no row in `class_spell_slots` at
    all); otherwise the character class's own real slot table (see
    `_class_spell_slots`) if it has one -- this replaces the standard
    full-caster `ceil(level/2)` heuristic with the class's actual
    progression, which matters for Magus/Summoner specifically, whose new
    ranks arrive two levels behind a full caster's and who hold 2 slots per
    rank rather than 3-4. (An earlier version of this docstring claimed those
    two were also *non-monotonic*, holding only their top two ranks. They are
    not: that was the pre-Remaster table being ingested from a stale journal
    page, explained away rather than checked. Ingestion now overrides both
    tables and warns on any class whose slots decrease on level-up.) If
    neither applies (the character's class isn't
    a recognized caster at all), falls back to the `ceil(level/2)`
    heuristic same as before, since a caller may still be probing a
    non-standard tradition/dedication combination this project can't
    resolve on its own.

    `spell_slots` in the result is the real per-rank slot table when the
    class has one, else `None` (honestly "not known," not a guess) --
    resolves the previous "spell slots aren't modeled at all" gap for every
    class with real ingested data.

    Cantrips are stored in the source data with `level: 1` (they
    auto-heighten to half the caster's level) rather than rank 0, so they'd
    otherwise be indistinguishable from true 1st-rank slotted spells --
    each result is tagged `is_cantrip` to keep that mechanical distinction
    visible (cantrips are at-will, leveled spells consume slots).

    **Heightening.** A high-rank slot is often best filled with a heightened
    lower-rank spell, so every result carries:

    * `heightening` -- `{"type": "interval", "interval": n}` for a
      "Heightened (+n)" spell, `{"type": "fixed", "ranks": [...]}` for one
      with named steps, or `null` if the spell gains nothing from a bigger
      slot.
    * `useful_ranks` -- the ranks up to the cap at which casting it actually
      improves. A spell can always be *cast* from a higher slot, and that
      does raise its rank for counteracting, but its effects only change at
      its own steps: a "Heightened (+2)" 3rd-rank spell is worth preparing at
      3rd, 5th, 7th, 9th and nowhere between. Empty for cantrips, which
      auto-heighten and never take a slot.

    Pass `slot_rank` to ask the question a caster actually asks -- *what is
    worth putting in this slot?* -- and the result is filtered to spells whose
    `useful_ranks` include that rank, each carrying `heightened_from` (its
    base rank, or null when it natively belongs there). That puts a 1st-rank
    Force Barrage heightened to 9th beside the natively-9th spells, which is
    the comparison the choice really is.

    Both fields read `system.heightening` where it exists and the
    description's "Heightened (...)" lines otherwise -- 528 spells state
    their heightening only in prose, against 597 with the structured field,
    and the prose-only ones are disproportionately the spells whose
    heightening changes duration, targets or conditions rather than damage
    dice. What neither source captures is whether a given step is *worth* a
    higher slot to this character; that is a judgement, and this tool
    deliberately reports the steps rather than ranking them.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    spells unless set. See `_legacy_filter_sql`."""
    character = _native.as_legacy(character)
    class_slots = _class_spell_slots(character)
    if max_rank is not None:
        cap = max_rank
    elif class_slots is not None:
        rank_keys = [int(k) for k in class_slots if k != "cantrips"]
        cap = max(rank_keys) if rank_keys else 0
    else:
        cap = _max_spell_rank(character.get("level", 1))

    rows = _fetchall(
        "SELECT id, name, level, traits, description, raw_json FROM entries "
        "WHERE pack = 'spells' AND level <= ?"
        + licensing.legacy_filter_sql(include_legacy),
        (cap,),
    )
    matches = []
    for r in rows:
        system = json.loads(r["raw_json"]).get("system", {})
        if tradition not in system.get("traits", {}).get("traditions", []):
            continue
        traits = json.loads(r["traits"] or "[]")
        is_cantrip = "cantrip" in traits
        heightening = _heightening(system, r["description"] or "", r["level"])
        spell = {
            "id": r["id"], "name": r["name"], "rank": r["level"],
            "is_cantrip": is_cantrip,
            "heightening": heightening,
            # A cantrip auto-heightens to half the caster's level and never
            # occupies a slot, so "which slots is it worth preparing in" is
            # not a question that applies to it.
            "useful_ranks": ([] if is_cantrip
                             else _useful_ranks(r["level"], heightening, cap)),
        }
        if slot_rank is not None:
            if is_cantrip or slot_rank not in spell["useful_ranks"]:
                continue
            spell["heightened_from"] = r["level"] if r["level"] < slot_rank else None
        matches.append(spell)
    matches.sort(key=lambda s: (s["rank"], not s["is_cantrip"], s["name"]))
    return {
        "max_rank": cap,
        "slot_rank": slot_rank,
        "spell_slots": {k: v for k, v in class_slots.items() if k != "cantrips"} if class_slots else None,
        "cantrips_known": class_slots.get("cantrips") if class_slots else None,
        "spells": matches,
    }


_KNOWN_VARIANT_RULES = ("free-archetype", "ancestry-paragon")


def get_level_up_choices(
    character: dict[str, Any],
    target_level: int,
    variant_rules: list[str] | None = None,
) -> dict[str, Any]:
    """What unlocks at target_level for the character's class: which feat
    categories/skill increases become available (from the class's
    structured level-gate arrays) plus any automatically-granted class
    features (no choice required). Ability boosts are always at 5/10/15/20
    per core PF2e rules, not class-specific data.

    `variant_rules` is a list of slugs for optional/variant rules this
    build is using -- see rules_list_variant_rules for the full catalog and
    rules_get_entry for each one's official text. Only two currently change
    what this function reports (the rest are recognized as valid slugs but
    don't affect the computed unlocks yet):

    - `'ancestry-paragon'` (GM Core p.194): 2 ancestry feats at level 1
      instead of 1, then one more at every odd level (3, 5, 7, 9, ...)
      instead of the class's normal 1/5/9/13/17 schedule. Reflected in
      `unlocks.ancestry_feat_count`.
    - `'free-archetype'` (GM Core p.84): one bonus archetype-only feat at
      every even level. Reflected in `unlocks.archetype_feat` -- when true,
      call build_list_available_feats(feat_category='archetype') for that
      slot specifically, in addition to whatever the normal class-feat slot
      allows this level.
    """
    character = _native.as_legacy(character)
    variant_rules = variant_rules or []
    row = _fetchall(
        "SELECT class_feat_levels, ancestry_feat_levels, general_feat_levels, "
        "skill_feat_levels, skill_increase_levels, granted_items "
        "FROM class_progression WHERE class_slug = ?",
        (_real_slug("classes", character.get("class", "")),),
    )
    if not row:
        return {"error": "Character has no recognized class set yet"}
    c = row[0]
    granted = json.loads(c["granted_items"])

    notes = []
    unrecognized = [v for v in variant_rules if v not in _KNOWN_VARIANT_RULES]
    if unrecognized:
        notes.append(
            f"Unrecognized variant_rules slug(s) {unrecognized!r} -- no effect on this "
            "result. Call rules_list_variant_rules for valid slugs (only "
            f"{_KNOWN_VARIANT_RULES} currently change computed unlocks)."
        )

    if "ancestry-paragon" in variant_rules:
        if target_level == 1:
            ancestry_feat_count = 2
        elif target_level >= 3 and target_level % 2 == 1:
            ancestry_feat_count = 1
        else:
            ancestry_feat_count = 0
        notes.append(
            "Ancestry Paragon variant active: 2 ancestry feats at level 1, then one "
            "more at every odd level, replacing the class's normal ancestry-feat "
            "schedule (GM Core p.194)."
        )
    else:
        ancestry_feat_count = 1 if target_level in json.loads(c["ancestry_feat_levels"]) else 0

    archetype_feat = "free-archetype" in variant_rules and target_level >= 2 and target_level % 2 == 0
    if archetype_feat:
        notes.append(
            "Free Archetype variant active: bonus archetype-only feat this level, on "
            "top of any normal class-feat slot (GM Core p.84)."
        )

    return {
        "level": target_level,
        "unlocks": {
            "class_feat": target_level in json.loads(c["class_feat_levels"]),
            "ancestry_feat": ancestry_feat_count > 0,
            "ancestry_feat_count": ancestry_feat_count,
            "archetype_feat": archetype_feat,
            "general_feat": target_level in json.loads(c["general_feat_levels"]),
            "skill_feat": target_level in json.loads(c["skill_feat_levels"]),
            "skill_increase": target_level in json.loads(c["skill_increase_levels"]),
            "ability_boosts": target_level in ABILITY_BOOST_LEVELS,
        },
        "auto_granted_features": [g for g in granted if g["level"] == target_level],
        "variant_rule_notes": notes,
    }


def to_pathbuilder_export(character: dict[str, Any]) -> dict[str, Any]:
    """Wrap the working character representation as a Pathbuilder-compatible
    export. The working schema already follows Pathbuilder's `build` shape
    (per the user's decision -- see the plan's Pathbuilder-schema
    findings), so this is a normalize/strip pass rather than a full
    transform: drop any internal-only keys (none introduced in Phase 1) and
    wrap in the {success, build} envelope real exports use."""
    character = _native.as_legacy(character)
    internal_only_prefix = "_"
    clean = {k: v for k, v in character.items() if not k.startswith(internal_only_prefix)}
    return {"success": True, "build": clean}
