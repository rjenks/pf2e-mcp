"""validate_build should accept what Pathbuilder accepts.

Granted rows, repeatable feats, class-feature prerequisites and compound skill
prerequisites were each reported as errors or unverifiable on a rules-valid
build (#70, #115, #3).
"""

from __future__ import annotations

from pf2e_mcp.server import build_tools, pf2e_math as m


def test_repeatable_feat_is_recognised_from_its_text(conn):
    assert build_tools._is_repeatable_feat(conn, "General Training")
    assert build_tools._is_repeatable_feat(conn, "Advanced Maneuver")
    assert not build_tools._is_repeatable_feat(conn, "Toughness")


def test_granted_rows_are_not_feat_choices():
    assert build_tools._is_granted_row(["Surprise Attack", None, "Awarded Feat", 8, "Granted Feat"])
    assert not build_tools._is_granted_row(["Toughness", None, "General Feat", 3, "General Feat 3"])


def test_prerequisite_naming_a_class_feature_uses_specials():
    character = {"feats": [], "specials": ["Unimpeded Journey"]}
    assert m.check_single_prerequisite(character, "named_reference", {"name": "unimpeded journey"}, {})


def test_skill_rank_any_of_two_skills():
    character = {"proficiencies": {"athletics": 2}}
    spec = {"skill": "acrobatics or athletics", "rank": "trained"}
    assert m.check_single_prerequisite(character, "skill_rank", spec, {}) is True
    assert m.check_single_prerequisite({"proficiencies": {}}, "skill_rank", spec, {}) is False


def test_skill_mastery_prerequisite_needs_two_ranks():
    spec = {"skill": "at least one skill and expert in at least one skill", "rank": "trained"}
    assert m.check_single_prerequisite({"proficiencies": {"stealth": 4, "thievery": 2}}, "skill_rank", spec, {})
    assert not m.check_single_prerequisite({"proficiencies": {"stealth": 2}}, "skill_rank", spec, {})
    assert not m.check_single_prerequisite({"proficiencies": {"stealth": 4}}, "skill_rank", spec, {})
