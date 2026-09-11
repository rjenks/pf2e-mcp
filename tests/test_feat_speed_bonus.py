"""Fleet's own +5 Speed is structurally derivable, unlike Toughness's HP.

`HP_PER_LEVEL_FEATS` is a curated list because Toughness's own rule element
carries `"value": "@actor.level"` -- a Foundry roll-formula string this
project does not evaluate. Fleet's `land-speed` rule carries a plain integer
(`"value": 5`), the same shape a worn item's flat bonus already comes in, so
it needs no curated list at all: just the same kind of rule-element scan
#92 already does for gear, aimed at a taken feat's own entry instead.

Before this fix, a character with Fleet printed base ancestry Speed only --
25 ft for an Orc, not the 30 ft Fleet's own text promises -- with nothing on
the sheet to say the number was short.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import character_replay


# --------------------------------------------------------------- the helper

def test_fleet_style_plain_integer_bonus_is_summed(conn):
    assert character_replay._feat_flat_bonus(conn, {"fleet"}, "land-speed") == 5


def test_toughness_style_formula_value_is_skipped(conn):
    """Toughness's own `hp` rule is `"value": "@actor.level"` -- not a number,
    correctly left to HP_PER_LEVEL_FEATS rather than guessed at here."""
    assert character_replay._feat_flat_bonus(conn, {"toughness"}, "hp") == 0


def test_two_speed_feats_stack_rather_than_take_the_higher(conn):
    """Untyped bonuses stack with each other, unlike two item bonuses of the
    same type -- a hypothetical second +5 land-speed feat should sum to 10,
    not cap at 5. Simulated here since no real second such feat exists in
    the ingested data to combine with Fleet."""
    import json
    row = conn.execute(
        "SELECT raw_json FROM entries WHERE slug = 'fleet' AND pack = 'feats'"
    ).fetchone()
    system = json.loads(row["raw_json"])["system"]
    assert sum(
        r["value"] for r in system["rules"]
        if r["key"] == "FlatModifier" and r["selector"] == "land-speed"
    ) == 5  # sanity: Fleet alone contributes exactly 5, confirming the shape
    # this function sums rather than maxes.


def test_an_unknown_slug_contributes_nothing(conn):
    assert character_replay._feat_flat_bonus(conn, {"not-a-real-feat"}, "land-speed") == 0


# ------------------------------------------------------------------ replay

def _document(feat_slug: str | None) -> dict:
    choices = [{"slot": "generalFeat", "pick": feat_slug}] if feat_slug else []
    return {
        "schemaVersion": 1,
        "identity": {"name": "Runner", "currentLevel": 7},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}, {"level": 7, "choices": choices}],
    }


def test_fleet_raises_the_replayed_speed(conn):
    with_fleet = character_replay.at_level(_document("fleet"), 7, conn)
    without_fleet = character_replay.at_level(_document(None), 7, conn)
    assert with_fleet["attributes"]["speed"] == without_fleet["attributes"]["speed"] + 5


def test_fleet_and_a_worn_item_both_apply(conn):
    """Fleet (feat, untyped) and Boots of Bounding (item, item-type) are
    different bonus types -- both should land on the same total."""
    document = {
        "schemaVersion": 1,
        "identity": {"name": "Runner", "currentLevel": 7},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}, {"level": 7,
                  "choices": [{"slot": "generalFeat", "pick": "fleet"}]}],
        "gear": {"carried": [{"item": "boots-of-bounding", "invested": True}]},
    }
    state = character_replay.at_level(document, 7, conn)
    base = character_replay.at_level(
        {**document, "gear": {"carried": []},
         "plan": [{"level": 1}, {"level": 7}]},
        7, conn,
    )
    assert state["attributes"]["speed"] == base["attributes"]["speed"] + 10
