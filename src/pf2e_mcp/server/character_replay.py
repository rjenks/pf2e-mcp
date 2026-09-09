"""Replaying a build plan into a character's state at a given level.

This is what makes the plan authoritative rather than decorative. A character
file records the *choices* made at each level and nothing about their
consequences; `at_level` walks levels 1 through N applying them, and produces
the Pathbuilder-shaped dict that every existing consumer in this server --
`calculate_derived_stats`, `validate_build`, the sheet renderer -- already
understands. Asking for a character at 3rd level and at 20th is the same
operation, which is why the per-level snapshot export files are no longer
needed.

Where each number comes from
----------------------------
Very little of a character's state is recorded anywhere; almost all of it is
looked up and accumulated:

- **Attributes** replay the boost entries in source order. A boost adds 1 to a
  modifier, except that it adds 2 to a score below 18, so the running total
  decides what a later boost in the same batch is worth -- which is why order
  is recorded rather than a set.
- **Proficiency ranks** start from the class's level-1 baseline
  (`class_progression`'s `*_rank`, `attacks`, `defenses`) and are raised by the
  class features granted along the way. Those bumps come from
  `item_proficiency_grants`, which carries a row per rank a feature confers --
  a class's "Fortitude Expertise" at 3rd, "Expert Spellcaster" at 7th, and so
  on. 287 of the 556 granted class features carry such rows; the rest grant no
  proficiency and correctly contribute nothing.
- **Skills** come from the class's fixed trained list, the background's, the
  free picks recorded as `skillTraining`, and one step per `skillIncrease`.
- **Hit Points** come from the ancestry and class tables.

What cannot be derived, and is stated instead
---------------------------------------------
Two gaps, both handled by declaring the answer rather than pretending to
compute it:

**Feat-granted proficiency.** A dedication that trains "a skill of your choice"
has no structured representation in the rules data, so a character who spent
that grant on Religion has no way to show it. `proficiencyOverrides` in the
character file states such a rank outright, and each entry names the feat that
granted it. `at_level` applies them last and reports any that derivation has
since caught up with, so the overrides shrink as the data improves.

**Hit Points per level from a feat.** Toughness and Mountain's Stoutness each
add 1 HP per level through a Foundry rule element that this project's ingestion
does not capture -- `item_stat_modifiers` records only their effect on the
dying recovery DC. `HP_PER_LEVEL_FEATS` below is a curated list, which is
honest about being one (#61).

A third gap needs no machinery here but is worth knowing about: a subclass
option that grants skills in prose rather than in a rule element -- an Animist
apparition's two Lores -- cannot be derived either, and is recorded in the
character file as an explicit `skillTraining` choice with a note saying what
granted it (#62).

Everything replayed carries its provenance in the `_derivation` key, which is
stripped from any Pathbuilder export (`to_pathbuilder_export` already discards
underscore-prefixed keys) but is what makes a surprising number explicable.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import class_skills
from . import character as ch

#: The sixteen skills, which is a fixed list in the Remaster.
SKILLS = (
    "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
    "intimidation", "medicine", "nature", "occultism", "performance",
    "religion", "society", "stealth", "survival", "thievery",
)

#: Feats granting +1 Hit Point per level. Their rule elements are FlatModifiers
#: on `hp`, which the ingestion does not extract -- see the module docstring.
#: Curated, and therefore incomplete by construction; a character with a source
#: not listed here will be short on HP rather than silently wrong in a way
#: nobody notices, because `_derivation` names what was counted. See #61.
HP_PER_LEVEL_FEATS = {
    "toughness": 1,
    "mountains-stoutness": 1,
}

#: Fundamental runes, as slug -> the Pathbuilder field and value they set.
#: Property runes are not here: they stay in the `runes` list, which is where
#: the sheet reads them from.
_POTENCY = {
    "weapon-potency-1": 1, "weapon-potency-2": 2, "weapon-potency-3": 3,
    "armor-potency-1": 1, "armor-potency-2": 2, "armor-potency-3": 3,
}
_STRIKING = {"striking": 1, "striking-greater": 2, "striking-major": 3}
_RESILIENT = {
    "resilient": "resilient",
    "resilient-greater": "greater resilient",
    "resilient-major": "major resilient",
}

#: Foundry's size codes into Pathbuilder's numeric size and its display name.
_SIZES = {
    "tiny": (1, "Tiny"),
    "sm": (1, "Small"),
    "med": (2, "Medium"),
    "lg": (3, "Large"),
}

#: The four spellcasting proficiency keys in a Pathbuilder `proficiencies`
#: dict, by tradition.
_CASTING_KEYS = {
    "arcane": "castingArcane",
    "divine": "castingDivine",
    "occult": "castingOccult",
    "primal": "castingPrimal",
}

#: Which `slot` values spend a feat budget, and the `category` string the
#: legacy feat tuple carries for each. `sheet.py` and
#: `build_tools._feat_slot_bucket` both read index 2 of the tuple.
_FEAT_CATEGORIES = {
    "classFeat": "Class Feat",
    "ancestryFeat": "Ancestry Feat",
    "generalFeat": "General Feat",
    "skillFeat": "Skill Feat",
    "archetypeFeat": "Class Feat",
}

# Not feats, but carried in the same list because that is where the sheet's
# Advancement page reads a level's mechanical gains from. A Pathbuilder import
# produces exactly these two categories for the same reason, and the renderer
# already knows to route them to the skill lines rather than making feat cards
# of them; the native replay simply was not emitting them, so a plan that
# records precisely which skill was trained or increased still rendered as a
# bare "Skill increase" with no skill named.
_SKILL_CATEGORIES = {
    "skillIncrease": "Skill Increase",
    "skillTraining": "Skill Training",
}


def _rank_to_project(rank: int | None) -> int:
    """Foundry's 0-4 into this project's 0/2/4/6/8 convention."""
    return 0 if rank is None else rank * 2


def _ability_mod(score: int) -> int:
    return (score - 10) // 2


def _apply_boost(score: int) -> int:
    """One boost. Below 18 it is worth 2 points of score; at or above, 1.

    Both statements describe the same rule -- a boost adds 1 to the *modifier*
    -- and this is the only place the score-side special case appears.
    """
    return score + (2 if score < 18 else 1)


# ------------------------------------------------------------- lookups


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _entry(conn: sqlite3.Connection, slug: str, pack: str | None = None) -> dict | None:
    if not slug:
        return None
    if pack:
        return _one(
            conn,
            "SELECT id, name, slug, pack, level FROM entries WHERE slug = ? AND pack = ?",
            (slug, pack),
        )
    return _one(
        conn, "SELECT id, name, slug, pack, level FROM entries WHERE slug = ?", (slug,)
    )


def _name_of(conn: sqlite3.Connection, slug: str, pack: str | None = None) -> str:
    """A slug's printed name, falling back to a readable form of the slug.

    A slug that does not resolve is reported by `validate_document`; this stays
    tolerant so that replaying a slightly wrong file still produces a sheet
    with a legible placeholder rather than an exception.
    """
    entry = _entry(conn, slug, pack)
    if entry:
        return entry["name"]
    return (slug or "").replace("-", " ").title()


# ------------------------------------------------------------ attributes


def _replay_attributes(
    plan: list[dict], level: int
) -> tuple[dict[str, int], dict[str, Any], list[str]]:
    """Attribute scores at `level`, plus the `breakdown` the sheet expects.

    The breakdown is emitted in the shape `_boost_abilities` and
    `_validate_attribute_boosts` already read (`ancestryBoosts`, `ancestryFree`,
    `ancestryFlaws`, `backgroundBoosts`, `classBoosts`, `mapLevelledBoosts`), so
    the advancement page keeps working unchanged.
    """
    scores = {a: 10 for a in ch.ATTRIBUTES}
    breakdown: dict[str, Any] = {
        "ancestryBoosts": [],
        "ancestryFree": [],
        "ancestryFlaws": [],
        "backgroundBoosts": [],
        "classBoosts": [],
        "mapLevelledBoosts": {},
    }
    notes: list[str] = []

    for entry in plan:
        entry_level = entry.get("level")
        if not isinstance(entry_level, int) or entry_level > level:
            continue
        boosts = entry.get("attributeBoosts") or {}
        if not boosts:
            continue

        ancestry = boosts.get("ancestry") or {}
        # Flaws first: a flaw is -2 to the score, and applying it before the
        # boosts is what lets a flawed attribute be boosted back up by 2 rather
        # than by 1.
        for attribute in ancestry.get("flaw") or []:
            scores[attribute] -= 2
            breakdown["ancestryFlaws"].append(attribute.capitalize())
        for attribute in ancestry.get("boosts") or []:
            scores[attribute] = _apply_boost(scores[attribute])
            breakdown["ancestryBoosts"].append(attribute.capitalize())
        for attribute in ancestry.get("free") or []:
            scores[attribute] = _apply_boost(scores[attribute])
            breakdown["ancestryFree"].append(attribute.capitalize())
        for attribute in boosts.get("background") or []:
            scores[attribute] = _apply_boost(scores[attribute])
            breakdown["backgroundBoosts"].append(attribute.capitalize())
        for attribute in boosts.get("class") or []:
            scores[attribute] = _apply_boost(scores[attribute])
            breakdown["classBoosts"].append(attribute.capitalize())

        free = list(boosts.get("free") or [])
        if free:
            for attribute in free:
                scores[attribute] = _apply_boost(scores[attribute])
            breakdown["mapLevelledBoosts"][str(entry_level)] = [
                a.capitalize() for a in free
            ]
            # A boost that raised a score to an odd number bought no modifier.
            # Harmless before 20th -- the next milestone finishes it, a level
            # earlier than an even score would have -- but worth surfacing.
            wasted = [a for a in free if scores[a] % 2 == 1 and scores[a] > 10]
            if wasted and entry_level == 20:
                notes.append(
                    f"Level 20 boosts leave {', '.join(sorted(wasted))} on an odd "
                    f"score. At 20th there is no later milestone to finish the "
                    f"half-step, so those points buy nothing."
                )

    return scores, breakdown, notes


# ---------------------------------------------------------- proficiencies


def _class_baseline(
    progression: dict, class_slug: str
) -> dict[str, int]:
    """The class's proficiency ranks at 1st level."""
    proficiencies: dict[str, int] = {
        "perception": _rank_to_project(progression.get("perception_rank")),
        "fortitude": _rank_to_project(progression.get("fortitude_rank")),
        "reflex": _rank_to_project(progression.get("reflex_rank")),
        "will": _rank_to_project(progression.get("will_rank")),
        "classDC": _rank_to_project(progression.get("class_dc_rank")),
    }
    for group in ("attacks", "defenses"):
        values = json.loads(progression.get(group) or "{}")
        for key, rank in values.items():
            if key == "other":
                continue
            proficiencies[key] = _rank_to_project(rank)
    for skill in SKILLS:
        proficiencies.setdefault(skill, 0)
    return proficiencies


