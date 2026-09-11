"""A caller-supplied page merges into the same file as the rest of the sheet.

Before this, a hand-authored quick-reference page (a printable cheat sheet
for conditional bonuses, say) had to live in a separate document -- there was
no hook to fold it into the one file a player actually prints. `extra_pages`
is that hook: raw HTML the renderer inserts verbatim, ahead of the licence
page (which has to stay last) and behind everything this project generates
from the character's own data.
"""

from __future__ import annotations

import re

import pytest

from pf2e_mcp.server import sheet


@pytest.fixture
def character() -> dict:
    return {
        "name": "Reference Tester", "class": "Fighter", "ancestry": "Human",
        "level": 5,
        "abilities": {"str": 18, "dex": 14, "con": 14, "int": 10, "wis": 12,
                      "cha": 10},
        "proficiencies": {}, "feats": [],
    }


def test_no_extra_pages_by_default(character, conn, tmp_path):
    out = tmp_path / "plain.html"
    summary = sheet.render_character_sheet(character, str(out), level=5)
    assert "quick-reference" not in summary["sections"]
    assert 'data-sec="quick-reference"' not in out.read_text(encoding="utf-8")


def test_an_extra_page_is_inserted_verbatim(character, conn, tmp_path):
    out = tmp_path / "with_extra.html"
    marker = '<section class="page" data-sec="quick-reference"><p id="canary">hello</p></section>'
    summary = sheet.render_character_sheet(
        character, str(out), level=5, extra_pages=[marker],
    )
    html = out.read_text(encoding="utf-8")
    assert marker in html
    assert "quick-reference" in summary["sections"]


def test_an_extra_page_lands_before_the_licence_page(character, conn, tmp_path):
    out = tmp_path / "order.html"
    marker = '<section class="page" data-sec="quick-reference"><p id="canary"></p></section>'
    sheet.render_character_sheet(character, str(out), level=5, extra_pages=[marker])
    html = out.read_text(encoding="utf-8")
    assert html.index('id="canary"') < html.index('data-sec="attribution"')


def test_multiple_extra_pages_are_all_inserted(character, conn, tmp_path):
    out = tmp_path / "multi.html"
    pages = [
        '<section class="page" data-sec="quick-reference"><p id="one"></p></section>',
        '<section class="page" data-sec="quick-reference"><p id="two"></p></section>',
    ]
    sheet.render_character_sheet(character, str(out), level=5, extra_pages=pages)
    html = out.read_text(encoding="utf-8")
    assert 'id="one"' in html and 'id="two"' in html


def test_the_toolbar_carries_a_toggle_for_it(character, conn, tmp_path):
    out = tmp_path / "toggle.html"
    marker = '<section class="page" data-sec="quick-reference"><p></p></section>'
    sheet.render_character_sheet(character, str(out), level=5, extra_pages=[marker])
    toolbar = re.search(r'<div class="toolbar">(.*?)</div>\s*</div>',
                         out.read_text(encoding="utf-8"), re.S)
    assert 'value="quick-reference"' in out.read_text(encoding="utf-8")
