"""A worn or invested item's flat skill bonus has to reach the printed total.

`calculate_derived_stats` and the sheet's skill table read only ability,
proficiency and level -- so a character carrying Armbands of Athleticism (a
+2 item bonus to Athletics, gated on being worn) had a printed Athletics total
two points short of what the character actually rolls, with nothing on the
sheet to say so. A player had to remember the bonus and add it by hand every
time, which is exactly the number a calculated total exists to not require.

Armbands of Athleticism's own entry carries the bonus as a standard Foundry
rule element -- `{"key": "FlatModifier", "selector": "athletics", "type":
"item", "value": 2}` -- the same shape used for every item that grants this
kind of bonus, not something special-cased per item.
"""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import build_tools, character_replay, sheet


# ------------------------------------------------------- the extraction itself

def test_an_active_flat_item_modifier_is_picked_up():
    system = {"rules": [
        {"key": "FlatModifier", "selector": "athletics", "type": "item", "value": 2},
    ]}
    assert character_replay._item_skill_bonuses(system, active=True) == {"athletics": 2}


def test_an_inactive_item_contributes_nothing():
    """Not worn, not invested -- the bonus is not currently doing anything."""
    system = {"rules": [
        {"key": "FlatModifier", "selector": "athletics", "type": "item", "value": 2},
    ]}
    assert character_replay._item_skill_bonuses(system, active=False) == {}


def test_a_non_item_modifier_is_not_a_skill_item_bonus():
    """A status or circumstance bonus is a different type; only `item` counts,
    matching the type this mechanism exists to fold in."""
    system = {"rules": [
        {"key": "FlatModifier", "selector": "athletics", "type": "status", "value": 1},
    ]}
    assert character_replay._item_skill_bonuses(system, active=True) == {}


def test_an_unrelated_selector_is_ignored():
    """A rule element can target anything -- a save, a resistance, a weapon
    category. Only the 16 core skills are this mechanism's business."""
    system = {"rules": [
        {"key": "FlatModifier", "selector": "will", "type": "item", "value": 1},
    ]}
    assert character_replay._item_skill_bonuses(system, active=True) == {}


def test_two_item_bonuses_to_the_same_skill_do_not_stack():
    system = {"rules": [
        {"key": "FlatModifier", "selector": "athletics", "type": "item", "value": 2},
        {"key": "FlatModifier", "selector": "athletics", "type": "item", "value": 1},
    ]}
    assert character_replay._item_skill_bonuses(system, active=True) == {"athletics": 2}


# --------------------------------------------------------------- through replay

def _document(invested: bool) -> dict:
    return {
        "schemaVersion": 1,
        "identity": {"name": "Armbanded", "currentLevel": 9},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "armbands-of-athleticism", "invested": invested},
        ]},
    }


def test_replay_reports_the_real_items_bonus(conn):
    state = character_replay.at_level(_document(True), 9, conn)
    assert state["skillItemBonuses"] == {"athletics": 2}


def test_an_owned_but_uninvested_item_grants_nothing(conn):
    """On the sheet but not doing anything -- the same distinction the
    inventory already draws for pricing."""
    state = character_replay.at_level(_document(False), 9, conn)
    assert state["skillItemBonuses"] == {}


def test_derived_athletics_includes_the_item_bonus(conn):
    state = character_replay.at_level(_document(True), 9, conn)
    derived = build_tools.calculate_derived_stats(state)
    plain = build_tools.calculate_derived_stats({**state, "skillItemBonuses": {}})
    assert derived["skills"]["athletics"] == plain["skills"]["athletics"] + 2


# ------------------------------------------------------------------- the sheet

def _athletics_total(page: str) -> int:
    body = re.search(r'data-sec="core"(.*?)</section>', page, re.S).group(1)
    row = re.search(r'>Athletics<.*?class="t[^"]*">([+-]\d+)</td>', body, re.S)
    return int(row.group(1))


@pytest.fixture
def base_character() -> dict:
    return {
        "name": "Armbanded", "class": "Fighter", "ancestry": "Orc", "level": 9,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 10},
        "proficiencies": {"athletics": 4},
        "feats": [],
    }


def test_the_sheets_athletics_total_includes_a_worn_items_bonus(base_character, conn, tmp_path):
    with_bonus = {**base_character, "skillItemBonuses": {"athletics": 2}}
    without_bonus = {**base_character, "skillItemBonuses": {}}
    out_a = tmp_path / "with.html"
    out_b = tmp_path / "without.html"
    sheet.render_character_sheet(with_bonus, str(out_a), level=9)
    sheet.render_character_sheet(without_bonus, str(out_b), level=9)
    with_total = _athletics_total(out_a.read_text(encoding="utf-8"))
    without_total = _athletics_total(out_b.read_text(encoding="utf-8"))
    assert with_total == without_total + 2


def test_assurance_does_not_pick_up_the_item_bonus(conn, tmp_path):
    """Assurance is flatly 10 + level + proficiency rank -- no ability, no
    item, no circumstance or status bonus of any kind. Folding the armbands
    in here would be a second, wrong, kind of "not modelled"."""
    character = {
        "name": "Assured", "class": "Fighter", "ancestry": "Orc", "level": 9,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 10},
        "proficiencies": {"athletics": 2},  # trained
        "feats": [["Assurance (Athletics)", None, "Skill Feat", 2, "Skill Feat 2"]],
        "skillItemBonuses": {"athletics": 2},
    }
    out = tmp_path / "assured.html"
    sheet.render_character_sheet(character, str(out), level=9)
    core = re.search(r'data-sec="core"(.*?)</section>', out.read_text(encoding="utf-8"), re.S).group(1)
    assurance_row = re.search(r'Assurance.*?class="t">(\d+)</td>', core, re.S)
    # 10 + level(9) + trained(2) -- no ability, no item bonus.
    assert int(assurance_row.group(1)) == 21
