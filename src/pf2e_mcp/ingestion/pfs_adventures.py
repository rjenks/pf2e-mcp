"""Fetch the Pathfinder Society adventure index from PathfinderWiki.

Organized Play players identify an adventure by its code -- "we played 8-02",
not "we played The Fey Reclamation" -- so a chronicle log is only useful if
something can resolve a code to a title, a tier and an adventure type. None of
that is in the foundryvtt/pf2e data this project otherwise ingests: adventure
packs are excluded on licensing grounds, and `entries.source_book` is a bare
publication title with no structure behind it.

Paizo publishes no usable feed of its own. The store and organizedplay.paizo.com
are a single-page app with no public API (every path returns the same shell) and
reporting is behind a login; the Archives of Nethys Elasticsearch index carries
only the ~31 PFS products that introduce new *rules* content, so it indexes
sources rather than adventures; and the `pathfinder-society` npm package that
still surfaces in search results was last published in January 2022 and its
GitHub repository now 404s.

PathfinderWiki is the practical source, and specifically its `Facts:` namespace
rather than its article prose. Each adventure has a `Facts:<Name>` page holding
a single `{{Facts/Book|Key=Value|...}}` block -- a structured record with a
stable key set -- plus a `Facts:<Name>/Releases` subpage carrying the Paizo
pubcode and an ISO release date. Both are fetchable through the ordinary
MediaWiki revisions API, ~40 pages per request, which puts a full index rebuild
at well under a hundred HTTP calls.

Two shapes of code exist and are handled differently:

- **Scenarios** carry an explicit `Society code` field ("8-02"), and every one
  of them does -- there is no fallback path worth writing for scenarios.
- **Quests and bounties** carry no such field, but their `Full title` reliably
  spells the number out ("Pathfinder Bounty #21: Against the Unliving",
  "Pathfinder Quest (Series 2) #26: Dragon's Plea"). Those are normalised here
  into `B21` and `Q2-26` so that every adventure has one primary key, and so
  that a quest's series -- which determines its XP award -- survives into the
  database.

Only factual index data is stored. The `Blurb text` field on these pages is
Paizo's marketing copy and is deliberately dropped, consistent with NOTICE.md
and with the product-identity pack exclusions in build.py.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://pathfinderwiki.com/w/api.php"

# PathfinderWiki asks for a descriptive User-Agent; a generic client string is
# the documented way to get rate-limited or blocked outright.
USER_AGENT = "pf2e-mcp/ingestion (+https://github.com/rjenks/pf2e-mcp)"

# The API accepts up to 50 titles per query for non-bot clients. 40 leaves
# headroom and keeps individual responses small enough to parse comfortably.
_BATCH = 40

# Categories that between them cover every PF2-era Organized Play adventure.
# The per-season categories are listed explicitly rather than discovered because
# a new season's category appears before it has any members, and an empty
# category is indistinguishable from a typo'd one.
_MAX_SEASON = 12
_CATEGORIES = [
    "Pathfinder Bounties",
    "Pathfinder Quests",
    "Pathfinder Society (2E) scenarios",
    *(f"Season {n} (2E) scenarios" for n in range(1, _MAX_SEASON + 1)),
]

# Only PF2 material. The quest and bounty categories are edition-agnostic and
# carry PF1 entries (Ambush in Absalom, The Silverhex Chronicles, the Rose
# Street Revenge playtest), which would otherwise collide with PF2 numbering.
_RULE_SYSTEM = "PF2"

_BOOK_TYPE_KINDS = {
    "Pathfinder Society (2E) scenario": "scenario",
    "Pathfinder Quest": "quest",
    "Pathfinder Bounty": "bounty",
}

_FACTS_BOOK_RE = re.compile(r"\{\{Facts/Book\s*\n(.*)\n\s*\}\}", re.DOTALL)
_FACTS_RELEASE_RE = re.compile(r"\{\{Facts/Book/Release\s*\n(.*?)\n\s*\}\}", re.DOTALL)

# "Pathfinder Bounty #21: Against the Unliving"
_BOUNTY_NUM_RE = re.compile(r"Bounty\s*#\s*(\d+)")
# "Pathfinder Quest (Series 2) #26: Dragon's Plea" -- the series is parenthesised
# only for Series 2; Series 1 quests read "Pathfinder Society Quest #7: ...".
_QUEST_NUM_RE = re.compile(r"Quest\s*(?:\(Series\s*(\d+)\)\s*)?#\s*(\d+)")

# A scenario code is "<season>-<number>", except for the two evergreen intros
# which Paizo numbers 99-1 and 99-2.
_CODE_RE = re.compile(r"^(\d+)-(\d+)$")

_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
_HTML_ENTITY_RE = re.compile(r"&(?:ndash|mdash);")


@dataclass
class Adventure:
    """One Organized Play adventure, as stored in the `pfs_adventures` table."""

    code: str
    name: str
    kind: str
    full_title: str | None = None
    season: int | None = None
    number: int | None = None
    tier_low: int | None = None
    tier_high: int | None = None
    series: str | None = None
    tags: list[str] = field(default_factory=list)
    factions: list[str] = field(default_factory=list)
    metaplot: list[str] = field(default_factory=list)
    location: str | None = None
    author: str | None = None
    sanctioned: int | None = None
    pubcode: str | None = None
    release_date: str | None = None
    wiki_page: str | None = None


def _clean(value: str | None) -> str | None:
    """Strip wiki markup down to plain text.

    Facts values are mostly bare strings, but a few (`Location`, `Blurb`-
    adjacent fields) carry `[[Page|Label]]` links, and tiers are written with
    HTML entity dashes.
    """
    if value is None:
        return None
    text = _WIKILINK_RE.sub(r"\1", value)
    text = _HTML_ENTITY_RE.sub("-", text).strip()
    return text or None


def _split_list(value: str | None) -> list[str]:
    """Facts uses '; ' as its multi-value separator throughout."""
    cleaned = _clean(value)
    if not cleaned:
        return []
    return [part.strip() for part in cleaned.split(";") if part.strip()]


def _parse_int(value: str | None) -> int | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    match = re.search(r"\d+", cleaned)
    return int(match.group()) if match else None


def _parse_template_params(body: str) -> dict[str, str]:
    """Parse the `|Key=Value` lines of a Facts template body.

    Values may run across lines (blurbs do), so a new parameter is recognised
    only by a line that begins with `|`. No Facts/Book value contains a nested
    template, so this does not need brace tracking.
    """
    params: dict[str, str] = {}
    key: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("|") and "=" in stripped:
            name, _, value = stripped[1:].partition("=")
            key = name.strip()
            params[key] = value.strip()
        elif key is not None:
            params[key] = (params[key] + "\n" + line).strip()
    return params


class _Wiki:
    """Thin MediaWiki API client with retry, sharing one HTTP connection.

    PathfinderWiki sits behind Cloudflare and intermittently returns 521 or a
    maintenance page mid-rebuild; a full index pull makes enough requests that
    hitting one is likely rather than exceptional, so every call retries with a
    linear backoff before giving up.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(self, **params: Any) -> dict[str, Any]:
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        last: Exception | None = None
        for attempt in range(5):
            try:
                response = self._client.get(API, params=params, timeout=60.0)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 -- retried, then re-raised
                last = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"PathfinderWiki request failed after 5 attempts: {last}")

    def category_members(self, category: str) -> list[str]:
        titles: list[str] = []
        params: dict[str, Any] = {}
        while True:
            data = self.get(
                action="query",
                list="categorymembers",
                cmtitle=f"Category:{category}",
                cmlimit="500",
                **params,
            )
            titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
            if "continue" not in data:
                return titles
            params = data["continue"]

    def page_contents(self, titles: Iterable[str]) -> dict[str, str]:
        """Fetch raw wikitext for many pages, keyed by title.

        Missing pages are simply absent from the result -- a Facts page not
        existing is normal for a very recent release, not an error.
        """
        out: dict[str, str] = {}
        batch = list(titles)
        for i in range(0, len(batch), _BATCH):
            data = self.get(
                action="query",
                prop="revisions",
                rvprop="content",
                rvslots="main",
                titles="|".join(batch[i : i + _BATCH]),
            )
            for page in data.get("query", {}).get("pages", []):
                revisions = page.get("revisions")
                if not revisions:
                    continue
                out[page["title"]] = revisions[0]["slots"]["main"]["content"]
        return out


