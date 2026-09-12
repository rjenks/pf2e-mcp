"""`variant_rules` has to accept two legitimate spellings.

The native character schema's `build.variantRules` enum is camelCase
('freeArchetype', 'ancestryParagon') -- the character-builder skill instructs
passing that field straight through to `validate_build`/`get_level_up_choices`.
The rules database's own slugs, what `rules_list_variant_rules` returns, are
kebab-case ('free-archetype', 'ancestry-paragon'). Before the fix, both
functions compared the raw string against kebab-case literals only, so a
caller following the documented flow (pass `build.variantRules` straight
through) silently got no Free Archetype/Ancestry Paragon effect at all.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import build_tools


@pytest.mark.parametrize("value, expected", [
    ("free-archetype", "free-archetype"),
    ("freeArchetype", "free-archetype"),
    ("ancestry-paragon", "ancestry-paragon"),
    ("ancestryParagon", "ancestry-paragon"),
    ("ANCESTRY-PARAGON", "ancestry-paragon"),
])
def test_normalize_variant_rule_folds_both_spellings(value, expected):
    assert build_tools._normalize_variant_rule(value) == expected


def test_variant_rule_set_recognizes_camel_case():
    normalized = build_tools._variant_rule_set(["freeArchetype", "ancestryParagon"])
    assert normalized == {"free-archetype", "ancestry-paragon"}


def test_get_level_up_choices_treats_camel_case_the_same_as_kebab_case(conn):
    """`conn` is unused directly -- requesting it is how this test skips
    cleanly when the ingested database isn't present, same as every other
    DB-backed test. `get_level_up_choices` opens its own connection."""
    character = {"class": "Fighter", "level": 1}

    kebab = build_tools.get_level_up_choices(
        character, target_level=2, variant_rules=["free-archetype"])
    camel = build_tools.get_level_up_choices(
        character, target_level=2, variant_rules=["freeArchetype"])

    assert kebab["unlocks"]["archetype_feat"] is True
    assert camel["unlocks"]["archetype_feat"] is True
    assert not any("Unrecognized" in n for n in camel.get("notes", []))


def test_validate_build_ancestry_paragon_budget_is_camel_case_aware(conn):
    """A character with 2 ancestry feats at level 1 is legal under Ancestry
    Paragon and should not be flagged over-budget regardless of which
    spelling requested it."""
    character = {
        "class": "Fighter",
        "level": 1,
        "feats": [
            ["Some Ancestry Feat", None, "Ancestry Feat", 1],
            ["Another Ancestry Feat", None, "Ancestry Feat", 1],
        ],
    }
    result = build_tools.validate_build(character, variant_rules=["ancestryParagon"])
    assert not any("ancestry-category" in e for e in result["errors"])