def _granted_features(
    conn: sqlite3.Connection, progression: dict, level: int
) -> list[dict[str, Any]]:
    """Class features granted automatically at or below `level`, in level order."""
    features = []
    for item in json.loads(progression.get("granted_items") or "[]"):
        item_level = item.get("level") or 1
        if item_level > level:
            continue
        entry_id = (item.get("uuid") or "").split(".")[-1]
        features.append({
            "level": item_level,
            "name": item.get("name"),
            "entry_id": entry_id,
        })
    return sorted(features, key=lambda f: (f["level"], f["name"] or ""))


def _apply_feature_proficiencies(
    conn: sqlite3.Connection,
    proficiencies: dict[str, int],
    features: list[dict[str, Any]],
    class_slug: str,
    tradition: str | None,
) -> list[str]:
    """Raise ranks according to what each granted feature confers.

    Two of the grant keys are indirections. A key equal to the class's own slug
    is that class's Class DC -- Foundry stores it under the class name. The key
    `spellcasting` is the class's casting proficiency, which lands on whichever
    tradition the class actually casts in.
    """
    trace: list[str] = []
    for feature in features:
        rows = conn.execute(
            "SELECT key, rank FROM item_proficiency_grants WHERE entry_id = ?",
            (feature["entry_id"],),
        ).fetchall()
        for row in rows:
            key, rank = row["key"], _rank_to_project(row["rank"])
            if key == class_slug:
                key = "classDC"
            elif key == "spellcasting":
                if not tradition:
                    continue
                key = _CASTING_KEYS[tradition]
            if rank > proficiencies.get(key, 0):
                proficiencies[key] = rank
                trace.append(f"L{feature['level']} {feature['name']}: {key}")
    return trace


