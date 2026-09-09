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


# ---------------------------------------------------------------- advancement

@pytest.fixture
def advancement(conn, tmp_path) -> str:
    """A native-format character whose plan names every skill choice."""
    from pf2e_mcp.server import character_replay
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Skill Namer", "currentLevel": 7},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "ranger",
                  "heritage": None},
        "plan": [
            {"level": 1, "choices": [
                {"slot": "skillTraining",
                 "pick": ["athletics", "nature", "intimidation", "stealth"]},
            ]},
            {"level": 3, "choices": [{"slot": "skillIncrease", "pick": "athletics"}]},
            {"level": 5, "choices": [{"slot": "skillIncrease", "pick": "intimidation"}]},
            {"level": 7, "choices": [{"slot": "skillIncrease", "pick": "athletics"}]},
        ],
    }
    state = character_replay.at_level(document, 7, conn)
    out = tmp_path / "adv.html"
    sheet.render_character_sheet(state, str(out), level=7)
    return out.read_text(encoding="utf-8")


def _advancement_lines(html: str) -> list[str]:
    body = re.search(r'data-sec="advancement"(.*?)</section>', html, re.S)
    return re.findall(r'<div class="adv-boost">([^<]*)</div>', body.group(1))


def test_level_one_names_the_free_picks_instead_of_counting_them(advancement):
    """The native format records exactly which skills were chosen; only a
    Pathbuilder export, which stores ranks rather than choices, has to say
    "+4 free"."""
    line = next(l for l in _advancement_lines(advancement) if l.startswith("Skill training"))
    for skill in ("Athletics", "Nature", "Intimidation", "Stealth"):
        assert skill in line, line
    assert "free" not in line, line


def test_each_skill_increase_names_its_skill(advancement):
    lines = _advancement_lines(advancement)
    assert "Skill increase: Athletics" in lines
    assert "Skill increase: Intimidation" in lines
    # Bare, skill-less rows are the bug this replaced.
    assert "Skill increase" not in lines


def test_repeated_increases_on_one_skill_are_not_duplicate_feats(conn):
    """Athletics goes trained -> expert -> master through separate increases
    naming the same skill. That is the norm, not a duplicate feat."""
    from pf2e_mcp.server import build_tools
    character = {
        "name": "Repeater", "class": "Ranger", "ancestry": "Orc", "level": 7,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 14, "cha": 10},
        "proficiencies": {},
        "feats": [
            ["Athletics", None, "Skill Increase", 3, "Skill Increase"],
            ["Athletics", None, "Skill Increase", 7, "Skill Increase"],
        ],
    }
    result = build_tools.validate_build(character)
    assert not [e for e in result["errors"] if "Duplicate" in e], result["errors"]
    assert not [w for w in result["warnings"] if "not found in rules database" in w], \
        result["warnings"]