def _derive_code(kind: str, facts: dict[str, str]) -> str | None:
    """Work out the primary key for one adventure.

    Scenarios always carry `Society code`. Quests and bounties never do, so
    their number is read out of `Full title` and given a kind-specific prefix
    (`Q`/`B`) -- both series of quests restart at #1, and a bare "7" would
    otherwise collide across all three kinds.
    """
    if kind == "scenario":
        return _clean(facts.get("Society code"))

    title = _clean(facts.get("Full title")) or ""
    if kind == "bounty":
        match = _BOUNTY_NUM_RE.search(title)
        return f"B{int(match.group(1))}" if match else None
    if kind == "quest":
        match = _QUEST_NUM_RE.search(title)
        if not match:
            return None
        series, number = match.group(1), int(match.group(2))
        # Series 1 quests predate the numbering convention and are written
        # without a series marker; Series 2 spells it out.
        return f"Q{series or '1'}-{number}"
    return None


def _adventure_from_facts(page_title: str, facts: dict[str, str]) -> Adventure | None:
    if _clean(facts.get("Rule system")) != _RULE_SYSTEM:
        return None
    kind = _BOOK_TYPE_KINDS.get(_clean(facts.get("Book type")) or "")
    if kind is None:
        return None
    code = _derive_code(kind, facts)
    name = _clean(facts.get("Name"))
    if not code or not name:
        return None

    season = number = None
    if kind == "scenario":
        match = _CODE_RE.match(code)
        if match:
            season, number = int(match.group(1)), int(match.group(2))

    tier_low = _parse_int(facts.get("Level range start"))
    tier_high = _parse_int(facts.get("Level range end"))
    # Bounties are frequently written with only a start level because they are
    # single-level adventures; treat that as a one-level tier rather than an
    # open-ended one, which would let any character claim to be in tier.
    if tier_low is not None and tier_high is None:
        tier_high = tier_low

    series_values = _split_list(facts.get("Series"))
    # "Pathfinder Society (second edition)" and "Paizo Organized Play" appear on
    # nearly every record and say nothing; the useful entry is the season's
    # in-world year name ("Year of Clockwork Mystery").
    season_name = next(
        (s for s in series_values if s.lower().startswith("year of")),
        None,
    )

    return Adventure(
        code=code,
        name=name,
        kind=kind,
        full_title=_clean(facts.get("Full title")),
        season=season,
        number=number,
        tier_low=tier_low,
        tier_high=tier_high,
        series=season_name,
        tags=_split_list(facts.get("Society tag")),
        factions=_split_list(facts.get("Faction")),
        metaplot=_split_list(facts.get("Metaplot")),
        location=(_split_list(facts.get("Location")) or [None])[0],
        author=_clean(facts.get("Primary author")) or _clean(facts.get("Author")),
        sanctioned=1 if (_clean(facts.get("Sanctioned")) or "").lower() == "yes" else 0,
        wiki_page=page_title,
    )


