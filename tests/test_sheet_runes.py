"""Runes have to reach the sheet, and reach it priced.

Three separate failures met here, all about a rune that exists in the
character data but not on the page:

* A striking rune spelled as `increasedDice` -- which is how *both* producers
  spell plain striking -- was read by nothing, so a striking weapon rolled one
  damage die short and its rune line never mentioned striking (#82).
* The Inventory table read only the freeform `runes` list, which by
  construction holds no fundamental rune at all, and priced every row at the
  base item's cost. A +2 striking returning light hammer printed as
  "3 sp | returning" (#83).
* Doubling rings copy runes onto the off-hand weapon; nothing modelled that,
  so the second weapon rendered unetched (#84).
"""

from __future__ import annotations

import html
import re

import pytest

from pf2e_mcp.server import character_import, character_replay, sheet


def _inventory_rows(page: str) -> dict[str, list[str]]:
    body = re.search(r'data-sec="inventory"(.*?)</section>', page, re.S).group(1)
    rows = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = [html.unescape(re.sub("<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if cells:
            rows[cells[0]] = cells[1:]
    return rows


def _strike_rows(page: str) -> str:
    return html.unescape(re.sub("<[^>]+>", " ", page))


@pytest.fixture
def page(conn, tmp_path) -> str:
    character = {
        "name": "Rune Tester",
        "class": "Ranger",
        "ancestry": "Orc",
        "level": 11,
        "abilities": {"str": 20, "dex": 16, "con": 16, "int": 10, "wis": 18,
                      "cha": 10},
        "proficiencies": {"martial": 6, "simple": 6, "light": 4},
        "feats": [],
        "weapons": [
            # Plain striking, spelled the only way either producer spells it.
            {"name": "Light Hammer", "qty": 1, "prof": "martial", "die": "d6",
             "pot": 2, "runes": ["returning"], "increasedDice": True,
             "damageType": "B"},
            # The same runes, borrowed rather than bought.
            {"name": "Gauntlet", "qty": 1, "prof": "simple", "die": "d4",
             "pot": 2, "runes": [], "increasedDice": True, "damageType": "B",
             "runesFrom": "doubling-rings"},
        ],
        "armor": [
            {"name": "Studded Leather Armor", "qty": 1, "prof": "light",
             "pot": 1, "res": "resilient", "worn": True, "runes": []},
        ],
        "equipment": [["Doubling Rings", 1, "Invested"]],
    }
    out = tmp_path / "runes.html"
    sheet.render_character_sheet(character, str(out), level=11)
    return out.read_text(encoding="utf-8")


# ------------------------------------------------------- the striking rune

def test_striking_from_increased_dice_adds_its_damage_die(page):
    """1d6 was the bug: a 65 gp fundamental rune doing nothing at all."""
    assert "2d6+5" in _strike_rows(page)
    assert "1d6+5" not in _strike_rows(page)


def test_striking_is_named_on_the_rune_line(page):
    line = re.search(r'<div class="tr rune">([^<]*)</div>', page).group(1)
    assert "striking" in line
    assert "+2 potency" in line


def test_striking_is_not_printed_twice_when_the_list_already_names_it(conn, tmp_path):
    """A graded rune travels by name *and* sets the boolean. One label."""
    character = {
        "name": "Graded", "class": "Fighter", "ancestry": "Orc", "level": 11,
        "abilities": {"str": 20, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {"martial": 6}, "feats": [],
        "weapons": [{"name": "Longsword", "qty": 1, "prof": "martial",
                     "die": "d8", "pot": 2, "runes": ["greater striking"],
                     "increasedDice": True, "damageType": "S"}],
    }
    out = tmp_path / "graded.html"
    sheet.render_character_sheet(character, str(out), level=11)
    rendered = out.read_text(encoding="utf-8")
    line = re.search(r'<div class="tr rune">([^<]*)</div>', rendered).group(1)
    assert line.lower().count("striking") == 1, line
    # Greater striking is three dice, and the boolean must not cost it one.
    assert "3d8+5" in _strike_rows(rendered)


# --------------------------------------------------------------- inventory

def test_inventory_names_every_rune_including_the_fundamentals(page):
    notes = _inventory_rows(page)["Light Hammer"][-1]
    assert "Weapon Potency (+2)" in notes
    assert "Striking" in notes
    assert "Returning" in notes


def test_inventory_prices_an_item_with_its_runes(page):
    """3 sp for a 1,055 gp weapon is not a rounding difference."""
    assert _inventory_rows(page)["Light Hammer"][2] == "1055 gp 3 sp"


def test_armor_keeps_both_worn_and_its_runes(page):
    qty, bulk, price, notes = _inventory_rows(page)["Studded Leather Armor"]
    assert "Worn" in notes
    assert "Armor Potency (+1)" in notes and "Resilient" in notes
    assert price == "503 gp"  # 3 gp armour + 160 potency + 340 resilient


def test_a_rune_free_item_still_prices_at_its_own_cost(page):
    assert _inventory_rows(page)["Doubling Rings"][2] == "50 gp"


# ------------------------------------------------------ borrowed runes (#84)

def test_copied_runes_apply_to_the_attack_and_the_damage(page):
    """The whole point of the rings: the off hand strikes as if etched."""
    assert "2d4+5" in _strike_rows(page)


def test_copied_runes_are_named_and_credited_but_not_charged_for(page):
    qty, bulk, price, notes = _inventory_rows(page)["Gauntlet"]
    assert "Weapon Potency (+2)" in notes and "Striking" in notes
    assert "Doubling Rings" in notes
    assert price == "2 sp"  # the gauntlet alone; the rings are their own row


# ------------------------------------------ where a striking tier lives (#82)

def test_a_graded_striking_rune_survives_the_replay(conn):
    """`increasedDice` is a boolean, so collapsing every tier into it lost the
    difference between striking and major striking -- two damage dice, and
    30,000 gp of rune, gone between the file and the sheet."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Graded", "currentLevel": 11},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "longsword",
             "runes": ["weapon-potency-2", "striking-greater"]},
        ]},
    }
    weapon = character_replay.at_level(document, 11, conn)["weapons"][0]
    assert weapon["increasedDice"] is True
    assert "greater striking" in weapon["runes"]
    assert sheet._striking_dice(weapon) == 3


def test_a_graded_rune_does_not_import_back_as_two_striking_runes(conn):
    """The round trip is what makes this reachable: replay writes the name and
    the boolean, and the import used to believe both."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Graded", "currentLevel": 11},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "longsword",
             "runes": ["weapon-potency-2", "striking-greater"]},
        ]},
    }
    export = character_replay.at_level(document, 11, conn)
    back = character_import.from_pathbuilder({"build": export}, conn)
    runes = back["gear"]["carried"][0]["runes"]
    assert sorted(runes) == ["striking-greater", "weapon-potency-2"], runes


