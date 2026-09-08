"""Converting between the native format and Pathbuilder's export.

Import exists to get twenty-two existing characters into the new format without
retyping them, and to keep Pathbuilder usable as an entry point -- it is where
most builds actually get made. Export exists because Pathbuilder is also where
they get *checked*, and because a portable file that another player's tools can
read is worth keeping.

What survives the trip, and what does not
-----------------------------------------
Pathbuilder's `feats` tuples carry the level and slot of every chosen feat,
which is more than it looks: it means a plan can be reconstructed rather than
guessed, level by level, up to wherever the character has reached. Levels beyond
that are left empty for the player to fill, because a Pathbuilder file has
nothing to say about them -- which is the whole reason this format exists.

Three things import cannot recover, and does not pretend to:

- **Why.** No export records a reason. Where an agent-authored build smuggled
  prose into the tuple's "choice" slot -- which several did, for want of
  anywhere better -- that prose is rescued into the choice's `note`.
- **Automatic features.** Pathbuilder writes these as "Awarded Feat" entries
  mixed in with chosen ones. They are recorded as `automatic` rather than as
  choices, so they do not consume a feat budget that they never spent.
- **Anything above the current level.** There is nothing there to import.

Proficiencies are the interesting case. Import replays the plan it has just
reconstructed and compares the result against what the export claims; anything
the export has that derivation cannot reach becomes a `proficiencyOverride`
naming `pathbuilder-import` as its source. That turns an opaque number into a
visible, attributable claim, and the count of them is a direct measure of how
much of a build this project can actually derive -- 0 would mean derivation is
complete.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import character as ch
from . import character_replay as replay
from .build_tools import _feat_slot_bucket

#: The four feat buckets `build_tools._feat_slot_bucket` reports, mapped onto
#: plan slots.
_BUCKET_SLOTS = {
    "class": "classFeat",
    "ancestry": "ancestryFeat",
    "general": "generalFeat",
    "skill": "skillFeat",
}

#: Which pack a slot's pick should be resolved in, for the name -> slug lookup.
_SLOT_PACKS = {
    "classFeat": ("feats", "class-features"),
    "ancestryFeat": ("feats",),
    "generalFeat": ("feats",),
    "skillFeat": ("feats",),
    "archetypeFeat": ("feats",),
}

#: What Pathbuilder writes into a field the player left empty. Treated as
#: absence rather than as a name that failed to resolve.
_PLACEHOLDERS = {"none", "not set", "n/a", "-", ""}

#: A feat name carrying its parameter, as Pathbuilder writes it:
#: "Assurance (Medicine)", "Specialty Crafting (Stonemasonry)".
_PARAMETERISED = re.compile(r"^(?P<name>.+?)\s*\((?P<parameter>[^)]+)\)\s*$")


def _slug_for(
    conn: sqlite3.Connection, name: str | None, packs: tuple[str, ...] | None = None
) -> str | None:
    """Resolve a display name to a slug, preferring the named packs.

    This is the only place in the new format where a name is matched, and it
    runs once, at import. Everything downstream works from the slug it
    produces, which is the point.
    """
    if not name:
        return None
    name = str(name).strip()
    if packs:
        marks = ",".join("?" * len(packs))
        row = conn.execute(
            f"SELECT slug FROM entries WHERE name = ? COLLATE NOCASE "
            f"AND pack IN ({marks}) LIMIT 1",
            (name, *packs),
        ).fetchone()
        # Deliberately no cross-pack fallback when packs were named. "Chosen
        # One" is a background and not any kind of feat, so a file recording it
        # as an ancestry feat has a real mistake in it; falling back would
        # convert that mistake into a confident, wrong slug -- exactly the
        # silent mismatching this format exists to stop.
        return row["slug"] if row else None
    row = conn.execute(
        "SELECT slug FROM entries WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    return row["slug"] if row else None


def _describe_unresolved(conn: sqlite3.Connection, name: str) -> str:
    """A name that did not resolve, plus where it *was* found, if anywhere."""
    row = conn.execute(
        "SELECT pack FROM entries WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    return f"{name!r} (exists as {row['pack']})" if row else repr(name)


def _rune_slugs(
    conn: sqlite3.Connection, runes: list[Any], unresolved: list[str], where: str
) -> list[str]:
    """Rune display names into slugs.

    Exports write these several ways -- "Striking", "major striking",
    "Weapon Potency (+1)" -- and a rune that does not resolve is dropped rather
    than written through as a name, since a name in a slug field fails
    validation and would block the whole import.
    """
    out: list[str] = []
    for rune in runes or []:
        if not isinstance(rune, str) or not rune.strip():
            continue
        slug = _slug_for(conn, rune, ("equipment",))
        if not slug:
            slug = _slug_for(conn, rune.title(), ("equipment",))
        if not slug:
            # Exports write a graded rune as "major striking"; the rules data
            # names it "Striking (Major)".
            words = rune.title().split()
            if len(words) == 2 and words[0] in ("Greater", "Major", "Lesser", "Moderate"):
                slug = _slug_for(conn, f"{words[1]} ({words[0]})", ("equipment",))
        if slug:
            out.append(slug)
        else:
            unresolved.append(f"{where} rune: {_describe_unresolved(conn, rune)}")
    return out


def _split_parameter(
    conn: sqlite3.Connection, name: str, packs: tuple[str, ...] | None
) -> tuple[str, str | None]:
    """Split "Assurance (Medicine)" into a name and its parameter -- carefully.

    A parenthetical is only sometimes a parameter. "Shattering Strike (Monk)"
    is the entry's actual printed name, disambiguating it from the Weapon
    Improviser feat of the same name, and stripping it loses the feat. So the
    whole string is tried first, and only a name that resolves to nothing gets
    taken apart.
    """
    if not name:
        return name, None
    if _slug_for(conn, name, packs):
        return name, None
    match = _PARAMETERISED.match(name)
    if not match:
        return name, None
    return match.group("name"), match.group("parameter")


def _boosts_from_breakdown(breakdown: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Pathbuilder's `abilities.breakdown` into per-level boost entries.

    Exports use two spellings for the same thing -- `backgroundBoosts` and
    `mapLevelledBoosts` in newer files, `backgroundAbilities` and `lvl1`..`lvl20`
    in older ones -- and both are accepted here so that an old file imports.
    """
    lower = lambda values: [str(v).lower() for v in values or []]  # noqa: E731

    level_one: dict[str, Any] = {}
    ancestry = {
        "boosts": lower(breakdown.get("ancestryBoosts")),
        "free": lower(breakdown.get("ancestryFree")),
        "flaw": lower(breakdown.get("ancestryFlaws")),
    }
    if any(ancestry.values()):
        level_one["ancestry"] = {k: v for k, v in ancestry.items() if v}
    background = lower(
        breakdown.get("backgroundBoosts") or breakdown.get("backgroundAbilities")
    )
    if background:
        level_one["background"] = background
    class_boosts = lower(breakdown.get("classBoosts"))
    if class_boosts:
        level_one["class"] = class_boosts

    per_level: dict[int, dict[str, Any]] = {}
    levelled = breakdown.get("mapLevelledBoosts") or {}
    if not levelled:
        levelled = {
            key[3:]: value for key, value in breakdown.items()
            if key.startswith("lvl") and key[3:].isdigit()
        }
    for key, values in levelled.items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            continue
        free = lower(values)
        if free:
            per_level.setdefault(level, {})["free"] = free

    if level_one:
        per_level.setdefault(1, {}).update(level_one)
    return per_level