def _lore_key(name: str) -> str:
    """A Lore's display name from the slug-ish form recorded in a choice.

    Hyphens are preserved rather than turned into spaces: "Fortune-Telling
    Lore" is genuinely hyphenated, and every other Lore in practice is a single
    word, so restoring a space would corrupt the one name that needs the
    hyphen to be right.
    """
    return "-".join(part.capitalize() for part in str(name).split("-"))


def _background_lore_name(raw: Any) -> str | None:
    """One background's granted Lore, cleaned up, or None if it is prose.

    `background_boosts.trained_skills.lore` is not reliably a single Lore name
    (#39). Most entries that read as prose are legitimately a free choice --
    "a Lore skill pertaining to your place of origin" -- and grant nothing
    specific, so they are skipped. Two are genuine ingestion damage:
    "UndeadLore " with the space missing, and one carrying a stray markdown
    prefix. Both are repaired here rather than propagated onto a sheet, which
    is what the sheet renderer already does defensively for the same reason.
    """
    text = str(raw or "").strip().lstrip("*").strip()
    if not text:
        return None
    # A free-text choice, not a grant.
    if " or " in text.lower() or "pertaining" in text.lower():
        return None
    text = re.sub(r"\s*\(.*$", "", text).strip()
    # "UndeadLore" -> "Undead Lore"
    text = re.sub(r"(?<=[a-z])Lore$", " Lore", text)
    return text.removesuffix(" Lore").strip() or None


