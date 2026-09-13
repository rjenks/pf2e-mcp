"""Tests for structured quick-reference / tactical cards and token interpolation."""

from __future__ import annotations

import copy
import pytest

from pf2e_mcp.server import character, sheet


@pytest.fixture
def native_doc() -> dict:
    return {
        "schemaVersion": 1,
        "identity": {
            "name": "Valeros",
            "currentLevel": 5,
        },
        "build": {
            "ancestry": "human",
            "background": "guard",
            "class": "fighter",
            "keyAttribute": "str",
            "languages": ["common"],
        },
        "plan": [
            {
                "level": 1,
                "attributeBoosts": {
                    "ancestry": {"free": ["str", "dex"]},
                    "background": ["str", "con"],
                    "class": ["str"],
                    "free": ["str", "dex", "con", "wis"],
                },
                "choices": [
                    {"slot": "classFeat", "pick": "sudden-charge"},
                    {"slot": "ancestryFeat", "pick": "natural-ambition"},
                    {"slot": "skillTraining", "pick": "athletics"},
                    {"slot": "skillTraining", "pick": "intimidation"},
                    {"slot": "skillTraining", "pick": "warfare"},
                ],
            },
            {
                "level": 2,
                "choices": [
                    {"slot": "classFeat", "pick": "combat-grab"},
                    {"slot": "skillFeat", "pick": "intimidating-glare"},
                ],
            },
            {
                "level": 3,
                "choices": [
                    {"slot": "generalFeat", "pick": "fleet"},
                    {"slot": "skillIncrease", "pick": "athletics"},
                ],
            },
            {
                "level": 4,
                "choices": [
                    {"slot": "classFeat", "pick": "slam-down"},
                    {"slot": "skillFeat", "pick": "titan-wrestler"},
                ],
            },
            {
                "level": 5,
                "attributeBoosts": {
                    "free": ["str", "dex", "con", "wis"],
                },
                "choices": [
                    {"slot": "ancestryFeat", "pick": "clever-improviser"},
                    {"slot": "skillIncrease", "pick": "athletics"},
                ],
            },
            {
                "level": 6,
                "choices": [
                    {"slot": "classFeat", "pick": "dazing-blow"},
                    {"slot": "skillFeat", "pick": "battle-cry"},
                ],
            },
        ],
        "quickReference": {
            "title": "Tactical Summary",
            "eyebrow": "Corner Card",
            "subtitle": "Valeros &middot; Fighter 5",
            "lede": "Combat flow reminders.",
            "legend": [
                {"label": "Flourish", "note": "Only one flourish per turn."},
                {"label": "Press", "note": "Only right after a Strike."},
            ],
            "tables": [
                {
                    "title": "Critical Specialization",
                    "headers": ["Weapon", "Effect"],
                    "rows": [
                        {
                            "label": "Longsword",
                            "sub": "main hand",
                            "detail": "Target is off-guard until start of next turn.",
                        },
                        {
                            "label": "Shield Boss",
                            "sub": "off hand",
                            "detail": "Target makes Fort DC {dc:class} or pushed 5 ft.",
                        },
                    ],
                }
            ],
            "groups": [
                {
                    "title": "Openers",
                    "category": "open",
                    "badge": "Open the Round",
                    "when": "Setup before striking",
                    "moves": [
                        {
                            "name": "Demoralize",
                            "cost": 1,
                            "text": "Frighten one observed enemy.",
                            "chip": "{intimidation} to hit",
                            "chipTone": "ruby",
                        },
                        {
                            "name": "Sudden Charge",
                            "cost": 2,
                            "traits": ["flourish", "open"],
                            "text": "Stride twice and Strike. Mod: {str}.",
                            "chip": "Str {modifier:str}",
                            "chipTone": "gold",
                        },
                    ],
                },
                {
                    "title": "Lockdown",
                    "category": "press",
                    "badge": "Follow a Hit",
                    "when": "Must chain off a Strike",
                    "moves": [
                        {
                            "name": "Combat Grab",
                            "cost": 1,
                            "traits": ["press"],
                            "text": "Strike with free hand; grab on hit. Athletics DC {dc:athletics}.",
                            "chip": "Athletics {athletics}",
                            "chipTone": "jade",
                        }
                    ],
                },
            ],
            "footer": "Core maneuvers (Trip, Shove, Grapple) on page 1.",
        },
    }


def test_quick_reference_validation_valid(native_doc, conn):
    report = character.validate_document(native_doc, conn)
    assert report["valid"] is True
    assert report["errors"] == 0


def test_quick_reference_validation_invalid_token(native_doc, conn):
    doc = copy.deepcopy(native_doc)
    doc["quickReference"]["groups"][0]["moves"][0]["chip"] = "{bogus_token} vs Will"
    report = character.validate_document(doc, conn)
    assert report["valid"] is False
    assert any(
        i["code"] == "invalid_quickref_token" and "bogus_token" in i["message"] for i in report["issues"]
    )


def test_quick_reference_rendering_at_current_level(native_doc, conn, tmp_path):
    out = tmp_path / "sheet.html"
    summary = sheet.render_character_sheet(native_doc, str(out))
    assert "quick-reference" in summary["sections"]

    content = out.read_text(encoding="utf-8")
    assert 'data-sec="quick-reference"' in content
    assert "Tactical Summary" in content
    assert "Critical Specialization" in content
    assert "Demoralize" in content
    assert "Sudden Charge" in content
    assert "Combat Grab" in content

    # Check that tokens were interpolated into numbers rather than left as {token}
    assert "{dc:class}" not in content
    assert "{athletics}" not in content
    assert "{intimidation}" not in content
    assert "{str}" not in content
    assert "Athletics +" in content


def test_quick_reference_omitted_at_different_level(native_doc, conn, tmp_path):
    out = tmp_path / "sheet_lvl6.html"
    # Replaying at level 6 when currentLevel is 5 should omit quickReference
    summary = sheet.render_character_sheet(native_doc, str(out), level=6)
    assert "quick-reference" not in summary["sections"]

    content = out.read_text(encoding="utf-8")
    assert 'data-sec="quick-reference"' not in content


def test_multi_page_quick_reference(native_doc, conn, tmp_path):
    doc = copy.deepcopy(native_doc)
    doc["quickReference"] = {
        "pages": [
            {
                "title": "Combat Page 1",
                "groups": [
                    {
                        "title": "Round 1",
                        "moves": [{"name": "Stride", "cost": 1, "text": "Move up to Speed {speed} ft."}],
                    }
                ],
            },
            {
                "title": "Combat Page 2",
                "groups": [
                    {
                        "title": "Round 2",
                        "moves": [{"name": "Strike", "cost": 1, "text": "Hit with AC {ac} defence."}],
                    }
                ],
            },
        ]
    }
    out = tmp_path / "multi_page.html"
    summary = sheet.render_character_sheet(doc, str(out))
    assert "quick-reference" in summary["sections"]

    content = out.read_text(encoding="utf-8")
    assert "Combat Page 1" in content
    assert "Combat Page 2" in content
    assert content.count('data-sec="quick-reference"') == 2