def fetch_adventures() -> list[Adventure]:
    """Pull the full PF2 Organized Play adventure index.

    Roughly 20 requests: a handful of category listings, ~6 batches of Facts
    pages, and ~6 batches of Releases subpages.
    """
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        wiki = _Wiki(client)

        titles: list[str] = []
        for category in _CATEGORIES:
            titles += wiki.category_members(category)
        # Category listings overlap heavily (a scenario is in both its season
        # category and the umbrella one) and include sub-category rows.
        pages = [
            t
            for t in dict.fromkeys(titles)
            if not t.startswith(("Category:", "Season "))
        ]

        facts_pages = wiki.page_contents(f"Facts:{t}" for t in pages)

        adventures: list[Adventure] = []
        for facts_title, content in facts_pages.items():
            match = _FACTS_BOOK_RE.search(content)
            if not match:
                continue
            page_title = facts_title[len("Facts:") :]
            adventure = _adventure_from_facts(page_title, _parse_template_params(match.group(1)))
            if adventure is not None:
                adventures.append(adventure)

        release_pages = wiki.page_contents(f"Facts:{a.wiki_page}/Releases" for a in adventures)
        by_page = {a.wiki_page: a for a in adventures}
        for release_title, content in release_pages.items():
            page_title = release_title[len("Facts:") : -len("/Releases")]
            adventure = by_page.get(page_title)
            if adventure is None:
                continue
            # A product can have several release rows (PDF, print, a later
            # bundle). The earliest date is the one that dates the adventure.
            best: tuple[str, str | None] | None = None
            for release in _FACTS_RELEASE_RE.finditer(content):
                params = _parse_template_params(release.group(1))
                date = _clean(params.get("Release date"))
                if not date:
                    continue
                if best is None or date < best[0]:
                    best = (date, _clean(params.get("Pubcode")))
            if best is not None:
                adventure.release_date, adventure.pubcode = best[0], best[1]

    adventures.sort(key=lambda a: (a.kind, a.season or 0, a.number or 0, a.code))
    return adventures
