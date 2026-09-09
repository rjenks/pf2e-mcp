"""Core PF2e arithmetic and prerequisite evaluation, shared by the
character-building tools. Kept separate from build_tools.py so the
tool-facing functions stay focused on request/response shaping.

Phase 2 adds: skill-rank-vs-level legality (a fixed core rule, not
class-specific) and equipment-aware AC. Still out of scope: validating
proficiency-rank increases to saves/Perception/class DC/spellcasting, which
come from class features (e.g. "Weapon Expertise") rather than the uniform
Skill Increase mechanic -- those would need parsing granted-feature names
heuristically, which risks silently asserting wrong rules, so
validate_build does not attempt it.
"""

from __future__ import annotations

import json
from typing import Any

ABILITY_KEYS = {
    "strength": "str", "dexterity": "dex", "constitution": "con",
    "intelligence": "int", "wisdom": "wis", "charisma": "cha",
}

RANK_BONUS = {"untrained": 0, "trained": 2, "expert": 4, "master": 6, "legendary": 8}
# Pathbuilder stores proficiencies as this same 0/2/4/6/8 rank-bonus value directly.
RANK_BY_VALUE = {v: k for k, v in RANK_BONUS.items()}

_RESILIENT_TIER_NAMES = {"resilient": 1, "greater resilient": 2, "major resilient": 3}


def resilient_tier(res: Any) -> int:
    """Normalizes an armor's `res` field to a 0-3 tier number (the rune's
    item bonus to saves). Confirmed against real Pathbuilder exports that
    this is stored as the rune's name ("resilient", "Greater Resilient",
    any case) -- not the numeric 0-3 convention a hand-authored file might
    use instead (mirroring a weapon's numeric `pot` field). Handles both,
    plus a missing/empty/malformed value as 0 rather than raising, since
    this reads third-party export data of inconsistent shape."""
    if isinstance(res, str):
        text = res.strip().lower()
        if text in _RESILIENT_TIER_NAMES:
            return _RESILIENT_TIER_NAMES[text]
        res = text
    try:
        return int(res or 0)
    except (TypeError, ValueError):
        return 0

# Fixed core rule (PF2e Player Core, Skill Increase choices): a skill
# cannot be raised to a given rank before the character reaches this level,
# regardless of class.
SKILL_RANK_MIN_LEVEL = {"trained": 1, "expert": 3, "master": 7, "legendary": 15}

KNOWN_SKILLS = {
    "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
    "intimidation", "medicine", "nature", "occultism", "performance", "religion",
    "society", "stealth", "survival", "thievery",
}


def ability_mod(score: int) -> int:
    return (score - 10) // 2


def total_bonus(ability_score: int, proficiency_value: int, level: int) -> int:
    """PF2e RAW: untrained is ability mod only (no level added); trained+
    adds level plus the rank bonus."""
    mod = ability_mod(ability_score)
    if proficiency_value <= 0:
        return mod
    return mod + level + proficiency_value


def default_character_abilities(character: dict) -> dict:
    return character.get("abilities", {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10})


def has_feat(character: dict, name: str) -> bool:
    feats = character.get("feats", [])
    return any(f and f[0] and f[0].strip().lower() == name.strip().lower() for f in feats)


def has_lore(character: dict, name: str) -> bool:
    lores = character.get("lores", [])
    return any(l and l[0] and name.strip().lower() in l[0].strip().lower() for l in lores)


def next_rank(value: int) -> str | None:
    """The rank one step above the given proficiency value, or None if
    already legendary."""
    order = ["untrained", "trained", "expert", "master", "legendary"]
    current = RANK_BY_VALUE.get(value, "untrained")
    idx = order.index(current)
    return order[idx + 1] if idx + 1 < len(order) else None


def skill_rank_legal_at_level(rank: str, level: int) -> bool:
    """Fixed core rule: can a skill be raised to `rank` at character
    `level`, independent of class."""
    return level >= SKILL_RANK_MIN_LEVEL.get(rank, 1)


def armor_ac(
    dex_score: int,
    prof_value: int,
    level: int,
    ac_bonus: int,
    dex_cap: int | None,
    potency: int = 0,
) -> int:
    """PF2e AC with worn armor: 10 + (dex mod, capped by the armor's dex cap
    if any) + proficiency bonus (0 if untrained) + the armor's item bonus +
    any potency rune. A potency rune is etched into the armor itself, so its
    bonus simply adds to the armor's own item bonus rather than needing
    separate bonus-type stacking rules. Does not yet account for a raised
    shield's bonus -- that needs whether the shield is currently raised
    tracked as character state, which doesn't exist in this project's
    schema yet (a static character sheet has no "currently raised" concept)."""
    dex_mod = ability_mod(dex_score)
    effective_dex = min(dex_mod, dex_cap) if dex_cap is not None else dex_mod
    prof_component = (level + prof_value) if prof_value > 0 else 0
    return 10 + effective_dex + prof_component + ac_bonus + potency