def _replay_skills(
    conn: sqlite3.Connection,
    document: dict,
    progression: dict,
    plan: list[dict],
    level: int,
    proficiencies: dict[str, int],
) -> tuple[list[list[Any]], list[str]]:
    """Trained skills and Lores, from the class, the background and the plan.

    Returns the `lores` list in Pathbuilder's `[[Name, rank]]` shape; ordinary
    skills are written into `proficiencies` in place.
    """
    trace: list[str] = []
    lores: dict[str, int] = {}

    class_trained = class_skills.apply(
        (document.get("build") or {}).get("class") or "",
        json.loads(progression.get("trained_skills") or "{}"),
    )
    for skill in class_trained.get("fixed") or []:
        proficiencies[skill] = max(proficiencies.get(skill, 0), 2)
        trace.append(f"class grants {skill}")

    background = _one(
        conn,
        "SELECT trained_skills FROM background_boosts WHERE background_slug = ?",
        ((document.get("build") or {}).get("background") or "",),
    )
    if background:
        granted = json.loads(background.get("trained_skills") or "{}")
        for skill in granted.get("fixed") or []:
            proficiencies[skill] = max(proficiencies.get(skill, 0), 2)
            trace.append(f"background grants {skill}")
        for lore in granted.get("lore") or []:
            name = _background_lore_name(lore)
            if not name:
                continue
            lores[name] = max(lores.get(name, 0), 2)
            trace.append(f"background grants {name} Lore")

    for entry in plan:
        entry_level = entry.get("level")
        if not isinstance(entry_level, int) or entry_level > level:
            continue
        for choice in entry.get("choices") or []:
            slot = choice.get("slot")
            if slot not in ("skillTraining", "skillIncrease"):
                continue
            picks = choice.get("pick")
            picks = picks if isinstance(picks, list) else [picks]
            for pick in picks:
                if not isinstance(pick, str):
                    continue
                if pick in SKILLS:
                    if slot == "skillTraining":
                        proficiencies[pick] = max(proficiencies.get(pick, 0), 2)
                    else:
                        proficiencies[pick] = max(proficiencies.get(pick, 0) + 2, 2)
                    trace.append(f"L{entry_level} {slot}: {pick}")
                else:
                    name = _lore_key(pick)
                    if slot == "skillTraining":
                        lores[name] = max(lores.get(name, 0), 2)
                    else:
                        lores[name] = max(lores.get(name, 0) + 2, 2)
                    trace.append(f"L{entry_level} {slot}: {name} Lore")

    return [[name, rank] for name, rank in sorted(lores.items())], trace


# -------------------------------------------------------------- languages


