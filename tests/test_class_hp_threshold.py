"""The <Class> Resiliency feats gate on "no more Hit Points per level than
8 + your Constitution modifier". The ingestion override stores only the
constant (`max_hp`), and the evaluator was comparing against it alone --
dropping the Constitution term and, with it, the whole point of the
prerequisite.

Seven feats carry one of these: Fighter, Ranger, Monk, Champion and Exemplar
Resiliency at 8, Barbarian and Guardian Resiliency at 10. All of them are
written to let a *martial* multiclass take them, so excluding every d10 class
inverted the intent.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import pf2e_math as m


def _char(con_score: int) -> dict:
    return {"abilities": {"str": 18, "dex": 14, "con": con_score,
                          "int": 10, "wis": 14, "cha": 10}}


def _check(con_score: int, class_hp: int, max_hp: int = 8):
    return m.check_single_prerequisite(
        _char(con_score),
        "override",
        {"kind": "class_hp_threshold", "max_hp": max_hp},
        {"class_hp": class_hp},
    )


@pytest.mark.parametrize("con_score,expected", [
    (10, False),  # Con +0 -> threshold 8, a d10 class is over it
    (12, False),  # Con +1 -> threshold 9
    (14, True),   # Con +2 -> threshold 10, exactly met
    (16, True),   # Con +3 -> threshold 11
    (20, True),   # Con +5 -> threshold 13
])
def test_d10_class_qualifies_once_constitution_is_high_enough(con_score, expected):
    """A Ranger grants 10 HP per level. Whether that clears 8 + Con is the
    entire question the prerequisite asks."""
    assert _check(con_score, class_hp=10) is expected


def test_d8_class_qualifies_even_at_con_zero():
    assert _check(10, class_hp=8) is True


def test_d12_class_needs_more_constitution():
    """Barbarian grants 12; against the 8 + Con form it needs Con +4."""
    assert _check(16, class_hp=12) is False   # Con +3 -> 11
    assert _check(18, class_hp=12) is True    # Con +4 -> 12


def test_the_ten_plus_con_variant_scales_too():
    """Barbarian and Guardian Resiliency use max_hp 10."""
    assert _check(10, class_hp=12, max_hp=10) is False  # Con +0 -> 10
    assert _check(14, class_hp=12, max_hp=10) is True   # Con +2 -> 12


def test_unknown_class_hp_stays_unconfirmed():
    """None means "couldn't verify", and must never collapse to False."""
    assert m.check_single_prerequisite(
        _char(16), "override",
        {"kind": "class_hp_threshold", "max_hp": 8},
        {"class_hp": None},
    ) is None