def skill_rank_value(character: dict, skill: str) -> int | None:
    """Returns the character's current proficiency rank value (0/2/4/6/8)
    for a named skill, or None if the skill name can't be resolved at all
    (not one of the 16 core skills, not a Lore reference, and not present
    in the character's own proficiencies dict). A recognized skill simply
    absent from `proficiencies` defaults to untrained (0) -- every
    character is untrained-by-default in everything, that's not the same
    as "can't verify"."""
    proficiencies = character.get("proficiencies", {})
    key = skill.strip().lower().replace(" ", "")
    if key in proficiencies:
        return proficiencies[key]
    if "lore" in skill.lower():
        return RANK_BONUS["trained"] if has_lore(character, skill) else 0
    if key in KNOWN_SKILLS:
        return 0
    return None


_CASTING_TRADITIONS = ("arcane", "divine", "occult", "primal")


def _spellcasting_trained(character: dict, structured: dict) -> bool:
    """True if the character has a spellcasting class feature at trained+
    in any (or a specified) tradition. Pathbuilder character JSON has been
    observed storing this two different ways depending on class/version --
    e.g. a Cleric stores divine proficiency under the bare tradition key
    "divine", while a Thaumaturge stores (always-present, often 0)
    "castingDivine" -- so both forms are checked."""
    prof = character.get("proficiencies", {})
    traditions = structured.get("tradition") or list(_CASTING_TRADITIONS)
    if isinstance(traditions, str):
        traditions = [traditions]
    return any(
        prof.get(t.lower(), 0) > 0 or prof.get(f"casting{t.capitalize()}", 0) > 0
        for t in traditions
    )


VISION_RANK = {"normal": 0, "low-light-vision": 1, "darkvision": 2}
# Names as they actually appear in PF2e prerequisite/access text, mapped to
# the `ancestry_boosts.vision` / `item_senses.selector` vocabulary.
VISION_NAME_ALIASES = {
    "darkvision": "darkvision",
    "low-light vision": "low-light-vision",
    "low-light-vision": "low-light-vision",
}


