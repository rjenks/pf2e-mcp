from __future__ import annotations

import pytest

from pf2e_mcp.server import character_import, sheet


@pytest.fixture
def character_doc(conn) -> dict:
    return character_import.from_pathbuilder(
        {
            "build": {
                "name": "Action Test",
                "class": "Fighter",
                "level": 1,
                "ancestry": "Dwarf",
                "heritage": "Death Warden Dwarf",
                "background": "Field Medic",
                "abilities": {
                    "str": 18,
                    "dex": 12,
                    "con": 16,
                    "int": 10,
                    "wis": 12,
                    "cha": 8,
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
        },
        conn,
    )


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


def test_card_header_prefixes_glyph_and_omits_redundant_cost_label():
    """Card headings prefix the action glyph before the title and omit
    redundant right-aligned text like '1 ACTION'."""
    entry = {
        "name": "Demoralize",
        "system": {"actions": {"value": 1}, "actionType": {"value": "action"}},
        "traits": ["auditory", "concentrate", "emotion", "fear", "mental"],
        "desc_html": "<p>Attempt an Intimidation check...</p>",
    }
    card_html = sheet._card(entry, kicker="Intimidation")
    assert (
        '<h3><span class="action-glyph">1</span>Demoralize</h3><span class="rank">Intimidation</span>'
        in card_html
    )
    assert '<span class="cost">' not in card_html
    assert "1 action" not in card_html.lower()


def test_features_section_includes_action_icons_and_rules(tmp_path):
    """Class features granting actions (like Hunt Prey, Rage, Reactive Strike)
    carry their action glyph prefix and full rules text on their card."""
    ranger = {
        "name": "Hunt Prey Tester",
        "class": "Ranger",
        "ancestry": "Orc",
        "level": 1,
        "abilities": {"str": 18, "dex": 14, "con": 12, "int": 10, "wis": 14, "cha": 10},
        "proficiencies": {"survival": 2, "nature": 2, "stealth": 2},
        "feats": [],
    }
    out = tmp_path / "ranger.html"
    sheet.render_character_sheet(ranger, str(out), level=1)
    content = out.read_text(encoding="utf-8")
    # Hunt Prey has 1-action glyph prefixing its name in features
    assert '<h3><span class="action-glyph">1</span>Hunt Prey</h3>' in content
    # Hunt Prey has full rules text rather than a stub
    assert "designate a single creature as your prey" in content
    # No redundant "1 action" cost span
    assert '<span class="cost">' not in content
