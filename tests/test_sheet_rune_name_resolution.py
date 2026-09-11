"""A property rune's slug and its display name only coincide by accident.

The Inventory table always resolved a rune slug to its rules-database name
(`_inventory`'s `add` calls `lib.by_slug` on every entry). The Strikes table
and the page-1 armor block did not -- they printed whatever string sat in
`runes` verbatim. That went unnoticed for a long time because every property
rune this project's characters had carried so far happened to be a single
word whose slug is just its own lowercased spelling (Fearsome -> "fearsome",
Returning -> "returning"). Crushing (Greater) is the first one that isn't:
its slug is "crushing-greater", and printing that raw on the sheet a player
actually reads mid-combat is exactly the kind of thing a rules-data project
should not do.
"""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import sheet


@pytest.fixture
def page(conn, tmp_path) -> str:
    character = {
        "name": "Graded Rune Tester",
        "class": "Fighter",
        "ancestry": "Orc",
        "level": 13,
        "abilities": {"str": 20, "dex": 14, "con": 16, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {"martial": 6, "simple": 6, "light": 4},
        "feats": [],
        "weapons": [
            {"name": "Gauntlet", "qty": 1, "prof": "simple", "die": "d4",
             "pot": 2, "runes": ["crushing-greater", "greater striking"],
             "increasedDice": True, "damageType": "B"},
        ],
        "armor": [
            {"name": "Studded Leather Armor", "qty": 1, "prof": "light",
             "pot": 2, "res": "resilient", "worn": True,
             "runes": ["crushing-greater"]},
        ],
    }
    out = tmp_path / "graded.html"
    sheet.render_character_sheet(character, str(out), level=13)
    return out.read_text(encoding="utf-8")


def test_strikes_table_prints_the_runes_real_name(page):
    core = re.search(r'data-sec="core"(.*?)</section>', page, re.S).group(1)
    gauntlet_row = re.search(r"Gauntlet.*?</tr>", core, re.S).group(0)
    assert "Crushing (Greater)" in gauntlet_row
    assert "crushing-greater" not in gauntlet_row


def test_armor_block_prints_the_runes_real_name(page):
    core = re.search(r'data-sec="core"(.*?)</section>', page, re.S).group(1)
    assert "Crushing (Greater)" in core
    assert "crushing-greater" not in core


def test_a_single_word_rune_still_prints_fine(conn, tmp_path):
    """Fearsome's slug already equals its lowercased name -- confirms the
    resolution path doesn't break the common case it was invisible in."""
    character = {
        "name": "Plain Rune Tester", "class": "Fighter", "ancestry": "Orc",
        "level": 13,
        "abilities": {"str": 20, "dex": 14, "con": 16, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {"martial": 6}, "feats": [],
        "weapons": [
            {"name": "Light Hammer", "qty": 1, "prof": "martial", "die": "d6",
             "pot": 2, "runes": ["fearsome"], "increasedDice": True,
             "damageType": "B"},
        ],
    }
    out = tmp_path / "plain.html"
    sheet.render_character_sheet(character, str(out), level=13)
    core = re.search(r'data-sec="core"(.*?)</section>',
                      out.read_text(encoding="utf-8"), re.S).group(1)
    assert "Fearsome" in core


def test_graded_striking_still_reads_as_prose_not_a_slug(page):
    """`_graded_striking` already emits "greater striking" as prose, not a
    slug -- resolution must not turn it into something worse when it fails
    to match (there is no "greater-striking" slug; the real one is
    "striking-greater")."""
    core = re.search(r'data-sec="core"(.*?)</section>', page, re.S).group(1)
    gauntlet_row = re.search(r"Gauntlet.*?</tr>", core, re.S).group(0)
    assert "greater striking" in gauntlet_row
