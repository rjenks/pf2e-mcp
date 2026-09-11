"""Tool surface over the native character format.

The model half of this project's contract: `build_character_schema` teaches a
caller what a character file looks like, and `build_validate_character` tells
them whether the one they wrote is right. The format itself, and both layers of
checking, are in server/character.py.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import character as ch
from . import character_import as imports
from . import character_replay as replay
from .db import get_connection


def character_schema() -> dict[str, Any]:
    """Return the JSON Schema for a character file.

    A character file is one YAML document holding everything about one
    character: the level-by-level build plan from 1st to 20th, the level they
    have actually reached, the gear they carry, their Organized Play record,
    and their backstory and party introduction. It replaces the Pathbuilder
    JSON export, the companion markdown design document, the per-level snapshot
    exports and the chronicle sidecar that were previously maintained as
    separate, unlinked files.

    Two things about the shape are worth understanding before writing one.

    **The plan is the source of truth, and state is derived from it.** Nothing
    in the file records what the character's proficiencies, attributes or Hit
    Points are. `plan` holds the choices made at each level and
    `identity.currentLevel` says how far along the character is; everything
    else is replayed from those on demand, so there is no second copy to
    contradict the first. This is why a 3rd-level character can carry a plan
    through 20th in the same file, and why asking for a sheet at any level is
    the same operation.

    **Rules content is referenced by slug, not by name.** A slug is the `slug`
    column of the rules database -- the entry name lowercased and hyphenated,
    as in 'soul-warden-dedication'. Every tool here resolves them exactly, and
    a slug that does not exist is reported rather than fuzzily matched onto
    something else.

    Prose has typed homes: `story.backstory` and `story.introduction` for
    narrative, a `note` on each individual choice for why that pick was made,
    and `notes[]` for longer analysis that belongs with the character but not
    with one choice. Notes about the *tools* belong in the issue tracker
    instead.

    Call this before authoring or editing a character file by hand.
    """
    return {
        "schema": ch.load_schema(),
        "schema_version": ch.SCHEMA_VERSION,
        "storage": "characters/AshKordun-Orc-Cleric/AshKordun.pf2e.yaml",
        "notes": [
            "One folder per character, holding the .pf2e.yaml alongside "
            "everything else about them -- rendered sheets, chronicle scans, a "
            "portrait. Two builds of the same character collide on that path, "
            "so a variant needs its own `identity.name`.",
            "YAML, not JSON: these files carry multi-paragraph prose, and are "
            "edited by hand as well as written by this server. Comments and "
            "key order are preserved across a tool write.",
            "Only `schemaVersion`, `identity` and `build` and `plan` are "
            "required. A character part-way through being built is a valid "
            "file with a short plan.",
            "A level above `identity.currentLevel` is intent, not a claim. "
            "Recording levels the character has not reached is the point of "
            "the format.",
            "`automatic` on a plan level is informational. Features a class "
            "grants automatically are derived from the class progression "
            "whether or not they are listed.",
            "`proficiencyOverrides` is an escape hatch for training that "
            "cannot be derived -- a dedication granting 'a skill of your "
            "choice', say. Each one must name its source, and validation "
            "reports any that derivation has caught up with.",
        ],
    }


def validate_character(character: dict[str, Any]) -> dict[str, Any]:
    """Check that a character file is well formed and internally consistent.

    Runs two layers. The **structural** layer checks the document against the
    schema: required fields, value types, unrecognised keys, enum membership.
    The **semantic** layer checks what a schema cannot express -- plan levels
    in ascending order and not repeated, the current level actually covered by
    the plan, attribute boosts from a single source not doubling up on one
    attribute, every slug resolving to a real rules entry, an Organized
    Play record that agrees with the build it now sits beside, and a gold
    journal (`ledger`) that sums to the currency actually on hand.

    Structural failure stops the run. Telling someone a feat does not exist is
    noise when the real problem is that `plan` is a string.

    Every finding names the field it is about as a JSON Pointer, so a caller
    fixing the file knows where to look:

        {"level": "error", "code": "unknown_slug",
         "path": "/plan/3/choices/0/pick",
         "message": "No rules entry with slug 'combat-grabb'."}

    Args:
        character: A character document matching the schema returned by
            `build_character_schema`.

    This checks the *file*, not the *build*. Whether the character's feats meet
    their prerequisites, whether they have spent more feats than their level
    grants, and whether every option is legal for Organized Play are
    `build_validate_build`'s questions, asked against the state this document
    replays to.
    """
    if not isinstance(character, dict) or not character:
        raise ValueError("character must be a non-empty character document")
    conn = get_connection()
    try:
        return ch.validate_document(character, conn)
    finally:
        conn.close()


def character_at_level(
    character: dict[str, Any], level: int | None = None
) -> dict[str, Any]:
    """Work out what a character looks like at a given level.

    A character file records the choices made at each level and nothing about
    their consequences. This replays the plan from 1st level up to `level` and
    returns the resulting state -- attributes, proficiency ranks, feats,
    automatic class features, skills and Lores, gear and spellcasting -- in the
    Pathbuilder shape that `build_calculate_derived_stats`,
    `build_validate_build` and `build_render_character_sheet` all accept. Pass
    the result straight to any of them.

    Because nothing is stored, any level is as cheap as any other: this is how
    to see a 10th-level plan's numbers on a 3rd-level character, or to render
    the sheet the character had five levels ago. It replaces keeping separate
    per-level export files.

    The result carries a `_derivation` key explaining itself -- which class
    features granted which proficiency bumps and at what level, where each
    trained skill came from, which feats contributed Hit Points, and any
    `proficiencyOverrides` that derivation has caught up with and that can now
    be deleted. Underscore-prefixed keys are stripped from a Pathbuilder
    export, so this never leaks into a file meant for Pathbuilder.

    Args:
        character: A character document matching `build_character_schema`.
        level: Which level to replay to, 1-20. Defaults to the character's
            `identity.currentLevel`. A level beyond the plan's coverage is
            allowed and returns what the plan does cover, saying so in
            `_derivation.notes`.
    """
    if not isinstance(character, dict) or not character:
        raise ValueError("character must be a non-empty character document")
    conn = get_connection()
    try:
        return replay.at_level(character, level, conn)
    finally:
        conn.close()


def import_pathbuilder(export: dict[str, Any]) -> dict[str, Any]:
    """Convert a Pathbuilder export into a character document.

    Takes either the `{"success": true, "build": {...}}` envelope Pathbuilder
    produces or a bare build object, and returns a document in the native
    format -- ready to check with `build_validate_character` and save as
    `characters/<Name>.yaml`.

    A Pathbuilder file records more than it looks: each feat tuple carries the
    level and the slot it was taken in, so the plan is *reconstructed* level by
    level rather than guessed. Levels above the character's current one come
    back empty, because the export has nothing to say about them -- filling
    those in is the reason to move to this format.

    Three things import cannot recover, and reports rather than invents:

    - **Reasons.** No export records why a pick was made. Where prose had been
      smuggled into a feat tuple's "choice" slot, it is rescued into that
      choice's `note`.
    - **Proficiency the rules data cannot derive.** Anything the export rates
      higher than replaying the plan reaches becomes a `proficiencyOverride`
      sourced to `pathbuilder-import`. Each is a claim worth reviewing: either
      a genuine feat grant nothing models yet, or a gap in derivation.
    - **Attributes that disagree with their own boosts.** An export's scores
      and its boost list are two statements of the same thing, and when they
      differ one is wrong -- usually because an apex item was folded into the
      scores. Reported in `_import.attribute_mismatch` rather than resolved.

    The `_import` block carries all of that, plus any name that resolved to no
    rules entry. Review it, then delete it: it is a report on the conversion,
    not part of the character.

    Args:
        export: A Pathbuilder export object or bare build.
    """
    if not isinstance(export, dict) or not export:
        raise ValueError("export must be a non-empty Pathbuilder export object")
    conn = get_connection()
    try:
        return imports.from_pathbuilder(export, conn)
    finally:
        conn.close()


def export_pathbuilder(
    character: dict[str, Any], level: int | None = None
) -> dict[str, Any]:
    """Render a character at a given level as a Pathbuilder export.

    Replays the plan to `level` and wraps the result in Pathbuilder's
    `{"success": true, "build": {...}}` envelope, dropping this format's own
    provenance keys so what comes back is a plain Pathbuilder file its importer
    will accept.

    Because any level replays as cheaply as any other, this is also how to get
    the character as they were five levels ago, or as they will be at 20th,
    without keeping separate files for each.

    Args:
        character: A character document matching `build_character_schema`.
        level: Which level to export, 1-20. Defaults to the character's
            `identity.currentLevel`.
    """
    if not isinstance(character, dict) or not character:
        raise ValueError("character must be a non-empty character document")
    conn = get_connection()
    try:
        return imports.to_pathbuilder(character, level, conn)
    finally:
        conn.close()


# Pathbuilder's JSON endpoint sits behind Cloudflare bot management, which
# scores the shape of the request rather than checking for a human. Foundry's
# Pathbuilder importer is an ordinary browser `fetch()` and is served normally;
# a default httpx or curl User-Agent draws the managed challenge instead. These
# headers ask for the same treatment Foundry already gets from a documented,
# machine-readable endpoint.
_PATHBUILDER_URL = "https://pathbuilder2e.com/json.php"
_PATHBUILDER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_pathbuilder(character_id: int | str) -> dict[str, Any]:
    """Fetch a Pathbuilder build by the id from its Export -> JSON link.

    Returns the `{"success": true, "build": {...}}` envelope unchanged, so it
    goes straight into `build_import_pathbuilder`.

    The id is the `?id=` in the URL Pathbuilder's **Export -> JSON** produces.

    **What comes back is the character as it stood at the last export, not as
    it stands now.** Editing in Pathbuilder does not update the payload; only
    exporting again does. This is the trap worth knowing about, because a fetch
    against an id whose owner has been editing returns *stale data with no
    indication that it is stale* -- confirmed live, where three fetches of one
    id returned the pre-edit build twice and the edited build only after the
    player exported. Before treating a fetch as "what they have now", make sure
    they have exported since their last change.

    Re-exporting was observed to **reuse** the same id and refresh its contents,
    rather than minting a new one -- so a stored id stays useful as a pointer.
    That is one character over one re-export, though, so the safe habit is to
    glance at the id in the link after exporting and say so if it differs. A
    wrong or genuinely dead id comes back as `{"success": false}` from
    Pathbuilder itself, which this reports as an error asking for a fresh
    export -- it does not mean the character is gone.

    **Pathbuilder is not upstream of a character file.** This project's format
    records levels above the one reached, the reasoning behind each pick, and
    what depends on what; none of that survives a Pathbuilder round trip. Use
    this to pull a build in for the first time, or to diff an existing file
    against what the player has been editing -- not to overwrite a maintained
    file.

    Args:
        character_id: The numeric id from the export URL.

    Returns:
        The export envelope, or `{"error": ...}` naming which of the three
        lookalike failures occurred: a bot challenge, a rejected id, or a
        transport error.
    """
    try:
        ident = int(str(character_id).strip())
    except (TypeError, ValueError):
        return {"error": f"character_id must be a number; got {character_id!r}."}

    try:
        response = httpx.get(
            _PATHBUILDER_URL,
            params={"id": ident},
            headers=_PATHBUILDER_HEADERS,
            timeout=20,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return {"error": f"Could not reach Pathbuilder: {exc}"}

    if response.status_code != 200:
        return {
            "error": (
                f"Pathbuilder returned HTTP {response.status_code} for id {ident}. "
                "A 403 is usually Cloudflare rather than a bad id."
            )
        }

    try:
        payload = response.json()
    except ValueError:
        # HTML where JSON belongs is the managed challenge, not a bad id --
        # worth saying so, since the two are indistinguishable to a caller.
        snippet = response.text.lstrip()[:80]
        hint = (
            "Cloudflare served a bot challenge instead of the export."
            if "<" in snippet
            else "Pathbuilder returned something that isn't JSON."
        )
        return {"error": f"{hint} First bytes: {snippet!r}"}

    if not isinstance(payload, dict):
        return {"error": f"Expected a JSON object from Pathbuilder, got {type(payload).__name__}."}

    if payload.get("success") is False or "build" not in payload:
        return {
            "error": (
                f"Pathbuilder has no export for id {ident}. Export -> JSON ids go stale "
                "when the character is re-exported; ask for a fresh link."
            )
        }

    # Pathbuilder's payload doesn't name the id it was served under, so stamp
    # it on for `build_import_pathbuilder` to record. Underscore-prefixed keys
    # are this format's convention for provenance and are stripped on export.
    payload["_pathbuilderId"] = ident
    return payload
