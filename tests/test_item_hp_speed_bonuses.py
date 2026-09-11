"""A worn item can adjust more than a skill total.

Belt of Good Health's own rule element is `{"key": "FlatModifier",
"selector": "hp", "value": 4}` -- the same shape #87 already taught this
project to read for a skill, just aimed at a different total. HP and Speed
share the gap #87 closed for skills: nothing derived either before this.

Separately, Boots of Bounding is the item that caught a real bug in #87's own
fix: its entry carries *two* FlatModifier/item rules, an unconditional +5 to
land-speed and a *predicated* +2 to Athletics that only applies to the High
Jump and Long Jump actions specifically (`predicate: [{"or": ["action:high-
jump", "action:long-jump"]}]`). #87's extraction had no predicate check at
all, so wearing these boots would have silently added +2 to the character's
flat, always-printed Athletics total -- overstating an ordinary Trip or
Escape roll with a bonus that only ever applies to two specific actions.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import build_tools, character_replay


# ------------------------------------------------------ the extraction itself

def test_an_unconditional_hp_modifier_is_picked_up():
    system = {"rules": [{"key": "FlatModifier", "selector": "hp", "value": 4}]}
    assert character_replay._item_flat_bonus(system, "hp", active=True) == 4


def test_a_predicated_modifier_is_excluded():
    """Boots of Bounding's real shape: unconditional land-speed, predicated
    Athletics. The predicated one must never reach a flat total."""
    system = {"rules": [
        {"key": "FlatModifier", "selector": "land-speed", "type": "item", "value": 5},
        {"key": "FlatModifier", "selector": "athletics", "type": "item", "value": 2,
         "predicate": [{"or": ["action:high-jump", "action:long-jump"]}]},
    ]}
    assert character_replay._item_flat_bonus(system, "land-speed", active=True) == 5
    assert character_replay._item_skill_bonuses(system, active=True) == {}


def test_an_inactive_item_contributes_no_hp_or_speed():
    system = {"rules": [{"key": "FlatModifier", "selector": "hp", "value": 4}]}
    assert character_replay._item_flat_bonus(system, "hp", active=False) == 0


# ------------------------------------------------------------------ replay

def _document(carried: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "identity": {"name": "Belted", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": carried},
    }


def test_belt_of_good_health_needs_no_investiture_flag(conn):
    """Its own entry carries no `invested` trait at all -- marking it
    invested would be a category error, and the +4 must still apply."""
    document = _document([{"item": "belt-of-good-health"}])
    state = character_replay.at_level(document, 13, conn)
    assert state["attributes"]["bonushp"] == 4


def test_belt_of_good_health_feeds_the_derived_hp_total(conn):
    document = _document([{"item": "belt-of-good-health"}])
    with_belt = character_replay.at_level(document, 13, conn)
    without_belt = character_replay.at_level(_document([]), 13, conn)
    derived_with = build_tools.calculate_derived_stats(with_belt)
    derived_without = build_tools.calculate_derived_stats(without_belt)
    assert derived_with["hp"] == derived_without["hp"] + 4


def test_boots_of_bounding_needs_investiture(conn):
    """Its entry does carry the `invested` trait -- unlike the belt, the
    +5 Speed only applies once actually invested."""
    invested = character_replay.at_level(
        _document([{"item": "boots-of-bounding", "invested": True}]), 13, conn)
    uninvested = character_replay.at_level(
        _document([{"item": "boots-of-bounding", "invested": False}]), 13, conn)
    assert invested["attributes"]["speed"] == uninvested["attributes"]["speed"] + 5


def test_boots_of_bounding_does_not_inflate_the_flat_athletics_total(conn):
    """The predicated Athletics bonus (High Jump/Long Jump only) must not
    show up in `skillItemBonuses` -- that total is read as always-true."""
    state = character_replay.at_level(
        _document([{"item": "boots-of-bounding", "invested": True}]), 13, conn)
    assert state["skillItemBonuses"].get("athletics", 0) == 0


def test_speed_item_bonus_reaches_the_printed_attribute(conn):
    document = _document([{"item": "boots-of-bounding", "invested": True}])
    state = character_replay.at_level(document, 13, conn)
    base = character_replay.at_level(_document([]), 13, conn)
    assert state["attributes"]["speed"] == base["attributes"]["speed"] + 5
