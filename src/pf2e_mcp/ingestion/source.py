"""Fetch the official PF2e data bundle from foundryvtt/pf2e GitHub releases.

Paizo releases new content and errata on a near-weekly cadence and
foundryvtt/pf2e tracks it closely, so this is designed to be re-run
regularly rather than once: each call re-checks the latest release tag and
only re-downloads if it differs from what's cached.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO = "foundryvtt/pf2e"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"
ASSET_NAME = "json-assets.zip"
RAW_CONTENT_BASE = f"https://raw.githubusercontent.com/{REPO}"

# The repo also publishes Starfinder (`sf2e-*`) and "anachronism" module
# releases (e.g. `pf2e-anachronism-2.3.0`), interleaved with the actual
# PF2e system releases and sometimes newer by publish date -- so
# GitHub's own `/releases/latest` pointer can land on one of those instead
# of a PF2e system release, and those releases don't carry a
# `json-assets.zip` asset at all. Only a bare `pf2e-X.Y.Z` tag is a system
# release built from the game data this ingestion needs.
_PF2E_SYSTEM_TAG_RE = re.compile(r"^pf2e-\d+\.\d+\.\d+$")


@dataclass
class Release:
    tag: str
    asset_url: str


def get_latest_release() -> Release:
    resp = httpx.get(RELEASES_API, params={"per_page": 30}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    releases = resp.json()
    release = next((r for r in releases if _PF2E_SYSTEM_TAG_RE.match(r["tag_name"])), None)
    if release is None:
        raise RuntimeError(f"No PF2e system release (pf2e-X.Y.Z) found in latest {len(releases)} releases")
    asset = next((a for a in release["assets"] if a["name"] == ASSET_NAME), None)
    if asset is None:
        raise RuntimeError(f"Latest PF2e release {release['tag_name']} has no {ASSET_NAME} asset")
    return Release(tag=release["tag_name"], asset_url=asset["browser_download_url"])


def download_and_extract(release: Release, cache_dir: Path) -> Path:
    """Download+extract into cache_dir/<tag>/, skipping work if already present.

    Returns the directory containing the extracted `packs/` folder.
    """
    dest = cache_dir / release.tag
    packs_dir = dest / "packs"
    if packs_dir.exists():
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / ASSET_NAME
    with httpx.stream("GET", release.asset_url, timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    zip_path.unlink()

    return dest


def prune_cache(cache_dir: Path, keep_tag: str) -> list[str]:
    """Delete every cached release except `keep_tag`. Returns what was removed.

    `download_and_extract` keeps each release under its own `<tag>/`
    directory and never removes the previous one, so re-running ingestion
    against Paizo's near-weekly errata cadence -- which this module is
    explicitly designed for -- accumulates a full extracted release every time.
    At a few hundred MB apiece that grows without bound, and only the tag
    just ingested is of any use: the database is rebuilt from scratch on
    every run, so an older extraction can never be consulted again.

    Called after a successful build, so a failed run leaves the cache alone
    and the next attempt can still reuse whatever was already downloaded.
    Failures here are swallowed: not reclaiming disk is not a reason to fail
    an otherwise-good ingestion.
    """
    removed: list[str] = []
    if not cache_dir.is_dir():
        return removed
    for child in cache_dir.iterdir():
        if not child.is_dir() or child.name == keep_tag:
            continue
        try:
            shutil.rmtree(child)
            removed.append(child.name)
        except OSError:
            continue
    return removed


def fetch_source_file(tag: str, repo_path: str, cache_dir: Path) -> str | None:
    """Fetch a single file from the `foundryvtt/pf2e` repo's TypeScript
    source (not the packaged data release `download_and_extract` pulls
    from), pinned to the exact same release tag already being ingested --
    so this stays in sync automatically as new releases come out, rather
    than depending on a hand-copied snapshot that silently goes stale. Used
    for things the packaged JSON data doesn't carry at all: CONFIG.PF2E
    lookup dictionaries (e.g. the game's own skill/weapon-group name
    lists) referenced by some `ChoiceSet` rule elements by path instead of
    a literal choice array. Cached under the same `cache_dir/<tag>/`
    directory as the data release, so a rebuild against an
    already-ingested tag doesn't re-fetch. Returns None (not raises) on
    any fetch failure -- this is a best-effort enrichment, not a required
    part of ingestion, and a network hiccup fetching one config file
    shouldn't fail an entire rebuild."""
    dest = cache_dir / tag / "source-files" / repo_path
    if dest.exists():
        return dest.read_text()

    url = f"{RAW_CONTENT_BASE}/{tag}/{repo_path}"
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(resp.text)
    return resp.text


def fetch_source_bytes(tag: str, repo_path: str, cache_dir: Path) -> bytes | None:
    """Fetch a single binary file from the `foundryvtt/pf2e` repo's source,
    pinned to the exact same release tag being ingested. Cached under
    `cache_dir/<tag>/source-files/` so rebuilds against the same tag don't
    re-download. Returns None on network error."""
    dest = cache_dir / tag / "source-files" / repo_path
    if dest.exists():
        return dest.read_bytes()

    url = f"{RAW_CONTENT_BASE}/{tag}/{repo_path}"
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return resp.content

