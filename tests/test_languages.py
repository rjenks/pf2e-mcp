"""Languages, and the level they were learned at.

Player Core p. 29 grants "an additional skill and language" whenever a boost
raises the Intelligence *modifier*. The skill half has always been visible in
the plan as a `skillTraining`; the language half had nowhere to live, so a flat
`build.languages` list was handed out whole at every level and nothing noticed
when it was short. These tests pin both halves of the fix: replay stops
over-reporting at earlier levels, and validation counts what is owed.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import character, character_replay


def _boosts(*attributes: str) -> dict:
    return {"free": list(attributes)}


@pytest.fixture
def scholar() -> dict:
    """A Human Ranger whose Intelligence modifier rises at 5th and 15th.

    Int starts at +0, so nothing is owed at 1st: the two starting languages are
    the Human ancestry's Common plus one free.
    """
    return {
        "schemaVersion": 1,
        "identity": {"name": "Test Subject", "currentLevel": 20},
        "build": {
            "ancestry": "human",
            "heritage": "versatile-human",
            "background": "farmhand",
            "class": "ranger",
            "languages": ["common", "elven"],
        },
        "plan": [
            {"level": 1, "attributeBoosts": {
                "free": ["str", "con", "dex", "wis"],
                "ancestry": {"free": ["str", "dex"]},
                "background": ["con", "str"],
                "class": ["str"],
            }},
            {"level": 5, "attributeBoosts": _boosts("str", "con", "dex", "int"),
             "choices": [{"slot": "language", "pick": "dwarven"}]},
            {"level": 10, "attributeBoosts": _boosts("str", "con", "wis", "dex")},
            {"level": 15, "attributeBoosts": _boosts("str", "wis", "dex", "int"),
             "choices": [{"slot": "language", "pick": "orcish"}]},
            {"level": 20, "attributeBoosts": _boosts("str", "wis", "dex", "int")},
        ],
    }


# ------------------------------------------------------- when Int actually rises


def test_only_modifier_increases_count(scholar):
    """A boost from 18 to 19 buys no modifier and so grants no language.

    Int here goes +0 -> +1 at 5th, +1 -> +2 at 15th, +2 -> +3 at 20th; all
    three are real modifier increases, so all three are owed.
    """
    assert character_replay.int_gain_levels(scholar["plan"]) == [5, 15, 20]


def test_creation_boosts_grant_nothing(scholar):
    """1st-level Intelligence buys starting languages, not a levelled grant."""
    scholar["plan"][0]["attributeBoosts"]["free"] = ["int", "con", "dex", "wis"]
    assert 1 not in character_replay.int_gain_levels(scholar["plan"])


def test_a_half_step_grants_no_language():
    """Two boosts taking Int 18 -> 19 -> 20 grant one language, not two."""
    plan = [
        {"level": 1, "attributeBoosts": {"free": ["int", "int", "int", "int"]}},
        {"level": 5, "attributeBoosts": {"free": ["int", "str", "dex", "con"]}},
        {"level": 10, "attributeBoosts": {"free": ["int", "str", "dex", "con"]}},
    ]
    # 1st gets Int to 18 (+4). 5th takes it to 19, still +4 -- no grant.
    # 10th takes it to 20 (+5), which is the grant.
    assert character_replay.int_gain_levels(plan) == [10]


def test_levels_are_capped_by_the_level_asked_for(scholar):
    assert character_replay.int_gain_levels(scholar["plan"], 15) == [5, 15]
    assert character_replay.int_gain_levels(scholar["plan"], 4) == []


# ------------------------------------------------------------------- replay


def test_replay_at_an_earlier_level_omits_later_languages(scholar, conn):
    """The bug this fixes: a 3rd-level sheet listed 20th-level languages."""
    at_three = character_replay.at_level(scholar, 3, conn)
    assert at_three["languages"] == ["Common", "Elven"]

    at_twenty = character_replay.at_level(scholar, 20, conn)
    assert at_twenty["languages"] == ["Common", "Elven", "Dwarven", "Orcish"]


def test_replay_picks_up_a_language_the_level_it_was_learned(scholar, conn):
    assert "Dwarven" not in character_replay.at_level(scholar, 4, conn)["languages"]
    assert "Dwarven" in character_replay.at_level(scholar, 5, conn)["languages"]


def test_derivation_reports_an_unspent_increase(scholar, conn):
    """20th grants a language and the fixture never spends it."""
    trace = character_replay.at_level(scholar, 20, conn)["_derivation"]["language_grants"]
    assert any("20" in line and "without a language" in line for line in trace)


# --------------------------------------------------------------- validation


def test_validation_warns_about_an_unspent_increase(scholar, conn):
    result = character.validate_document(scholar, conn)
    unspent = [i for i in result["issues"] if i["code"] == "unspent_language"]
    assert len(unspent) == 1
    assert "level 20" in unspent[0]["message"]


def test_no_warning_once_every_increase_is_spent(scholar, conn):
    scholar["plan"][4]["choices"] = [{"slot": "language", "pick": "jotun"}]
    result = character.validate_document(scholar, conn)
    assert not [i for i in result["issues"] if i["code"] == "unspent_language"]


def test_short_starting_languages_are_flagged(scholar, conn):
    """Human grants Common plus one free; recording only Common is short."""
    scholar["build"]["languages"] = ["common"]
    result = character.validate_document(scholar, conn)
    assert [i for i in result["issues"] if i["code"] == "missing_starting_languages"]


def test_a_positive_starting_intelligence_raises_the_floor(scholar, conn):
    """Int +2 at 1st buys two more languages on top of the ancestry's two.

    Two boosts from two different sources, since one source may never boost the
    same attribute twice.
    """
    scholar["plan"][0]["attributeBoosts"]["ancestry"] = {"free": ["int", "dex"]}
    scholar["plan"][0]["attributeBoosts"]["background"] = ["int", "str"]
    result = character.validate_document(scholar, conn)
    issue = [i for i in result["issues"] if i["code"] == "missing_starting_languages"]
    assert issue and "grant 4" in issue[0]["message"]


def test_language_warnings_are_never_errors(scholar, conn):
    """Which language to learn is the player's call; only the count is checked."""
    scholar["build"]["languages"] = []
    result = character.validate_document(scholar, conn)
    assert result["valid"] is True
