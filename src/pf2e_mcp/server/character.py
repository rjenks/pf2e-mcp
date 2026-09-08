"""The native character format: one YAML file holding a whole character.

A Pathbuilder export records what a character *is* right now. Almost everything
this project actually does with a character is about what they *will be*, or
about *why* a pick was made -- and neither has anywhere to live in that format.
So a character used to be three or four files: the Pathbuilder JSON, a markdown
design document carrying the 1-20 plan and the reasoning, a chronicle sidecar
paired only by filename, and a scatter of per-level snapshot exports. Nothing
linked them and nothing validated any of them.

This module defines the replacement: `characters/<Name>.yaml`, one file, schema
checked.

The plan is the source of truth
-------------------------------
`plan` is a list of levels, each holding the choices made at that level and the
reasons for them. `identity.currentLevel` says how far along the character
actually is. **Nothing stores the character's state**: their proficiencies,
attributes, Hit Points and feat list at any level are replayed from the plan on
demand (see `at_level`, phase 2). There is deliberately no second copy to
disagree with the first, and asking for the character at 3rd level is the same
operation as asking for them at 20th.

Slugs, not names
----------------
Every reference to rules content is a slug from the `entries` table. Pathbuilder
records names, which is why resolving a build means fuzzy-matching English
strings and why two different things sharing a name is an unfixable ambiguity
there. A slug is exact.

Why YAML, and why ruamel
------------------------
These files carry prose -- backstory, a party introduction, a paragraph on why
one feat beat another -- and JSON's single-line escaped strings make that
unreadable and undiffable. They are also edited by hand *and* written by the
server, so a load/dump cycle that discarded comments and reordered keys would
quietly destroy a player's own annotations the first time a tool touched the
file. `ruamel.yaml` in round-trip mode preserves both.

Validation is two layers
------------------------
`validate_document` runs a structural pass (JSON Schema, in
`character_schema.json`) and then semantic checks that a schema cannot express
-- levels in order and not duplicated, `currentLevel` covered by the plan,
slugs that actually resolve against the rules database. Structural failures are
reported and stop there: there is no point telling someone their feat does not
exist when the real problem is that `plan` is a string.

The same two-layer split is applied to chronicle logs, whose schema was written
first and served to callers but never actually enforced (see
`chronicle.validate_chronicle_log`).
"""

from __future__ import annotations

import json
import sqlite3
from io import StringIO
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from ruamel.yaml import YAML

from . import chronicle

SCHEMA_PATH = Path(__file__).parent / "character_schema.json"

#: Current `schemaVersion` of a character file.
SCHEMA_VERSION = 1

#: The six attributes, in the order sheets print them.
ATTRIBUTES = ("str", "dex", "con", "int", "wis", "cha")

#: Proficiency rank names, in ascending order, indexed by the project's
#: 0/2/4/6/8 convention divided by two.
RANK_NAMES = ("untrained", "trained", "expert", "master", "legendary")

#: Rank name -> the project's numeric convention (Foundry's 0-4 doubled, which
#: is how `proficiencies` values are stored everywhere else in this server).
RANK_VALUES = {name: i * 2 for i, name in enumerate(RANK_NAMES)}

#: Which rules-database pack a choice's `pick` should be found in, by slot.
#: `skillIncrease` and `skillTraining` name skills rather than entries and so
#: are absent; `other` is deliberately unchecked.
_SLOT_PACKS: dict[str, tuple[str, ...]] = {
    "classFeat": ("feats", "class-features"),
    "ancestryFeat": ("feats",),
    "generalFeat": ("feats",),
    "skillFeat": ("feats",),
    "archetypeFeat": ("feats",),
    "subclass": ("class-features",),
    "spell": ("spells",),
}


# --------------------------------------------------------------- YAML I/O