def int_gain_levels(plan: list[dict], through: int = 20) -> list[int]:
    """Levels at or below `through` where the Intelligence *modifier* rose.

    Player Core p. 29: an attribute boost that increases the Intelligence
    modifier grants "an additional skill and language". The modifier is what
    counts, not the score, so a boost from 18 to 19 grants nothing and the one
    from 19 to 20 grants both -- which is why this replays the score rather
    than counting boosts spent on Intelligence.
    """
    score = 10
    levels: list[int] = []
    for entry in sorted(plan, key=lambda e: e.get("level") or 0):
        entry_level = entry.get("level")
        if not isinstance(entry_level, int) or entry_level > through:
            continue
        boosts = entry.get("attributeBoosts") or {}
        if not boosts:
            continue
        ancestry = boosts.get("ancestry") or {}
        groups = (
            ("flaw", ancestry.get("flaw")),
            ("boost", ancestry.get("boosts")),
            ("boost", ancestry.get("free")),
            ("boost", boosts.get("background")),
            ("boost", boosts.get("class")),
            ("boost", boosts.get("free")),
        )
        for kind, values in groups:
            for attribute in values or []:
                if attribute != "int":
                    continue
                before = _ability_mod(score)
                score = score - 2 if kind == "flaw" else _apply_boost(score)
                # Creation-time boosts all land at 1st level and buy the
                # starting modifier rather than a mid-career grant; only a
                # levelled increase earns the extra skill and language.
                if entry_level > 1 and _ability_mod(score) > before:
                    levels.append(entry_level)
    return levels


def _replay_languages(
    document: dict, plan: list[dict], level: int
) -> tuple[list[str], list[str]]:
    """Languages known at `level`, in the order they were learned.

    `build.languages` is the 1st-level set; everything after it is a
    `language` choice in the plan at the level it was learned. Replaying to an
    earlier level therefore stops handing the character languages they have
    not earned yet, which a flat list could never do.
    """
    known: list[str] = []
    trace: list[str] = []
    for language in (document.get("build") or {}).get("languages") or []:
        if isinstance(language, str) and language.lower() not in known:
            known.append(language.lower())
    if known:
        trace.append(f"1st level: {', '.join(known)}")

    for entry in sorted(plan, key=lambda e: e.get("level") or 0):
        entry_level = entry.get("level")
        if not isinstance(entry_level, int) or entry_level > level:
            continue
        for choice in entry.get("choices") or []:
            if choice.get("slot") != "language":
                continue
            picks = choice.get("pick")
            for pick in picks if isinstance(picks, list) else [picks]:
                if isinstance(pick, str) and pick.lower() not in known:
                    known.append(pick.lower())
                    trace.append(f"L{entry_level} language: {pick.lower()}")

    owed = len(int_gain_levels(plan, level))
    unspent = [lv for lv in int_gain_levels(plan, level)
               if not _language_taken_at(plan, lv)]
    if unspent:
        trace.append(
            f"Intelligence rose at {', '.join(str(lv) for lv in unspent)} without a "
            f"language recorded there; {owed} increase(s) through level {level} each "
            f"grant one."
        )
    return known, trace


def _language_taken_at(plan: list[dict], level: int) -> bool:
    for entry in plan:
        if entry.get("level") != level:
            continue
        for choice in entry.get("choices") or []:
            if choice.get("slot") == "language" and choice.get("pick"):
                return True
    return False


# ------------------------------------------------------------------ feats


def _replay_feats(
    conn: sqlite3.Connection, document: dict, plan: list[dict], level: int
) -> tuple[list[list[Any]], list[str]]:
    """Chosen feats as legacy tuples, plus the names of automatic features.

    The tuple is `[name, choice, category, level, slotName]`, which is what
    Pathbuilder writes and what `sheet.py` and `_validate_dedication_exclusivity`
    read positionally. A choice's `note` goes into index 1 -- the slot
    Pathbuilder calls "choice" and which agent-authored builds had already
    started using for prose, for want of anywhere better.
    """
    feats: list[list[Any]] = []
    specials: list[str] = []

    build = document.get("build") or {}
    heritage = build.get("heritage")
    if heritage:
        name = _name_of(conn, heritage, "heritages")
        feats.append([name, None, "Heritage", 1, "Heritage Feat"])
        specials.append(name)

    for tag, value in (build.get("subclasses") or {}).items():
        picks = value if isinstance(value, list) else [value]
        for index, pick in enumerate(picks):
            name = _name_of(conn, pick, "class-features")
            if len(picks) > 1:
                specials.append(
                    f"{'Primary' if index == 0 else 'Attuned'} "
                    f"{tag.split('-')[-1].capitalize()}: {name}"
                )
            else:
                specials.append(name)

    for entry in plan:
        entry_level = entry.get("level")
        if not isinstance(entry_level, int) or entry_level > level:
            continue
        for choice in entry.get("choices") or []:
            skill_category = _SKILL_CATEGORIES.get(choice.get("slot"))
            if skill_category:
                picks = choice.get("pick")
                picks = picks if isinstance(picks, list) else [picks]
                for pick in picks:
                    feats.append([
                        _lore_key(str(pick)), choice.get("note"),
                        skill_category, entry_level, skill_category,
                    ])
                continue
            category = _FEAT_CATEGORIES.get(choice.get("slot"))
            if not category:
                continue
            picks = choice.get("pick")
            picks = picks if isinstance(picks, list) else [picks]
            for pick in picks:
                name = choice.get("label") or _name_of(conn, pick, "feats")
                if choice.get("parameter"):
                    name = f"{name} ({choice['parameter']})"
                slot_name = (
                    "Archetype Feat" if choice.get("slot") == "archetypeFeat"
                    else f"{category} {entry_level}"
                )
                feats.append([
                    name, choice.get("note"), category, entry_level, slot_name,
                ])

    return feats, specials


