"""One-off migration of characters/ into the native format.

Reads each character's Pathbuilder JSON, its markdown design document and its
chronicle sidecar, and writes a single `<Name>.yaml`. Deletes nothing: run it,
read the report, check a few files, and remove the old ones yourself.

The structured half converts cleanly -- identity, the plan reconstructed from
the feat tuples, gear, and the chronicle log embedded verbatim. The narrative
half cannot. The markdown files come in two shapes (the builder skill's
`## Level N` / `### Automatic` / `### Choices` layout, and a freer one), and
per-level rationale is written as prose bullets that no parser should pretend
to understand. So sections are carried across whole:

- Headings that clearly hold narrative -- Background, Backstory, Concept,
  Introduction, Roleplaying -- go into the matching `story` field.
- `## Level N` sections are dropped, because the plan already carries that
  information structurally and keeping both would guarantee they diverge.
  Their *reasoning* is the loss, and lifting it into each choice's `note` is
  the hand-finishing step this script deliberately leaves to a person.
- Sections about the tooling -- "Tool caveats", "Rules-legality notes",
  "Known tool limitations" -- are dropped and listed in the report instead.
  Per AGENTS.md those belong in the issue tracker, where they can be closed.
- Everything else becomes a `notes[]` entry, heading and body intact.

Usage:
    python scripts/migrate_characters.py [--out DIR] [--only NAME]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pf2e_mcp.server import character, character_import, character_replay  # noqa: E402
from pf2e_mcp.server.db import get_connection  # noqa: E402

CHARACTERS = Path("characters")

#: Markdown headings whose content is narrative, and the `story` field each
#: maps onto. Matched on a lowercased, stripped heading.
_STORY_HEADINGS = {
    "background": "backstory",
    "backstory": "backstory",
    "concept": "concept",
    "introduction": "introduction",
    "roleplaying": "roleplaying",
    "appearance": "appearance",
}

#: Headings whose content is about this project's tools rather than the
#: character. Reported for triage into the issue tracker, not migrated.
_TOOLING_PATTERNS = (
    "tool caveat", "known tool", "tool/data", "rules-legality",
    "known limits", "caveats and open items", "tool caveats",
)

#: `## Level 7` and friends -- superseded by the structured plan.
_LEVEL_HEADING = re.compile(r"^level\s+\d+", re.IGNORECASE)

#: A heading naming a level, at either depth. The builder skill writes `## Level
#: 7`; a couple of files nest them under `### Level 7` instead.
_LEVEL_ANY = re.compile(r"^#{2,3}\s+Level\s+(?P<level>\d+)", re.IGNORECASE)

#: A choice bullet inside a level section:
#:     - **Skill feat: Titan Wrestler.** Attempt Disarm, Grapple...
#:     - **Class feat: Dirge of Doom** -- ***the keystone.*** Composition...
#: The slot label before the colon is discarded -- the plan already knows which
#: slot a pick filled -- and the name is used only to find the choice this
#: rationale belongs to.
_CHOICE_BULLET = re.compile(
    r"^-\s+\*\*(?P<slot>[A-Za-z][A-Za-z' ]*?)"
    r"(?:\s*\([^)]*\))?\s*:\s*(?P<name>[^*]+?)\*\*[.,]?\s*(?P<rest>.*)$"
)

#: A third format, used by a couple of files: no list bullet, and the pick's
#: name in a bold run of its own.
#:     **Class feat:** **Cantrip Expansion** -- one additional cantrip slot.
_CHOICE_BOLD = re.compile(
    r"^\*\*(?P<slot>[A-Za-z][A-Za-z' ]*?)"
    r"(?:\s*\([^)]*\))?:\*\*\s*\*\*(?P<name>[^*]+?)\*\*[.,]?\s*(?P<rest>.*)$"
)

#: Bullets whose "slot" is really a heading for a list of skills, languages or
#: boosts. All of that is structured in the plan already.
_NOT_A_CHOICE = ("skill", "language", "ability", "attribute", "boost", "automatic")


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Split a markdown document into (heading, body) at `##` boundaries.

    Only `##` -- `###` subsections stay inside their parent's body, which keeps
    a section that reads as one piece in one piece.
    """
    out: list[tuple[str, str]] = []
    # Text before the first `##` is the file's opening thesis -- often the only
    # statement of what the character is for. Kept under a synthetic heading so
    # it lands in `story.concept` rather than being dropped.
    heading, body = "Concept", []
    for line in markdown.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            continue
        if line.startswith("## ") and not line.startswith("### "):
            if heading is not None:
                out.append((heading, "\n".join(body).strip()))
            heading, body = line[3:].strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        out.append((heading, "\n".join(body).strip()))
    return out


