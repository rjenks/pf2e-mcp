# Notice

This file covers licensing for third-party content this project *uses* but
does not itself distribute, as distinct from `LICENSE` (Apache 2.0), which
covers this project's own original source code.

## What's in this repository

Everything in `src/`, `scripts/`, and the project's documentation is
original code and writing. This repository stores **no Pathfinder 2nd
Edition game database, no rules text or descriptive prose, no stat blocks,
and no artwork** -- none of the ingested content is vendored here, and none
ever has been (`git log` has only ever carried source files). The game data
this project serves is downloaded on the end user's own machine at build
time; see "What this project downloads at build/run time" below.

What the repository *does* contain, which the above should not be read to
deny:

- **Pathfinder game terminology and proper nouns used as identifiers.**
  Skill names, proficiency ranks, ability scores, magical traditions, and
  the names of specific classes, class features, subclasses, feats, and
  spells appear throughout the source as dictionary keys, lookup values,
  enum members, and match targets -- for example the skill list and
  `{"trained": 2, "expert": 4, ...}` bonus table in
  `server/pf2e_math.py`, and the class-feature and hybrid-study names in
  `ingestion/prerequisite_overrides.json`. A handful of Golarion proper
  nouns (deity names) appear in docstrings purely as filename examples for
  the character sheet's symbol lookup.
- **Short verbatim prerequisite phrases used as exact-match keys.**
  `ingestion/prerequisite_overrides.json` keys its hand-curated entries on
  the prerequisite string exactly as it appears in the source data (e.g.
  `"class granting no more Hit Points per level than 10 + your
  Constitution modifier"`), because an inexact key would silently stop
  matching after errata. These are a few dozen short mechanical clauses,
  not rules text carried for its own sake.
- **Rules facts embedded in the algorithms.** Numbers like the ability
  boost levels `(5, 10, 15, 20)`, the minimum character level for each
  skill proficiency rank, and the derived-statistic formulas in
  `server/pf2e_math.py` and `server/build_tools.py` are game rules
  expressed as code. They are what makes this software do anything.
- **Documentation that discusses specific rules by name.** `README.md`,
  `AGENTS.md`, the character-builder skill, and this project's GitHub
  issues describe behaviour and known gaps in terms of the actual feats,
  spells, classes, and prerequisites involved, because a gap cannot be
  usefully documented in the abstract.
- **The full text of the Open Game License 1.0a**, reproduced below and
  again in `server/sheet.py` so that rendered character sheets carry it.
  Reproducing it is a requirement of the license, not incidental use.
- **Three subsetted, OFL-licensed Noto font files**, base64-encoded in
  `src/pf2e_mcp/server/sheet_assets.py`, with the SIL Open Font License 1.1
  reproduced in full at `LICENSES/OFL-1.1.txt` as that license requires (see
  "Vendored fonts" below). These are the only third-party binary assets in
  the repository, and they are not Paizo's.

The distinction being drawn is between *bulk content* -- the rules text,
descriptions, and narrative material that the licenses govern, which is
never stored here -- and *references to the game's mechanics and
vocabulary*, which are unavoidable in software whose entire purpose is to
compute Pathfinder 2nd Edition character statistics, and which serve
identification and interoperability rather than substituting for Paizo's
published material. Nothing in this section is offered as a legal
conclusion; it is a description of the contents so a reader can judge for
themselves.

## Vendored fonts