# ------------------------------------------------------------------ gear


def _replay_gear(conn: sqlite3.Connection, document: dict) -> dict[str, Any]:
    """Split the single `carried` list into Pathbuilder's three.

    Whether an item is a weapon, armour or kit is a fact about the item in the
    rules database, so the character file records one list and this decides.
    """
    gear = document.get("gear") or {}
    weapons: list[dict[str, Any]] = []
    armor: list[dict[str, Any]] = []
    equipment: list[list[Any]] = []

    for item in gear.get("carried") or []:
        slug = item.get("item")
        entry = _one(
            conn,
            "SELECT name, category, raw_json FROM entries WHERE slug = ? AND pack = 'equipment'",
            (slug or "",),
        )
        name = entry["name"] if entry else _name_of(conn, slug)
        quantity = item.get("quantity", 1)
        runes = list(item.get("runes") or [])
        kind = _one(
            conn,
            "SELECT type FROM entries WHERE slug = ? AND pack = 'equipment'",
            (slug or "",),
        )
        item_type = (kind or {}).get("type")

        if item_type == "weapon":
            system = json.loads(entry["raw_json"])["system"] if entry else {}
            weapons.append({
                "name": name,
                "qty": quantity,
                "prof": (system.get("category") or "simple"),
                "die": (system.get("damage") or {}).get("die") or "d4",
                "pot": max((_POTENCY[r] for r in runes if r in _POTENCY), default=0),
                "str": "",
                "mat": None,
                "display": name,
                "runes": [r for r in runes if r not in _POTENCY and r not in _STRIKING],
                "increasedDice": any(r in _STRIKING for r in runes),
                "damageType": ((system.get("damage") or {}).get("damageType") or "B")[:1].upper(),
                "damageBonus": 0,
                "extraDamage": [],
                "isInventor": False,
                "grade": item.get("grade") or "",
            })
        elif item_type == "armor":
            system = json.loads(entry["raw_json"])["system"] if entry else {}
            armor.append({
                "name": name,
                "qty": quantity,
                "prof": system.get("category") or "light",
                "pot": max((_POTENCY[r] for r in runes if r in _POTENCY), default=0),
                "res": next((_RESILIENT[r] for r in runes if r in _RESILIENT), ""),
                "mat": None,
                "display": name,
                "worn": bool(item.get("worn")),
                "runes": [
                    r for r in runes if r not in _POTENCY and r not in _RESILIENT
                ],
                "grade": item.get("grade") or "",
            })
        else:
            row: list[Any] = [name, quantity]
            if item.get("invested"):
                row.append("Invested")
            equipment.append(row)

    currency = gear.get("currency") or {}
    return {
        "weapons": weapons,
        "armor": armor,
        "equipment": equipment,
        "money": {coin: currency.get(coin, 0) for coin in ("cp", "sp", "gp", "pp")},
    }