def _level_rationale(markdown: str) -> dict[int, dict[str, str]]:
    """Per-pick rationale out of the `## Level N` sections, keyed by level.

    The reason to parse rather than drop these: they are the only record of
    *why* a build looks the way it does, and the schema has had a `note` on
    every choice since it was written. Dropping the sections wholesale, as the
    first migration did, threw that away while leaving the field empty.

    Bullets under `### Automatic` are skipped -- those describe features the
    class grants, which replay derives -- as are bullets whose label is really
    a heading for a list of skills, languages or attribute boosts, all of which
    the plan already carries structurally.
    """
    out: dict[int, dict[str, str]] = {}
    level: int | None = None
    automatic = False
    continuation: tuple[int, str] | None = None

    for line in markdown.splitlines():
        heading = _LEVEL_ANY.match(line)
        if heading:
            level, automatic, continuation = int(heading.group("level")), False, None
            continue
        if line.startswith("#"):
            automatic = "automatic" in line.lower()
            continuation = None
            continue
        if level is None or automatic:
            continue

        match = _CHOICE_BULLET.match(line) or _CHOICE_BOLD.match(line)
        if match:
            slot = match.group("slot").strip().lower()
            if any(word in slot for word in _NOT_A_CHOICE) and "feat" not in slot:
                continuation = None
                continue
            name = match.group("name").strip().rstrip(".")
            rest = match.group("rest").strip()
            out.setdefault(level, {})[name.lower()] = rest
            continuation = (level, name.lower()) if rest else None
        elif continuation and line.strip() and not line.startswith(("|", "**", "-")):
            # A bullet wrapped over several lines; keep the whole paragraph.
            out[continuation[0]][continuation[1]] += " " + line.strip()
        elif not line.strip():
            continuation = None
    return out


def _attach_rationale(document: dict, rationale: dict[int, dict[str, str]], conn) -> int:
    """Match parsed rationale onto the choices it explains.

    Matched by name at the same level, since that is what the markdown records.
    A bullet that matches nothing is left behind rather than guessed at -- the
    report says how many, and those are the ones worth a human's eye.
    """
    from pf2e_mcp.server.character_replay import _name_of

    attached = 0
    for entry in document.get("plan") or []:
        level = entry.get("level")
        by_name = rationale.get(level) or {}
        if not by_name:
            continue
        for choice in entry.get("choices") or []:
            if choice.get("note"):
                continue
            picks = choice.get("pick")
            picks = picks if isinstance(picks, list) else [picks]
            for pick in picks:
                if not isinstance(pick, str):
                    continue
                for candidate in (
                    _name_of(conn, pick, "feats").lower(),
                    pick.replace("-", " "),
                ):
                    if candidate in by_name:
                        choice["note"] = by_name.pop(candidate)
                        attached += 1
                        break
                else:
                    continue
                break
    return attached


def _apply_markdown(document: dict, markdown: str) -> dict[str, list[str]]:
    """Fold a markdown design document into story, notes and openItems."""
    report: dict[str, list[str]] = {"story": [], "notes": [], "dropped": [], "tooling": []}
    story: dict[str, str] = {}
    notes: list[dict[str, str]] = []
    open_items: list[str] = []

    for heading, body in _sections(markdown):
        if not body:
            continue
        key = heading.lower().strip().rstrip(":")
        base = re.split(r"\s*[-(]", key, maxsplit=1)[0].strip()

        if any(pattern in key for pattern in _TOOLING_PATTERNS):
            report["tooling"].append(heading)
            continue
        if _LEVEL_HEADING.match(key):
            # Handled separately by _level_rationale; the structured plan
            # already carries everything else these sections say.
            continue
        if base in _STORY_HEADINGS:
            field = _STORY_HEADINGS[base]
            story[field] = f"{story[field]}\n\n{body}" if field in story else body
            report["story"].append(f"{heading} -> story.{field}")
            continue
        if base.startswith("open item"):
            open_items += [
                re.sub(r"^[-*]\s+", "", line).strip()
                for line in body.splitlines()
                if line.strip().startswith(("-", "*"))
            ]
            report["notes"].append(f"{heading} -> openItems")
            continue
        notes.append({"heading": heading, "body": body})
        report["notes"].append(heading)

    if story:
        document["story"] = story
    if notes:
        document["notes"] = notes
    if open_items:
        document["openItems"] = open_items
    return report


