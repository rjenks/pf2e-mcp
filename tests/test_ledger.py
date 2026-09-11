"""The gold journal: starting funds and every buy, sell, treasure or gift
since, with `gear.currency` checked against what the history actually sums
to rather than trusted as an independent, hand-edited number.

Modelled on the PFS chronicle log's own gold journal (`chronicle.to_cp`,
`_check_organized_play`'s currency check) -- the same shape of problem for a
character with no session-by-session XP/Reputation chain to validate
against, so one flat, signed-amount entry per event rather than a full
chronicle's gained/spent split.
"""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import character as ch
from pf2e_mcp.server import character_replay, sheet


def _document(ledger: list[dict], currency: dict | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "identity": {"name": "Ledger Tester", "currentLevel": 5},
        "build": {"ancestry": "orc", "background": "acolyte", "class": "fighter",
                  "heritage": None},
        "plan": [{"level": 1}],
        "gear": {"currency": currency or {"gp": 0, "sp": 0}},
        "ledger": ledger,
    }


# ------------------------------------------------------------- validation

def test_a_ledger_summing_to_the_purse_is_clean(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 1, "kind": "purchase", "gp": -10, "note": "Bought a sword."},
        ],
        currency={"gp": 5, "sp": 0},
    )
    result = ch.validate_document(document, conn)
    mismatches = [i for i in result["issues"] if i["code"] == "currency_disagrees_with_ledger"]
    assert not mismatches, result["issues"]


def test_a_purse_that_does_not_match_the_ledger_is_flagged(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 1, "kind": "purchase", "gp": -10, "note": "Bought a sword."},
        ],
        currency={"gp": 999, "sp": 0},  # deliberately wrong
    )
    result = ch.validate_document(document, conn)
    mismatches = [i for i in result["issues"] if i["code"] == "currency_disagrees_with_ledger"]
    assert len(mismatches) == 1
    assert "5 gp" in mismatches[0]["message"]  # the correct ledger total
    assert "999 gp" in mismatches[0]["message"]  # the wrong purse


def test_fractional_gold_reconciles_exactly_in_copper(conn):
    """1 sp is 0.1 gp -- exercised because float drift across many small
    entries is exactly the bug a copper-based check exists to catch."""
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 1, "kind": "purchase", "gp": -0.3, "note": "Torch."},
            {"level": 1, "kind": "purchase", "gp": -0.1, "note": "Chalk."},
        ],
        currency={"gp": 14, "sp": 6},
    )
    result = ch.validate_document(document, conn)
    mismatches = [i for i in result["issues"] if i["code"] == "currency_disagrees_with_ledger"]
    assert not mismatches, result["issues"]


def test_missing_starting_entry_is_flagged(conn):
    document = _document(
        [{"level": 3, "kind": "purchase", "gp": -10, "note": "Bought a sword."}],
        currency={"gp": 0},
    )
    result = ch.validate_document(document, conn)
    codes = [i["code"] for i in result["issues"]]
    assert "ledger_missing_start" in codes


def test_more_than_one_starting_entry_is_flagged(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 1, "kind": "starting", "gp": 15, "note": "A duplicate."},
        ],
        currency={"gp": 30},
    )
    result = ch.validate_document(document, conn)
    codes = [i["code"] for i in result["issues"]]
    assert "duplicate_starting_entry" in codes


def test_out_of_order_levels_are_flagged(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 5, "kind": "purchase", "gp": -5, "note": "Later purchase."},
            {"level": 3, "kind": "purchase", "gp": -5, "note": "Out of order."},
        ],
        currency={"gp": 5},
    )
    result = ch.validate_document(document, conn)
    codes = [i["code"] for i in result["issues"]]
    assert "ledger_order" in codes


def test_an_unresolvable_item_slug_is_flagged_as_a_warning(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 1, "kind": "purchase", "gp": -10, "item": "not-a-real-slug",
             "note": "Typo'd slug."},
        ],
        currency={"gp": 5},
    )
    result = ch.validate_document(document, conn)
    warning = next(i for i in result["issues"] if i["code"] == "unknown_slug"
                    and i["path"].startswith("/ledger"))
    assert warning["level"] == "warning"


