"""Plain doubling rings copy fundamental runes only -- a property rune etched
directly on the item they feed is genuinely bought, and stays priced even
while the fundamentals riding along with it are free.

Before this, `runesFrom` was all-or-nothing: every rune slug on an item was
either fully priced or fully free, with no way to say "this one rune here was
actually bought." That's exactly wrong for a weapon built around plain
doubling rings (as opposed to greater doubling rings, which really do copy
everything) plus an owned property rune on the borrowing weapon -- a build
this project's own data confirms is legal, since the plain rings' text names
fundamental runes specifically and never mentions property runes at all.
"""

from __future__ import annotations

import html
import re

import pytest

from pf2e_mcp.server import character_replay, sheet


def _inventory_rows(page: str) -> dict[str, list[str]]:
    body = re.search(r'data-sec="inventory"(.*?)</section>', page, re.S).group(1)
    rows = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = [html.unescape(re.sub("<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if cells:
            rows[cells[0]] = cells[1:]
    return rows


@pytest.fixture
def page(conn, tmp_path) -> str:
    character = {
        "name": "Mixed Runes Tester",
        "class": "Ranger",
        "ancestry": "Orc",
        "level": 13,
        "abilities": {"str": 20, "dex": 16, "con": 16, "int": 10, "wis": 18,
                      "cha": 10},
        "proficiencies": {"martial": 6, "simple": 6, "light": 4},
        "feats": [],
        "weapons": [
            {"name": "Light Hammer", "qty": 1, "prof": "martial", "die": "d6",
             "pot": 2, "runes": ["returning"], "increasedDice": True,
             "damageType": "B"},
            # Plain doubling rings: the fundamentals are borrowed, but the
            # property rune (crushing-greater) is genuinely etched and owed
            # its own price.
            {"name": "Gauntlet", "qty": 1, "prof": "simple", "die": "d4",
             "pot": 2, "runes": ["crushing-greater"], "increasedDice": True,
             "damageType": "B", "runesFrom": "doubling-rings",
             "ownRunes": ["crushing-greater"]},
        ],
        "armor": [
            {"name": "Studded Leather Armor", "qty": 1, "prof": "light",
             "pot": 2, "res": "resilient", "worn": True, "runes": []},
        ],
        "equipment": [["Doubling Rings", 1, "Invested"]],
    }
    out = tmp_path / "own_runes.html"
    sheet.render_character_sheet(character, str(out), level=13)
    return out.read_text(encoding="utf-8")


def test_the_borrowed_fundamentals_stay_free(page):
    """weapon-potency-2 and striking (greater, from increasedDice) are still
    the hammer's, copied for nothing -- own_runes doesn't change that."""
    qty, bulk, price, notes = _inventory_rows(page)["Gauntlet"]
    assert "Weapon Potency (+2)" in notes
    assert "Striking" in notes


def test_the_owned_property_rune_is_actually_priced(page):
    """Crushing (Greater) is 650 gp on its own -- that cost has to survive
    even though it sits on an item whose fundamentals are all borrowed."""
    qty, bulk, price, notes = _inventory_rows(page)["Gauntlet"]
    assert "Crushing" in notes
    assert price == "650 gp 2 sp"  # the gauntlet's own 2 sp, plus the rune


def test_the_borrowed_runes_still_say_where_they_came_from(page):
    qty, bulk, price, notes = _inventory_rows(page)["Gauntlet"]
    assert "Doubling Rings" in notes


def test_the_owned_rune_does_not_carry_a_borrowed_label(page):
    """Crushing (Greater) was bought outright -- it should read as itself,
    not as something borrowed from the rings alongside the fundamentals."""
    qty, bulk, price, notes = _inventory_rows(page)["Gauntlet"]
    crushing_clause = re.search(r"Crushing[^,]*", notes).group(0)
    assert "Doubling Rings" not in crushing_clause


# ------------------------------------------------------------- through replay

def test_own_runes_survives_the_replay(conn):
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Mixed", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "gauntlet", "runes": ["weapon-potency-2", "striking-greater",
                                           "crushing-greater"],
             "runesFrom": "doubling-rings", "ownRunes": ["crushing-greater"]},
        ]},
    }
    weapon = character_replay.at_level(document, 13, conn)["weapons"][0]
    assert weapon["runesFrom"] == "doubling-rings"
    assert weapon["ownRunes"] == ["crushing-greater"]


def test_omitting_own_runes_keeps_the_old_all_borrowed_behaviour(conn, tmp_path):
    """No `ownRunes` at all -- the legacy, still-supported case where a
    greater doubling ring (or nothing distinguishing at all) means every
    rune present is free. Confirms nothing regressed for that shape."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Legacy Borrower", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "gauntlet", "runes": ["weapon-potency-2", "striking-greater"],
             "runesFrom": "doubling-rings-greater"},
        ]},
    }
    state = character_replay.at_level(document, 13, conn)
    weapon = state["weapons"][0]
    assert weapon["ownRunes"] is None
    out = tmp_path / "legacy_borrower.html"
    sheet.render_character_sheet(state, str(out), level=13)
    rows = _inventory_rows(out.read_text(encoding="utf-8"))
    qty, bulk, price, notes = rows["Gauntlet"]
    assert price == "2 sp"  # the bare gauntlet; both runes still free