def test_borrowed_runes_survive_the_replay(conn):
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Borrower", "currentLevel": 11},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "gauntlet", "runes": ["weapon-potency-2", "striking"],
             "runesFrom": "doubling-rings"},
        ]},
    }
    weapon = character_replay.at_level(document, 11, conn)["weapons"][0]
    assert weapon["runesFrom"] == "doubling-rings"
    assert weapon["pot"] == 2 and weapon["increasedDice"] is True


# ------------------------------------------- Pathbuilder's real field (#85)

@pytest.mark.parametrize("name,dice,label", [
    ("striking", 2, "striking"),
    ("greater striking", 3, "greater striking"),
    ("major striking", 4, "major striking"),
])
def test_striking_is_read_from_pathbuilders_str_field(conn, tmp_path,
                                                      name, dice, label):
    """A live export writes the tier's *name* into `str` and leaves
    `increasedDice` false. Reading only the boolean lost the die entirely."""
    character = {
        "name": "Exported", "class": "Fighter", "ancestry": "Orc", "level": 11,
        "abilities": {"str": 20, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {"martial": 6}, "feats": [],
        "weapons": [{"name": "Longsword", "qty": 1, "prof": "martial",
                     "die": "d8", "pot": 2, "str": name, "runes": [],
                     "increasedDice": False, "damageType": "S"}],
    }
    out = tmp_path / f"{dice}.html"
    sheet.render_character_sheet(character, str(out), level=11)
    rendered = out.read_text(encoding="utf-8")
    assert f"{dice}d8+5" in _strike_rows(rendered)
    assert label in re.search(r'<div class="tr rune">([^<]*)</div>',
                              rendered).group(1)


def test_the_str_field_is_priced_and_named_on_the_inventory(conn, tmp_path):
    character = {
        "name": "Exported", "class": "Fighter", "ancestry": "Orc", "level": 11,
        "abilities": {"str": 20, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {"martial": 6}, "feats": [],
        "weapons": [{"name": "Longsword", "qty": 1, "prof": "martial",
                     "die": "d8", "pot": 1, "str": "striking", "runes": [],
                     "increasedDice": False, "damageType": "S"}],
    }
    out = tmp_path / "inv.html"
    sheet.render_character_sheet(character, str(out), level=11)
    qty, bulk, price, notes = _inventory_rows(out.read_text(encoding="utf-8"))["Longsword"]
    assert "Striking" in notes and "Weapon Potency (+1)" in notes
    assert price == "101 gp"  # 1 gp sword + 35 potency + 65 striking


@pytest.mark.parametrize("name,slug", [
    ("striking", "striking"),
    ("greater striking", "striking-greater"),
    ("major striking", "striking-major"),
])
def test_importing_an_export_keeps_the_striking_tier(conn, name, slug):
    """The import read `increasedDice`, which a real export leaves false, so
    every striking rune was dropped on the way into a character file."""
    export = {"build": {
        "name": "Exported", "class": "Fighter", "level": 11, "ancestry": "Orc",
        "background": "Acolyte", "heritage": "Battle Ready",
        "abilities": {"str": 20, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10, "breakdown": {}},
        "feats": [], "weapons": [
            {"name": "Longsword", "qty": 1, "prof": "martial", "die": "d8",
             "pot": 2, "str": name, "runes": [], "increasedDice": False},
        ],
    }}
    document = character_import.from_pathbuilder(export, conn)
    runes = document["gear"]["carried"][0]["runes"]
    assert slug in runes, runes
    assert sorted(runes) == sorted([slug, "weapon-potency-2"]), runes


def test_replay_writes_the_tier_back_into_str(conn):
    """So the field Pathbuilder reads is the field this project fills."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Roundtrip", "currentLevel": 11},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "longsword", "runes": ["weapon-potency-2", "striking-greater"]},
        ]},
    }
    weapon = character_replay.at_level(document, 11, conn)["weapons"][0]
    assert weapon["str"] == "greater striking"
