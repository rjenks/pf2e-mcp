"""Two more things the ingested rules data can't supply on its own.

A doubling ring boosts a weapon's *effective* potency above what it actually
owns -- the Strikes table has to use the effective figure (that is what it
hits with), but the Inventory table's business is what was actually bought,
and printing the effective tier there says a rune was purchased that never
was.

Separately, some real RAW costs live only in another entry's prose. The Orc
Warmask ancestry feat's own text says "you can spend 1 hour performing a
ceremony that costs 50 gp" -- that cost belongs to the *feat*, not to the
warmask *item*'s ingested equipment-table row, so nothing derives it, and the
Inventory table printed a bare dash where a real 50 gp belongs.
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


# --------------------------------------------------------------- ownPotency

@pytest.fixture
def own_potency_page(conn, tmp_path) -> str:
    character = {
        "name": "Ring Borrower",
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
            # Effectively +2 (via the rings) but only ever etched at +1.
            {"name": "Gauntlet", "qty": 1, "prof": "simple", "die": "d4",
             "pot": 2, "runes": [], "increasedDice": True, "damageType": "B",
             "runesFrom": "doubling-rings", "ownRunes": ["weapon-potency-1"],
             "ownPotency": 1},
        ],
        "armor": [],
        "equipment": [],
    }
    out = tmp_path / "own_potency.html"
    sheet.render_character_sheet(character, str(out), level=13)
    return out.read_text(encoding="utf-8")


def test_inventory_shows_the_weapons_own_lower_potency(own_potency_page):
    qty, bulk, price, notes = _inventory_rows(own_potency_page)["Gauntlet"]
    assert "Weapon Potency (+1)" in notes
    assert "Weapon Potency (+2)" not in notes


def test_inventory_prices_the_owned_potency_rune(own_potency_page):
    """35 gp for the gauntlet's own +1 potency rune, plus its own 2 sp base
    price -- and nothing at all for the ring-boosted effective tier."""
    qty, bulk, price, notes = _inventory_rows(own_potency_page)["Gauntlet"]
    assert price == "35 gp 2 sp"


def test_strikes_table_still_uses_the_effective_potency(own_potency_page):
    """The number that actually lands the hit must stay +2 -- ownPotency is
    an Inventory-page concern only, never a combat-math one."""
    core = re.search(r'data-sec="core"(.*?)</section>', own_potency_page, re.S).group(1)
    gauntlet_row = re.search(r"Gauntlet.*?</tr>", core, re.S).group(0)
    assert "+2 potency" in gauntlet_row, gauntlet_row


def test_own_potency_survives_the_replay(conn):
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Ring Borrower", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "gauntlet", "runes": ["weapon-potency-2", "striking-greater"],
             "runesFrom": "doubling-rings", "ownRunes": ["weapon-potency-1"],
             "ownPotency": 1},
        ]},
    }
    weapon = character_replay.at_level(document, 13, conn)["weapons"][0]
    assert weapon["ownPotency"] == 1
    assert weapon["pot"] == 2  # the effective tier is untouched


def test_omitting_own_potency_keeps_showing_the_effective_tier(conn, tmp_path):
    """No `ownPotency` at all -- the ordinary case, and Inventory should read
    exactly as it always has."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Ordinary", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "gauntlet", "runes": ["weapon-potency-2", "striking-greater"]},
        ]},
    }
    state = character_replay.at_level(document, 13, conn)
    assert state["weapons"][0]["ownPotency"] is None
    out = tmp_path / "ordinary.html"
    sheet.render_character_sheet(state, str(out), level=13)
    qty, bulk, price, notes = _inventory_rows(out.read_text(encoding="utf-8"))["Gauntlet"]
    assert "Weapon Potency (+2)" in notes


# ------------------------------------------------------------- priceOverride

@pytest.fixture
def price_override_page(conn, tmp_path) -> str:
    character = {
        "name": "Ceremony Payer",
        "class": "Ranger",
        "ancestry": "Orc",
        "level": 13,
        "abilities": {"str": 20, "dex": 16, "con": 16, "int": 10, "wis": 18,
                      "cha": 10},
        "proficiencies": {},
        "feats": [],
        "weapons": [],
        "armor": [],
        "equipment": [{"name": "Orc Warmask", "qty": 1, "invested": True,
                        "priceOverride": 50}],
    }
    out = tmp_path / "override.html"
    sheet.render_character_sheet(character, str(out), level=13)
    return out.read_text(encoding="utf-8")


def test_the_override_price_is_shown_instead_of_the_items_own(price_override_page):
    """Orc Warmask's own ingested equipment row has no price at all -- the
    real 50 gp lives in the *feat*'s prose. Without an override the row would
    print a bare dash."""
    qty, bulk, price, notes = _inventory_rows(price_override_page)["Orc Warmask"]
    assert price == "50 gp"


def test_the_invested_flag_still_reaches_the_note(price_override_page):
    qty, bulk, price, notes = _inventory_rows(price_override_page)["Orc Warmask"]
    assert notes == "Invested"


def test_price_override_survives_the_replay(conn):
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Ceremony Payer", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [
            {"item": "orc-warmask", "invested": True, "priceOverride": 50},
        ]},
    }
    state = character_replay.at_level(document, 13, conn)
    row = state["equipment"][0]
    assert row["priceOverride"] == 50
    assert row["invested"] is True


def test_omitting_price_override_keeps_the_plain_list_row(conn):
    """No override -- the equipment row stays the ordinary Pathbuilder-shaped
    list, not the richer dict, so nothing about an un-overridden item's shape
    changes."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Plain Owner", "currentLevel": 13},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"carried": [{"item": "orc-warmask", "invested": True}]},
    }
    state = character_replay.at_level(document, 13, conn)
    row = state["equipment"][0]
    assert isinstance(row, list)
    assert row == ["Orc Warmask", 1, "Invested"]
