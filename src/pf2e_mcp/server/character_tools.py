"""Tool surface over the native character format.

The model half of this project's contract: `build_character_schema` teaches a
caller what a character file looks like, and `build_validate_character` tells
them whether the one they wrote is right. The format itself, and both layers of
checking, are in server/character.py.
"""

from __future__ import annotations

from typing import Any

from . import character as ch
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
        "storage": "characters/<Name>.yaml",
        "notes": [
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
    attribute, every slug resolving to a real rules entry, and an Organized
    Play record that agrees with the build it now sits beside.

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
