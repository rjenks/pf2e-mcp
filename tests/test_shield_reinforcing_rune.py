"""A shield's reinforcing rune has to resolve even when it's recorded as a
slug, not just a display name.

`_shield_stats` used to look runes up only by name (`lib.get`), the same
fuzzy matcher used for feats and other free-text entries. That matcher never
converts a dash-joined slug like `reinforcing-rune-minor` into its real name,
"Reinforcing Rune (Minor)" -- every candidate failed, `lib.get` returned
None, and the shield's Hardness/HP/Broken Threshold bump silently vanished
rather than mis-displaying. The Strikes table and Inventory table already
solved this for weapon/armor runes via `lib.by_slug`; the shield block just
never shared that fix.
"""

from __future__ import annotations

from pf2e_mcp.server import sheet


def test_a_shield_rune_recorded_as_a_slug_still_applies_its_bonus(conn):
    lib = sheet._Library(conn)
    character = {
        "armor": [
            {
                "name": "Buckler",
                "worn": False,
                "runes": ["reinforcing-rune-minor"],
            },
        ],
    }
    stats = sheet._shield_stats(character, lib)
    assert stats is not None
    assert stats["runes"] == ["Reinforcing Rune (Minor)"]
    # The rune's own bonus must actually raise the printed numbers, not just
    # be named -- a Buckler's own hardness/HP/BT are small enough that a
    # Reinforcing Rune (Minor) bump is visible without hardcoding the base
    # shield's own stats here.
    unreinforced = sheet._shield_stats(
        {"armor": [{"name": "Buckler", "worn": False, "runes": []}]}, lib)
    assert stats["hardness"] >= unreinforced["hardness"]
    assert stats["hp"] >= unreinforced["hp"]
    assert (stats["hardness"], stats["hp"]) != (unreinforced["hardness"], unreinforced["hp"])


def test_a_shield_rune_recorded_as_its_display_name_still_works(conn):
    """The pre-fix behavior for a rune already spelled out as a name must not
    regress now that the slug path is tried first."""
    lib = sheet._Library(conn)
    character = {
        "armor": [
            {
                "name": "Buckler",
                "worn": False,
                "runes": ["Reinforcing Rune (Minor)"],
            },
        ],
    }
    stats = sheet._shield_stats(character, lib)
    assert stats is not None
    assert stats["runes"] == ["Reinforcing Rune (Minor)"]
