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

## Quick install

Published on PyPI as [`pf2e-mcp`](https://pypi.org/project/pf2e-mcp/). With
[uv](https://docs.astral.sh/uv/), nothing needs to be cloned or installed
up front:

```bash
# Build the rules database once (~1 minute, a few hundred MB downloaded)
uvx --from pf2e-mcp python -m pf2e_mcp.ingestion.build
```

Then point your MCP client at it:

```json
{
  "mcpServers": {
    "pf2e-mcp": {
      "command": "uvx",
      "args": ["pf2e-mcp"]
    }
  }
}
```

Or install it as a normal package (`pip install pf2e-mcp`, `uv tool install
pf2e-mcp`) and use `pf2e-mcp` as the command instead.

**The database build is not optional** — no game data ships with this
project (see
[NOTICE.md](https://github.com/rjenks/pf2e-mcp/blob/main/NOTICE.md)), so the
server starts but every tool call fails until you run it. It's a one-time
step; re-run it whenever you want newer rules data. See
[Where the files go](#where-the-files-go) for where it puts things.

If you'd like the companion character-building Skill as well, or want to
work on the project itself, clone the repository instead — see
[Manual setup](#manual-setup-for-developers-or-if-youd-rather-do-it-yourself).

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

If you only want to *use* the server, you don't need a checkout at all —
see [Quick install](#quick-install) above. Clone the repository if you want
the companion Skill, or intend to work on the project:

```bash
git clone https://github.com/rjenks/pf2e-mcp
cd pf2e-mcp
uv sync
```

The server reads from a local SQLite database that isn't checked into the
repo — no game data ships with this project (see
[NOTICE.md](https://github.com/rjenks/pf2e-mcp/blob/main/NOTICE.md)) — so
you need to build it once before first use:

```bash
uv run python -m pf2e_mcp.ingestion.build
```

(From a PyPI install rather than a checkout, the equivalent is
`uvx --from pf2e-mcp python -m pf2e_mcp.ingestion.build`. Either way the
database lands in the same per-user location, so it doesn't matter which
one you used to build it.)

This downloads the latest `foundryvtt/pf2e` GitHub release's `json-assets.zip`
(the official, community-maintained Pathfinder 2e Remastered rules data),
extracts it, and builds the database. Takes under a minute; only re-downloads
if a newer release than what's cached is available.

#### Where the files go

The two artifacts land in the standard per-user locations for your platform,
so the server works the same whether it's run from a checkout, installed with
`pip`, or launched via `uvx`:

| | Linux | macOS |
| --- | --- | --- |
| Database (~100 MB) | `~/.local/share/pf2e-mcp/` | `~/Library/Application Support/pf2e-mcp/` |
| Extracted release cache | `~/.cache/pf2e-mcp/raw/` | `~/Library/Caches/pf2e-mcp/raw/` |

They're split deliberately. The database is expensive to recreate, so it sits
in the data directory where nothing reclaims it. The extracted release is pure
cache — a few hundred MB, re-downloadable at any time — so it sits in the
cache directory, and deleting it costs you one re-download and nothing else.
A successful build prunes superseded release tags automatically, keeping only
the one it just ingested.

Override either with `PF2E_MCP_DB` (full path to the `.sqlite` file) and
`PF2E_MCP_CACHE` (directory) — useful for a throwaway build, a shared
read-only database, or to keep everything inside a checkout while developing:

```bash
PF2E_MCP_DB=.data/pf2e.sqlite PF2E_MCP_CACHE=.data/raw \
  uv run python -m pf2e_mcp.ingestion.build
```

## Using it

### As an MCP server

**From PyPI** (no checkout needed). For Claude Code:

```bash
claude mcp add pf2e-mcp -- uvx pf2e-mcp
```

Or in your MCP client's config:

```json
{
  "mcpServers": {
    "pf2e-mcp": {
      "command": "uvx",
      "args": ["pf2e-mcp"]
    }
  }
}
```

**From a checkout**, when you're working on the project and want your local
edits to be what runs:

```bash
claude mcp add pf2e-mcp -- uv run --directory "$(pwd)" pf2e-mcp
```

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
uvx pf2e-mcp        # from PyPI
uv run pf2e-mcp     # from a checkout
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
| `build_list_available_spells`                      | Spells of a tradition at or below the character's current max rank, each with its heightening steps; `slot_rank` filters to what's worth preparing in one slot |
| `build_check_prerequisite`                         | Check one specific feat's eligibility                                                                                                                 |
| `build_validate_build`                             | Structured errors/warnings for the current build                                                                                                      |
| `build_calculate_derived_stats`                    | AC, saves, Perception, skills, HP, class DC, spell DC/attack                                                                                          |
| `build_get_level_up_choices`                       | What unlocks at a target level                                                                                                                        |
| `build_to_pathbuilder_export`                      | Export the working character as Pathbuilder-compatible JSON                                                                                           |
| `build_render_character_sheet`                     | Write a print-ready, single-file HTML character sheet with full rules text (see below)                                                                 |
| `pfs_get_adventure`                                | Resolve an Organized Play adventure code (`8-02`, `Q2-26`, `B21`) to its title, tier, season and standard rewards                                     |
| `pfs_find_adventures`                              | Search the adventure index by title, kind, season, or a character level that must fall inside the tier                                                |
| `pfs_chronicle_schema`                             | The JSON Schema for a chronicle log — read this before authoring one by hand                                                                          |
| `pfs_validate_chronicle`                           | Check a chronicle log's ledger chain and report the totals it implies                                                                                 |
| `pfs_earn_income`                                  | DC and payout for one Earn Income check, on the Guide's PFS-modified table                                                                            |
| `pfs_render_chronicle_sheet`                       | Write a print-ready, single-file HTML Organized Play record (see below)                                                                                |

Every `build_*` tool but one is a pure function — the server holds no
character state between calls. The calling agent passes the in-progress
character JSON (shaped like a Pathbuilder 2e export) on every call and keeps
it in conversation. The exceptions are `build_render_character_sheet` and
`pfs_render_chronicle_sheet`, which write a file to the `output_path` they
are given; they return a summary rather than the HTML, because a real sheet
runs to 120–220 KB.

### Character sheets

`build_render_character_sheet(character, output_path, paper="letter")`
renders a print-ready HTML sheet with no external dependencies of any kind —
fonts are inlined as base64 WOFF2, every ornament is generated inline SVG,
and nothing is fetched at view time — so the file can be emailed to a player
as a single attachment and printed unchanged.

Sections are ordered **quick reference first, rules text behind it**. The pages
a player handles mid-session come first and are tables; everything that exists
to be looked something up in follows, in page-width cards that reflow across
page boundaries rather than being cropped. In order:

| Section | What it is |
| --- | --- |
| `core` | One-page statistics summary: attributes, skills with proficiency pips, AC and shield, saves, HP with dying/wounded trackers, Perception, strikes, spell DC and a condition tracker. Held to a single physical page for every character in `characters/`, up to a level-14 animist |
| `advancement` | Every feat and class feature as a level-by-level table (see below) |
| `spellcasting` | A casting-statistics band (see below), then the slot table alone — every source's spells, what's prepared, a bubble per slot to strike off as it's spent |
| `inventory` | The carried-gear table alone — quantity, Bulk, price, notes |
| `skill-actions` | Skill actions the character's training unlocks, in full (see below) |
| `spells` | Every spell's **complete** rules text and traits, alphabetical across all casting sources |
| `features` | A deity block where there is one, then ancestry, heritage, background, level-appropriate auto-granted class features, detected subclass, and every feat |
| `item-rules` | Full text for the carried gear, in inventory order |
| `notes` | How each number was derived |
| `attribution` | Only the sourcebooks actually quoted |

Splitting the slot table off its spell descriptions, and the inventory off its
item text, is what makes that ordering possible — as one section each, the two
tables were stranded on top of a dozen pages nobody re-reads. Sections whose
content is empty are skipped, and the returned `sections` list names those
actually written, in file order.

The attribution section comes last and carries every notice in one place: the
Community Use Policy statement, the ORC License attribution, a Reserved
Material note when a deity block was rendered, the artwork-and-typography
statement, and then the full text of both the Open Game License 1.0a (when
OGL-licensed rules are quoted) and the SIL Open Font License 1.1 (always, since
the sheet embeds the Noto fonts). Both licences are required to travel with
what the sheet carries, so they can't be dropped from the file — but the
toolbar's **Notices** switch (below) leaves them off the paper copy.

#### The advancement page

Page 2, modelled on the official sheet's second page: one row per level from 1
to the character's, ancestry/general/skill feats down the left column and class
abilities down the right, with the level number in a tinted gutter. Archetype
feats sit on the class side because that is where their slot comes from —
PF2e's archetype feats are taken with class feat slots, not a category of their
own. Levels that grant attribute boosts say so, and every line carries its
category (`Ancestry feat`, `Class feature`) plus **automatic** where the class
grants it rather than the player choosing it — the same distinction the
companion `.md` files draw, and the one that tells you which lines a level-up
screen will actually prompt for.

Feats used to sit at the foot of page 1, fitted by a character-count budget
standing in for "how many lines will this wrap to". That proxy failed on a
dense build: a level-10 character with six feat categories and eighteen
features spilled onto a second sheet regardless. Giving feats their
own page removes the budget, the per-character tuning, and the failure mode
together — page 1 is now unconditionally one page, since nothing on it grows
with level except within fixed-height blocks.

#### The casting statistics band

In the spirit of the official sheet's Magical Tradition and Spell Statistics
boxes — except that everything in it is derived rather than blank. Two rows:
the tradition and casting style this group actually is (`Arcane · Prepared ·
Focus`), then spell attack, spell DC and proficiency as three stacked cells,
each label over figure over arithmetic. The proficiency term carries its own
bonus (`WIS +4 · level 2 · trained +2`) so the parts visibly sum to the figure
above them.

The official sheet prints a tick-box per tradition and per casting style
because it's filled in by hand; a rendered sheet already knows the answer, so
only what the character *is* gets printed. "Not occult, not primal, not
spontaneous" is nothing a player would act on.

**Each band is followed by its own table**, listing only the spells its numbers
govern. One combined table under stacked bands made the reader carry "which DC
applies to this row" down the page — harmless for a cleric whose two entries
share a statistic, wrong for a magus whose innate cantrips are cast off a
different attribute entirely.

**Innate groups sort first.** Everything else on the page was studied, prepared
or bargained for; innate spells are the ones the character simply has. The sort
is stable, so the character's own order survives within each bucket.

Styles read in the order Prepared, Spontaneous, **Innate** — the official sheet
knows only the first two — with anything else (Focus) after them. A group can
carry more than one, and says so: a magus whose focus spell shares the class's
statistic reads `Arcane · Prepared · Focus`.

Cantrips are *not* in the band. They're a resource like any other, so they get
a group header row in the slot table itself — `Cleric · Cantrip · At will ·
5 known, cast at rank 1` — with the cantrip names indented beneath it, exactly
like a prepared rank or a focus pool. The count and the heightening rank are
true of the whole group, so they're stated once on the header instead of
repeated in the Uses column against every name.

One band per **set of statistics**, not per entry in `spellCasters` — the
grouping key is (tradition, attribute, proficiency), which is exactly what the
numbers are made of. A cleric's spell list and their divine font are two
entries sharing one statistic, and printing DC 18 twice under two headings
invites the reader to hunt for a difference that isn't there; the sources
sharing a band are named on it instead ("Cleric · Cleric Font", "Bard · Focus
Spells"). A character with genuinely different statistics — an elf magus whose
prepared arcane spells run off Intelligence and whose ancestry's innate
cantrips run off Charisma — gets one band and one table per set.

The arithmetic is spelled out because this is a reference page: a player
checking a DC against their memory of it wants to see *which* term disagrees.

#### Choosing what prints

A toolbar above the sheet, hidden when printing, carries a checkbox per
optional section — **Advancement, Skill actions, Spells, Features, Item rules,
Sheet notes, Notices** — all ticked by default, and each shown only when the
character actually has that section. Unticking one hides it on screen as well as leaving
it out of the print: the point is to see what will come out of the printer, and
a page still on screen but silently absent from the print is worse than no
switch at all. Untick everything and you get the statistics page and the two
quick-reference tables, which is what a player who keeps the rules to hand (or
the file open on a phone) usually wants on paper.

`core`, `spellcasting` and `inventory` have no switch. They are the sheet, and
a control that can empty it invites printing a character sheet with no
character on it.

Nothing is removed from the file — the licence text still travels with what the
sheet carries whatever the boxes say. These switches are the file's only
script, about twenty lines inline; with scripting off every box stays ticked
and the whole sheet prints as it always did.

#### The skill actions section

The first of the rules-text sections, since it is the rest of what page 1's
skills table says the character can do: every core skill action their training
unlocks, each with its complete rules text, credited to the skill (or skills)
that grant it.
Actions reachable through several of a character's skills — Learn a Spell via
both Arcana and Religion — appear once, listed against all of them, and a
trained Lore of any topic unlocks the generic Lore row.

Only **trained-gated** actions are printed. Anyone can attempt Balance, Climb or
Demoralize untrained, so printing those would reprint the basic rules instead of
telling the player anything about this character. Trained is also the only
threshold there is to apply: expert and above unlock no further core skill
actions.

**Downtime** actions — Craft, Earn Income, Create Forgery, Treat Disease — are
left out as well, on what-is-this-page-for grounds rather than as a rules
judgement. They happen between sessions with the rules to hand, and their text
is long out of proportion to that: Earn Income alone carries the whole Income
Earned table, a printed page by itself. `rules_get_entry` still has them.

Which skill an action belongs to, and whether it needs training, is not
recoverable from the action items themselves — a `type='action'` item names no
skill (Treat Wounds mentions Medicine only in prose and never says "trained"),
and not one action in the database has a `prerequisites` row. The single place
`foundryvtt/pf2e` records it is the GM Screen journal's "Skill Actions" page
(Player Core pg. 227), whose cells are `@UUID` links to the action items; that
page is parsed into the `skill_actions` table at ingestion. One upstream
copy-paste is corrected there by a named exclusion in `build.py`
(`_SKILL_ACTION_EXCLUSIONS`): Borrow an Arcane Spell is repeated into the
Occultism and Religion rows although the action is arcane-only by its own text.

Actions granted by a *feat* (Battle Medicine, Bon Mot) are not in this section.
They're `type='feat'`, already carry structured `prerequisites` of
`kind='skill_rank'`, and are printed in full on the features page.

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

### Pathfinder Society chronicle sheets

Organized Play characters earn one **Chronicle Sheet** per session, and the
thing worth understanding about them is that a chronicle is a *ledger entry,
not a receipt*. Each sheet records starting XP and starting gold, adds what the
session awarded, and prints the new totals — which the next sheet takes as its
own starting values. A character's current level, wealth and Reputation are only
correct if the whole chain is applied in order, so a single transcription slip
halfway down a stack of a dozen PDFs quietly corrupts everything after it.

A character's chronicles live in a `characters/<Name>.chronicles.json` sidecar,
deliberately *not* inside the Pathbuilder JSON, so that export stays a clean,
portable Pathbuilder file. Call `pfs_chronicle_schema` for the full JSON Schema
before authoring one by hand.

**Looking up an adventure.** Players speak in codes, so the tools do too:

```
pfs_get_adventure("8-02")
  → The Fey Reclamation, scenario, tier 3–4, Year of Clockwork Mystery
    standard award: 4 XP, 4 Reputation, 8 downtime days
    treasure bundle: 3 gp 8 sp at level 3, 6 gp 4 sp at level 4
```

`#8-02`, `8-2` and `PFS 8-02` all resolve to the same thing. Scenarios use
Paizo's Society code (including the evergreen intros `99-1` and `99-2`), quests
use `Q<series>-<number>` and bounties `B<number>` — both restart their numbering,
so the prefix is what keeps them apart. Use `pfs_find_adventures` when the code
isn't known; `level=4` finds everything a 4th-level character is in tier for.

**Where the index comes from.** Paizo publishes no usable feed: the store and
`organizedplay.paizo.com` are a single-page app with no public API and reporting
is behind a login, the Archives of Nethys Elasticsearch index carries only the
~31 PFS products that introduce new *rules* content, and the `pathfinder-society`
npm package that search results still recommend was last published in January
2022 with its repository now gone. So ingestion reads PathfinderWiki's `Facts:`
namespace — a structured data layer behind each adventure page — into the
`pfs_adventures` table: 213 PF2 adventures across seasons 1–8, with every
scenario carrying an explicit Society code. Only factual index data is stored;
blurb text is Paizo marketing copy and is deliberately dropped. A wiki outage
skips this one step with a warning rather than failing the whole ingestion run.

**Validation.** `pfs_validate_chronicle` distinguishes two kinds of finding.
*Errors* are internal contradictions the file cannot be right about — a ledger
where start + gained ≠ end, a chronicle whose starting values don't match the
previous one's ending values, an unrecognised code, a non-repeatable adventure
recorded twice without a replay. *Warnings* are deviations from the Guide's
standard awards — nonstandard XP, low Reputation, a treasure bundle total that
doesn't match the character's level, playing out of tier — which individual
adventures are entitled to make, so they're advisory. The `derived` block is
usually the point: current XP and the level it implies, currency on hand,
Reputation per faction, downtime banked, and how many credits were earned
running rather than playing.

Almost nothing about the rewards is looked up per-adventure. Adventure *type*
fixes XP, Reputation and downtime (scenario 4/4/8, Series 2 quest 2/2/4, Series
1 quest 1/1/2, bounty 1/1/—) and *character* level fixes what a Treasure Bundle
is worth — note character level, not tier, so two characters at one table earn
different gold from the same scenario. Slow advancement halves all of it without
rounding. Money is stored as gold (fractional gold is normal — a 1st-level
bundle is 1 gp 4 sp) but all arithmetic runs in integer copper, because
validating a ledger means asserting exact equality across a dozen additions.

Fame is deliberately absent: it was replaced by Achievement Points on
31 July 2020, and AcP is account-level rather than character-level.

**The gold journal.** Validation also returns a `journal`: every movement of
money in order, with a running balance. A chronicle's currency block says what
one session did, but a stack of chronicles hides every purchase inside a lump
`spent` figure — so "where did my money go" has no answer until all of it is
flattened into one column. Itemise `startingPurchases` and each chronicle's
`purchases` and each becomes a line:

```
LVL  TRANSACTION                                        IN      OUT      BALANCE
 3   Character created at level 3 — PFS starting funds  75 gp            75 gp
 3   Sickle                                                     −2 sp    74 gp, 8 sp
 3   Weapon Potency (+1) rune                                   −35 gp   39 gp, 8 sp
 …
 3   Soap                                                       −2 cp    31 gp
 3   7-02 Shipyard Sabotage                             38 gp            69 gp
```

`startingLevel` matters more than it looks. PFS characters begin play at 1st,
3rd, 5th or 7th level — **always with 0 XP**, whichever they chose — so XP alone
never tells you a character's level, and a level-3 character reaches 4th at 12
XP exactly as a 1st-level one reaches 2nd. Starting funds follow the same table:
15 gp at 1st, 75 gp at 3rd, 270 gp at 5th, 720 gp at 7th under the credits-only
option, or a smaller purse plus permanent items of set levels. The journal and
the chronicle ledger are computed independently and cross-checked, so if they
disagree that is itself reported as an error.

**Downtime and Earn Income.** `pfs_earn_income(level, proficiency,
adventure_type)` returns the DC and the value of each degree of success. PFS
downtime differs from a home game in ways that change both the arithmetic and
the strategy, and all four are modelled:

- Downtime is granted per Chronicle and **spent when that Chronicle is applied,
  or lost** — it cannot be accrued, so there is no balance to plan against. The
  validator reports days *granted* as history, never as a total on hand.
- One adventure's downtime is one **Downtime Unit** and takes **one check for
  the whole unit**, not one per day: 8 days for a Scenario, 4 for a Series 2
  Quest, 2 for a Series 1 Quest, none for a Bounty. The payout scales with the
  unit.
- The skill must be **Crafting, Performance, or a Lore** — not whatever the
  character is best at.
- The default **Task Level is character level − 2**, already folded into the
  table. A critical success treats the character as one level higher (minimum
  3); a critical failure earns nothing.

Income lands in a chronicle's `currency.incomeEarned`, so it flows into the gold
journal like any other award.

**The printable record.** `pfs_render_chronicle_sheet(chronicle_log,
output_path, paper="letter", logo_path=None, blank_adventure_rows=6,
blank_journal_rows=12)` writes a self-contained HTML
document sharing the character sheet's stylesheet and fonts, so the two print as
one family. It lays out a summary page (level, XP, currency, Reputation per
faction, downtime, and every adventure played), a validation page when anything
was flagged, one page per chronicle in the order applied — provenance, both
ledgers, Reputation, summary checkboxes, boons, treasure access with purchased
lines struck through, notes — a gold journal page, and a notices page.

Adventures Played and the gold journal both end in **ruled blank rows**, sized
for handwriting rather than for the sheet's 8pt type. A chronicle arrives by
email a day after the session, so at a convention a player runs several games
ahead of their records; the blanks are for writing those in by pen and
transcribing them later. With blanks present the journal's total line reads
*Balance carried forward* rather than *Balance on hand*, since anything written
below supersedes it. Pass 0 to either for a records-only printout. Findings print alongside the
entry that produced them, because the person who needs to see that chronicle 7
doesn't follow from chronicle 6 is the person holding the paper.

None of this is authoritative. Award rates and Treasure Bundle values are
transcribed from the Guide to Organized Play; consult the current Guide and your
actual chronicle sheets for a real game.

### Licensing of ingested content

This project's own code is Apache 2.0 (`LICENSE`); the Pathfinder 2e data
it ingests is Paizo Inc.'s content, used under separate terms — see
[NOTICE.md](https://github.com/rjenks/pf2e-mcp/blob/main/NOTICE.md) for the full picture. Two things relevant to using
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
  paths.py            # where the DB and download cache live
  ingestion/          # pulls foundryvtt/pf2e data, builds the SQLite DB
    source.py          # GitHub release fetching
    prerequisites.py    # feat prerequisite parsing
    prerequisite_overrides.json
    pfs_adventures.py   # Organized Play adventure index, from PathfinderWiki
    schema.sql
    build.py            # orchestrates a full ingestion run
  server/             # the MCP server
    app.py              # tool registration and entrypoint
    rules_tools.py       # rules_* tools
    build_tools.py       # build_* tools
    pf2e_math.py          # PF2e arithmetic and prerequisite evaluation
    pfs.py                 # PFS-legality heuristic (rarity + override file)
    pfs_overrides.json      # hand-curated PFS legality exceptions (starts empty)
    pfs_tools.py             # pfs_* tools: adventure lookup, chronicle validation
    chronicle.py              # chronicle ledger math, awards, validation
    chronicle_schema.json      # JSON Schema for a chronicle log sidecar
    chronicle_sheet.py          # printable Organized Play record
    licensing.py             # OGL/ORC + Product Identity classification heuristic
    db.py                    # read-only SQLite connection helper
scripts/
  spike_prerequisites.py  # standalone prerequisite-parseability report
.claude/skills/pf2e-character-builder/SKILL.md
.github/workflows/publish.yml  # tag-triggered PyPI release
```

## Releasing

`.github/workflows/publish.yml` publishes to PyPI when a semver tag is
pushed. It uses [Trusted
Publishing](https://docs.pypi.org/trusted-publishers/), so no PyPI API token
exists anywhere — PyPI verifies a short-lived OIDC token minted by GitHub for
this specific repository, workflow and environment.

**One-time setup on PyPI**, before the first release. Since the project isn't
on PyPI yet, add a *pending* publisher at
<https://pypi.org/manage/account/publishing/>:

| Field | Value |
| --- | --- |
| PyPI project name | `pf2e-mcp` |
| Owner | `rjenks` |
| Repository name | `pf2e-mcp` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Then create a matching `pypi` environment under the repository's Settings →
Environments. Adding yourself as a required reviewer there makes every
release pause for a manual approval, which is worth doing given that a
version number, once uploaded, can never be reused.

To cut a release, bump the version, commit, then tag:

```bash
# edit pyproject.toml: version = "0.2.0"
git commit -am "Release 0.2.0"
git tag v0.2.0
git push && git push --tags
```

The workflow refuses to publish if the tag and `pyproject.toml` disagree, so
a forgotten version bump fails loudly rather than burning the wrong version
number. It also verifies `LICENSE`, `NOTICE.md` and `LICENSES/OFL-1.1.txt`
are present in the built wheel — the fonts it bundles are OFL-licensed, and
that licence has to travel with them.

## Current status

Level 1–20 character creation and leveling across all ancestries,
backgrounds, and classes, including spellcasting and equipped-armor AC.
See the [GitHub issues](https://github.com/rjenks/pf2e-mcp/issues) for tracked gaps.