`src/pf2e_mcp/server/sheet_assets.py` contains three font files as base64
WOFF2: **Noto Serif**, **Noto Serif Italic**, and **Noto Sans**, subset to
the Latin glyph range the HTML character sheet renderer prints, with the
variable `wght` axis preserved. They are
`Copyright 2022 The Noto Project Authors`
(https://github.com/notofonts/latin-greek-cyrillic) and licensed under the
**SIL Open Font License, Version 1.1** (https://openfontlicense.org).

**The full OFL text, with that copyright notice, is reproduced in
[`LICENSES/OFL-1.1.txt`](LICENSES/OFL-1.1.txt).** OFL Condition 2 requires
that each redistributed copy of the Font Software carry both the copyright
notice and the license itself -- a link to openfontlicense.org is not
sufficient on its own -- so that file, not this section, is what satisfies
the condition for this repository.

The OFL permits redistribution of the fonts, bundled with other software,
in original or modified (here, subsetted and format-converted) form, at no
cost, provided the licence and copyright notice travel with them and the
fonts are not sold on their own. Three consequences worth spelling out,
since this project both redistributes the fonts and generates files that
embed them:

- The subsets retain name ID 0, so the exact upstream copyright notice
  travels inside each font binary as machine-readable metadata. Name IDs 13
  and 14 (the license notice and URL) were dropped by `pyftsubset`'s default
  `--name-IDs=0,1,2,3,4,5,6`, so the binaries alone do **not** carry the
  license -- the human-readable copies described here are what cover it.
- **A rendered sheet is itself a redistribution of the Font Software**, since
  the WOFF2 bytes are embedded in the HTML. So every sheet reproduces the
  copyright notice and the full OFL text in its "Notices & Attribution"
  section (`_OFL_PARAS` in `server/sheet.py`), exactly as it already does for
  the OGL, and for the same reason: a sheet emailed to a player has to
  satisfy Condition 2 on its own, without this file. That adds roughly 4 KB
  and typically spills the attribution section onto a second printed page --
  it is the last section of the sheet, so anyone who doesn't want the license
  pages can simply not print them.
- The subset files are not renamed. OFL Section 3 only requires a Reserved
  Font Name change when one exists; the Noto families reserve no font names,
  so a subsetted "Noto Serif" may keep its name. The CSS family names
  (`SheetSerif`, `SheetSans`) are internal aliases, not a rebrand of the
  fonts themselves.

They are vendored rather than subset at request time so that rendering has
no dependency on `fonttools`, on `brotli`, or on which fonts happen to be
installed on the machine running the server.

## What this project downloads at build/run time

At build time (`python -m pf2e_mcp.ingestion.build`), this project downloads
structured Pathfinder 2nd Edition game data from the public, open-source
**foundryvtt/pf2e** project (https://github.com/foundryvtt/pf2e), maintained
by the PF2E for Foundry VTT volunteer development team and published under
Foundry Gaming LLC's license arrangement with Paizo Inc. That download is
not vendored or redistributed by this repository -- it happens fresh on the
end user's own machine, from foundryvtt/pf2e's own public GitHub releases,
each time the build step is run.

This project's own tools (`rules_search`, `rules_get_entry`, and the rest of
the `rules_*`/`build_*` tool surface) then serve pieces of that downloaded
content back to whoever is using this software. That is a real act of
redistribution on this software's part, even though no game data is stored
in this git repository -- this section exists to cover that.

## Licensing of the underlying game content

The Pathfinder 2nd Edition game content downloaded and served by this
project is licensed by its rights holder, **Paizo Inc.**, under two
licenses, depending on which sourcebook a given piece of content is from:

- **Open Game License Version 1.0a ("OGL")** -- covers legacy/pre-Remaster
  Pathfinder 2nd Edition content (Core Rulebook through 2022-era books).
  Full terms reproduced below.
- **ORC License** -- covers Pathfinder 2nd Edition Remaster content
  (Player Core, GM Core, and everything published from late 2023 onward).
  Registered at the U.S. Copyright Office, Library of Congress, TX
  9-307-067, and available online at multiple mirrors; not reproduced in
  full here (see "Where the attribution lists live" below for why).

This project's database distinguishes Remaster from legacy content per
entry (the `is_remaster` column in the ingested `entries` table), so it is
possible to determine which of the two licenses applies to any specific
piece of content this software serves.

### Where the attribution lists live

Both licenses require reproducing a running list of every sourcebook whose
content is included (OGL Section 15's "Copyright Notice"; the ORC License's
"Attribution Notice"). In `foundryvtt/pf2e`'s own copies, both of those
lists are already hundreds of entries long and grow with every new Paizo
release. Freezing a copy of either list into this file would go stale the
moment Paizo publishes something new -- and since this project always
downloads the *current* `foundryvtt/pf2e` release at build time rather than
a fixed snapshot, the only attribution list that stays accurate is
`foundryvtt/pf2e`'s own, live:

- OGL Copyright Notice: https://github.com/foundryvtt/pf2e/blob/master/static/licenses/OpenGameLicense.md
- ORC Attribution Notice: https://github.com/foundryvtt/pf2e/blob/master/static/licenses/ORCLicense.md

### Open Game License Version 1.0a -- full text

The following text is the property of Wizards of the Coast, Inc. and is
Copyright 2000 Wizards of the Coast, Inc. ("Wizards"). All Rights Reserved.

1. **Definitions:** (a)"Contributors" means the copyright and/or trademark
   owners who have contributed Open Game Content; (b)"Derivative Material"
   means copyrighted material including derivative works and translations
   (including into other computer languages), potation, modification,
   correction, addition, extension, upgrade, improvement, compilation,
   abridgment or other form in which an existing work may be recast,
   transformed or adapted; (c) "Distribute" means to reproduce, license,
   rent, lease, sell, broadcast, publicly display, transmit or otherwise
   distribute; (d)"Open Game Content" means the game mechanic and includes
   the methods, procedures, processes and routines to the extent such
   content does not embody the Product Identity and is an enhancement over
   the prior art and any additional content clearly identified as Open
   Game Content by the Contributor, and means any work covered by this
   License, including translations and derivative works under copyright
   law, but specifically excludes Product Identity. (e) "Product Identity"
   means product and product line names, logos and identifying marks
   including trade dress; artifacts; creatures characters; stories,
   storylines, plots, thematic elements, dialogue, incidents, language,
   artwork, symbols, designs, depictions, likenesses, formats, poses,
   concepts, themes and graphic, photographic and other visual or audio
   representations; names and descriptions of characters, spells,
   enchantments, personalities, teams, personas, likenesses and special
   abilities; places, locations, environments, creatures, equipment,
   magical or supernatural abilities or effects, logos, symbols, or
   graphic designs; and any other trademark or registered trademark
   clearly identified as Product identity by the owner of the Product
   Identity, and which specifically excludes the Open Game Content; (f)
   "Trademark" means the logos, names, mark, sign, motto, designs that are
   used by a Contributor to identify itself or its products or the
   associated products contributed to the Open Game License by the
   Contributor (g) "Use", "Used" or "Using" means to use, Distribute,
   copy, edit, format, modify, translate and otherwise create Derivative
   Material of Open Game Content. (h) "You" or "Your" means the licensee
   in terms of this agreement.
2. **The License:** This License applies to any Open Game Content that
   contains a notice indicating that the Open Game Content may only be
   Used under and in terms of this License. You must affix such a notice
   to any Open Game Content that you Use. No terms may be added to or
   subtracted from this License except as described by the License
   itself. No other terms or conditions may be applied to any Open Game
   Content distributed using this License.
3. **Offer and Acceptance:** By Using the Open Game Content You indicate
   Your acceptance of the terms of this License.
4. **Grant and Consideration:** In consideration for agreeing to use this
   License, the Contributors grant You a perpetual, worldwide,
   royalty-free, non-exclusive license with the exact terms of this
   License to Use, the Open Game Content.
5. **Representation of Authority to Contribute:** If You are contributing
   original material as Open Game Content, You represent that Your
   Contributions are Your original creation and/or You have sufficient
   rights to grant the rights conveyed by this License.
6. **Notice of License Copyright:** You must update the COPYRIGHT NOTICE
   portion of this License to include the exact text of the COPYRIGHT
   NOTICE of any Open Game Content You are copying, modifying or
   distributing, and You must add the title, the copyright date, and the
   copyright holder's name to the COPYRIGHT NOTICE of any original Open
   Game Content you Distribute.
7. **Use of Product Identity:** You agree not to Use any Product Identity,
   including as an indication as to compatibility, except as expressly
   licensed in another, independent Agreement with the owner of each
   element of that Product Identity. You agree not to indicate
   compatibility or co-adaptability with any Trademark or Registered
   Trademark in conjunction with a work containing Open Game Content
   except as expressly licensed in another, independent Agreement with
   the owner of such Trademark or Registered Trademark. The use of any
   Product Identity in Open Game Content does not constitute a challenge
   to the ownership of that Product Identity. The owner of any Product
   Identity used in Open Game Content shall retain all rights, title and
   interest in and to that Product Identity.
8. **Identification:** If you distribute Open Game Content You must
   clearly indicate which portions of the work that you are distributing
   are Open Game Content.
9. **Updating the License:** Wizards or its designated Agents may publish
   updated versions of this License. You may use any authorized version
   of this License to copy, modify and distribute any Open Game Content
   originally distributed under any version of this License.
10. **Copy of this License:** You MUST include a copy of this License
    with every copy of the Open Game Content You Distribute.
11. **Use of Contributor Credits:** You may not market or advertise the
    Open Game Content using the name of any Contributor unless You have
    written permission from the Contributor to do so.
12. **Inability to Comply:** If it is impossible for You to comply with
    any of the terms of this License with respect to some or all of the
    Open Game Content due to statute, judicial order, or governmental
    regulation then You may not Use any Open Game Material so affected.
13. **Termination:** This License will terminate automatically if You
    fail to comply with all terms herein and fail to cure such breach
    within 30 days of becoming aware of the breach. All sublicenses shall
    survive the termination of this License.
14. **Reformation:** If any provision of this License is held to be
    unenforceable, such provision shall be reformed only to the extent
    necessary to make it enforceable.
15. **COPYRIGHT NOTICE:** Open Game License v 1.0 Copyright 2000, Wizards
    of the Coast, Inc. The full, current per-sourcebook copyright notice
    (which titles' content is included) is maintained live by
    `foundryvtt/pf2e` -- see "Where the attribution lists live" above
    rather than a snapshot here.

### ORC License -- summary

The ORC License's own Section 1 notice, as reproduced in `foundryvtt/pf2e`:
"This product is licensed under the ORC License located at the Library of
Congress at TX 9-307-067 and available online at various locations. All
warranties are disclaimed as set forth therein."

Its Section 3 ("Reserved Material") is the direct source for the next
section below -- it explicitly carves out "all trademarks, registered
trademarks, proper nouns (characters, deities, locations, etc.[...]),
artworks, characters, dialogue, locations, organizations, plots,
storylines, and trade dress" from the license's open grant.

## Content not covered by an open license

Both licenses above explicitly exclude a category of content -- OGL calls
it "Product Identity," the ORC License calls it "Reserved Material" -- that
covers proper nouns, deity write-ups, named characters/locations, artwork,
and narrative/story content. That material is Paizo Inc.'s ordinary
copyrighted content, not freely licensed for redistribution the way
mechanical rules text is. `foundryvtt/pf2e` includes this content under
Foundry Gaming LLC's own specific license arrangement with Paizo Inc.; that
arrangement does not automatically extend to third parties, including this
project.

Two things this project does about that, neither of them a complete fix:

- **Bestiary, Monster Core, NPC Core, and full-adventure content is
  excluded from ingestion entirely** (`ingestion/build.py`), not just
  flagged. That's the single largest concentration of Product Identity/
  Reserved Material in the upstream data -- unique named creatures and
  adventure-specific narrative, by the dozens of sourcebooks -- and none
  of it is used by this project's character-building tools, so excluding
  it is a pure reduction in exposure with no functional cost.
- **What remains is classified, not filtered.** Content this project *does*
  need for character building but that's still predominantly proper nouns
  (deities, most visibly) is still ingested and served, but every
  `rules_*` tool result now carries a `license` field
  (`server/licensing.py`) noting which regime applies (OGL vs. ORC, from
  the `is_remaster` flag) and whether the entry is likely Product Identity/
  Reserved Material, based on which pack it came from. This is a
  pack-level heuristic, not a per-entry legal determination -- a deity
  entry's mechanical grants (a domain's initial spell, a favored weapon)
  are genuine game mechanics even though the same entry's flavor text
  (title, dogma, edicts) is squarely Product Identity, and the classifier
  doesn't distinguish that. It exists so a caller *can* tell the two apart
  before reusing content, not to guarantee they're already separated.

If this project is published or distributed, both of the above should
still be disclosed explicitly rather than assumed to be a complete
resolution -- they reduce risk, they don't eliminate it.

## Trademark disclaimer

**Pathfinder** and associated marks are trademarks of **Paizo Inc.**
This project is not affiliated with, endorsed by, or sponsored by
**Paizo Inc.**, and use of Paizo Inc.'s trademarks here is solely to
describe the compatibility/subject matter of this software.

## Attribution

This project depends on data from **foundryvtt/pf2e**
(https://github.com/foundryvtt/pf2e), maintained by the PF2E for Foundry
VTT volunteer development team and published under Foundry Gaming LLC's
license arrangement with Paizo Inc. See that project's own repository for
its full license terms. The underlying game content is licensed under the
Open Game License 1.0a (legacy content) and the ORC License (Remaster
content) -- see "Licensing of the underlying game content" above.
