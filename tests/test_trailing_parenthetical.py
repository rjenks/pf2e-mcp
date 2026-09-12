"""`strip_trailing_parenthetical` is shared between `pf2e_math.has_feat` and
`build_tools.validate_build`'s prerequisite check -- both need "the recorded
name without its choice" the same way, and used to keep two copies of the
same regex (see #87's sibling finding on duplication)."""

from __future__ import annotations

from pf2e_mcp.server import pf2e_math as m


def test_strips_a_trailing_parenthetical():
    assert m.strip_trailing_parenthetical("Advanced Maneuver (Combat Grab)") == "Advanced Maneuver"


def test_returns_none_with_nothing_to_strip():
    assert m.strip_trailing_parenthetical("Toughness") is None


def test_a_parenthetical_that_is_part_of_the_name_still_strips_here():
    """This function does the mechanical strip only -- the "try the whole
    name first" ordering that keeps "Tusks (Orc)" from being treated as
    "Tusks" lives in the caller (`has_feat`), not here."""
    assert m.strip_trailing_parenthetical("Tusks (Orc)") == "Tusks"


def test_blank_input_does_not_raise():
    assert m.strip_trailing_parenthetical("") is None
    assert m.strip_trailing_parenthetical(None) is None
