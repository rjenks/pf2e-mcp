"""Phase 0 spike: how parseable are PF2e feat prerequisite strings?

Reads packs/feats.json (and companion class-features/ancestry-features packs,
which use the same prerequisites shape) from the official json-assets.zip
release bundle, extracts every prerequisite string, buckets it against a set
of candidate regex patterns, and reports what fraction is auto-parseable vs.
left over for manual curation.

This does not persist anything -- it's a one-off measurement to de-risk the
`list_available_feats` design before writing real ingestion code.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

PATTERNS = [
    ("skill_rank", re.compile(
        r"^(trained|expert|master|legendary) in ([\w'()\- ]+?)(?: or (\w[\w'()\- ]*))*$",
        re.IGNORECASE,
    )),
    ("ability_score", re.compile(
        r"^(strength|dexterity|constitution|intelligence|wisdom|charisma) (\+\d+|\d+ or higher)$",
        re.IGNORECASE,
    )),
    ("character_level", re.compile(r"^(character )?level \d+$", re.IGNORECASE)),
]

# Recognizable connective scaffolding that, once stripped, often still leaves
# an unparsed remainder (multiple prereqs, "or" chains, etc.) -- tracked
# separately so we can see how much of the tail is *structurally* compound
# rather than just a pattern we haven't written yet.
COMPOUND_HINTS = re.compile(r"\bor\b|\band\b|;", re.IGNORECASE)


def load_name_index(data_dir: Path) -> set[str]:
    """Every name that could plausibly be referenced as a 'named X' prerequisite:
    feats, class features, ancestry features, heritages -- lowercased for
    case-insensitive matching, since prereq text capitalization is inconsistent
    (e.g. 'spirit instinct' vs 'Spirit Instinct')."""
    packs = ["feats.json", "class-features.json", "ancestry-features.json",
             "heritages.json", "kingmaker-features.json", "backgrounds.json",
             "ancestries.json", "actions.json", "spells.json",
             "pathfinder-society-boons.json"]
    names = set()
    for pack in packs:
        path = data_dir / "packs" / pack
        if not path.exists():
            continue
        for item in json.loads(path.read_text()):
            names.add(item["name"].strip().lower())
    return names


def classify(text: str, name_index: set[str]) -> str:
    t = text.strip()
    for name, pat in PATTERNS:
        if pat.match(t):
            return name
    if t.lower() in name_index:
        return "named_reference (verified)"
    return "unmatched"


LIST_SPLIT = re.compile(r",\s*(?:or\s+)?|\s+or\s+")


def try_compound_named(text: str, name_index: set[str]) -> bool:
    """Handle PF2e's common shared-suffix list phrasing, e.g.
    'Acrobat, Celebrity, Dandy, or Gladiator Dedication' == each of
    {Acrobat, Celebrity, Dandy, Gladiator} + ' Dedication'. Returns True if
    every item in the list resolves to a known name, either as-is or with
    the trailing word(s) from the last item appended."""
    t = text.strip()
    if not COMPOUND_HINTS.search(t):
        return False
    parts = [p.strip() for p in LIST_SPLIT.split(t) if p.strip()]
    if len(parts) < 2:
        return False
    last = parts[-1]
    # does the last part carry a shared suffix, e.g. "Gladiator Dedication"?
    last_words = last.split()
    for cut in range(len(last_words) - 1, 0, -1):
        suffix = " ".join(last_words[cut:])
        candidates = [p if p is last else f"{p} {suffix}" for p in parts]
        if all(c.strip().lower() in name_index for c in candidates):
            return True
    # no shared suffix: check each part verbatim
    return all(p.lower() in name_index for p in parts)


def main(data_dir: Path):
    feats = json.loads((data_dir / "packs" / "feats.json").read_text())
    name_index = load_name_index(data_dir)
    print(f"Loaded {len(name_index)} known feat/feature/heritage names for cross-reference.\n")

    all_prereqs = []
    feats_with_prereqs = 0
    for feat in feats:
        prereqs = feat.get("system", {}).get("prerequisites", {}).get("value", [])
        if prereqs:
            feats_with_prereqs += 1
        for p in prereqs:
            all_prereqs.append(p.get("value", "").strip())

    print(f"Total feats: {len(feats)}")
    print(f"Feats with >=1 prerequisite entry: {feats_with_prereqs}")
    print(f"Total prerequisite strings: {len(all_prereqs)}")
    print(f"Distinct prerequisite strings: {len(set(all_prereqs))}")
    print()

    classified = Counter(classify(p, name_index) for p in all_prereqs)
    matched = sum(v for k, v in classified.items() if k != "unmatched")
    print("=== Classification (single-value patterns + verified name lookup) ===")
    for name, count in classified.most_common():
        pct = 100 * count / len(all_prereqs)
        print(f"  {name:28s} {count:5d}  ({pct:5.1f}%)")
    print(f"\nAuto-parseable so far: {matched}/{len(all_prereqs)} "
          f"({100*matched/len(all_prereqs):.1f}%)")

    unmatched = [p for p in all_prereqs if classify(p, name_index) == "unmatched"]
    compound_resolved = [p for p in unmatched if try_compound_named(p, name_index)]
    still_unmatched = [p for p in unmatched if p not in compound_resolved]

    print(f"\nOf the {len(unmatched)} initially unmatched, "
          f"{len(compound_resolved)} resolve as compound OR-lists of known names "
          f"(e.g. shared-suffix dedication lists).")
    total_parseable = matched + len(compound_resolved)
    print(f"Combined auto-parseable estimate: {total_parseable}/{len(all_prereqs)} "
          f"({100*total_parseable/len(all_prereqs):.1f}%)")

    compound_unresolved = [p for p in still_unmatched if COMPOUND_HINTS.search(p)]
    simple_unmatched = [p for p in still_unmatched if p not in compound_unresolved]
    print(f"\nRemaining unparseable tail: {len(still_unmatched)} "
          f"({100*len(still_unmatched)/len(all_prereqs):.1f}%)")
    print(f"  of which still look compound but didn't resolve: {len(compound_unresolved)}")
    print(f"  simple (non-compound) leftovers: {len(simple_unmatched)}")

    print("\n=== Sample of remaining simple (non-compound) unmatched strings ===")
    for p in sorted(set(simple_unmatched))[:40]:
        print(f"  - {p!r}")

    print("\n=== Sample of remaining unresolved compound strings ===")
    for p in sorted(set(compound_unresolved))[:20]:
        print(f"  - {p!r}")


if __name__ == "__main__":
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else
                     "/tmp/claude-1000/-home-rjenks-Projects-pf2e-mcp/"
                     "67c3ba1a-a186-4648-b45c-f5bf9b4eb43c/scratchpad/pf2e-data/extracted")
    main(data_dir)
