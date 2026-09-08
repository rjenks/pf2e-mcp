"""Every character-taking tool accepts the native format.

The point of the front door is that nothing downstream had to be rewritten:
`sheet.py`, the derived-stat math and every validator still see the
Pathbuilder-shaped dict they always did. These tests assert that a native
document reaches all of them and comes out the same as the equivalent legacy
build would.
"""

from __future__ import annotations

import pytest

from pf2e_mcp.server import build_tools, character, character_import, sheet


@pytest.fixture
def native(conn) -> dict:
    return character_import.from_pathbuilder({
        "build": {
            "name": "Front Door",
            "class": "Fighter",
            "level": 3,
            "ancestry": "Dwarf",
            "heritage": "Death Warden Dwarf",
            "background": "Field Medic",
            "abilities": {
                "str": 18, "dex": 12, "con": 16, "int": 10, "wis": 12, "cha": 8,
                "breakdown": {
                    "ancestryBoosts": ["Con", "Wis"],
                    "ancestryFree": ["Str"],
                    "ancestryFlaws": ["Cha"],
                    "backgroundBoosts": ["Con", "Str"],
                    "classBoosts": ["Str"],
                    "mapLevelledBoosts": {"1": ["Str", "Dex", "Con", "Wis"]},
                },
            },
            "feats": [
                ["Death Warden Dwarf", None, "Heritage", 1],
                ["Stonemason's Eye", None, "Ancestry Feat", 1],
                ["Power Attack", None, "Class Feat", 2],
                ["Battle Medicine", None, "Skill Feat", 2],
                ["Toughness", None, "General Feat", 3],
            ],
            "weapons": [{"name": "Longsword", "qty": 1}],
            "armor": [{"name": "Chain Mail", "qty": 1, "worn": True}],
            "money": {"gp": 10},
        },
    }, conn)


def test_is_native_discriminates_on_schema_version(native):
    assert character.is_native(native)
    assert not character.is_native({"build": {"name": "X"}})
    assert not character.is_native({"name": "X", "level": 1})


def test_as_legacy_passes_a_legacy_build_through_unchanged():
    build = {"name": "X", "level": 1}
    assert character.as_legacy(build) is build


def test_as_legacy_unwraps_a_pathbuilder_envelope():
    build = {"name": "X", "level": 1}
    assert character.as_legacy({"success": True, "build": build}) is build


def test_derived_stats_accepts_a_native_document(native, conn):
    derived = build_tools.calculate_derived_stats(native)
    # 10 ancestry + 3 x (10 class + 3 Con) + 3 x 1 Toughness = 52
    assert derived["hp"] == 52


def test_validate_build_accepts_a_native_document(native):
    result = build_tools.validate_build(native)
    assert "errors" in result and "warnings" in result


def test_level_up_choices_accepts_a_native_document(native):
    result = build_tools.get_level_up_choices(native, 4)
    assert result


def test_available_feats_replays_to_the_level_asked_about(native):
    """The one tool taking an explicit level replays to it, not to the current one."""
    at_two = build_tools.list_available_feats(native, "class", level=2)
    at_six = build_tools.list_available_feats(native, "class", level=6)
    assert at_six and at_two
    assert len(at_six) >= len(at_two)


def test_pathbuilder_export_strips_provenance(native):
    exported = build_tools.to_pathbuilder_export(native)
    assert exported["success"] is True
    assert not [key for key in exported["build"] if key.startswith("_")]


def test_one_file_renders_a_sheet_at_any_level(native, tmp_path, conn):
    """What replaces keeping a separate export per level.

    The level-1 sheet must not contain the 3rd-level general feat, and the
    3rd-level one must -- proving the level parameter reaches the renderer
    rather than being quietly ignored.
    """
    results = {}
    for level in (1, 3):
        out = tmp_path / f"sheet-{level}.html"
        results[level] = sheet.render_character_sheet(
            native, str(out), level=level
        )
        assert out.exists() and out.stat().st_size > 10_000

    assert results[1]["rendered"]["feats"] < results[3]["rendered"]["feats"]
    assert "Toughness" not in (tmp_path / "sheet-1.html").read_text()
    assert "Toughness" in (tmp_path / "sheet-3.html").read_text()


def test_rendered_sheet_resolves_every_name(native, tmp_path):
    """Slugs in, canonical names out: nothing should be left unresolved.

    `unresolved` exists because Pathbuilder records names the rules data may
    not match. A native document carries slugs that were checked at import, so
    a sheet rendered from one should have nothing in that list.
    """
    result = sheet.render_character_sheet(
        native, str(tmp_path / "sheet.html")
    )
    assert result["unresolved"] == []
