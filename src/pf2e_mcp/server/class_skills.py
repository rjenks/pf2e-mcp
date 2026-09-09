"""Corrections to a class's 1st-level trained skills.

`class_progression.trained_skills` comes from the upstream Foundry class item's
`system.trainedSkills`, and that field does not say everything the printed class
entry says. Two different things go missing, and both cost a character real
skills:

**A flat skill simply absent.** The Ranger is trained in *Nature and Survival*
(Player Core; confirmed against Archives of Nethys and against GM Core's
dual-class worked example, which says "the trained proficiency rank in Nature
and Survival from ranger"). The upstream data carries only
`{"additional": 4, "value": ["survival"]}`. Nothing else in the class item
grants Nature -- no rule element, no proficiency grant -- so every Ranger built
against this data silently lost a trained skill, and typically spent one of
their free picks buying back something they already had.

**A skill the class defers to a subclass or deity.** A Cleric is trained in "one
skill determined by your deity", a Druid in "one skill determined by your
druidic order", a Sorcerer in "two skills determined by your bloodline". These
genuinely cannot live in `fixed`, since they are not knowable from the class
alone -- but saying nothing about them leaves a caller counting too few skills
and never knowing to ask. They are surfaced as `conditional` so the count can be
right even when the specific skill cannot be.

Audited against Archives of Nethys: Alchemist, Barbarian, Bard, Champion,
Cleric, Druid, Fighter, Monk, Ranger, Rogue, Sorcerer, Wizard. The classes
outside that list are **not** yet verified, so an absent entry here means
"unchecked", not "correct".
"""

from __future__ import annotations

from typing import Any

# Unconditional skills the upstream data omits outright. Keep this to skills
# the class entry grants flatly, with no choice and no subclass dependency --
# anything conditional belongs in CONDITIONAL_SKILLS instead.
MISSING_FIXED_SKILLS: dict[str, list[str]] = {
    "ranger": ["nature"],
}

# Skills a class grants but defers to a subclass, deity or other later choice.
# The value is the wording from the class entry, so a caller can quote it when
# asking the player which skill they actually got.
CONDITIONAL_SKILLS: dict[str, str] = {
    "champion": "one skill determined by your choice of deity",
    "cleric": "one skill determined by your choice of deity",
    "druid": "one skill determined by your druidic order",
    "rogue": "one or more skills determined by your racket",
    "sorcerer": "two skills determined by your bloodline",
}

# Classes checked against Archives of Nethys. Anything not here is unverified,
# and its absence from the two tables above says nothing about its correctness.
AUDITED: frozenset[str] = frozenset({
    "alchemist", "barbarian", "bard", "champion", "cleric", "druid",
    "fighter", "monk", "ranger", "rogue", "sorcerer", "wizard",
})


def apply(class_slug: str, trained: dict[str, Any] | None) -> dict[str, Any]:
    """Merge the corrections into one class's `trained_skills` dict.

    Returns a new dict; the input is left alone. `fixed` gains any flatly
    missing skill, without duplicating one already present, and `conditional`
    is added where the class defers a skill to a later choice.
    """
    result = dict(trained or {})
    slug = (class_slug or "").strip().lower()

    fixed = list(result.get("fixed") or [])
    have = {s.strip().lower() for s in fixed if s}
    for skill in MISSING_FIXED_SKILLS.get(slug, []):
        if skill.lower() not in have:
            fixed.append(skill)
            have.add(skill.lower())
    result["fixed"] = fixed

    conditional = CONDITIONAL_SKILLS.get(slug)
    if conditional:
        result["conditional"] = conditional
    result["audited"] = slug in AUDITED
    return result