def check_single_prerequisite(
    character: dict, kind: str, structured: dict | None, derived: dict | None = None
) -> bool | None:
    """Returns True (satisfied), False (definitely not satisfied), or None
    (can't be automatically verified -- surfaced as 'unconfirmed', never
    silently treated as satisfied).

    `derived` carries facts this module can't compute itself since it's
    deliberately DB-free (pure functions over the character dict):
    `{"vision": <effective vision level>, "has_familiar": <bool>,
    "class_hp": <int | None>}`, resolved by the caller
    (`build_tools._build_derived`, which does need DB access) from the
    character's ancestry/heritage/taken feats/class. Absent/None `derived`
    just means those specific checks fall back to `None` (unconfirmed)
    instead of silently guessing."""
    if structured is None:
        return None

    if kind == "skill_rank":
        current = skill_rank_value(character, structured["skill"])
        if current is None:
            return None
        return current >= RANK_BONUS.get(structured["rank"], 999)

    if kind == "ability_score":
        ability = ABILITY_KEYS.get(structured["ability"], structured["ability"][:3])
        score = default_character_abilities(character).get(ability, 10)
        threshold = structured["threshold"]
        if threshold.startswith("+"):
            return ability_mod(score) >= int(threshold[1:])
        digits = "".join(c for c in threshold if c.isdigit())
        return score >= int(digits) if digits else None

    if kind == "character_level":
        return character.get("level", 1) >= structured["level"]

    if kind == "named_reference":
        name = structured["name"]
        name_lower = name.lower()
        vision_target = VISION_NAME_ALIASES.get(name_lower)
        if vision_target:
            # A vision-level requirement (e.g. "darkvision") isn't a named
            # feat/ancestry/heritage/background/class -- matching it against
            # those (as this branch used to, unconditionally) always came
            # back False, even for a character whose ancestry/heritage
            # genuinely grants it. `derived["vision"]` is the caller-supplied
            # effective vision level (see this function's docstring); if the
            # caller didn't supply one, this specific check is honestly
            # unconfirmed rather than asserted false.
            current_vision = (derived or {}).get("vision")
            if current_vision is None:
                return None
            return VISION_RANK.get(current_vision, 0) >= VISION_RANK.get(vision_target, 0)
        if name_lower == "familiar":
            # Same shape of bug as vision: "familiar" is a capability, not
            # a named feat/ancestry/etc., so the generic match below always
            # returned False even when the character genuinely has one.
            # `derived["has_familiar"]` -- see this function's docstring
            # and `build_tools._character_has_familiar` for exactly what
            # this does and doesn't detect.
            has_familiar = (derived or {}).get("has_familiar")
            if has_familiar is None:
                return None
            return bool(has_familiar)
        return (
            has_feat(character, name)
            or character.get("ancestry", "").lower() == name_lower
            or character.get("heritage", "").lower() == name_lower
            or character.get("background", "").lower() == name_lower
            or character.get("class", "").lower() == name_lower
        )

    if kind == "compound_named":
        return any(
            check_single_prerequisite(character, "named_reference", {"name": n}, derived)
            for n in structured["any_of"]
        )

    if kind == "skill_rank_any":
        threshold = RANK_BONUS.get(structured["rank"], 999)
        values = [skill_rank_value(character, s) for s in structured["skills"]]
        if any(v is not None and v >= threshold for v in values):
            return True
        if all(v is not None for v in values):
            return False
        return None

    if kind == "override":
        # Overrides carry their own inner "kind" for dispatch. Any kind
        # with an existing evaluator can be reused directly (e.g. a
        # disambiguating any_of expressed as "compound_named"); a bespoke
        # kind like "spellcasting_trained" gets its own branch here.
        inner_kind = structured.get("kind")
        if inner_kind == "spellcasting_trained":
            return _spellcasting_trained(character, structured)
        if inner_kind == "class_hp_threshold":
            class_hp = (derived or {}).get("class_hp")
            if class_hp is None:
                return None
            # The prerequisite reads "no more Hit Points per level than
            # 8 + your Constitution modifier" -- `max_hp` is only the constant
            # half of that, and the Constitution term is not decoration. A
            # Ranger grants 10 HP per level, so at Con +0 the threshold is 8 and
            # they are excluded, but from Con +2 onward it is 10 or more and
            # they qualify. Dropping the modifier made every one of the seven
            # <Class> Resiliency feats permanently unavailable to exactly the
            # d10 classes they are written for.
            con = ability_mod(default_character_abilities(character).get("con", 10))
            return class_hp <= structured["max_hp"] + con
        if inner_kind == "class_feature_reference":
            # For a prerequisite that names an automatic, unconditionally-
            # granted class feature (e.g. Magus's "Spellstrike" or "Arcane
            # Cascade" -- every Magus gets both at 1st level, they're not a
            # choice) rather than a literal feat/ancestry/heritage/
            # background/class name. The generic "named_reference" fallback
            # (has_feat-or-ancestry/heritage/background/class-name match)
            # always resolves these False, even for a character who
            # genuinely has the referenced feature, because this project's
            # character-JSON convention never records an automatic class
            # feature as an owned `feats` entry (see character-file
            # examples in characters/ -- Spellstrike/Arcane Cascade go in
            # `specials`, not `feats`). Confirmed this silently dropped 28
            # real Magus feats (all levels) from every `list_available_feats`
            # result for every Magus character, not surfaced as
            # "unconfirmed" -- a hard, wrong False. Deliberately narrow:
            # only true if the character's class matches, not a broader
            # "does this class ever get this feature" claim.
            return character.get("class", "").lower() == structured["class"].lower()
        if inner_kind and inner_kind != "override":
            return check_single_prerequisite(character, inner_kind, structured, derived)
        return None

    return None


def evaluate_prerequisites(
    character: dict, prereq_rows: list, derived: dict | None = None
) -> tuple[str, list[dict]]:
    """Given a character and the `prerequisites` table rows for one entry,
    returns (status, checks) where status is 'eligible' if every row is
    satisfied or absent, 'ineligible' if any row is definitely False, else
    'unconfirmed'. `derived` is passed straight through to
    `check_single_prerequisite` -- see its docstring."""
    if not prereq_rows:
        return "eligible", []

    checks = []
    any_false = False
    any_none = False
    for row in prereq_rows:
        structured = json.loads(row["structured"]) if row["structured"] else None
        satisfied = check_single_prerequisite(character, row["kind"], structured, derived)
        checks.append({"raw_text": row["raw_text"], "satisfied": satisfied})
        if satisfied is False:
            any_false = True
        elif satisfied is None:
            any_none = True

    if any_false:
        return "ineligible", checks
    if any_none:
        return "unconfirmed", checks
    return "eligible", checks