def _apply_chronicles(document: dict, log: dict) -> None:
    """Embed a chronicle sidecar as the organizedPlay block.

    `schemaVersion` and `character` are dropped: both existed only to pair a
    standalone file with a character, and the file is now inside that
    character.
    """
    document["organizedPlay"] = {
        key: value for key, value in log.items()
        if key not in ("schemaVersion", "character") and value not in (None, [], {})
    }


def migrate(path: Path, out_dir: Path, conn) -> dict:
    name = path.stem
    source = json.loads(path.read_text())
    document = character_import.from_pathbuilder(source, conn)
    report = document.pop("_import")

    markdown = path.with_suffix(".md")
    if markdown.exists():
        text = markdown.read_text()
        report["markdown"] = _apply_markdown(document, text)
        rationale = _level_rationale(text)
        found = sum(len(v) for v in rationale.values())
        attached = _attach_rationale(document, rationale, conn)
        report["rationale"] = {"found": found, "attached": attached}

    chronicles = path.with_name(f"{name}.chronicles.json")
    if chronicles.exists():
        _apply_chronicles(document, json.loads(chronicles.read_text()))
        report["chronicles"] = "embedded"

    validation = character.validate_document(document, conn)
    report["valid"] = validation["valid"]
    report["issues"] = [i for i in validation["issues"] if i["level"] == "error"]

    # Prove the file replays before it is trusted with anything.
    try:
        character_replay.at_level(document, None, conn)
        report["replays"] = True
    except Exception as exc:  # noqa: BLE001
        report["replays"] = False
        report["replay_error"] = str(exc)

    target = out_dir / f"{name}.yaml"
    character.save(target, document)
    report["written"] = str(target)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(CHARACTERS), type=Path)
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    paths = sorted(
        p for p in CHARACTERS.glob("*.json")
        if ".chronicles" not in p.name and " - Level " not in p.name
        and (args.only is None or args.only.lower() in p.stem.lower())
    )
    if not paths:
        print("Nothing to migrate.")
        return 1

    conn = get_connection()
    tooling: dict[str, list[str]] = {}
    failures = []
    try:
        for path in paths:
            report = migrate(path, args.out, conn)
            flags = []
            if not report["valid"]:
                flags.append(f"INVALID ({len(report['issues'])} errors)")
            if not report["replays"]:
                flags.append("REPLAY FAILED")
            if report["unresolved"]:
                flags.append(f"{len(report['unresolved'])} unresolved")
            if report.get("attribute_mismatch"):
                flags.append("attribute mismatch")
            overrides = len(report.get("stated_proficiencies") or [])
            r = report.get("rationale") or {}
            notes = (
                f"{r['attached']:3d}/{r['found']:<3d} notes  " if r else " " * 13
            )
            print(
                f"{path.stem[:26]:26s} {overrides:2d} ovr  {notes}"
                f"{'; '.join(flags) if flags else 'clean'}"
            )
            for issue in report["issues"]:
                print(f"    ! {issue['path']}: {issue['message'][:100]}")
            for line in report["unresolved"]:
                print(f"    ? {line}")
            md = report.get("markdown") or {}
            if md.get("tooling"):
                tooling[path.stem] = md["tooling"]
            if flags:
                failures.append(path.stem)
    finally:
        conn.close()

    if tooling:
        print("\nTooling sections NOT migrated -- triage into GitHub issues:")
        for name, headings in tooling.items():
            for heading in headings:
                print(f"  {name}: {heading}")

    print(f"\n{len(paths)} migrated, {len(failures)} needing attention.")
    print("Nothing was deleted. Review the .yaml files before removing the old ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
