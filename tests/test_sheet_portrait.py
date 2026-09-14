"""Test character portrait resolution and embedding on Page 1 of the sheet."""

from __future__ import annotations

import base64
import re
import pytest

from pf2e_mcp.server import sheet, character_replay, db


@pytest.fixture
def base_character() -> dict:
    return {
        "name": "Portrait Hero",
        "class": "Ranger",
        "ancestry": "Human",
        "level": 3,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 10},
        "proficiencies": {},
        "feats": [],
    }


def test_portrait_data_uri_embedded_verbatim(base_character, conn, tmp_path):
    data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    base_character["portrait"] = data_uri

    out = tmp_path / "portrait_uri.html"
    sheet.render_character_sheet(base_character, str(out), level=3)
    html = out.read_text(encoding="utf-8")

    assert '<div class="portrait-box">' in html
    assert f'src="{data_uri}"' in html


def test_portrait_relative_filename_resolution(base_character, conn, tmp_path):
    img_path = tmp_path / "hero.png"
    img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa7\x96\x18\xd7\x00\x00\x00\x00IEND\xaeB`\x82"
    img_path.write_bytes(img_bytes)

    base_character["portrait"] = "hero.png"

    out = tmp_path / "portrait_file.html"
    sheet.render_character_sheet(base_character, str(out), level=3)
    html = out.read_text(encoding="utf-8")

    assert '<div class="portrait-box">' in html
    expected_b64 = base64.b64encode(img_bytes).decode("ascii")
    assert f"data:image/png;base64,{expected_b64}" in html


def test_native_document_preserves_portrait_through_at_level(conn, tmp_path):
    native_doc = {
        "schemaVersion": 1,
        "identity": {
            "name": "Native Portrait",
            "currentLevel": 5,
            "portrait": "test_portrait.jpeg",
        },
        "build": {
            "ancestry": "orc",
            "background": "field-medic",
            "class": "fighter",
        },
        "plan": [{"level": 1}],
    }
    replayed = character_replay.at_level(native_doc, 5, conn)
    assert replayed["portrait"] == "test_portrait.jpeg"


def test_default_generic_portrait_spellcaster_vs_melee(conn, tmp_path):
    # Spellcaster class without specific portrait
    wizard_char = {
        "name": "Generic Wizard",
        "class": "Wizard",
        "ancestry": "Elf",
        "level": 1,
        "abilities": {"str": 10, "dex": 14, "con": 12, "int": 18, "wis": 12, "cha": 10},
        "proficiencies": {},
    }
    out_wiz = tmp_path / "wizard.html"
    sheet.render_character_sheet(wizard_char, str(out_wiz), level=1)
    wiz_html = out_wiz.read_text(encoding="utf-8")

    # Martial class without specific portrait
    fighter_char = {
        "name": "Generic Fighter",
        "class": "Fighter",
        "ancestry": "Dwarf",
        "level": 1,
        "abilities": {"str": 18, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
        "proficiencies": {},
    }
    out_ftr = tmp_path / "fighter.html"
    sheet.render_character_sheet(fighter_char, str(out_ftr), level=1)
    ftr_html = out_ftr.read_text(encoding="utf-8")

    assert '<div class="portrait-box">' in wiz_html
    assert '<div class="portrait-box">' in ftr_html
    # Should use different data URIs for spellcaster vs melee defaults
    wiz_src = re.search(r'<img class="portrait" src="([^"]+)"', wiz_html).group(1)
    ftr_src = re.search(r'<img class="portrait" src="([^"]+)"', ftr_html).group(1)
    assert wiz_src != ftr_src


def test_unknown_class_defaults_to_generic_melee(conn, tmp_path):
    unknown_char = {
        "name": "Unknown Adventurer",
        "class": "CustomHomebrewClass",
        "ancestry": "Human",
        "level": 1,
        "abilities": {"str": 14, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "proficiencies": {},
    }
    out_unk = tmp_path / "unknown.html"
    sheet.render_character_sheet(unknown_char, str(out_unk), level=1)
    unk_html = out_unk.read_text(encoding="utf-8")

    fighter_char = {
        "name": "Generic Fighter",
        "class": "Fighter",
        "ancestry": "Dwarf",
        "level": 1,
        "abilities": {"str": 18, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
        "proficiencies": {},
    }
    out_ftr = tmp_path / "fighter.html"
    sheet.render_character_sheet(fighter_char, str(out_ftr), level=1)
    ftr_html = out_ftr.read_text(encoding="utf-8")

    unk_src = re.search(r'<img class="portrait" src="([^"]+)"', unk_html).group(1)
    ftr_src = re.search(r'<img class="portrait" src="([^"]+)"', ftr_html).group(1)
    # Unknown class matches generic melee default
    assert unk_src == ftr_src
