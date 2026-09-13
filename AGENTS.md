# Overview

This project is to build an MCP for Pathfinder 2nd Edition Remastered MCP server

This project can also be used to build characters which should be stored in pathbuilder compatible JSON files in the characters/ folder.

Please rely on the guides referenced from Zenith Games Guide to the Guides and don't always trust RPGBOT. https://zenithgames.blogspot.com/2019/09/pathfinder-2nd-edition-guide-to-guides.html

# Running Tests

Always use `uv run` to execute pytest:

```bash
uv run pytest
```

# Track bugs and gaps as GitHub issues, not a markdown file

Known bugs, ingestion gaps, and deferred work belong in this repo's
[GitHub issues](https://github.com/rjenks/pf2e-mcp/issues), not in a
`KNOWN_ISSUES.md`-style file. A markdown file like that one drifts out of
sync with reality, can't be assigned/labeled/closed independently, and
isn't searchable the way an issue tracker is — that file existed once and
was retired for exactly this reason (its ~30 entries became GitHub issues
instead). File a new issue for anything discovered during a task rather
than appending to a doc; label it `bug` for something producing wrong
output, `enhancement` for missing coverage/deferred scope, or
`documentation` for a caveat worth recording so it doesn't get
rediscovered as a "bug" later.

**Every commit ties back to at least one GitHub issue, so the _why_ stays
attached to the code and not just the _what_.** A diff already says what
changed; the issue is what says why it was worth changing — what was
broken, what gap it closed, who'd hit it. Before committing:

- If an issue already covers the change (a bug being fixed, a gap being
  closed), reference it in the commit message body with `Refs #N`, or
  `Fixes #N`/`Closes #N` when the commit fully resolves it — that syntax
  auto-closes the issue on merge to the default branch, so use `Refs #N`
  instead if the commit only partially addresses it or the issue should
  stay open for follow-up.
- If no issue exists yet — a new feature, a refactor, a fix for something
  noticed mid-task — file one first (see above) describing the _why_, then
  reference it the same way. This is true even for small changes; a
  one-line issue body beats no record of the motivation at all.
- Routine maintenance with no real "why" beyond the obvious (a dependency
  bump, a data re-ingestion re-run, a typo fix) doesn't need an issue —
  use judgment, but default to filing one if there's any real decision or
  motivation behind the change worth preserving.

**Never reference a specific character by name in an issue, commit message
touching `characters/`, or anywhere else meant for other users** — the
files in `characters/` are the user's personal player characters,
including ones from live campaigns. Describe the underlying game mechanic
generically instead: by class, archetype, ancestry, or feature name (all
public game terms, e.g. "a Magus with Dexterity-keyed AC but
Intelligence-based spellcasting," "a Wrestler-archetype character with a
free skill-proficiency grant") rather than by which of the user's
characters exhibited it. This keeps the issue useful to any user hitting
the same gap, not just legible to this one.

# Never query the SQLite database directly; go through the MCP

**Unless you are working on the MCP itself, do not open `.data/pf2e.sqlite`.**
No `sqlite3` on the command line, no `import sqlite3` in a throwaway script, no
reading `raw_json` out of `entries` to answer a rules question. Use the
`rules_*`, `build_*` and `pfs_*` tools.

The reason is not tidiness. A direct query answers the question for _you_ and
for nobody else:

- **It hides gaps.** Every question answered by reaching around the server is a
  missing tool that never gets recorded, because the workaround is faster than
  filing the issue. The tool surface then looks complete while the real
  coverage quietly rots.
- **Real clients have no shell.** An MCP client gets the tools and nothing
  else. A rules answer reachable only via sqlite is an answer this project
  cannot actually give.
- **Query shapes never harden.** The same awkward join gets re-derived, subtly
  differently, every session, instead of becoming a tested function.

So when no existing tool can express what you need:

1. **Add a tool**, or extend one, so the capability exists for every caller.
   File an issue for the gap first, per the section above.
2. **Or use `rules_sql`**, the deliberate read-only escape hatch — `SELECT`
   only, with row, cell-size and VM-step caps, and `schema=True` to discover
   the tables. It exists so that hitting an unforeseen gap does not push you
   outside the MCP entirely.

`rules_sql` is a fallback, not a destination. **A query worth running twice is
a tool worth adding** — when you find yourself reaching for the same shape
again, that is the signal to promote it and file the issue.

The exception is building or debugging the MCP itself: ingestion, schema work,
and writing a tool all legitimately touch the database directly, as does
`tests/conftest.py`'s read-only fixture.

# Talk in attribute modifiers, not attribute scores

**Always say `+4`, never `18`.** The Remaster works in modifiers throughout:
Player Core has each attribute modifier start at +0, a boost "adding 1 to an
attribute modifier" and a flaw subtracting 1. The 10-to-20 score is legacy
notation that survives only because Pathbuilder's export format stores it, and
every rule that consumes an attribute — DCs, saves, AC, spell attack, HP per
level — reads the modifier.

This applies to explanations, tables, character notes in `characters/*.md`,
commit messages and anything else said to a person. Where scores are
unavoidable, they're the secondary form: the `abilities` dict of a Pathbuilder
JSON is scores because that is the file format (don't "fix" it), and the
character sheet prints the score small beside the modifier because a player
occasionally needs it for an item requirement.

Two habits follow from working in modifiers, both of which prevent real
mistakes:

- **Boost arithmetic stays visible.** A boost is +1 to the modifier, except it
  is +2 to a score below 18 — which is the same statement said twice, and the
  modifier form has no special case to forget. Say "Dex +4 becomes +5",
  not "Dex 18 becomes 19, which is still +4".
- **Half-steps become obvious.** In modifier terms there is nothing to notice:
  a boost either raises the modifier or it doesn't, where "19" hides it.
  A half-step is only a _mistake_ at 20th, though -- earlier it is half a step
  that completes at the next milestone, and arrives a milestone sooner than an
  even score would. When a 20th-level array does have one, redirect the
  20th-level boost, since that is the one with nothing after it to finish it.

# Paizo Community Use Package assets

Artwork for character sheets comes from Paizo's Community Use Package. The
package page (https://paizo.com/community/communityuse/package) requires a
Paizo sign-in, but the archives themselves are downloadable directly:

| Pack                         | URL                                                                               |
| ---------------------------- | --------------------------------------------------------------------------------- |
| Logos & Branding             | https://downloads.paizo.com/Logos_and_Branding.zip                                |
| Pathfinder Religious Symbols | https://downloads.paizo.com/Pathfinder_Religious_Symbols.zip                      |
| Pathfinder Organizations     | https://downloads.paizo.com/Pathfinder_Organizations.zip                          |
| Pathfinder Regional Symbols  | https://downloads.paizo.com/Pathfinder_Regional_Symbols.zip                       |
| Pathfinder Runes             | https://downloads.paizo.com/Pathfinder_Runes.zip                                  |
| Pathfinder Maps              | https://downloads.paizo.com/Pathfinder_Maps.zip                                   |
| PF2e Iconic Heroes Portraits | https://downloads.paizo.com/Pathfinder_Second_Edition_Iconic_Heroes_Portraits.zip |

Extracted assets live under `.data/logos/`, which is gitignored — the same
place this project already keeps downloaded third-party content. **Do not commit
these files.** `NOTICE.md` states that the repository contains no Paizo artwork,
and that should stay true; only `sheet_assets.py`'s OFL fonts are vendored.

Files worth knowing about:

- `.data/logos/PF2Logo.png` — the Pathfinder Second Edition wordmark, 500×175
  RGBA, 66 KB. This is the one to pass as `logo_path`.
- `.data/logos/P-mark.png` — the tall "P" mark, 500×684. Portrait aspect, so it
  does not suit the nameplate slot.
- `.data/logos/religious/` — 61 deity symbols named `<Deity>.png` with
  underscores for spaces (`Sun_Wukong.png`). Pass the directory as `symbol_dir`.
- `.data/logos/organizations/`, `regional/`, `runes/` — extracted but not yet
  wired into the sheet renderer; nothing in the character JSON schema records an
  organization, region, or rune affiliation to key them off.

`Logos_and_Branding.zip` is 580 MB and mostly `.tif` plus historic/1E logos;
only a handful of PNGs are useful. Re-download it only if you need something
beyond the two files above.

## Usage rules that constrain the code

Per Paizo's Community Use Policy (https://paizo.com/licenses/communityuse),
which the FAQ confirms permits a Pathfinder logo on free fan material:

- Logos may **not** be altered in colour, typography, design, or proportions.
  Proportional resizing is allowed, so `sheet.py` embeds image bytes verbatim
  and scales with `height` + `width: auto` — never a filter, recolour, crop, or
  blend mode.
- Greyscale renderings are allowed **only if the entire work is greyscale**. The
  sheets are not, so assets go in full colour.
- Only assets from the package may be used; don't substitute a logo found
  elsewhere.
- Every rendered sheet already carries the required Community Use notice, and
  states it is not published, endorsed, or approved by Paizo.
- EPS/AI/TIF files in the packs cannot be embedded in HTML. Use the PNGs.
