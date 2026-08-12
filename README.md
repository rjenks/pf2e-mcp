# pf2e-mcp

An MCP server for building Pathfinder 2nd Edition Remastered characters
together with an AI assistant. Rather than relying on the assistant's own
memory of the rules — which is often incomplete or out of date — it looks
everything up in a local, structured database built from the official
rules data.

That local database also means your AI assistant doesn't need to search
the web or query sites like Archives of Nethys just to answer a rules
question. That keeps character-building conversations fast and
token-efficient, and keeps traffic off those free community resources.

It exposes two tool namespaces over a single local SQLite database:

- `rules_*` — general rules reference (search, get an entry, cross-reference
  prerequisites, explain a rules topic).
- `build_*` — character-building tools (browse ancestries/backgrounds/
  classes/equipment/spells, find feats a character actually qualifies for,
  validate a build, compute derived stats, see what unlocks on level-up,
  export to Pathbuilder JSON).

A companion Claude Skill (`.claude/skills/pf2e-character-builder/`) teaches
an agent the conversational workflow for using these tools together.

## Getting started (no coding experience required)

Never used a command line before? You don't need to learn one — the
easiest way to set this up is to let an AI assistant do it for you. This
works the same way on Windows, Mac, or Linux.

**1. Install an AI assistant that's allowed to run commands on your
computer.** Any of these have simple installers and can act as your
"installer" for everything else:

- **[Claude Code](https://github.com/anthropics/claude-code)** —
  Anthropic's assistant for working with project folders on your own
  computer. This is what was used to build pf2e-mcp itself, so it's a safe
  bet for setting it up too.
- **[Visual Studio Code](https://code.visualstudio.com/)** with the
  GitHub Copilot extension, using its "agent mode" chat — a free,
  widely-used code editor with a simple Windows installer.
- **[Cursor](https://cursor.com)** — another free code editor built
  around AI agent chat, similar idea to VS Code + Copilot.

You only need one of these, and only to do the one-time setup below — you
don't need to learn it in any depth.

(Why not just use Claude Desktop for this step? It's a great chat app —
see below — but it doesn't have a built-in way to run commands on your
computer, so it can't install things or edit its own config file for you
the way these three can. You'll still want it, or another chat app, for
step 5.)

**2. Download this project.** Click the green **Code** button near the
top of this page on GitHub, then **Download ZIP**, and unzip it somewhere
easy to find, like your Documents folder. (If you already use `git`,
`git clone` works too, but the ZIP download is simpler if you don't.)

**3. Open the unzipped folder** in whichever assistant you installed in
step 1.

**4. Ask it, in plain English, to set this up for you.** For example,
paste something like this into the chat:

> Please set up the pf2e-mcp server in this folder so I can use it with
> Claude Desktop. Install anything it needs (like Python and `uv`), build
> its rules database, and register the server in my Claude Desktop config.

Swap "Claude Desktop" for whichever chat app you actually want to use
day-to-day — see "Chat apps you can connect this to" below for a few
options. Your assistant should be able to figure out and run the actual
commands (they're all documented further down this page, in "Manual
setup", if you're curious what it's doing).

**5. Start building a character.** Once it's set up, open a conversation
in your chosen chat app and just ask for help building a Pathfinder 2e
character — e.g. *"help me build a Goblin Swashbuckler"* or *"what does
the Toughness feat do?"* It'll use this project's tools automatically
instead of guessing from memory.

### Chat apps you can connect this to

pf2e-mcp is an MCP *server* — on its own it doesn't have a chat window, it
needs a chat app that speaks MCP to actually talk to. A few well-known
options:

- **[Claude Desktop](https://claude.ai/download)** — Anthropic's free
  chat app for Windows and Mac. The simplest day-to-day experience once
  it's connected.
- **Claude Code** — if you used it to do the setup in step 1 above, you
  can just keep chatting with it directly; no separate app needed.
- **ChatGPT** and other AI chat apps are adding support for tools like
  this too. If you'd rather use one of those, ask your setup assistant
  (step 1) to connect pf2e-mcp to it instead of Claude Desktop — just
  check that whichever app you pick currently supports MCP servers first,
  since this is a fast-moving area and support varies.

## Manual setup (for developers, or if you'd rather do it yourself)

Everything below is what the AI-assisted setup above does on your behalf.
Follow it directly if you're comfortable with a terminal, want more
control, or are extending the project yourself.

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Setup

```bash
uv sync
```

The server reads from a local SQLite database that isn't checked into the
repo (`.data/pf2e.sqlite`, gitignored) — you need to build it once before
first use:

```bash
uv run python -m pf2e_mcp.ingestion.build
```

This downloads the latest `foundryvtt/pf2e` GitHub release's `json-assets.zip`
(the official, community-maintained Pathfinder 2e Remastered rules data),
caches the extracted JSON under `.data/raw/<release-tag>/`, and builds
`.data/pf2e.sqlite`. Takes under a minute; only re-downloads if a newer
release than what's cached is available.

## Using it

### As an MCP server

Register it with an MCP client. For Claude Code, from this directory:

```bash
claude mcp add pf2e-mcp -- uv run --directory "$(pwd)" pf2e-mcp
```

Or add it manually to your MCP client's config:

```json
{
  "mcpServers": {
    "pf2e-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/pf2e-mcp", "pf2e-mcp"]
    }
  }
}
```

For a project-scoped registration in this repo, copy `.mcp.json.example` to
`.mcp.json` and replace `/path/to/pf2e-mcp` with your checkout path. The
real `.mcp.json` is gitignored, since that path is per-machine.

To run it directly (e.g. for debugging over stdio):

```bash
uv run pf2e-mcp
```

### The companion Skill

`.claude/skills/pf2e-character-builder/SKILL.md` is project-scoped, so
Claude Code picks it up automatically when working in this repo. If you're
using the server from a different project, copy that directory into the
other project's `.claude/skills/`, or your user-level `~/.claude/skills/`.

### Tool reference

| Tool                                               | Purpose                                                                                                                                               |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rules_search`                                     | Full-text search across all ingested rules content                                                                                                    |
| `rules_get_entry`                                  | Fetch one entry's full detail by id/name/slug, including any `ChoiceSet` options it presents (e.g. a class's trained-skill choice)                    |
| `rules_related`                                    | A feat's prerequisites/what requires it, plus structured (not prose-parsed) `grants`/`granted_by` and `stat_modifiers` (e.g. a skill-training grant)  |
| `rules_explain`                                    | Explain a rules topic (conditions, actions, glossary terms)                                                                                           |
| `rules_data_version`                               | Which `foundryvtt/pf2e` release the DB was built from                                                                                                 |
| `rules_list_variant_rules`                         | Browse optional/variant rules (Free Archetype, Ancestry Paragon, Proficiency without Level, etc.) and GM-facing subsystems (Chases, Influence, etc.)  |
| `rules_list_subclass_option_groups`                | List every subclass-style choice-group tag (Druid Order, Animist Apparition, Sorcerer Bloodline, Witch Patron, etc.), with option counts             |
| `rules_list_subclass_options`                      | List every option in one subclass choice-group by exact tag, with full text and structured `grants`/`stat_modifiers` fields (exact rule-element data) |
| `build_list_ancestries` / `build_list_backgrounds` | Browse character-building options                                                                                                                     |
| `build_list_classes`                               | Browse classes, including level-1 baseline (HP, Perception, saves, trained skills — including any "X or Y" choice — weapon/armor proficiencies)      |
| `build_list_equipment`                             | Browse weapons/armor/gear, with price filtering                                                                                                       |
| `build_list_available_feats`                       | Feats a character currently qualifies for (the main token-saving discovery tool)                                                                      |
| `build_list_ability_boost_options`                 | Legal ability boosts for a given source (ancestry/background/class/free)                                                                              |
| `build_list_skill_increase_options`                | Skills legally eligible for a rank increase right now                                                                                                 |
| `build_list_available_spells`                      | Spells of a tradition at or below the character's current max rank                                                                                    |
| `build_check_prerequisite`                         | Check one specific feat's eligibility                                                                                                                 |
| `build_validate_build`                             | Structured errors/warnings for the current build                                                                                                      |
| `build_calculate_derived_stats`                    | AC, saves, Perception, skills, HP, class DC, spell DC/attack                                                                                          |
| `build_get_level_up_choices`                       | What unlocks at a target level                                                                                                                        |
| `build_to_pathbuilder_export`                      | Export the working character as Pathbuilder-compatible JSON                                                                                           |
| `build_render_character_sheet`                     | Write a print-ready, single-file HTML character sheet with full rules text (see below)                                                                 |

Every `build_*` tool but one is a pure function — the server holds no
character state between calls. The calling agent passes the in-progress
character JSON (shaped like a Pathbuilder 2e export) on every call and keeps
it in conversation. The exception is `build_render_character_sheet`, which
writes a file to the `output_path` it is given; it returns a summary rather
than the HTML, because a real sheet runs to 120–170 KB.

### Character sheets

`build_render_character_sheet(character, output_path, paper="letter")`
renders a print-ready HTML sheet with no external dependencies of any kind —
fonts are inlined as base64 WOFF2, every ornament is generated inline SVG,
and nothing is fetched at view time — so the file can be emailed to a player
as a single attachment and printed unchanged.

Page 1 is a one-sheet statistics summary (attributes, skills with proficiency
pips, AC and shield, saves, HP with dying/wounded trackers, Perception,
strikes, spell DC, a condition tracker and space for session notes). It is
held to a single physical page for every character, verified against every
build in `characters/`, from level 2 through a level-9 dual-class. Subsequent
sections run to whatever length the content needs, in page-width cards that
reflow across page boundaries rather than being cropped: one page per
spellcasting entry with each spell's **complete** rules text and traits, then
features (a deity block where there is one, then ancestry, heritage,
background, level-appropriate auto-granted class features, detected subclass,
and every feat), equipment with item rules text, a page showing how each
number was derived, and a licence attribution page listing only the
sourcebooks actually quoted.

The attribution section comes last and reproduces the full text of both the
Open Game License 1.0a (when OGL-licensed rules are quoted) and the SIL Open
Font License 1.1 (always, since the sheet embeds the Noto fonts). Both are
required to travel with what the sheet carries, so they can't be dropped from
the file — but they are the final pages, so you can simply set a page range in
the Print dialog and leave them off the paper copy.

#### The deity block

Deities *are* in the ingested data — 480 entries, thoroughly structured — even
though no `rules_*` tool exposes their mechanical fields. The sheet reads them
straight from the database, and handles three cases, reported back as
`deity.status`:

| `status` | When | What renders |
| --- | --- | --- |
| `none` | No deity, including the export's own placeholders (`"Not set"`, `""`, `"-"`) | Nothing — block omitted |
| `resolved` | The recorded name matched the rules data | Divine font, sanctification, divine skill, favored weapon, primary and alternate domains, divine attribute, and the deity's cleric spells resolved to names by rank — then the full descriptive text: title, areas of concern, edicts, anathema, iconography. Pantheons, covenants and philosophies resolve here too, labelled as such |
| `unrecognized` | A name is recorded but isn't in the database — a home-game or non-standard pantheon | A labelled block with ruled lines for the standard fields, to fill in by hand |

An unrecognized deity is deliberately **not** reported in `unresolved`: a
homebrew god is a legitimate thing for a character to have, not a defect in
the data.

Note that a deity block reproduces more than mechanics. Titles, edicts,
anathema and iconography are Reserved Material under the ORC License (Product
Identity under the OGL), not the generic game content either licence grants
freely — so when one is rendered, the attribution page says explicitly that
this material appears as a descriptive reference under Paizo's Community Use
Policy rather than as an exercise of the ORC License. This matches
`licensing.py`'s `product_identity_likely` flag on the `deities` pack.

#### Optional logo

`logo_path` embeds an image top-left on page 1 in place of the sheet's own
spiral mark — intended for the Pathfinder logo from Paizo's [Community Use
Package](https://paizo.com/community/communityuse/package), which the
[Community Use FAQ](https://paizo.com/licenses/communityuse/faq) permits on
free fan material (it gives putting "a Pathfinder logo on the cover" of a
non-commercial fan adventure as an example). Section headings keep the spiral,
so the sheet still has a mark of its own.

Accepts PNG, SVG, JPEG, GIF or WebP. EPS and AI are rejected with an
explanation — they ship in the package but no browser can display them. The
bytes are embedded verbatim and scaled by height with `width: auto`: the policy
forbids altering a logo's colour, typography, design or proportions, and allows
proportional resizing, so the renderer applies no filter, recolour, crop or
blend mode. Downloads from the package require a Paizo sign-in, so the file has
to be fetched by hand once.

**Equipment values vs. transient effects.** Printed statistics reflect the gear
as it permanently is, never as it might temporarily be. A shield's Hardness, HP
and Broken Threshold are the item's own values plus any etched rune — a
reinforcing rune's increments and caps are parsed from the rune's own text,
since they exist nowhere in structured form — while bonuses from spells, feats
and other effects are left out. A status bonus like Emblazon Armament's +1
Hardness may not be active when the shield is needed, so printing it would
overstate what the shield reliably blocks. Note that a shield recorded as a
bare `[name, quantity]` pair has nowhere to carry runes; only a dict-shaped
record does.

Everything is derived from the character data plus the ingested rules — there
is no authored commentary, so a sheet is exactly as complete as the export it
came from. Two consequences worth knowing:

- A character with no `spellCasters` block gets no spell pages, even for a
  caster class. Pathbuilder populates it; hand-written character JSON often
  doesn't.
- Class features are read from `class_progression.granted_items` filtered to
  the character's level, since exports record only choices. A subclass
  (cleric doctrine, druid order) is recovered by matching the class's tagged
  option names against the character's free-text `specials`, which is
  best-effort — check the returned `subclass`.

The return value reports `unresolved` names and `aliased` ones. An item an
export calls "Repair Kit" where the rules data has "Repair Toolkit" is
reported rather than guessed at or dropped silently; a systematic mismatch
that *is* resolved ("Thieves' Tools" → "Thieves' Toolkit", "Clothing
(Explorer's)" → "Explorer's Clothing") is listed as an alias on both the tool
result and the sheet's own notes page. Treat a non-empty `unresolved` as
something to fix in the character data.

### Optional/variant rules and Pathfinder Society legality

Every entry (feats, ancestries, etc.) carries a `rarity` and a best-effort
`pfs` legality status now, and `build_get_level_up_choices` /
`build_validate_build` take an optional `variant_rules: list[str]` argument.
See the companion Skill's step 0 for the intended flow: ask the user which
optional rules and PFS mode they want _before_ starting a build, since
that changes real math (extra feat slots, ability-boost timing) that isn't
safe to assume either way.

- **Variant rules** come from GM Core's "Subsystems and Variant Rules"
  section, ingested into the same `entries` table as everything else
  (`pack = 'variant-rules'`) — browse with `rules_list_variant_rules`, read
  full official text with `rules_get_entry`. Of these, only
  `'free-archetype'` and `'ancestry-paragon'` currently change computed
  output (extra feat-level unlocks, feat-count budget checks); the rest are
  recognized slugs with reference text only, no mechanical enforcement yet.
- **PFS legality** has no structured feed in the `foundryvtt/pf2e` data this
  project ingests — Organized Play legality is Paizo's own policy document,
  updated on its own cadence, and doesn't map cleanly onto rarity. The `pfs`
  field on every entry (and `build_validate_build`'s `pfs_legal_only`
  parameter) is a **best-effort heuristic**: common rarity = legal,
  uncommon/rare/unique = flagged restricted, with a hand-curated override
  file (`src/pf2e_mcp/server/pfs_overrides.json`) for known exceptions. Not
  authoritative — see that file's `_comment`/`_format` keys and
  `src/pf2e_mcp/server/pfs.py`'s module docstring for the reasoning, and
  "Maintaining PFS overrides" below for how to add exceptions.

### Licensing of ingested content

This project's own code is Apache 2.0 (`LICENSE`); the Pathfinder 2e data
it ingests is Paizo Inc.'s content, used under separate terms — see
[NOTICE.md](NOTICE.md) for the full picture. Two things relevant to using
the tools:

- Bestiary, Monster Core, NPC Core, and full-adventure packs are excluded
  from ingestion entirely (`ingestion/build.py`) — they're almost entirely
  proper nouns/narrative content ("Product Identity"/"Reserved Material"
  under OGL/ORC), and none of it is needed for character building.
- Every `rules_*` tool result carries a `license` field
  (`src/pf2e_mcp/server/licensing.py`) noting which regime applies (OGL
  1.0a vs. the ORC License, from the ingested `is_remaster` flag) and
  whether the entry is likely Product Identity/Reserved Material rather
  than open game mechanics. Like `pfs`, this is a best-effort, pack-level
  heuristic, not a per-entry legal determination.

## Updating the data

Paizo releases new content and errata on a near-weekly cadence, and
`foundryvtt/pf2e` tracks it closely. Re-run ingestion any time to refresh:

```bash
uv run python -m pf2e_mcp.ingestion.build
```

This is idempotent and safe to run repeatedly: it does a full rebuild into a
temp file and atomically swaps it in, so there's no incremental-patch state
to get out of sync. It also prints a changelog of entry-count deltas per
pack since the last build, e.g.:

```
Changes since last ingestion:
  feats: 6044 -> 6051 (+7)
  spells: 1802 -> 1805 (+3)
```

Check `rules_data_version` (or `SELECT * FROM meta` in the SQLite file) to
see which release is currently loaded.

### Maintaining prerequisite overrides

About 87% of feat prerequisites are automatically parsed into structured
data: skill rank (single, or a comma/or-separated list satisfied by any
one), ability score, character level, or a named feat/feature reference
(including one with a stripped trailing category word, e.g. "muse" or
"heritage"). The rest are free-form text the parser can't confidently
structure. Those show up as `"unresolved"` in the `prerequisites` table,
surfaced to callers as an `"unconfirmed"` bucket rather than silently
guessed at.

To hand-fix a specific one, add it to
`src/pf2e_mcp/ingestion/prerequisite_overrides.json`, keyed by the exact
raw prerequisite text, then re-run ingestion. If upstream errata later
changes that feat's prerequisite wording, the override simply stops
matching and the entry reverts to unconfirmed rather than silently
applying a stale rule — check for newly-unresolved entries after a refresh
if you maintain overrides.

An override's value must include its own `"kind"` field, dispatched by
`pf2e_math.check_single_prerequisite`'s `override` branch. Reuse an
existing kind directly if it fits — e.g. the `'Counterspell'` entry
(genuinely ambiguous between 6 real Counterspell-named feats) is expressed
as `{"kind": "compound_named", "any_of": [...]}` to reuse that evaluator
rather than inventing a new one. `spellcasting_trained` is available
built-in for "you have a spellcasting class feature" style text, with an
optional `"tradition"` (string or list) to restrict it to specific
traditions.

### Maintaining PFS overrides

`src/pf2e_mcp/server/pfs_overrides.json` starts empty on purpose — see
"Optional/variant rules and Pathfinder Society legality" above for why this
project doesn't ship a pre-populated ban/restriction list (no reliable
structured feed, and Organized Play rules change on their own quarterly
cadence independent of errata). Add an entry when a real exception comes up:

```json
{
  "Some Feat Name": {
    "status": "banned",
    "note": "Banned per Additional Resources, <season/date it changed>"
  }
}
```

`status` is `"legal"` (clears a rarity-based `"restricted"` flag — e.g. an
uncommon option that's actually fine in PFS), `"restricted"`, or `"banned"`
(stronger than the rarity default — no boon unlocks it). Keys are matched
case-insensitively against the entry name. Unlike
`prerequisite_overrides.json`, this file is **not** baked in at ingestion
time — it's read fresh by `server/pfs.py` on every relevant tool call, so
edits take effect immediately.

## Project layout

```
src/pf2e_mcp/
  ingestion/          # pulls foundryvtt/pf2e data, builds the SQLite DB
    source.py          # GitHub release fetching
    prerequisites.py    # feat prerequisite parsing
    prerequisite_overrides.json
    schema.sql
    build.py            # orchestrates a full ingestion run
  server/             # the MCP server
    app.py              # tool registration and entrypoint
    rules_tools.py       # rules_* tools
    build_tools.py       # build_* tools
    pf2e_math.py          # PF2e arithmetic and prerequisite evaluation
    pfs.py                 # PFS-legality heuristic (rarity + override file)
    pfs_overrides.json      # hand-curated PFS legality exceptions (starts empty)
    licensing.py             # OGL/ORC + Product Identity classification heuristic
    db.py                    # read-only SQLite connection helper
scripts/
  spike_prerequisites.py  # standalone prerequisite-parseability report
.claude/skills/pf2e-character-builder/SKILL.md
```

## Current status

Level 1–20 character creation and leveling across all ancestries,
backgrounds, and classes, including spellcasting and equipped-armor AC.
See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for tracked gaps.
