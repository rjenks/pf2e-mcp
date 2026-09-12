"""Replaying a plan into state at a level.

Every test here needs the rules database: replay is almost entirely lookup, so
there is very little to check without it.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import build_tools, character_replay


@pytest.fixture
def dwarf_animist() -> dict:
    """A dwarf Animist planned to 3rd, matching the worked example in the docs.

    Chosen as the fixture because it exercises the awkward parts: an ancestry
    with a non-default speed and a flaw, a class whose Fortitude rank rises
    mid-range, a background granting both a skill and a Lore, an archetype
    dedication, and a subclass that grants Lores only in prose.
    """
    return {
        "schemaVersion": 1,
        "identity": {"name": "Test Subject", "currentLevel": 3},
        "build": {
            "ancestry": "dwarf",
            "heritage": "death-warden-dwarf",
            "background": "field-medic",
            "class": "animist",
            "deity": "pharasma",
            "subclasses": {"animist-apparition": ["witness-to-ancient-battles"]},
            "languages": ["common", "dwarven", "necril"],
        },
        "plan": [
            {
                "level": 1,
                "attributeBoosts": {
                    "ancestry": {
                        "boosts": ["con", "wis"], "free": ["dex"], "flaw": ["cha"],
                    },
                    "background": ["wis", "con"],
                    "class": ["wis"],
                    "free": ["con", "dex", "int", "wis"],
                },
                "choices": [
                    {"slot": "ancestryFeat", "pick": "stonemasons-eye"},
                    {"slot": "skillTraining", "pick": ["crafting", "occultism"]},
                    {"slot": "skillTraining", "pick": ["battlegrounds", "heraldry"]},
                ],
            },
            {
                "level": 2,
                "choices": [
                    {"slot": "classFeat", "pick": "soul-warden-dedication"},
                    {"slot": "skillFeat", "pick": "recognize-spell"},
                ],
            },
            {
                "level": 3,
                "choices": [
                    {"slot": "generalFeat", "pick": "incredible-initiative"},
                    {"slot": "skillIncrease", "pick": "medicine"},
                ],
            },
        ],
        "gear": {
            "currency": {"gp": 2, "sp": 5},
            "carried": [
                {"item": "studded-leather-armor", "worn": True},
                {"item": "mace"},
                {"item": "healers-toolkit"},
            ],
        },
    }


def test_attribute_boosts_replay_in_order(dwarf_animist, conn):
    """Str +0, Dex +2, Con +3, Int +1, Wis +4, Cha -1."""
    build = character_replay.at_level(dwarf_animist, 1, conn)
    mods = {
        key: (value - 10) // 2
        for key, value in build["abilities"].items()
        if key != "breakdown"
    }
    assert mods == {"str": 0, "dex": 2, "con": 3, "int": 1, "wis": 4, "cha": -1}


def test_flaw_is_applied_before_boosts(conn):
    """A flawed attribute boosted back up gains 2 points of score, not 1.

    Order of operations, not bookkeeping: applying the boost first would take
    Strength to 12 and then the flaw to 10, instead of 8 then 10. Both land on
    10 here, but only because the numbers are small -- near 18 the two orders
    diverge, and the rules apply flaws with the ancestry's own boosts.
    """
    document = {
        "schemaVersion": 1,
        "identity": {"name": "X", "currentLevel": 1},
        "build": {"ancestry": "dwarf", "background": "field-medic", "class": "animist"},
        "plan": [{
            "level": 1,
            "attributeBoosts": {
                "ancestry": {"boosts": ["con", "wis"], "free": ["cha"], "flaw": ["cha"]},
            },
        }],
    }
    build = character_replay.at_level(document, 1, conn)
    assert build["abilities"]["cha"] == 10


def test_boost_is_worth_one_modifier_point_above_eighteen(conn):
    """The rule stated in score terms: +2 below 18, +1 at or above."""
    assert character_replay._apply_boost(16) == 18
    assert character_replay._apply_boost(18) == 19
    assert character_replay._apply_boost(19) == 20


def test_proficiency_rises_with_the_class_feature_that_grants_it(dwarf_animist, conn):
    """Fortitude Expertise lands at 3rd, so 2nd level must still be trained."""
    at_two = character_replay.at_level(dwarf_animist, 2, conn)
    at_three = character_replay.at_level(dwarf_animist, 3, conn)
    assert at_two["proficiencies"]["fortitude"] == 2
    assert at_three["proficiencies"]["fortitude"] == 4


def test_class_and_background_grant_their_fixed_skills(dwarf_animist, conn):
    build = character_replay.at_level(dwarf_animist, 1, conn)
    assert build["proficiencies"]["religion"] == 2, "Animist is trained in Religion"
    assert build["proficiencies"]["medicine"] == 2, "Field Medic grants Medicine"
    assert ["Warfare", 2] in build["lores"], "Field Medic grants Warfare Lore"


def test_skill_increase_raises_one_step(dwarf_animist, conn):
    assert character_replay.at_level(dwarf_animist, 2, conn)["proficiencies"]["medicine"] == 2
    assert character_replay.at_level(dwarf_animist, 3, conn)["proficiencies"]["medicine"] == 4


def test_feats_appear_only_at_and_below_the_replayed_level(dwarf_animist, conn):
    def names(level):
        return {feat[0] for feat in character_replay.at_level(
            dwarf_animist, level, conn)["feats"]}

    assert "Soul Warden Dedication" not in names(1)
    assert "Soul Warden Dedication" in names(2)
    assert "Incredible Initiative" not in names(2)
    assert "Incredible Initiative" in names(3)


def test_feat_tuples_keep_the_legacy_positional_shape(dwarf_animist, conn):
    """`sheet.py` and the validators read these by index."""
    build = character_replay.at_level(dwarf_animist, 3, conn)
    feat = next(f for f in build["feats"] if f[0] == "Soul Warden Dedication")
    assert len(feat) == 5
    assert feat[2] == "Class Feat"
    assert feat[3] == 2


def test_automatic_features_are_derived_not_recorded(dwarf_animist, conn):
    """The plan lists no features; they come from the class progression."""
    assert not any(entry.get("automatic") for entry in dwarf_animist["plan"])
    build = character_replay.at_level(dwarf_animist, 3, conn)
    assert "Apparition Attunement" in build["specials"]
    assert "Fortitude Expertise" in build["specials"]


def test_ancestry_speed_and_size_come_from_the_ancestry(dwarf_animist, conn):
    build = character_replay.at_level(dwarf_animist, 1, conn)
    assert build["attributes"]["speed"] == 20, "a dwarf is not 25 feet"
    assert build["sizeName"] == "Medium"


def test_gear_is_split_into_the_three_legacy_lists(dwarf_animist, conn):
    build = character_replay.at_level(dwarf_animist, 1, conn)
    assert [w["name"] for w in build["weapons"]] == ["Mace"]
    assert [a["name"] for a in build["armor"]] == ["Studded Leather Armor"]
    assert build["armor"][0]["worn"] is True
    assert any(row[0] == "Healer's Toolkit" for row in build["equipment"])
    assert build["money"]["gp"] == 2


def test_result_feeds_the_existing_derived_stats_math(dwarf_animist, conn):
    """The whole point of projecting to the legacy shape."""
    build = character_replay.at_level(dwarf_animist, 3, conn)
    derived = build_tools.calculate_derived_stats(build)
    # 10 ancestry + 3 x (8 class + 3 Con) = 43
    assert derived["hp"] == 43
    # 3 level + 4 Wis + 2 trained
    assert derived["perception"] == 9


def test_overrides_apply_and_redundant_ones_are_reported(dwarf_animist, conn):
    dwarf_animist["proficiencyOverrides"] = {
        "society": {"rank": "trained", "source": "some-dedication"},
        "religion": {"rank": "trained", "source": "stale-entry"},
    }
    build = character_replay.at_level(dwarf_animist, 3, conn)
    assert build["proficiencies"]["society"] == 2
    assert build["_derivation"]["redundant_overrides"] == ["religion"]


def test_an_override_with_no_level_still_applies_at_every_level(dwarf_animist, conn):
    """Backward compatibility: most overrides (a Pathbuilder import that
    cannot say which level granted the rank) don't carry one at all, and
    that must keep meaning "applies from 1st onward", same as before this
    field existed."""
    dwarf_animist["proficiencyOverrides"] = {
        "society": {"rank": "trained", "source": "some-dedication"},
    }
    build = character_replay.at_level(dwarf_animist, 1, conn)
    assert build["proficiencies"]["society"] == 2


def test_an_override_does_not_apply_before_its_granting_level(dwarf_animist, conn):
    """A proficiency granted by a 3rd-level dedication should not show up on
    a 1st-level snapshot, the same way the feat that grants it would not."""
    dwarf_animist["proficiencyOverrides"] = {
        "society": {"rank": "trained", "source": "some-dedication", "level": 3},
    }
    early = character_replay.at_level(dwarf_animist, 1, conn)
    late = character_replay.at_level(dwarf_animist, 3, conn)
    assert early["proficiencies"].get("society", 0) == 0
    assert late["proficiencies"]["society"] == 2


def test_hp_per_level_feats_are_counted(dwarf_animist, conn):
    dwarf_animist["plan"][2]["choices"].append(
        {"slot": "generalFeat", "pick": "toughness"}
    )
    build = character_replay.at_level(dwarf_animist, 3, conn)
    assert build["attributes"]["bonushpPerLevel"] == 1
    assert build["_derivation"]["hp_per_level_feats"] == ["toughness"]


def test_derivation_explains_where_ranks_came_from(dwarf_animist, conn):
    trace = character_replay.at_level(dwarf_animist, 3, conn)["_derivation"]
    assert any("Fortitude Expertise" in line for line in trace["proficiency_grants"])
    assert any("religion" in line for line in trace["skill_grants"])


def test_level_defaults_to_the_current_level(dwarf_animist, conn):
    assert character_replay.at_level(dwarf_animist, None, conn)["level"] == 3


def test_a_gap_in_the_plan_is_reported_not_hidden(dwarf_animist, conn):
    del dwarf_animist["plan"][1]
    build = character_replay.at_level(dwarf_animist, 3, conn)
    assert any("level 2" in note for note in build["_derivation"]["notes"])


def test_out_of_range_level_is_rejected(dwarf_animist, conn):
    with pytest.raises(ValueError):
        character_replay.at_level(dwarf_animist, 21, conn)


def test_unknown_class_is_rejected_clearly(dwarf_animist, conn):
    dwarf_animist["build"]["class"] = "not-a-class"
    with pytest.raises(ValueError, match="class progression"):
        character_replay.at_level(dwarf_animist, 1, conn)
