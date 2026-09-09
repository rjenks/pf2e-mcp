"""The purse against the chronicle ledger.

An Organized Play character's money has exactly one source of truth: the
chronicle log, where every award and every purchase is recorded and the running
balance is derived. `gear.currency` is a convenience copy of that balance with
no independent source, so the only way it can be wrong is by drifting -- which
it does silently, because nothing read the two together. One real character had
28 gp in the purse against a ledger ending at 97.92.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import character


@pytest.fixture
def agent() -> dict:
    """A Society character with one chronicle and a purse that agrees with it."""
    return {
        "schemaVersion": 1,
        "identity": {"name": "Test Subject", "currentLevel": 3},
        "build": {
            "ancestry": "human",
            "background": "farmhand",
            "class": "ranger",
        },
        "plan": [{"level": 1}],
        "gear": {"currency": {"gp": 70, "sp": 2}},
        "organizedPlay": {
            "startingLevel": 1,
            "chronicles": [{
                "adventure": "1-01",
                "characterLevel": 3,
                "xp": {"start": 0, "gained": 4, "end": 4},
                "currency": {"start": 30.0, "gained": 40.2, "spent": 0, "end": 70.2},
            }],
        },
    }


def _codes(document: dict) -> list[str]:
    return [i["code"] for i in character.validate_document(document, None)["issues"]]


def test_a_purse_matching_the_ledger_is_quiet(agent):
    assert "currency_disagrees_with_ledger" not in _codes(agent)


def test_drift_is_reported(agent):
    agent["gear"]["currency"] = {"gp": 28, "sp": 8}
    assert "currency_disagrees_with_ledger" in _codes(agent)


def test_the_message_names_both_figures_and_which_wins(agent):
    agent["gear"]["currency"] = {"gp": 28, "sp": 8}
    issue = next(
        i for i in character.validate_document(agent, None)["issues"]
        if i["code"] == "currency_disagrees_with_ledger"
    )
    assert "28 gp, 8 sp" in issue["message"]
    assert "70 gp, 2 sp" in issue["message"]
    assert "ledger is the authority" in issue["message"]
    assert issue["path"] == "/gear/currency"


def test_copper_precision_is_respected(agent):
    """Chronicle awards land on fractional gold; the purse has to match exactly."""
    agent["organizedPlay"]["chronicles"][0]["currency"]["end"] = 97.92
    agent["gear"]["currency"] = {"gp": 97, "sp": 9, "cp": 2}
    assert "currency_disagrees_with_ledger" not in _codes(agent)
    agent["gear"]["currency"] = {"gp": 97, "sp": 9, "cp": 3}
    assert "currency_disagrees_with_ledger" in _codes(agent)


def test_platinum_counts_toward_the_purse(agent):
    agent["gear"]["currency"] = {"pp": 7, "sp": 2}
    assert "currency_disagrees_with_ledger" not in _codes(agent)


def test_drift_is_a_warning_not_an_error(agent):
    """One of the two numbers is stale, which is worth saying and not worth
    refusing to load the file over."""
    agent["gear"]["currency"] = {"gp": 1}
    assert character.validate_document(agent, None)["valid"] is True


def test_no_chronicles_means_nothing_to_check(agent):
    agent["organizedPlay"]["chronicles"] = []
    assert "currency_disagrees_with_ledger" not in _codes(agent)


def test_a_character_with_no_purse_is_not_nagged(agent):
    """Absent is not the same as wrong -- a file may simply not track coins."""
    del agent["gear"]["currency"]
    assert "currency_disagrees_with_ledger" not in _codes(agent)


def test_a_non_society_character_is_unaffected(agent):
    del agent["organizedPlay"]
    agent["gear"]["currency"] = {"gp": 1}
    assert "currency_disagrees_with_ledger" not in _codes(agent)
