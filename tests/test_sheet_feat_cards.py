"""A feat recorded with a parenthetical choice has to print that choice.

Two failures were possible and both happened. `_card` headed every card with
the *resolved* entry's name, so "Advanced Maneuver (Combat Grab)" printed as
"Advanced Maneuver" -- and since Advanced Maneuver is repeatable, a character
who took it three times got three identical cards. Worse, the rules text under
each was Advanced Maneuver's own ("You gain a fighter feat"), so the sheet
never said what Combat Grab, Double Slice or Dueling Parry actually do.
"""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import sheet


def _headings(html: str) -> list[tuple[str, str]]:
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(
            r'<div class="card-hd"><h3>([^<]*)</h3><span class="rank">([^<]*)</span>',
            html,
        )
    ]


@pytest.fixture
def rendered(conn, tmp_path) -> str:
    character = {
        "name": "Parenthetical Tester",
        "class": "Ranger",
        "ancestry": "Orc",
        "level": 10,
        "abilities": {"str": 18, "dex": 14, "con": 12, "int": 10, "wis": 14, "cha": 10},
        "proficiencies": {},
        "feats": [
            ["Fighter Dedication", None, "Class Feat", 2, "Archetype Feat"],
            ["Basic Maneuver (Double Slice)", None, "Class Feat", 4, "Archetype Feat"],
            ["Advanced Maneuver (Combat Grab)", None, "Class Feat", 6, "Archetype Feat"],
            ["Advanced Maneuver (Dueling Parry)", None, "Class Feat", 10, "Archetype Feat"],
            ["Assurance (Athletics)", None, "Skill Feat", 2, "Skill Feat 2"],
        ],
    }
    out = tmp_path / "sheet.html"
    sheet.render_character_sheet(character, str(out), level=10)
    return out.read_text(encoding="utf-8")


def test_parenthetical_choice_survives_into_the_heading(rendered):
    """Otherwise a repeatable wrapper prints identical cards."""
    headings = [h for h, _ in _headings(rendered)]
    assert "Advanced Maneuver (Combat Grab)" in headings
    assert "Advanced Maneuver (Dueling Parry)" in headings
    assert "Basic Maneuver (Double Slice)" in headings


def test_repeated_wrapper_does_not_print_indistinguishable_cards(rendered):
    """Three Advanced Maneuvers, three different headings."""
    wrappers = [h for h, _ in _headings(rendered) if h.startswith("Advanced Maneuver")]
    assert len(wrappers) == len(set(wrappers)), wrappers


def test_the_granted_feat_prints_its_own_rules_text(rendered):
    """"You gain a fighter feat" is not something anyone can play from."""
    granted = {h for h, kicker in _headings(rendered) if kicker.startswith("Granted by")}
    assert {"Double Slice", "Combat Grab", "Dueling Parry"} <= granted
    # The text itself, not just the name, has to be on the page.
    assert "keeping one hand free" in rendered  # Combat Grab
    assert "one-handed melee weapon" in rendered  # Dueling Parry


def test_a_parenthetical_that_is_not_a_feat_grants_nothing(rendered):
    """Assurance's parenthetical names a skill. Nothing to print, and no
    spurious `unresolved` report either -- the lookup is made quietly."""
    granted = {h for h, kicker in _headings(rendered) if kicker.startswith("Granted by")}
    assert "Athletics" not in granted
    assert "Assurance (Athletics)" in [h for h, _ in _headings(rendered)]


def test_a_feat_taken_directly_is_not_also_printed_as_granted(conn, tmp_path):
    """Double Slice held in its own right and handed over by Basic Maneuver is
    still one feat, and should appear once."""
    character = {
        "name": "Duplicate Tester",
        "class": "Fighter",
        "ancestry": "Orc",
        "level": 4,
        "abilities": {"str": 18, "dex": 14, "con": 12, "int": 10, "wis": 14, "cha": 10},
        "proficiencies": {},
        "feats": [
            ["Double Slice", None, "Class Feat", 1, "Class Feat 1"],
            ["Basic Maneuver (Double Slice)", None, "Class Feat", 4, "Archetype Feat"],
        ],
    }
    out = tmp_path / "dupe.html"
    sheet.render_character_sheet(character, str(out), level=4)
    headings = [h for h, _ in _headings(out.read_text(encoding="utf-8"))]
    assert headings.count("Double Slice") == 1


def test_feat_cards_are_alphabetical(rendered):
    """These pages are looked things up in, not read through. The level a feat
    was taken at is still on its kicker, and the advancement table already
    tells the story in level order."""
    headings = [h for h, _ in _headings(rendered)]
    # Restrict to the Feats subsection: everything from the first feat card on.
    start = headings.index("Assurance (Athletics)")
    feats = headings[start:]
    assert feats == sorted(feats, key=str.casefold), feats


def test_a_granted_feat_files_under_its_own_name(rendered):
    """Combat Grab belongs under C, not trailing the Advanced Maneuver that
    handed it over -- otherwise alphabetical order is a lie for exactly the
    cards whose names the reader had to be told."""
    headings = [h for h, _ in _headings(rendered)]
    assert headings.index("Combat Grab") < headings.index("Dueling Parry")