def _fundamental_runes(
    potency: Any, potency_prefix: str, striking: Any = None, resilient: Any = None
) -> list[str]:
    """Pathbuilder's `pot`, `res` and `increasedDice` fields as rune slugs.

    Pathbuilder keeps fundamental runes out of the `runes` list and in fields
    of their own -- `pot` as an integer, `res` as either a name or a tier
    number. The native format has one list, so they are folded in here. Missing
    this is why an imported character's Armor Class came out up to three points
    low.
    """
    out: list[str] = []
    try:
        tier = int(potency or 0)
    except (TypeError, ValueError):
        tier = 0
    if 1 <= tier <= 3:
        out.append(f"{potency_prefix}-{tier}")

    if striking:
        out.append("striking")

    if resilient:
        text = str(resilient).strip().lower()
        if text.isdigit():
            text = {"1": "resilient", "2": "greater resilient",
                    "3": "major resilient"}.get(text, "")
        for slug, name in (
            ("resilient-major", "major resilient"),
            ("resilient-greater", "greater resilient"),
            ("resilient", "resilient"),
        ):
            if text == name:
                out.append(slug)
                break
    return out


def _gear_from_export(
    conn: sqlite3.Connection, build: dict[str, Any], unresolved: list[str]
) -> dict[str, Any]:
    """Pathbuilder's three item lists back into one `carried` list."""
    carried: list[dict[str, Any]] = []

    for weapon in build.get("weapons") or []:
        slug = _slug_for(conn, weapon.get("name"), ("equipment",))
        if not slug:
            continue
        item: dict[str, Any] = {"item": slug}
        if (weapon.get("qty") or 1) != 1:
            item["quantity"] = weapon["qty"]
        runes = _rune_slugs(conn, weapon.get("runes"), unresolved, str(weapon.get("name")))
        runes += _fundamental_runes(
            weapon.get("pot"), "weapon-potency",
            striking=weapon.get("increasedDice"),
        )
        if runes:
            item["runes"] = runes
        carried.append(item)

    for armor in build.get("armor") or []:
        slug = _slug_for(conn, armor.get("name"), ("equipment",))
        if not slug:
            continue
        item = {"item": slug}
        if armor.get("worn"):
            item["worn"] = True
        runes = _rune_slugs(conn, armor.get("runes"), unresolved, str(armor.get("name")))
        runes += _fundamental_runes(
            armor.get("pot"), "armor-potency", resilient=armor.get("res"),
        )
        if runes:
            item["runes"] = runes
        carried.append(item)

    for row in build.get("equipment") or []:
        if isinstance(row, dict):
            name, quantity, flags = row.get("name"), row.get("qty", 1), []
        elif isinstance(row, (list, tuple)) and row:
            name = row[0]
            quantity = row[1] if len(row) > 1 and isinstance(row[1], int) else 1
            flags = [str(v) for v in row[2:]]
        else:
            continue
        slug = _slug_for(conn, name, ("equipment",))
        if not slug:
            continue
        item = {"item": slug}
        if quantity != 1:
            item["quantity"] = quantity
        if any(f.lower() == "invested" for f in flags):
            item["invested"] = True
        carried.append(item)

    money = build.get("money") or {}
    currency = {c: money[c] for c in ("pp", "gp", "sp", "cp") if money.get(c)}

    gear: dict[str, Any] = {}
    if currency:
        gear["currency"] = currency
    if carried:
        gear["carried"] = carried
    return gear


