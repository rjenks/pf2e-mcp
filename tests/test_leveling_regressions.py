"""Regressions found by replaying every class from level 1 to 20 against the printed rules.

* Every class starts trained in its class DC (Player Core: "Class DC -- trained"); a NULL
  `class_dc_rank` in the progression table meant "no rank" and left the DC untrained.
* A class whose key attribute is a choice the database does not list (Psychic: Intelligence or
  Charisma) honours the document's `keyAttribute` instead of falling back to the first option.
* A class feature that raises Speed (Swashbuckler's Vivacious Speed: +5 ft at 3rd, then +5 ft
  per 8 levels, from its own `@actor.level` formula) is derived rather than dropped.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import character_replay


def _document(cls: str, level: int, key: str | None = None) -> dict:
    build = {"ancestry": "human", "background": "acolyte", "class": cls, "heritage": None}
    if key:
        build["keyAttribute"] = key
    return {
        "schemaVersion": 1,
        "identity": {"name": "Regression", "currentLevel": level},
        "build": build,
        "plan": [{"level": lv} for lv in range(1, level + 1)],
    }


@pytest.mark.parametrize("cls", ["fighter", "wizard", "alchemist", "champion", "summoner", "necromancer"])
def test_every_class_starts_trained_in_its_class_dc(conn, cls):
    state = character_replay.at_level(_document(cls, 1), 1, conn)
    assert state["proficiencies"]["classDC"] == 2


@pytest.mark.parametrize("key", ["int", "cha"])
def test_a_choice_of_key_attribute_is_honoured(conn, key):
    state = character_replay.at_level(_document("psychic", 1, key), 1, conn)
    assert state["keyability"] == key


@pytest.mark.parametrize("level,extra", [(1, 0), (3, 5), (7, 5), (11, 10), (15, 10), (19, 15)])
def test_vivacious_speed_follows_its_level_formula(conn, level, extra):
    state = character_replay.at_level(_document("swashbuckler", level), level, conn)
    assert state["attributes"]["speed"] == 25 + extra


@pytest.mark.parametrize("cls,extra", [("swashbuckler", [3, 7, 15]), ("investigator", list(range(3, 21, 2)))])
def test_class_features_add_skill_feats(conn, cls, extra):
    """Even levels as for every class, plus Stylish Tricks (Swashbuckler: 3rd, 7th, 15th) and Skillful
    Lessons (Investigator: 3rd and every odd level after); Player Core 2, read in the rules database
    2026-11-23."""
    from pf2e_mcp.server import build_tools

    granted = [lv for lv in range(2, 21)
               if build_tools.get_level_up_choices(_document(cls, lv), lv)["unlocks"]["skill_feat"]]
    assert granted == sorted([*range(2, 21, 2), *extra])