def _replay_spellcasting(
    conn: sqlite3.Connection, document: dict, proficiencies: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """Spellcasting entries in Pathbuilder's `spellCasters` shape."""
    spellcasting = document.get("spellcasting") or {}
    casters: list[dict[str, Any]] = []
    for entry in spellcasting.get("entries") or []:
        tradition = entry.get("tradition")
        spells = []
        for rank, slugs in sorted(
            (entry.get("spells") or {}).items(), key=lambda kv: int(kv[0])
        ):
            spells.append({
                "spellLevel": int(rank),
                "list": [_name_of(conn, s, "spells") for s in slugs or []],
            })
        casters.append({
            "name": entry.get("name"),
            "magicTradition": tradition,
            "spellcastingType": entry.get("type"),
            "ability": entry.get("ability") or "int",
            "proficiency": proficiencies.get(_CASTING_KEYS.get(tradition, ""), 0),
            "focusPoints": 0,
            "innate": entry.get("type") == "innate",
            "spells": spells,
            "prepared": [],
            "blendedSpells": [],
        })

    focus_spells = [
        _name_of(conn, s, "spells") for s in spellcasting.get("focusSpells") or []
    ]
    focus: dict[str, Any] = {}
    if focus_spells:
        # Pathbuilder keys the focus block by tradition; use the first caster's.
        tradition = casters[0]["magicTradition"] if casters else "divine"
        focus = {tradition: {"focusCantrips": [], "focusSpells": focus_spells}}
    return casters, focus, spellcasting.get("focusPoints", 0)


# ------------------------------------------------------------------ main


def at_level(
    document: Any, level: int | None, conn: sqlite3.Connection
) -> dict[str, Any]:
    """Replay a character file into their state at `level`.

    Returns a Pathbuilder-shaped build dict -- the same shape
    `build_calculate_derived_stats`, `build_validate_build` and
    `build_render_character_sheet` already take -- with an extra `_derivation`
    key recording what was computed and what could not be. Underscore-prefixed
    keys are stripped by `to_pathbuilder_export`, so they never reach a
    Pathbuilder file.

    `level` defaults to `identity.currentLevel`. Asking for a level above the
    plan's coverage is allowed and produces the character as far as the plan
    goes, with a note saying so; that is how a partially-planned build is
    inspected.
    """
    document = ch.to_plain(document)
    identity = document.get("identity") or {}
    build = document.get("build") or {}
    plan = document.get("plan") or []

    if level is None:
        level = identity.get("currentLevel") or 1
    if not isinstance(level, int) or not 1 <= level <= 20:
        raise ValueError(f"level must be between 1 and 20, got {level!r}")

    class_slug = build.get("class") or ""
    progression = _one(
        conn, "SELECT * FROM class_progression WHERE class_slug = ?", (class_slug,)
    )
    if progression is None:
        raise ValueError(
            f"No class progression for {class_slug!r}. "
            f"The build's `class` must be a class slug."
        )

    ancestry_row = _one(
        conn,
        "SELECT hp, size, vision FROM ancestry_boosts WHERE ancestry_slug = ?",
        (build.get("ancestry") or "",),
    ) or {}
    # Speed is not in ancestry_boosts; it lives in the ancestry entry itself.
    # It matters more than it looks: a dwarf's 20 feet changes what a turn can
    # reach, and defaulting every ancestry to 25 would be silently wrong for
    # exactly the ancestries that care.
    ancestry_entry = _one(
        conn,
        "SELECT raw_json FROM entries WHERE slug = ? AND pack = 'ancestries'",
        (build.get("ancestry") or "",),
    )
    ancestry_system = (
        json.loads(ancestry_entry["raw_json"])["system"] if ancestry_entry else {}
    )
    # A heritage can override ancestry Hit Points -- Hold-Scarred Orc takes an
    # orc from 10 to 12. Foundry expresses that as an ActiveEffectLike on
    # `system.attributes.ancestryhp`, which the ingestion does capture.
    ancestry_hp = ancestry_row.get("hp") or 8
    if build.get("heritage"):
        override = _one(
            conn,
            "SELECT m.value FROM item_stat_modifiers m JOIN entries e ON e.id = m.entry_id "
            "WHERE e.slug = ? AND m.path = 'system.attributes.ancestryhp' "
            "AND m.mode = 'override' LIMIT 1",
            (build["heritage"],),
        )
        if override:
            try:
                ancestry_hp = int(override["value"])
            except (TypeError, ValueError):
                pass

    notes: list[str] = []
    planned = {e.get("level") for e in plan if isinstance(e.get("level"), int)}
    missing = [n for n in range(1, level + 1) if n not in planned]
    if missing:
        notes.append(
            f"The plan has no entry for level{'s' if len(missing) > 1 else ''} "
            f"{', '.join(str(n) for n in missing)}; choices from those levels are "
            f"absent from this result."
        )

    abilities, breakdown, boost_notes = _replay_attributes(plan, level)
    notes += boost_notes

    tradition = None
    for entry in (document.get("spellcasting") or {}).get("entries") or []:
        if entry.get("type") in ("prepared", "spontaneous"):
            tradition = entry.get("tradition")
            break

    proficiencies = _class_baseline(progression, class_slug)
    features = _granted_features(conn, progression, level)
    prof_trace = _apply_feature_proficiencies(
        conn, proficiencies, features, class_slug, tradition
    )
    languages, language_trace = _replay_languages(document, plan, level)
    lores, skill_trace = _replay_skills(
        conn, document, progression, plan, level, proficiencies
    )

    redundant: list[str] = []
    for name, override in (document.get("proficiencyOverrides") or {}).items():
        value = ch.RANK_VALUES.get((override or {}).get("rank"), 0)
        if proficiencies.get(name, 0) >= value:
            redundant.append(name)
        else:
            proficiencies[name] = value

    if redundant:
        notes.append(
            f"proficiencyOverrides for {', '.join(sorted(redundant))} no longer "
            f"raise anything -- derivation already reaches that rank. They can "
            f"be deleted."
        )

    feats, specials = _replay_feats(conn, document, plan, level)
    specials = [f["name"] for f in features] + specials

    feat_slugs = {
        pick
        for entry in plan if (entry.get("level") or 21) <= level
        for choice in entry.get("choices") or []
        for pick in (
            choice.get("pick") if isinstance(choice.get("pick"), list)
            else [choice.get("pick")]
        )
    }
    hp_per_level = sum(
        bonus for slug, bonus in HP_PER_LEVEL_FEATS.items() if slug in feat_slugs
    )

    gear = _replay_gear(conn, document)
    casters, focus, focus_points = _replay_spellcasting(
        conn, document, proficiencies
    )

    key_ability = (json.loads(progression.get("key_ability") or '["str"]') or ["str"])[0]

    return {
        "name": identity.get("name"),
        "class": _name_of(conn, class_slug, "classes"),
        "dualClass": None,
        "level": level,
        "xp": 0,
        "ancestry": _name_of(conn, build.get("ancestry"), "ancestries"),
        "heritage": _name_of(conn, build.get("heritage"), "heritages")
        if build.get("heritage") else None,
        "background": _name_of(conn, build.get("background"), "backgrounds"),
        "alignment": "N",
        "gender": "Not set",
        "age": "Not set",
        "deity": _name_of(conn, build.get("deity"), "deities")
        if build.get("deity") else None,
        "size": _SIZES.get(ancestry_row.get("size") or "med", (2, "Medium"))[0],
        "sizeName": _SIZES.get(ancestry_row.get("size") or "med", (2, "Medium"))[1],
        "keyability": key_ability,
        "languages": [lang.capitalize() for lang in languages],
        "rituals": [],
        "resistances": [],
        "inventorMods": [],
        "abilities": {**abilities, "breakdown": breakdown},
        "attributes": {
            "ancestryhp": ancestry_hp,
            "classhp": progression.get("hp") or 8,
            "bonushp": 0,
            "bonushpPerLevel": hp_per_level,
            "speed": ancestry_system.get("speed") or 25,
            "speedBonus": 0,
        },
        "proficiencies": proficiencies,
        "mods": {},
        "feats": feats,
        "specials": specials,
        "lores": lores,
        "equipmentContainers": {},
        "equipment": gear["equipment"],
        "specificProficiencies": {
            "trained": [], "expert": [], "master": [], "legendary": [],
        },
        "weapons": gear["weapons"],
        "money": gear["money"],
        "armor": gear["armor"],
        "spellCasters": casters,
        "focusPoints": focus_points,
        "focus": focus,
        "formula": [],
        "pets": [],
        "familiars": [],
        "_derivation": {
            "level": level,
            "source": "plan",
            "proficiency_grants": prof_trace,
            "skill_grants": skill_trace,
            "language_grants": language_trace,
            "automatic_features": [
                f"L{f['level']} {f['name']}" for f in features
            ],
            "hp_per_level_feats": [
                slug for slug in HP_PER_LEVEL_FEATS if slug in feat_slugs
            ],
            "redundant_overrides": redundant,
            "notes": notes,
        },
    }
