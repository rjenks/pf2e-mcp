"""Skill training handed out by a feat's own text (archetype dedications).

The rules data carries "you become trained in Stealth or Thievery plus one
skill of your choice" as prose, so these are read from the description. The
parser tests use literal text and need no database; the replay and import
tests do.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import character_import, character_replay, feat_skill_grants as fsg


def grants(text: str) -> list[tuple]:
    return [(g["kind"], g["options"], g["if_trained"]) for g in fsg.parse(f"<p>{text}</p>")["grants"]]


def test_choice_plus_free_with_fallback():
    # Rogue Dedication
    text = (
        "You become trained in Stealth or Thievery plus one skill of your choice; "
        "if you are already trained in both Stealth and Thievery, you become "
        "trained in an additional skill of your choice."
    )
    assert grants(text) == [
        ("choice", ["stealth", "thievery"], "free"),
        ("free", None, None),
    ]


def test_choice_between_two_skills():
    text = (
        "You become trained in your choice of Acrobatics or Athletics; if you are "
        "already trained in both of these skills, you instead become trained in a "
        "skill of your choice."
    )
    assert grants(text) == [("choice", ["acrobatics", "athletics"], "free")]


def test_fixed_skill_with_expert_fallback():
    # Dandy Dedication
    text = (
        "You become trained in Deception and Society; if you were already trained "
        "in either, you become an expert in it instead."
    )
    assert grants(text) == [
        ("fixed", ["deception"], "expert"),
        ("fixed", ["society"], "expert"),
    ]


def test_fixed_skill_with_free_fallback():
    # Wizard Dedication
    text = (
        "You become trained in Arcana; if you were already trained in Arcana, you "
        "instead become trained in a skill of your choice."
    )
    assert grants(text) == [("fixed", ["arcana"], "free")]


def test_comma_separated_fallback_is_not_a_second_grant():
    # Battle Harbinger Dedication
    text = (
        "You become trained in your choice of Athletics or Acrobatics, if you are "
        "already trained in both skills, you instead become trained in another "
        "skill of your choice."
    )
    assert grants(text) == [("choice", ["athletics", "acrobatics"], "free")]


def test_lore_grants_are_not_skills():
    text = (
        "You gain the Additional Lore general feat for Warfare Lore. If you were "
        "already trained in Warfare Lore, you also become trained in a Lore skill "
        "of your choice."
    )
    assert grants(text) == []


def test_subclass_dependent_skill_is_unresolved():
    text = "You become trained in your deity's associated skill."
    parsed = fsg.parse(f"<p>{text}</p>")
    assert parsed["grants"] == []
    assert parsed["unresolved"] == 1


def test_markup_is_stripped():
    text = "You become trained in @UUID[Compendium.pf2e.skills.Item.x]{Arcana}."
    assert grants(text) == [("fixed", ["arcana"], None)]


def _document(plan: list[dict], cls: str = "ranger") -> dict:
    return {
        "schemaVersion": 1,
        "identity": {"name": "Test", "currentLevel": 8},
        "build": {"ancestry": "human", "heritage": "skilled-human",
                  "background": "warrior", "class": cls},
        "plan": plan,
    }


@pytest.fixture
def ranks(conn):
    def run(plan):
        return character_replay.at_level(_document(plan), 8, conn)["proficiencies"]
    return run


def test_fixed_dedication_grant_applies_without_a_recorded_pick(ranks):
    before = ranks([{"level": 2, "choices": []}])
    after = ranks([{"level": 2, "choices": [{"slot": "classFeat", "pick": "wizard-dedication"}]}])
    assert before["arcana"] == 0
    assert after["arcana"] == 2


def test_fixed_grant_makes_a_known_skill_expert_when_the_feat_says_so(ranks):
    # Warrior trains Intimidation; Hellknight Dedication raises it instead.
    plan = [{"level": 2, "choices": [{"slot": "classFeat", "pick": "hellknight-dedication"}]}]
    assert ranks(plan)["intimidation"] == 4


def test_choice_grant_waits_for_the_plan(ranks):
    plan = [{"level": 8, "choices": [{"slot": "classFeat", "pick": "rogue-dedication"}]}]
    assert ranks(plan)["stealth"] == 0
    plan[0]["choices"].append(
        {"slot": "skillTraining", "pick": ["stealth", "thievery"], "grantedBy": "rogue-dedication"}
    )
    out = ranks(plan)
    assert out["stealth"] == out["thievery"] == 2


def test_import_infers_dedication_picks_from_final_ranks(conn):
    export = {"success": True, "build": {
        "name": "Test", "class": "Ranger", "level": 8, "ancestry": "Human",
        "heritage": "Skilled Human", "background": "Warrior",
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 14, "cha": 10,
                      "breakdown": {}},
        "proficiencies": {"stealth": 2, "thievery": 2, "acrobatics": 0, "athletics": 0},
        "feats": [["Rogue Dedication", None, "Archetype Feat", 8, "Free Archetype 8", "parentChoice", None]],
    }}
    doc = character_import.from_pathbuilder(export, conn)
    grants_ = [
        c for e in doc["plan"] for c in e.get("choices", [])
        if c["slot"] == "skillTraining" and c.get("grantedBy") == "rogue-dedication"
    ]
    assert len(grants_) == 1
    assert sorted(grants_[0]["pick"]) == ["stealth", "thievery"]
    assert "stealth" not in doc.get("proficiencyOverrides", {})
    assert "thievery" not in doc.get("proficiencyOverrides", {})


def test_canny_acumen_reaches_master_at_17_even_for_a_class_already_expert(conn):
    plan = [{"level": 13, "choices": [{"slot": "generalFeat", "pick": "canny-acumen", "note": "Will"}]}]
    doc = _document(plan)
    assert character_replay.at_level(doc, 16, conn)["proficiencies"]["will"] >= 4
    assert character_replay.at_level(doc, 17, conn)["proficiencies"]["will"] == 6


from pf2e_mcp.server import skill_reconstruction as sr


def test_feat_increase_steps_read_skill_mastery_once():
    text = (
        "Rogue: Increase your proficiency rank in one of your skills from expert to "
        "master and in another of your skills from trained to expert. Investigator: "
        "Increase your proficiency rank in one of your skills from expert to master "
        "and in another of your skills from trained to expert."
    )
    assert sr.feat_increase_steps(text) == [3, 2]


def test_heritage_training_with_later_expert():
    text = (
        "You become trained in one skill of your choice. At 5th level, you become an "
        "expert in the chosen skill."
    )
    assert sr.heritage_training(text) == (True, 5)


def test_solver_respects_rank_gates_and_chains():
    # Legendary needs 15th level, and every step needs the one before it.
    final = {"athletics": 4}
    inc = [sr.Slot(lv) for lv in (3, 5, 7, 9, 11, 13, 15, 17, 19)]
    training, increases, short = sr.solve(final, {"athletics": 1}, {"athletics": 1}, [], inc)
    assert not short
    levels = sorted(a.slot.level for a in increases)
    assert len(levels) == 3 and levels[0] >= 3 and levels[1] >= 7 and levels[2] >= 15


def test_solver_leaves_unpayable_ranks_as_shortfall():
    # Nothing to buy legendary with before 15th level.
    _, _, short = sr.solve({"athletics": 4}, {"athletics": 1}, {"athletics": 1}, [], [sr.Slot(3), sr.Slot(5), sr.Slot(7)])
    assert short == {"athletics": 1}


def test_solver_picks_the_heritage_skill_that_lets_everything_fit():
    # Two skills each need expert at 14 (pinned) and master later; the heritage's
    # chosen skill is expert at 5, so it must be the one that needs two steps.
    heritage = sr.Slot(1, "skilled-human")
    linked = sr.Slot(5, "skilled-human", pin_target=2, link=heritage)
    inc = [linked, sr.Slot(14, "skill-mastery", pin_target=2), sr.Slot(15), sr.Slot(17)]
    final = {"acrobatics": 3, "diplomacy": 2}
    training, increases, short = sr.solve(final, {}, {}, [heritage, sr.Slot(1)], inc)
    assert not short