def test_a_character_with_no_ledger_at_all_is_unaffected(conn):
    """The feature is additive -- nothing about it should break a character
    file written before it existed."""
    document = _document([], currency={"gp": 100})
    document.pop("ledger")
    result = ch.validate_document(document, conn)
    assert result["valid"]
    assert not any("ledger" in i["path"] for i in result["issues"])


# --------------------------------------------------------------- replay

def test_ledger_survives_the_replay(conn):
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 5, "kind": "purchase", "gp": -10, "note": "Later purchase."},
        ],
        currency={"gp": 5},
    )
    state = character_replay.at_level(document, 5, conn)
    assert len(state["ledger"]) == 2


def test_ledger_is_filtered_to_the_replayed_level(conn):
    """A sheet for an earlier level shows only the history up to that level --
    the same rule `plan` itself follows."""
    document = _document(
        [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 5, "kind": "purchase", "gp": -10, "note": "Later purchase."},
        ],
        currency={"gp": 5},
    )
    state = character_replay.at_level(document, 3, conn)
    assert len(state["ledger"]) == 1
    assert state["ledger"][0]["kind"] == "starting"


# ----------------------------------------------------------------- sheet

def _ledger_rows(page: str) -> list[list[str]]:
    body = re.search(r'data-sec="ledger"(.*?)</section>', page, re.S)
    if not body:
        return []
    import html
    rows = []
    for row in re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S):
        cells = [html.unescape(re.sub("<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        if cells:
            rows.append(cells)
    return rows


@pytest.fixture
def ledger_character() -> dict:
    return {
        "name": "Ledger Tester", "class": "Fighter", "ancestry": "Human",
        "level": 5,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {}, "feats": [],
        "ledger": [
            {"level": 1, "kind": "starting", "gp": 15, "note": "Starting funds."},
            {"level": 3, "kind": "purchase", "gp": -0.3, "item": "torch",
             "quantity": 5, "note": "For the dungeon."},
            {"level": 5, "kind": "treasure", "gp": 50, "note": "A chest of coin."},
        ],
    }


def test_the_ledger_page_renders_a_row_per_entry(ledger_character, conn, tmp_path):
    out = tmp_path / "ledger.html"
    sheet.render_character_sheet(ledger_character, str(out), level=5)
    rows = _ledger_rows(out.read_text(encoding="utf-8"))
    # Header row plus three entries.
    assert len(rows) == 4


def test_the_running_balance_accumulates_correctly(ledger_character, conn, tmp_path):
    out = tmp_path / "balance.html"
    sheet.render_character_sheet(ledger_character, str(out), level=5)
    rows = _ledger_rows(out.read_text(encoding="utf-8"))
    # Balance column is index 4: 15 -> 14.7 -> 64.7
    assert rows[1][4] == "15 gp"
    assert rows[2][4] == "14 gp 7 sp"
    assert rows[3][4] == "64 gp 7 sp"


def test_a_negative_amount_prints_with_a_minus_sign(ledger_character, conn, tmp_path):
    out = tmp_path / "signed.html"
    sheet.render_character_sheet(ledger_character, str(out), level=5)
    rows = _ledger_rows(out.read_text(encoding="utf-8"))
    assert rows[2][3] == "-3 sp"


def test_a_positive_amount_prints_with_a_plus_sign(ledger_character, conn, tmp_path):
    out = tmp_path / "plus.html"
    sheet.render_character_sheet(ledger_character, str(out), level=5)
    rows = _ledger_rows(out.read_text(encoding="utf-8"))
    assert rows[1][3] == "+15 gp"


def test_the_item_slug_resolves_to_its_real_name(ledger_character, conn, tmp_path):
    out = tmp_path / "resolved.html"
    sheet.render_character_sheet(ledger_character, str(out), level=5)
    rows = _ledger_rows(out.read_text(encoding="utf-8"))
    assert "Torch" in rows[2][2]


def test_no_ledger_means_no_page_at_all(conn, tmp_path):
    character = {
        "name": "Plain", "class": "Fighter", "ancestry": "Human", "level": 5,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {}, "feats": [],
    }
    out = tmp_path / "no_ledger.html"
    summary = sheet.render_character_sheet(character, str(out), level=5)
    assert "ledger" not in summary["sections"]
    assert 'data-sec="ledger"' not in out.read_text(encoding="utf-8")
