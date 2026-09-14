"""Page 1's Class DC block names the actual class, and a multiclass archetype
dedication that grants its own class DC (Fighter Dedication's "you become
trained in fighter class DC", and the general pattern) shows up as a second
row rather than being invisible. Page 1 no longer duplicates the dedicated
Spellcasting page's own Spell DC tile."""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import sheet


def _core_section(html: str) -> str:
    match = re.search(r'<section class="page" data-sec="core">(.*?)</section>', html, re.DOTALL)
    assert match, "no core page section found"
    return match.group(1)


@pytest.fixture
def cleric_char() -> dict:
    return {
        "name": "Test Cleric",
        "class": "Cleric",
        "keyability": "wis",
        "ancestry": "Human",
        "level": 3,
        "abilities": {"str": 10, "dex": 12, "con": 14, "int": 10, "wis": 18, "cha": 10},
        "proficiencies": {"classDC": 2},
        "feats": [],
    }


def test_class_dc_row_names_the_class(cleric_char, conn, tmp_path):
    out = tmp_path / "cleric.html"
    sheet.render_character_sheet(cleric_char, str(out), level=3)
    core = _core_section(out.read_text(encoding="utf-8"))
    assert '<span class="nm">Cleric DC</span>' in core
    assert '<span class="nm">Class DC</span>' not in core


def test_multiclass_dedication_adds_a_second_class_dc_row(conn, tmp_path):
    """A Fighter (primary class, `classDC` bucket) who took Ranger Dedication
    -- which grants a literal `ranger`-keyed class DC, distinct from the
    primary bucket -- must show both rows."""
    fighter_with_ranger_dedication = {
        "name": "Test Multiclass",
        "class": "Fighter",
        "keyability": "str",
        "ancestry": "Human",
        "level": 6,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 10},
        "proficiencies": {"classDC": 4, "ranger": 2},
        "feats": [],
    }
    out = tmp_path / "multiclass.html"
    sheet.render_character_sheet(fighter_with_ranger_dedication, str(out), level=6)
    core = _core_section(out.read_text(encoding="utf-8"))
    assert '<span class="nm">Fighter DC</span>' in core
    assert '<span class="nm">Ranger DC</span>' in core


def test_no_secondary_class_dc_when_untrained(cleric_char, conn, tmp_path):
    """A class-slug key present but at rank 0 (untrained) -- the common case
    for every class the character never touched -- must not produce a row."""
    out = tmp_path / "cleric_untrained.html"
    sheet.render_character_sheet(cleric_char, str(out), level=3)
    core = _core_section(out.read_text(encoding="utf-8"))
    assert core.count('<span class="tot">') >= 1
    assert '<span class="nm">Fighter DC</span>' not in core


def test_page1_has_no_spellcasting_block(conn, tmp_path):
    caster = {
        "name": "Test Caster",
        "class": "Cleric",
        "keyability": "wis",
        "ancestry": "Human",
        "level": 3,
        "abilities": {"str": 10, "dex": 12, "con": 14, "int": 10, "wis": 18, "cha": 10},
        "proficiencies": {"classDC": 2, "divine": 2},
        "feats": [],
        "spellCasters": [
            {
                "name": "Cleric",
                "magicTradition": "divine",
                "spellcastingType": "prepared",
                "ability": "wis",
                "proficiency": 2,
                "spells": [{"spellLevel": 0, "list": []}],
            }
        ],
    }
    out = tmp_path / "caster.html"
    sheet.render_character_sheet(caster, str(out), level=3)
    html = out.read_text(encoding="utf-8")
    core = _core_section(html)

    assert "Spell DC" not in core
    assert "Spell attack" not in core
    # The dedicated Spellcasting page still carries it.
    assert 'data-sec="spellcasting"' in html
