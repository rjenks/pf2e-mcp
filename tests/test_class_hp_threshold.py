"""The <Class> Resiliency feats gate on the *class's* Hit Points per level.

"No more Hit Points per level than 8 + your Constitution modifier" compares
against what the class grants per level, which is itself "N + your Constitution
modifier". The Constitution term is on both sides and cancels, leaving the two
constants: a d8 class (8 + Con) qualifies for the 8 threshold, a d10 class
(10 + Con) does not, at any Constitution score.

Reading it as "8 + this character's Con modifier" inverts the feat, letting a
d10 class in from Con +2 onward. The sibling feats are the proof it cannot mean
that: Barbarian and Guardian Resiliency use a threshold of 10, which is exactly
what admits the d10 classes while still excluding the d12 barbarian. If the
modifier were really added, that variant would admit d12 at Con +2 and the two
thresholds would collapse into each other.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import pf2e_math as m


def _check(con_score: int, class_hp: int, max_hp: int = 8):
    character = {"abilities": {"str": 18, "dex": 14, "con": con_score,
                               "int": 10, "wis": 14, "cha": 10}}
    return m.check_single_prerequisite(
        character, "override",
        {"kind": "class_hp_threshold", "max_hp": max_hp},
        {"class_hp": class_hp},
    )


@pytest.mark.parametrize("con_score", [10, 12, 14, 16, 18, 20])
def test_constitution_never_admits_a_d10_class(con_score):
    """A Ranger grants 10 + Con against a threshold of 8 + Con. No amount of
    Constitution closes a gap that appears on both sides of the comparison."""
    assert _check(con_score, class_hp=10) is False


@pytest.mark.parametrize("con_score", [10, 20])
def test_a_d8_class_qualifies_regardless_of_constitution(con_score):
    assert _check(con_score, class_hp=8) is True


@pytest.mark.parametrize("con_score", [10, 20])
def test_a_d6_class_qualifies_regardless_of_constitution(con_score):
    assert _check(con_score, class_hp=6) is True


def test_the_ten_threshold_admits_d10_but_not_d12():
    """Barbarian and Guardian Resiliency. This pair is what proves the
    Constitution modifier is not added: were it, Con +2 would admit d12 here
    and the 8 and 10 thresholds would stop meaning different things."""
    assert _check(20, class_hp=10, max_hp=10) is True
    assert _check(20, class_hp=12, max_hp=10) is False


def test_unknown_class_hp_stays_unconfirmed():
    """None means "couldn't verify", and must never collapse to False."""
    character = {"abilities": {"con": 16}}
    assert m.check_single_prerequisite(
        character, "override",
        {"kind": "class_hp_threshold", "max_hp": 8},
        {"class_hp": None},
    ) is None