def _yaml() -> YAML:
    """A round-trip YAML handler configured the way these files are written.

    Block style throughout (`default_flow_style=False`) so a diff of a changed
    feat is one line, and a generous line width because wrapping prose at 80
    columns mid-sentence makes the *next* edit's diff touch every line of the
    paragraph.
    """
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load(path: str | Path) -> Any:
    """Read a character file, preserving comments and key order.

    The returned object is a `ruamel` round-trip mapping. It behaves as a dict
    -- and is one, for every purpose in this server -- but carries the
    formatting of the file it came from, so passing it back to `save` writes
    something a person would recognise as their own file.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No character file at {path}")
    with path.open(encoding="utf-8") as handle:
        return _yaml().load(handle)


def save(path: str | Path, document: Any) -> Path:
    """Write a character file, creating parent directories as needed.

    Writes through a temporary file in the same directory and replaces the
    original only once the write has succeeded. A character file is the single
    copy of everything about that character -- these files are not in version
    control -- so a half-written one is not an acceptable failure mode.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            _yaml().dump(document, handle)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def dumps(document: Any) -> str:
    """Serialize a character document to a YAML string."""
    buffer = StringIO()
    _yaml().dump(document, buffer)
    return buffer.getvalue()


def loads(text: str) -> Any:
    """Parse a character document from a YAML string."""
    return _yaml().load(text)


def to_plain(value: Any) -> Any:
    """Strip ruamel's round-trip wrappers, leaving plain dicts, lists and scalars.

    Tool results cross a JSON boundary, and ruamel's comment-carrying subclasses
    do not survive it. Everything returned to a caller goes through here.
    """
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


# ---------------------------------------------------------------- schema


def load_schema() -> dict[str, Any]:
    """The JSON Schema describing a character file."""
    return json.loads(SCHEMA_PATH.read_text())


def _registry() -> Registry:
    """Both schemas, so the character schema's cross-file `$ref`s resolve.

    `organizedPlay` embeds chronicle entries by referencing
    `chronicle_schema.json`'s `$defs` rather than restating them, which keeps
    one definition of a chronicle sheet for both the embedded and the
    standalone form.
    """
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in (load_schema(), chronicle.load_schema())
    ]
    return Registry().with_resources(resources)


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=_registry())


def _pointer(error: Any) -> str:
    """A JSON Pointer naming the field an error is about, for the report."""
    return "/" + "/".join(str(part) for part in error.absolute_path)


def check_structure(document: Any) -> list[dict[str, Any]]:
    """Validate a document against the schema. Returns findings, never raises.

    Findings use the same shape as `chronicle._issue` -- level, code, message,
    plus a `path` naming the offending field -- because a caller fixing a file
    needs to be told where the problem is, and a bare English sentence does not
    say that.
    """
    document = to_plain(document)
    validator = _validator(load_schema())
    issues: list[dict[str, Any]] = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
        issues.append({
            "level": "error",
            "code": "schema",
            "path": _pointer(error) or "/",
            "message": error.message,
        })
    return issues


