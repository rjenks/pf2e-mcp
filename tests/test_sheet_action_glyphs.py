from __future__ import annotations

import pytest

from pf2e_mcp.server import character_import, sheet


@pytest.fixture
def character_doc(conn) -> dict:
    return character_import.from_pathbuilder({
        "build": {
            "name": "Action Test",
            "class": "Fighter",
            "level": 1,
            "ancestry": "Dwarf",
            "heritage": "Death Warden Dwarf",
            "background": "Field Medic",
            "abilities": {
                "str": 18, "dex": 12, "con": 16, "int": 10, "wis": 12, "cha": 8,
                "breakdown": {
                    "ancestryBoosts": ["Con", "Wis"],
                    "ancestryFree": ["Str"],
                    "ancestryFlaws": ["Cha"],
                    "backgroundBoosts": ["Con", "Str"],
                    "classBoosts": ["Str"],
                    "mapLevelledBoosts": {"1": ["Str", "Dex", "Con", "Wis"]},
                },
            },
            "feats": [
                ["Death Warden Dwarf", None, "Heritage", 1],
                ["Power Attack", None, "Class Feat", 1],
            ],
            "weapons": [{"name": "Longsword", "qty": 1}],
            "armor": [{"name": "Chain Mail", "qty": 1, "worn": True}],
            "money": {"gp": 10},
        },
    }, conn)


def test_glyph_spans():
    assert sheet._glyph("1") == '<span class="action-glyph">1</span>'
    assert sheet._glyph("2") == '<span class="action-glyph">2</span>'
    assert sheet._glyph("3") == '<span class="action-glyph">3</span>'
    assert sheet._glyph("reaction") == '<span class="action-glyph">R</span>'
    assert sheet._glyph("free") == '<span class="action-glyph">F</span>'


def test_cost_glyphs_formatting():
    assert sheet._cost_glyphs("1") == '<span class="action-glyph">1</span>'
    assert sheet._cost_glyphs("reaction") == '<span class="action-glyph">R</span>'
    assert sheet._cost_glyphs("free") == '<span class="action-glyph">F</span>'
    assert "to" in sheet._cost_glyphs("1 to 3")


def test_rendered_sheet_embeds_action_font(character_doc, tmp_path):
    out = tmp_path / "sheet.html"
    sheet.render_character_sheet(character_doc, str(out), level=1)
    content = out.read_text(encoding="utf-8")
    assert "font-family:'Pathfinder2eActions'" in content
    assert '<span class="action-glyph">' in content

