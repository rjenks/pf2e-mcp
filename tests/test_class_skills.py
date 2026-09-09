"""A class's 1st-level trained skills, corrected against the printed entry.

The upstream Foundry `system.trainedSkills` is not the class entry. It omits
the Ranger's Nature outright, and it cannot express a skill the class defers to
a subclass or deity -- a Cleric's from their deity, a Sorcerer's two from their
bloodline. Both cost a character skills, silently, because every reader in the
codebase agrees on the same wrong baseline.
"""

from __future__ import annotations

from pf2e_mcp.server import class_skills


def test_ranger_gets_nature_back():
    """Player Core: "Trained in Nature / Trained in Survival / ... 4 plus your
    Intelligence modifier". The upstream data carries only Survival."""
    out = class_skills.apply("ranger", {"additional": 4, "fixed": ["survival"]})
    assert sorted(out["fixed"]) == ["nature", "survival"]
    assert out["additional"] == 4


def test_a_correction_never_duplicates_a_skill_already_present():
    """If the upstream data is ever fixed, the correction must go quiet rather
    than train Nature twice."""
    out = class_skills.apply("ranger", {"fixed": ["nature", "survival"]})
    assert sorted(out["fixed"]) == ["nature", "survival"]


def test_case_and_whitespace_do_not_defeat_the_duplicate_guard():
    out = class_skills.apply("Ranger", {"fixed": [" Nature ", "survival"]})
    assert len(out["fixed"]) == 2


def test_subclass_granted_skills_are_surfaced_not_invented():
    """These cannot go in `fixed` -- they are not knowable from the class -- but
    saying nothing leaves a caller counting one skill short."""
    for slug, expected in (("cleric", "deity"), ("druid", "order"),
                           ("sorcerer", "bloodline"), ("rogue", "racket")):
        out = class_skills.apply(slug, {"fixed": ["religion"]})
        assert expected in out["conditional"], (slug, out["conditional"])


def test_a_class_with_nothing_to_correct_is_left_alone():
    out = class_skills.apply("bard", {"additional": 4,
                                      "fixed": ["occultism", "performance"]})
    assert sorted(out["fixed"]) == ["occultism", "performance"]
    assert "conditional" not in out


def test_unaudited_classes_say_so_rather_than_implying_correctness():
    """An absent correction means "not checked", and callers must be able to
    tell that apart from "verified as having none"."""
    assert class_skills.apply("ranger", {"fixed": []})["audited"] is True
    assert class_skills.apply("thaumaturge", {"fixed": []})["audited"] is False


def test_the_input_is_not_mutated():
    original = {"additional": 4, "fixed": ["survival"]}
    class_skills.apply("ranger", original)
    assert original == {"additional": 4, "fixed": ["survival"]}


def test_an_unknown_class_passes_through_unharmed():
    out = class_skills.apply("", {"fixed": ["athletics"]})
    assert out["fixed"] == ["athletics"]
    assert out["audited"] is False


# ------------------------------------------ background-granted feats (#86)

def test_a_backgrounds_granted_feat_is_replayed(conn):
    """Nearly every background hands over a 1st-level skill feat, ingestion
    captures it in `background_boosts.granted_items`, and nothing read that
    column -- so a replayed character was a real feat short of the same
    character exported from Pathbuilder."""
    from pf2e_mcp.server import character_replay
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Granted", "currentLevel": 1},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "ranger",
                  "heritage": None},
        "plan": [{"level": 1}],
    }
    feats = character_replay.at_level(document, 1, conn)["feats"]
    assert ["Student of the Canon", None, "Awarded Feat", 1,
            "Background Feat"] in feats, feats


def test_the_granted_feat_does_not_spend_a_skill_feat_slot(conn):
    """It is a gift, not a slot. Categorising it as a plain Skill Feat would
    let a character look like they had filled a scheduled slot they hadn't."""
    from pf2e_mcp.server import build_tools
    assert build_tools._feat_slot_bucket("Awarded Feat", False) is None


def test_a_background_that_grants_nothing_adds_nothing(conn):
    from pf2e_mcp.server import character_replay
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Bare", "currentLevel": 1},
        "build": {"ancestry": "orc", "background": "abadars-avenger",
                  "class": "ranger", "heritage": None},
        "plan": [{"level": 1}],
    }
    feats = character_replay.at_level(document, 1, conn)["feats"]
    assert not [f for f in feats if f[4] == "Background Feat"], feats
