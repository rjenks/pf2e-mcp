"""Fetching a build by Pathbuilder export id.

The endpoint is behind Cloudflare bot management, so the three ways this fails
-- a bot challenge, a stale id, a dead connection -- look alike from outside
and are easy to confuse for each other. Each gets its own test, because a
caller told "that id is wrong" when the real answer was "Cloudflare blocked
us" will go and generate a fresh export for nothing.

No test here touches the network: httpx is stubbed, so the suite stays offline.
"""

from __future__ import annotations

import httpx
import pytest

from pf2e_mcp.server import character_tools


@pytest.fixture
def responses(monkeypatch):
    """Capture the outgoing request and serve a canned reply."""
    sent: dict = {}

    def install(response=None, error=None):
        def fake_get(url, **kwargs):
            sent["url"] = url
            sent["params"] = kwargs.get("params")
            sent["headers"] = kwargs.get("headers")
            if error is not None:
                raise error
            return response

        monkeypatch.setattr(character_tools.httpx, "get", fake_get)
        return sent

    return install


def _response(status=200, json_body=None, text=None):
    return httpx.Response(
        status_code=status,
        json=json_body if text is None else None,
        text=text,
        request=httpx.Request("GET", character_tools._PATHBUILDER_URL),
    )


def test_successful_fetch_returns_the_envelope_and_stamps_the_id(responses):
    sent = responses(_response(json_body={"success": True, "build": {"name": "Someone"}}))
    result = character_tools.fetch_pathbuilder(459493)
    assert result["success"] is True
    assert result["build"]["name"] == "Someone"
    # Pathbuilder never says which id served the payload, so import can't
    # record it unless the fetch stamps it.
    assert result["_pathbuilderId"] == 459493
    assert sent["params"] == {"id": 459493}


def test_request_looks_like_a_browser(responses):
    """A default httpx User-Agent draws the managed challenge; Foundry's
    ordinary browser fetch does not."""
    sent = responses(_response(json_body={"success": True, "build": {}}))
    character_tools.fetch_pathbuilder(1)
    assert "Mozilla/5.0" in sent["headers"]["User-Agent"]
    assert "httpx" not in sent["headers"]["User-Agent"]


def test_a_string_id_is_accepted(responses):
    responses(_response(json_body={"success": True, "build": {}}))
    assert "error" not in character_tools.fetch_pathbuilder(" 459493 ")


def test_a_non_numeric_id_is_rejected_before_any_request(responses):
    responses(_response(json_body={"success": True, "build": {}}))
    assert "must be a number" in character_tools.fetch_pathbuilder("not-an-id")["error"]


def test_html_is_reported_as_a_bot_challenge_not_a_bad_id(responses):
    responses(_response(text="<!DOCTYPE html><html><title>Just a moment...</title>"))
    error = character_tools.fetch_pathbuilder(459493)["error"]
    assert "Cloudflare" in error
    assert "stale" not in error


def test_success_false_is_reported_as_a_stale_id(responses):
    responses(_response(json_body={"success": False}))
    error = character_tools.fetch_pathbuilder(459493)["error"]
    assert "stale" in error
    assert "Cloudflare" not in error


def test_missing_build_key_is_treated_as_no_export(responses):
    responses(_response(json_body={"success": True}))
    assert "no export" in character_tools.fetch_pathbuilder(459493)["error"]


def test_403_names_cloudflare_as_the_likely_cause(responses):
    responses(_response(status=403, text="forbidden"))
    error = character_tools.fetch_pathbuilder(459493)["error"]
    assert "403" in error and "Cloudflare" in error


def test_transport_failure_is_returned_not_raised(responses):
    responses(error=httpx.ConnectError("no route to host"))
    assert "Could not reach" in character_tools.fetch_pathbuilder(459493)["error"]


def test_import_records_the_stamped_id(conn):
    """The stamp is only useful if import carries it into the document."""
    from pf2e_mcp.server import character_import

    export = {
        "success": True,
        "_pathbuilderId": 459493,
        "build": {
            "name": "Someone",
            "level": 1,
            "class": "Ranger",
            "ancestry": "Human",
            "background": "Farmhand",
            "abilities": {},
            "feats": [],
        },
    }
    document = character_import.from_pathbuilder(export, conn)
    assert document["identity"]["pathbuilderId"] == 459493


def test_import_without_a_stamp_omits_the_field(conn):
    """A pasted export has no id, and inventing one would be worse than none."""
    from pf2e_mcp.server import character_import

    export = {
        "success": True,
        "build": {
            "name": "Someone",
            "level": 1,
            "class": "Ranger",
            "ancestry": "Human",
            "background": "Farmhand",
            "abilities": {},
            "feats": [],
        },
    }
    document = character_import.from_pathbuilder(export, conn)
    assert "pathbuilderId" not in document["identity"]