def check_chronicle_structure(log: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a standalone chronicle log against its own schema.

    `chronicle_schema.json` has been served to callers since it was written but
    never applied to anything; `validate_chronicle_log` checks arithmetic and
    absorbs shape errors with `.get(...) or {}`. This is the missing structural
    pass, and it runs before the semantic one so that a malformed file is
    reported as malformed rather than as a ledger that does not add up.
    """
    validator = _validator(chronicle.load_schema())
    issues: list[dict[str, Any]] = []
    for error in sorted(validator.iter_errors(log), key=lambda e: list(e.absolute_path)):
        issues.append({
            "level": "error",
            "code": "schema",
            "path": _pointer(error) or "/",
            "message": error.message,
        })
    return issues


# ------------------------------------------------------------- semantics


def _issue(level: str, code: str, path: str, message: str) -> dict[str, Any]:
    return {"level": level, "code": code, "path": path, "message": message}


def _plan_levels(document: dict[str, Any]) -> list[int]:
    return [entry.get("level") for entry in document.get("plan") or []]


def _check_plan_shape(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Levels ascending, not repeated, and covering where the character is."""
    issues: list[dict[str, Any]] = []
    levels = _plan_levels(document)

    seen: set[int] = set()
    for index, level in enumerate(levels):
        if level in seen:
            issues.append(_issue(
                "error", "duplicate_level", f"/plan/{index}",
                f"Level {level} appears more than once in the plan.",
            ))
        seen.add(level)

    if levels != sorted(levels):
        issues.append(_issue(
            "error", "plan_order", "/plan",
            "Plan levels are not in ascending order.",
        ))

    current = (document.get("identity") or {}).get("currentLevel")
    if isinstance(current, int):
        missing = [n for n in range(1, current + 1) if n not in seen]
        if missing:
            issues.append(_issue(
                "warning", "plan_gap", "/plan",
                f"The character is level {current} but the plan has no entry for "
                f"level{'s' if len(missing) > 1 else ''} "
                f"{', '.join(str(n) for n in missing)}. Their state at the "
                f"current level cannot be fully derived.",
            ))
    return issues


def _check_boost_sources(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Two boosts from one source may never go to the same attribute.

    This is the rule most often got wrong, in both directions: an ancestry's
    fixed pair and its free boost are one source, and so are a background's
    named-or-free pair. Also flags a level's four free boosts overlapping, and
    boosts recorded at a level that has none.
    """
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(document.get("plan") or []):
        boosts = entry.get("attributeBoosts")
        if not boosts:
            continue
        level = entry.get("level")
        path = f"/plan/{index}/attributeBoosts"

        ancestry = boosts.get("ancestry") or {}
        combined = list(ancestry.get("boosts") or []) + list(ancestry.get("free") or [])
        for group, values in (
            ("ancestry", combined),
            ("background", list(boosts.get("background") or [])),
            ("free", list(boosts.get("free") or [])),
        ):
            duplicates = {v for v in values if values.count(v) > 1}
            if duplicates:
                issues.append(_issue(
                    "error", "same_source_boost", f"{path}/{group}",
                    f"{', '.join(sorted(duplicates))} boosted twice by the same "
                    f"source ({group}). A single source's boosts must go to "
                    f"different attributes.",
                ))

        if level not in (1, 5, 10, 15, 20) and boosts.get("free"):
            issues.append(_issue(
                "error", "boosts_off_milestone", f"{path}/free",
                f"Free attribute boosts recorded at level {level}; they are "
                f"granted only at 1st, 5th, 10th, 15th and 20th.",
            ))
        if level != 1 and (ancestry or boosts.get("background") or boosts.get("class")):
            issues.append(_issue(
                "error", "creation_boosts_off_level_one", path,
                f"Ancestry, background or class boosts recorded at level "
                f"{level}; those are applied once, at character creation.",
            ))
    return issues


def _resolve_slugs(
    document: dict[str, Any], conn: sqlite3.Connection
) -> list[dict[str, Any]]:
    """Every slug in the file resolves to a real rules entry.

    A slug that does not resolve is the failure mode this format exists to make
    visible: under the old name-matching approach it degraded into a silent
    fuzzy match onto something else. Reported as an error for build content and
    as a warning for gear, where free text is legitimate on the wishlist.
    """
    issues: list[dict[str, Any]] = []

    def known(slug: str, packs: tuple[str, ...] | None = None) -> bool:
        if packs:
            marks = ",".join("?" * len(packs))
            row = conn.execute(
                f"SELECT 1 FROM entries WHERE slug = ? AND pack IN ({marks}) LIMIT 1",
                (slug, *packs),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM entries WHERE slug = ? LIMIT 1", (slug,)
            ).fetchone()
        return row is not None

    def check(slug: Any, path: str, packs: tuple[str, ...] | None,
              level: str = "error") -> None:
        if not isinstance(slug, str) or known(slug, packs):
            return
        hint = ""
        if packs and known(slug):
            hint = (" It exists, but not as "
                    + ("a " + packs[0].rstrip("s") if len(packs) == 1
                       else "one of " + ", ".join(packs)) + ".")
        issues.append(_issue(
            level, "unknown_slug", path,
            f"No rules entry with slug {slug!r}.{hint}",
        ))

    build = document.get("build") or {}
    for field, packs in (
        ("ancestry", ("ancestries",)),
        ("heritage", ("heritages",)),
        ("background", ("backgrounds",)),
        ("class", ("classes",)),
        ("deity", ("deities",)),
    ):
        check(build.get(field), f"/build/{field}", packs)

    for tag, value in (build.get("subclasses") or {}).items():
        picks = value if isinstance(value, list) else [value]
        for i, pick in enumerate(picks):
            suffix = f"/{i}" if isinstance(value, list) else ""
            check(pick, f"/build/subclasses/{tag}{suffix}", ("class-features",))

    for index, entry in enumerate(document.get("plan") or []):
        for c_index, choice in enumerate(entry.get("choices") or []):
            slot = choice.get("slot")
            packs = _SLOT_PACKS.get(slot)
            if packs is None:
                continue
            picks = choice.get("pick")
            picks = picks if isinstance(picks, list) else [picks]
            for p_index, pick in enumerate(picks):
                suffix = f"/{p_index}" if isinstance(choice.get("pick"), list) else ""
                check(pick, f"/plan/{index}/choices/{c_index}/pick{suffix}", packs)
        for a_index, slug in enumerate(entry.get("automatic") or []):
            check(slug, f"/plan/{index}/automatic/{a_index}", None, level="warning")

    for index, item in enumerate((document.get("gear") or {}).get("carried") or []):
        check(item.get("item"), f"/gear/carried/{index}/item", ("equipment",),
              level="warning")
        for r_index, rune in enumerate(item.get("runes") or []):
            check(rune, f"/gear/carried/{index}/runes/{r_index}", ("equipment",),
                  level="warning")

    spellcasting = document.get("spellcasting") or {}
    for e_index, entry in enumerate(spellcasting.get("entries") or []):
        for rank, spells in (entry.get("spells") or {}).items():
            for s_index, slug in enumerate(spells or []):
                check(slug, f"/spellcasting/entries/{e_index}/spells/{rank}/{s_index}",
                      ("spells",))
    for f_index, slug in enumerate(spellcasting.get("focusSpells") or []):
        check(slug, f"/spellcasting/focusSpells/{f_index}", ("spells",))

    return issues


def _check_dependencies(document: dict[str, Any]) -> list[dict[str, Any]]:
    """A choice's `dependsOn` must name picks the plan actually contains.

    Two ways this goes wrong, both of which quietly invalidate the reason a
    pick is in the build. The named pick may not be in the plan at all --
    usually because it was retrained away and the choice that existed to serve
    it was left behind. Or it may be taken *later* than the choice depending on
    it, which is the ordering error a prose note can state without anyone
    noticing it is impossible.
    """
    issues: list[dict[str, Any]] = []
    at_level: dict[str, int] = {}
    for entry in document.get("plan") or []:
        level = entry.get("level")
        for choice in entry.get("choices") or []:
            picks = choice.get("pick")
            for pick in (picks if isinstance(picks, list) else [picks]):
                if isinstance(pick, str) and isinstance(level, int):
                    at_level.setdefault(pick, level)

    for index, entry in enumerate(document.get("plan") or []):
        level = entry.get("level")
        for c_index, choice in enumerate(entry.get("choices") or []):
            for d_index, needed in enumerate(choice.get("dependsOn") or []):
                path = f"/plan/{index}/choices/{c_index}/dependsOn/{d_index}"
                if needed not in at_level:
                    issues.append(_issue(
                        "warning", "dangling_dependency", path,
                        f"This choice depends on {needed!r}, which the plan does "
                        f"not contain. If it was retrained away, this choice may "
                        f"no longer have a reason to be here.",
                    ))
                elif isinstance(level, int) and at_level[needed] > level:
                    issues.append(_issue(
                        "error", "dependency_ordering", path,
                        f"This choice at level {level} depends on {needed!r}, "
                        f"which is not taken until level {at_level[needed]}.",
                    ))
    return issues


def _check_overrides(document: dict[str, Any]) -> list[dict[str, Any]]:
    """A proficiency override must name a plausible proficiency."""
    issues: list[dict[str, Any]] = []
    known = set(RANK_NAMES)
    for name, override in (document.get("proficiencyOverrides") or {}).items():
        rank = (override or {}).get("rank")
        if rank not in known:
            issues.append(_issue(
                "error", "bad_rank", f"/proficiencyOverrides/{name}/rank",
                f"{rank!r} is not a proficiency rank.",
            ))
    return issues


def _check_organized_play(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Cross-check the Organized Play block against the build it now lives with.

    Worth doing precisely because it was impossible before: the chronicle log
    used to be a separate file that no code path ever read alongside the
    character, so a ledger implying 5th level while the build said 3rd went
    unnoticed indefinitely.
    """
    issues: list[dict[str, Any]] = []
    op = document.get("organizedPlay")
    if not op:
        return issues

    identity = document.get("identity") or {}
    current = identity.get("currentLevel")
    starting = op.get("startingLevel", 1)
    if isinstance(current, int) and isinstance(starting, int) and current < starting:
        issues.append(_issue(
            "error", "level_below_start", "/identity/currentLevel",
            f"The character is recorded at level {current} but was created at "
            f"level {starting}. A character cannot be below their starting level.",
        ))

    entries = op.get("chronicles") or []
    if entries:
        highest = max(
            (e.get("characterLevel") or 0) for e in entries
        )
        if isinstance(current, int) and highest > current:
            issues.append(_issue(
                "warning", "chronicle_above_level", "/organizedPlay/chronicles",
                f"A chronicle records play at level {highest}, above the "
                f"character's recorded level of {current}.",
            ))
    return issues


def validate_document(
    document: Any, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Check a character file, structurally and then semantically.

    Returns `{valid, errors, warnings, issues}`, where `issues` carries the
    detail and each one names the field it is about. Structural failure stops
    the run: semantic checks assume a well-formed document, and reporting that
    a feat does not exist is noise when the real problem is that `plan` is a
    string.

    This checks the *file*. It does not check that the build is legal --
    prerequisites, feat budgets, skill caps and Organized Play legality are
    `build_validate_build`'s job, and run against the state this document
    replays to.
    """
    plain = to_plain(document)
    issues = check_structure(plain)

    if not issues:
        issues += _check_plan_shape(plain)
        issues += _check_boost_sources(plain)
        issues += _check_overrides(plain)
        issues += _check_dependencies(plain)
        issues += _check_organized_play(plain)
        if conn is not None:
            issues += _resolve_slugs(plain, conn)

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    return {
        "valid": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "issues": issues,
    }


# --------------------------------------------------------------- front door


def is_native(character: Any) -> bool:
    """Whether a dict is a character document rather than a Pathbuilder build.

    `schemaVersion` is the discriminator: it is required in the native format
    and appears in no Pathbuilder export.
    """
    return isinstance(character, dict) and "schemaVersion" in character


def as_legacy(
    character: Any, level: int | None = None, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Whatever a caller passed, as the Pathbuilder-shaped dict tools expect.

    A native character document is replayed to `level`; a Pathbuilder envelope
    is unwrapped; a bare build is returned as it came. This is the single
    adaptation point that lets every existing tool accept the new format
    without any of them being rewritten to read it.

    Opens its own database connection when replaying and none is supplied,
    matching how the rest of `build_tools` handles a lookup.
    """
    if is_native(character):
        from . import character_replay

        if conn is not None:
            return character_replay.at_level(character, level, conn)
        from .db import get_connection

        owned = get_connection()
        try:
            return character_replay.at_level(character, level, owned)
        finally:
            owned.close()
    if isinstance(character, dict):
        return character.get("build", character)
    return character