def _spellcasting_from_export(
    conn: sqlite3.Connection, build: dict[str, Any]
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for caster in build.get("spellCasters") or []:
        spells: dict[str, list[str]] = {}
        for block in caster.get("spells") or []:
            slugs = [
                slug for slug in (
                    _slug_for(conn, name, ("spells",))
                    for name in block.get("list") or []
                ) if slug
            ]
            if slugs:
                spells[str(block.get("spellLevel", 0))] = slugs
        entry: dict[str, Any] = {
            "name": caster.get("name") or "Spellcasting",
            "tradition": caster.get("magicTradition") or "arcane",
            "type": caster.get("spellcastingType") or "prepared",
        }
        if caster.get("ability"):
            entry["ability"] = caster["ability"]
        if spells:
            entry["spells"] = spells
        entries.append(entry)

    focus_spells: list[str] = []
    for block in (build.get("focus") or {}).values():
        for name in (block or {}).get("focusSpells") or []:
            slug = _slug_for(conn, name, ("spells",))
            if slug:
                focus_spells.append(slug)

    spellcasting: dict[str, Any] = {}
    if entries:
        spellcasting["entries"] = entries
    if build.get("focusPoints"):
        spellcasting["focusPoints"] = build["focusPoints"]
    if focus_spells:
        spellcasting["focusSpells"] = focus_spells
    return spellcasting


def from_pathbuilder(
    export: dict[str, Any], conn: sqlite3.Connection
) -> dict[str, Any]:
    """Convert a Pathbuilder export into a character document.

    Accepts either the `{"success": true, "build": {...}}` envelope or a bare
    build. Returns a document that `validate_document` should pass and
    `at_level` can replay, plus an `_import` block reporting what could not be
    resolved -- which is information, not failure: a name that resolves to no
    slug is a name Pathbuilder and this project disagree about, and that is
    worth seeing rather than silently dropping.
    """
    build = export.get("build", export)
    unresolved: list[str] = []

    def slug(name: str | None, packs: tuple[str, ...] | None, where: str) -> str | None:
        # Pathbuilder writes a placeholder rather than omitting an empty field.
        if not name or str(name).strip().lower() in _PLACEHOLDERS:
            return None
        value = _slug_for(conn, name, packs)
        if not value:
            unresolved.append(f"{where}: {_describe_unresolved(conn, str(name))}")
        return value

    level = int(build.get("level") or 1)

    document: dict[str, Any] = {
        "schemaVersion": ch.SCHEMA_VERSION,
        "identity": {
            "name": build.get("name") or "Unnamed",
            "currentLevel": level,
        },
        "build": {
            "ancestry": slug(build.get("ancestry"), ("ancestries",), "ancestry"),
            "heritage": slug(build.get("heritage"), ("heritages",), "heritage"),
            "background": slug(build.get("background"), ("backgrounds",), "background"),
            "class": slug(build.get("class"), ("classes",), "class"),
        },
    }
    deity = slug(build.get("deity"), ("deities",), "deity")
    if deity:
        document["build"]["deity"] = deity
    languages = [str(lang).lower() for lang in build.get("languages") or []]
    if languages:
        document["build"]["languages"] = languages

    # --- the plan, reconstructed from the feat tuples' level and category
    boosts = _boosts_from_breakdown((build.get("abilities") or {}).get("breakdown") or {})
    levels: dict[int, dict[str, Any]] = {}

    def at(n: int) -> dict[str, Any]:
        return levels.setdefault(n, {"level": n})

    for feat in build.get("feats") or []:
        if not isinstance(feat, (list, tuple)) or not feat:
            continue
        name = feat[0]
        note = feat[1] if len(feat) > 1 else None
        category = str(feat[2] if len(feat) > 2 else "").strip()
        feat_level = feat[3] if len(feat) > 3 and isinstance(feat[3], int) else 1

        # Files disagree wildly on this label: Pathbuilder writes bare slugs
        # ('class', 'skill', 'classfeature'), the hand-written ones write prose
        # ('Class Feat', 'Awarded Feat'), and some name a class feat after its
        # class ('Fighter Feat'). `_feat_slot_bucket` already knows all of that,
        # including that 'Skill Increase' contains "skill" but is not a feat.
        key = category.lower()
        target_slot = _BUCKET_SLOTS.get(_feat_slot_bucket(category, False) or "")
        base_name, parameter = _split_parameter(
            conn, str(name), _SLOT_PACKS.get(target_slot or "", ("feats",))
        )

        if target_slot is None:
            # A skill increase is a plan choice, just not a feat one.
            if "increase" in key:
                # Some files name the row "Skill Increase: Acrobatics" and
                # others just "Acrobatics"; both mean the skill.
                skill = base_name.split(":")[-1].strip().lower()
                at(feat_level).setdefault("choices", []).append(
                    {"slot": "skillIncrease", "pick": skill}
                )
                continue
            # Heritage is recorded once in `build`. Everything else that fills
            # no slot -- awarded feats, automatic class features -- is a grant.
            if "heritage" in key:
                continue
            pick = _slug_for(conn, base_name, ("feats", "class-features"))
            if pick:
                at(feat_level).setdefault("automatic", []).append(pick)
            continue

        pick = _slug_for(conn, base_name, _SLOT_PACKS.get(target_slot, None))

        if not pick:
            unresolved.append(
                f"level {feat_level} {category}: "
                f"{_describe_unresolved(conn, base_name)}"
            )
            continue

        choice: dict[str, Any] = {"slot": target_slot, "pick": pick}
        if parameter:
            choice["parameter"] = parameter
        # Agent-authored builds put explanatory prose in the "choice" slot for
        # want of anywhere better. That is exactly what `note` is for.
        if isinstance(note, str) and note.strip():
            choice["note"] = note.strip()
        if feat_level <= level:
            choice["status"] = "locked"
        at(feat_level).setdefault("choices", []).append(choice)

    for boost_level, entry in boosts.items():
        at(boost_level)["attributeBoosts"] = entry

    document["plan"] = [levels[n] for n in sorted(levels)]

    # --- gear, spells, and anything the plan cannot account for
    gear = _gear_from_export(conn, build, unresolved)
    if gear:
        document["gear"] = gear
    spellcasting = _spellcasting_from_export(conn, build)
    if spellcasting:
        document["spellcasting"] = spellcasting

    notes = build.get("notes")
    if isinstance(notes, str) and notes.strip():
        document["notes"] = [{
            "heading": "Imported notes",
            "body": notes.strip(),
        }]

    _recover_lores(document, build, conn)

    overrides, unexplained = _overrides_for(document, build, conn)
    if overrides:
        document["proficiencyOverrides"] = overrides

    document["_import"] = {
        "source": "pathbuilder",
        "unresolved": unresolved,
        "attribute_mismatch": _attribute_mismatch(document, build, conn),
        "stated_proficiencies": sorted(overrides),
        "note": (
            "Proficiencies the export claims that replaying the plan cannot "
            "reach are recorded as overrides sourced to the import. Each one is "
            "either a genuine feat grant the rules data cannot model, or a gap "
            "in derivation worth closing."
        ) if overrides else "Every proficiency in the export was derivable.",
    }
    if unexplained:
        document["_import"]["derived_higher_than_export"] = unexplained
    return document


def _overrides_for(
    document: dict[str, Any], build: dict[str, Any], conn: sqlite3.Connection
) -> tuple[dict[str, Any], list[str]]:
    """Whatever the export knows that replay cannot work out.

    Replays the freshly reconstructed plan and diffs the proficiencies against
    the export's own. Anything the export rates higher becomes an override;
    anything replay rates *higher* than the export is reported separately,
    since that direction means derivation is overreaching rather than falling
    short and is a bug to chase rather than a fact to record.
    """
    stated = build.get("proficiencies") or {}
    if not stated:
        return {}, []
    try:
        derived = replay.at_level(
            {**document, "plan": document.get("plan") or []},
            document["identity"]["currentLevel"], conn,
        )["proficiencies"]
    except ValueError:
        return {}, []

    overrides: dict[str, Any] = {}
    unexplained: list[str] = []
    for key, value in stated.items():
        if not isinstance(value, int) or key in ("piloting", "computers"):
            continue
        have = derived.get(key, 0)
        if value > have:
            overrides[key] = {
                "rank": ch.RANK_NAMES[min(value // 2, 4)],
                "source": "pathbuilder-import",
                "note": (
                    "Carried over from the Pathbuilder export; replaying the "
                    "plan reaches only "
                    f"{ch.RANK_NAMES[min(have // 2, 4)]}."
                ),
            }
        elif have > value and value == 0:
            unexplained.append(f"{key}: derived {have}, export 0")
    return overrides, unexplained


def to_pathbuilder(
    document: Any, level: int | None, conn: sqlite3.Connection
) -> dict[str, Any]:
    """Render a character at a level as a Pathbuilder export.

    Wraps the replay result in Pathbuilder's envelope and drops the
    underscore-prefixed keys the format uses for provenance, so the result is a
    plain Pathbuilder file that its importer will accept.
    """
    build = replay.at_level(document, level, conn)
    clean = {key: value for key, value in build.items() if not key.startswith("_")}
    return {"success": True, "build": clean}


def _attribute_mismatch(
    document: dict[str, Any], build: dict[str, Any], conn: sqlite3.Connection
) -> dict[str, Any]:
    """Where replaying the recorded boosts disagrees with the recorded scores.

    Worth reporting rather than resolving. The boosts and the scores in an
    export are two statements of the same thing, and when they differ one of
    them is wrong -- usually the scores, because they accumulate an apex item
    or a hand edit that the boost list never recorded. Silently preferring
    either would hide a real mistake in the source file.

    The apex-item case is common enough to name: an apex item raises one
    modifier by 1 and Pathbuilder folds that into the scores, while replay
    derives from boosts alone and so comes out one modifier short (#63).
    """
    stated = {
        key: value for key, value in (build.get("abilities") or {}).items()
        if key in ch.ATTRIBUTES and isinstance(value, int)
    }
    if not stated:
        return {}
    try:
        derived = replay.at_level(
            document, document["identity"]["currentLevel"], conn
        )["abilities"]
    except ValueError:
        return {}
    return {
        key: {
            "export": value,
            "derived": derived[key],
            "modifier_difference": (value - derived[key]) // 2,
        }
        for key, value in stated.items() if derived.get(key) != value
    }


def _lore_slug(name: str) -> str:
    """A Lore's display name into the form a choice records.

    Strips a trailing "Lore" that some files include and others do not -- the
    corpus has one "Theatre Lore" among eighteen bare names -- so both spellings
    converge on the same value.
    """
    text = str(name).strip()
    if text.lower().endswith(" lore"):
        text = text[:-5].strip()
    return text.lower().replace(" ", "-")


def _recover_lores(
    document: dict[str, Any], build: dict[str, Any], conn: sqlite3.Connection
) -> None:
    """Record Lores the plan does not account for as explicit grants.

    Pathbuilder keeps Lores in their own `lores` array, separate from the feat
    tuples, so nothing in a reconstructed plan explains them. Some are
    derivable -- a background's granted Lore comes back on its own -- but the
    rest arrive from sources the rules data does not model, most often a
    subclass option that names its skills in prose only (#62).

    Anything replay cannot reach is added as a `skillTraining` choice at 1st
    level. That is what the slot is for, and without it a character silently
    loses most of their Lores: one real 10th-level build kept one of seven.
    """
    stated = {
        str(row[0]).strip(): row[1]
        for row in build.get("lores") or []
        if isinstance(row, (list, tuple)) and row
    }
    if not stated:
        return
    try:
        derived = {
            str(name) for name, _ in replay.at_level(
                document, document["identity"]["currentLevel"], conn
            )["lores"]
        }
    except ValueError:
        derived = set()

    missing = sorted(name for name in stated if name not in derived)
    if not missing:
        return

    level_one = next(
        (entry for entry in document["plan"] if entry.get("level") == 1), None
    )
    if level_one is None:
        level_one = {"level": 1}
        document["plan"].insert(0, level_one)
    level_one.setdefault("choices", []).append({
        "slot": "skillTraining",
        "pick": [_lore_slug(name) for name in missing],
        "note": (
            "Lores carried over from the Pathbuilder export. Replaying the plan "
            "does not account for them, so their real source -- a subclass "
            "option, a feat, or a free pick -- is worth recording here."
        ),
    })
