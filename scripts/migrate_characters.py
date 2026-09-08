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
            report["dropped"].append(heading)
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
        report["markdown"] = _apply_markdown(document, markdown.read_text())

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
            print(
                f"{path.stem[:30]:30s} {overrides:2d} overrides  "
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
