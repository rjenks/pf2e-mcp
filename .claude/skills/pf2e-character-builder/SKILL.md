---
name: pf2e-character-builder
description: Collaboratively build a Pathfinder 2e Remastered character with a user, using the pf2e-mcp tools for all rules lookups, prerequisite checks, and math. Use whenever the user wants to build, level up, or validate a PF2e character.
---

# Building a PF2e character with pf2e-mcp

This skill is the conversational playbook for the `pf2e-mcp` tools. It does not
contain rules content itself -- PF2e content changes on a near-weekly cadence
via errata, and the whole point of the `rules_*` / `build_*` tools is that
they're regenerated from the current official data, so they're a better source
of truth than recalled training knowledge. **Always call a tool instead of
answering a rules or eligibility question from memory.**

## Tool namespaces

- `rules_*` (`rules_search`, `rules_get_entry`, `rules_related`, `rules_explain`,
  `rules_data_version`, `rules_list_variant_rules`): general reference lookup,
  independent of any character.
- `build_*` discovery and math (`build_list_ancestries`, `build_list_backgrounds`,
  `build_list_classes`, `build_list_available_feats`,
  `build_list_ability_boost_options`, `build_list_skill_increase_options`,
  `build_check_prerequisite`, `build_validate_build`,
  `build_calculate_derived_stats`, `build_get_level_up_choices`,
  `build_list_available_spells`, `build_list_equipment`): everything that
  answers "what can this character do".
- `build_character_*` (`build_character_schema`, `build_validate_character`,
  `build_character_at_level`, `build_import_pathbuilder`,
  `build_export_pathbuilder`): the character _file_ -- its shape, its
  validity, and conversion in and out of Pathbuilder.
- `pfs_*`: Organized Play adventure lookup and chronicle validation.

## The character file

**A character is one YAML file and you edit it in place.** Not a JSON blob you
carry in your head across the conversation -- write the file early, keep
writing to it, and read it back when you need it.

    characters/AshKordun-Orc-Cleric/AshKordun.pf2e.yaml

The folder is the character's name, ancestry and class -- each section
PascalCase, the sections joined with hyphens, so a hyphen always means a
section boundary and a two-word name never looks like one.

Everything else about that character lives in the same folder: rendered sheets,
chronicle scans, a portrait. The portrait can be stored as a standalone image
file (e.g. `DrozaTheArbiter.jpeg`) or embedded directly into `identity.portrait` in the YAML
as a base64 data URI (`data:image/jpeg;base64,...`).

**Portrait Aspect Ratio & Defaults**: Sheet rendering reserves a **9:16 portrait space** on Page 1.
The renderer uses `object-fit: contain` so images of any aspect ratio scale cleanly within
the reserved 9:16 area without cropping, clipping, or distortion. When no specific character
portrait is provided or auto-discovered, the sheet automatically falls back to a default generic
portrait mapped from the character's class (`characters/generic-spellcaster.jpeg` for spellcasters
such as Wizard, Cleric, Druid, Sorcerer, Bard, etc., or `characters/generic-melee.jpeg` for martial or unknown classes).

**Call `build_character_schema` before writing your first one** -- the schema carries a description on every field and is the actual specification, not this document.

Four things about the format change how you work:

- **The plan is the source of truth and state is derived from it.** The file
  records the _choices_ made at each level from 1st to 20th and nothing about
  their consequences -- no proficiency ranks, no Hit Points, no attribute
  scores. `build_character_at_level` replays the plan and computes those. So
  never write a derived number into the file, and never ask the user to
  re-state one.
- **A plan may run past where the character is.** `identity.currentLevel` says
  how far they have actually got; everything above it is intent. Recording a
  20th-level plan for a 3rd-level character is the point of the format, not an
  overreach -- but say which is which when you present it.
- **Rules content is referenced by slug**, from the `slug` field on any
  `rules_*`/`build_list_*` result -- `soul-warden-dedication`, not "Soul Warden
  Dedication". A name is ambiguous ("Spirit Familiar" is two different feats)
  and the validator rejects one where a slug belongs.
- **Reasoning is a field, not a separate document.** See step 12.

Two settings are _not_ in the file and must be held across the conversation and
passed explicitly, because they describe the table rather than the character:
`pfs_legal_only` on `build_validate_build`, and `include_legacy` on every
discovery tool (defaults to `False`, Remaster-only -- pass `True` for the whole
rest of the build once the user asks for legacy content, don't ask per call).
Variant rules _are_ in the file, at `build.variantRules`, but
`build_validate_build` and `build_get_level_up_choices` still take them as an
argument -- pass what the file says.

## Conversational flow

0. **State the defaults up front, once, and offer to customize -- don't
   interrogate the user with a checklist before they've even picked a
   concept.** Pathbuilder itself exposes a large options panel (variant
   rules, legacy/remastered content toggles, Mythic, Automatic Bonus
   Progression, Legacy GMG variants, and a pile of app-level settings this
   project has no equivalent for) -- asking through all of that up front is
   exactly the friction to avoid. Say something like: _"I'll default to
   Remaster-only content, no variant rules, and not PFS-restricted unless
   you tell me otherwise -- want to keep those defaults, or customize
   anything first?"_ Only drill into specifics (which variant rule, PFS
   yes/no, legacy content yes/no) if the user actually says they want to
   customize. This covers three independent preferences, all held across
   the conversation and threaded into every relevant tool call for the rest
   of the build (see "Tool namespaces" above for which calls take which):
   - **Legacy content.** Default `include_legacy=False` (Remaster/ORC
     content only). If the user wants legacy included, switch to `True` for
     the rest of the build. Worth knowing before it comes up: `False`
     hides a lot more than old-terminology/alignment-era stuff -- 54% of
     backgrounds and 38% of ancestries are legacy-flagged simply because
     they haven't been individually reprinted under ORC yet, not because
     they've been retired, so if a concept the user wants doesn't turn up
     under the default, that's worth mentioning as a reason to check with
     legacy content included rather than concluding it doesn't exist.
   - **Variant/optional rules.** Default none. If the user wants to
     customize, call `rules_list_variant_rules(category=
'character-building')` and offer the list -- most commonly Free
     Archetype and/or Ancestry Paragon, but the full list also includes
     Automatic Bonus Progression, Proficiency without Level, Gradual
     Attribute Boosts, Stamina, Mythic Characters, and Level 0 Characters.
     Only Free Archetype and Ancestry Paragon are mechanically enforced
     right now (extra slots via `build_get_level_up_choices`, budget checks
     via `build_validate_build`) -- if another one is chosen, say plainly
     that you'll track it by hand rather than the tools doing it for you
     (e.g. Proficiency without Level changes the whole proficiency-bonus
     formula; nothing here recomputes that automatically).
   - **Pathfinder Society legality.** Default not PFS-restricted (a home
     game). If the user says this is a PFS-legal build, pass
     `pfs_legal_only=True` to every `build_validate_build` call from here
     on, and treat its PFS warnings as real signal -- but say clearly, the
     first time it comes up, that this is a **best-effort heuristic**
     (common rarity = legal, uncommon/rare/unique = flagged restricted,
     plus a small hand-curated override list), **not** an authoritative
     read of Paizo's current Additional Resources document. Recommend the
     user cross-check anything flagged, and anything genuinely
     uncommon-but-PFS-legal or common-but-PFS-banned, against that document
     directly -- don't let a "legal" result read as a guarantee.

   If the user changes their mind on any of these mid-build, that's a
   backtrack -- see step 9 below.

1. **Concept before mechanics.** Before calling any discovery tool, ask what
   kind of character the user has in mind -- playstyle, theme, party role,
   or "surprise me." Don't front-load a wall of ancestry/class options before
   you know what they're going for; use their answer to pick a sensible
   `filter` argument for `build_list_ancestries` / `build_list_backgrounds` /
   `build_list_classes` rather than dumping the full list.

2. **When the user asks to optimize/min-max/build the strongest version of a
   character -- not just "a character" -- research a build-around before
   touching mechanics.** Once the class (or a short class list) is known
   from step 1, spend a pass identifying the specific feature, feat chain,
   or subclass option (a hybrid study, doctrine, bloodline, muse, etc.) the
   rest of the build should anchor around, before working through ancestry/
   background/feats level by level. Prefer real player-written optimization
   guides over reasoning from raw feat text alone or from recalled training
   knowledge -- per this project's CLAUDE.md, start from the class guides
   indexed by Zenith Games' "Guide to the Guides"
   (https://zenithgames.blogspot.com/2019/09/pathfinder-2nd-edition-guide-to-guides.html)
   via `WebFetch`, and treat RPGBOT with more skepticism than other sources
   rather than defaulting to it. This matters for two concrete reasons, both
   hit for real building a Magus in this project's own history:
   - **A build-around anchors every downstream choice so they reinforce
     each other; picking feat-by-feat in isolation produces a pile of
     individually-fine choices that don't.** (A Starlit Span Magus's
     ancestry, key ability, weapon, and skill investment should all serve
     the ranged-Spellstrike-plus-Recall-Knowledge combo once that combo is
     the anchor -- not get decided independently level by level.)
   - **A guide surfaces synergies a feat-by-feat rules read can miss**,
     especially where this project's own tooling has gaps (see Known
     limits) -- a real Magus guide would likely flag `Magus's Analysis` as
     a strong, commonly-recommended pick, which is exactly the feat a
     `build_list_available_feats` bug once silently hid from a
     from-scratch, guide-free build (see this project's GitHub issues).

   Once a guide points at a specific feat/feature/combo, **verify it still
   exists and is still legal against this project's own data**
   (`rules_search`/`rules_get_entry`/`build_check_prerequisite`) before
   committing -- a guide can be pre-errata, pre-remaster, or simply wrong
   about current game state; this project's ingested data is the current
   source of truth for _legality_ even when a guide is the better source
   for _strategy_. Present the build-around to the user as part of the
   concept conversation, so they can redirect before mechanics start rather
   than discovering the anchor after the fact.

3. **One major decision at a time.** Walk ancestry -> heritage -> background ->
   class -> attribute boosts -> skills -> feats, in that order (it's the order
   the boosts/prerequisites actually depend on). Don't ask the user to pick
   five things in one message.

   **A source that grants two boosts (ancestry's two free boosts, a
   background's "X-or-Y plus one free" pair) can never put both on the same
   ability -- each boost from that source must land on a different ability.**
   This is a real, repeated failure mode, not a hypothetical: it's been
   corrected by the user three separate times across different builds,
   most recently when a background's "Strength or Constitution, plus one
   free" pair was about to be spent as Strength twice to argue one
   background was more Strength-efficient than another (it wasn't -- both
   backgrounds can put exactly one boost into Strength, since the free
   boost is unrestricted in either case). Don't reason about which
   ability a background/ancestry's free boost "should" hit from memory or
   in a table -- call `build_list_ability_boost_options(character,
source=...)` for that source, record the chosen ability into
   `character.abilities.breakdown` immediately, then call it again for
   that same source's second boost before offering it -- the second call
   will correctly exclude whatever the first one just used. This applies
   separately per source (ancestry's pair, background's pair, the 4 free
   boosts at character creation, and again at every 5th-level boost
   milestone) -- a later source can still boost an ability an earlier
   source already touched (e.g. class key ability landing on the same
   ability ancestry already boosted, exactly how Str reaches +4 at 1st in
   a typical Str-primary build), it's only _within_ one source that a
   repeat is illegal.

   **Say attribute modifiers, never scores: `+4`, not `18`.** The Remaster
   works in modifiers -- Player Core starts each modifier at +0, a boost adds
   1 to it and a flaw subtracts 1 -- and every rule that consumes an attribute
   reads the modifier. The 10-to-20 score is legacy notation that survives
   because Pathbuilder's export stores it. The character file records boosts,
   not scores, so there is nowhere for a score to hide in it -- a Pathbuilder
   _export_ still carries the `abilities` dict, and that is the file format, so
   don't "fix" it there. Keep scores out of everything you say to the user. See
   AGENTS.md.

   Two things this makes harder to get wrong. **Boost arithmetic:** in
   modifiers a boost is simply +1, with the sole exception that an attribute
   at +4 or higher needs two boosts to advance -- no 18-threshold special case
   to forget mid-table.

   **Half-steps only matter at 20th.** A boost that doesn't move the modifier
   looks wasted, but below 20th it is half a step that completes at the next
   milestone -- and it gets there one milestone _sooner_ than an even score
   would, so it is usually good. Only at 20th is there nothing left to
   complete it. Don't tell a mid-career character their odd score is a
   mistake; `build_validate_build` deliberately reports this at 20th alone.

   When a 20th-level array does have a half-step, **redirect the 20th-level
   boost** -- it is the one with no later milestone to complete it, and moving
   an earlier boost instead delays a primary attribute for five levels for no
   gain at the finish. The exception is worth checking: if the redirect target
   is something the character actually uses (Constitution for hit points, not
   Strength on an archer), taking it at 15th buys five levels of benefit and
   may be worth the delay.

4. **Pull the class's initial proficiency baseline before hand-building
   anything.** As soon as class is chosen, call `build_list_classes` (filter
   to that class name) and use its `hp`/`perception`/`fortitude`/`reflex`/
   `will`/`trained_skills`/`attacks`/`defenses` fields to seed the
   character's `proficiencies` dict -- don't fill these in from memory.
   Proficiency ranks come back already in this project's 0/2/4/6/8
   convention, ready to drop straight into the character JSON. This is a
   real, previously-hit failure mode, not a hypothetical: a build was hand-
   constructed with Champion's Will save as trained when it's actually
   expert, and only caught several turns later when cross-checked against
   Archives of Nethys. Note `trained_skills.fixed` can be incomplete for a
   class with a "trained in X **or** Y" choice (e.g. Fighter's
   Acrobatics-or-Athletics) -- that specific pattern isn't captured by
   ingestion yet, so still ask the user which of the two they want.

   **`trained_skills.additional` is the class's flat baseline only -- it
   excludes the universal Intelligence-modifier bonus to additional
   trained skills every class grants at 1st level** ("...becomes trained
   in a number of skills equal to [class value] plus your Intelligence
   modifier" -- Player Core, Skills step of character creation). Add
   `max(0, Int modifier)` on top of the returned `additional` number
   yourself before presenting the free-skill-pick count to the user --
   another previously-hit failure mode, not a hypothetical: a level-10
   Magus build with Int 14 (+2) at 1st level was first given only 2
   additional trained skills instead of the correct 4. `build_validate_build`
   now carries a floor check for this (`_validate_trained_skill_count`) as
   a safety net, but don't rely on it catching what should be gotten right
   the first time.

5. **Use discovery tools, don't reason about eligibility yourself.** When
   presenting feat choices, call `build_list_available_feats` with the
   character's current state rather than recalling which feats "seem" legal.
   Its response has two buckets:
   - `available`: prerequisites confirmed met. Present these normally.
   - `unconfirmed`: at least one prerequisite couldn't be auto-verified
     (~16% of feats have prerequisite text too free-form to parse). Present
     these separately and say plainly that they need manual double-checking --
     use `rules_get_entry` on the feat to show the user the actual prerequisite
     text so they can judge for themselves. Never quietly upgrade an
     `unconfirmed` feat to look like a confirmed one.

6. **Group large option lists thematically**, don't paste raw arrays. If
   `build_list_available_feats` returns 40+ available feats, cluster them by
   what they do (offense/defense/utility/skill-boosting) in your own words
   rather than listing all 40.

7. **Read and understand the traits on every feat, item, and spell before
   recommending it -- a name and a damage die are not enough to know
   whether something is actually good.** A previously-hit failure mode: a
   level-10 "fully optimized" Magus was equipped with a Composite Longbow
   purely on "biggest die, best range" reasoning, without reading its
   traits. Two things only the traits reveal: `propulsive` adds half your
   _Strength_ modifier to damage -- worthless on a Dex-based build with
   Str +0, making the Composite version pure wasted gold over a plain
   Longbow; and Longbow/Composite Longbow both carry `volley 30`, a -2
   penalty to attack rolls against anything within 30 feet, which taxes
   the character's entire core combat action (Spellstrike) every time a
   fight closes to ordinary combat range -- exactly the situation a
   Shortbow (no Volley) avoids at the cost of one damage step. Neither
   problem is visible from the item's name, level, or price. Before
   recommending a feat, spell, or piece of equipment as part of an
   "optimized" build, pull its full entry (`rules_get_entry`, or the
   `traits` array already present on a `rules_search`/`build_list_equipment`
   result) and reason through what each trait actually does mechanically
   -- action-economy traits (`reload`, `manipulate`, `concentrate`),
   conditional-penalty traits (`volley`, `propulsive`, `nonlethal`),
   and interaction traits (`attack`, `incapacitation`, `finesse`) all
   change whether something is actually good for _this_ character, not
   just whether it sounds good. This applies to every recommendation, not
   only weapons -- a spell's or feat's traits are exactly as load-bearing
   as an item's.

8. **Validate before locking anything in.** After the character has a full
   set of level-1 choices (or after any level-up), call `build_validate_build`
   -- passing `variant_rules` and `pfs_legal_only` per step 0's answers --
   before treating the build as final. Surface `errors` as blocking ("this
   needs to change before we continue") and `warnings` as "worth double
   checking" -- don't silently ignore either.

9. **Backtracking re-runs discovery, it doesn't hand-patch one field.** If the
   user changes an earlier decision (e.g. swaps ancestry after already picking
   feats), don't just edit `build.ancestry` and leave stale choices in place --
   re-run `build_list_available_feats`/`build_check_prerequisite` against the
   changed character to see what's still valid, and flag anything that no
   longer qualifies via `build_validate_build`.

   Check `dependsOn` before changing anything. A choice recorded as depending
   on the pick being removed has just lost its reason to exist, and
   `build_validate_character` reports it -- that is what the field is for.
   Mark a swapped slot `status: retrained` and move the old pick into
   `alternatives` with a note, rather than deleting the history.

10. **Derived stats are always computed, never eyeballed.** Use
    `build_calculate_derived_stats` for AC/saves/Perception/skills/HP/class DC/
    spell DC rather than doing the arithmetic yourself -- PF2e's
    untrained-vs-trained proficiency stacking is easy to get subtly wrong.

11. **Leveling up**: call `build_get_level_up_choices` for the target level
    -- passing `variant_rules` per step 0's answer -- to see what actually
    unlocks (feat categories/counts, skill increases, attribute boosts,
    auto-granted class features, plus any `variant_rule_notes`) before asking
    the user what they want. Don't assume every level looks the same, and
    don't assume a plain "1 ancestry feat at 1/5/9/13/17" schedule if Ancestry
    Paragon is active, or that level 2/4/6/... only grants a normal class
    feat if Free Archetype is active.

12. **Write the reasoning into the file as you go, not afterwards.** The
    `note` on each choice is where a build's argument lives. This used to be a
    separate markdown document written at the end; it is a field now, and
    writing it at the moment of the decision is both easier and more honest
    than reconstructing it later.

    What goes in a `note` is **why this pick, for this character** -- what it
    combines with, what it is instead of, what breaks without it. What does
    _not_ go in is what the feat does: that is in the rules database, it will
    be re-read from there whenever a sheet is rendered, and a copy in the
    character file goes stale at the next errata.

    Three neighbouring fields carry the rest of the argument:
    - **`alternatives`** on a choice: options considered and rejected, each
      with a note. The reason a build did _not_ take the obvious feat is
      exactly what gets re-litigated when it is picked up again months later.
    - **`dependsOn`**: other picks this one exists to serve. "The dedication is
      only here to reach Combat Grab at 4th" is the single most common thing a
      build's prose says, and as a field it becomes checkable -- the validator
      catches a dependency on a pick that was retrained away, or on one taken
      at a later level than the choice needing it. Record the forward direction
      only; the reverse is found by searching.
    - **`role`** (`keystone`, `core`, `support`, `prerequisite`, `filler`,
      `flexible`): what the pick is doing for the build. Use it sparingly --
      it earns its place by marking the two or three picks that cannot change
      and the ones that can, and a build where everything is a keystone has
      said nothing.

    Longer argument that is not about one pick -- how a turn works, why one
    subclass beat another, what the build does against an on-level boss --
    goes in `notes[]` as headed markdown. Narrative goes in `story`:
    `backstory`, `introduction` (written to be read aloud to a new party),
    `roleplaying`, `appearance`.

    **Tactical quick-reference cards (`quickReference`)**: Combat flow reminders,
    action economy budgets ("One Action Left", "Open the Round", "Follow a Hit"),
    reaction triggers, and critical specialization summaries go in
    `quickReference`. Always author this section catered to the character's
    active `identity.currentLevel`.
    - **Use dynamic interpolation tokens instead of hardcoded numbers**:
      e.g. `{athletics}`, `{intimidation}`, `{dc:class}`, `{dc:spell}`,
      `{save:fortitude}`, `{dc:fortitude}`, `{modifier:str}`, `{ac}`, `{hp}`,
      `{speed}`, `{level}`. Tokens resolve dynamically at render time against
      the replayed stats, so numbers never drift out of sync when gear or
      proficiency changes.
    - **Keep it maintained as the character levels up or retrains**: When
      advancing `identity.currentLevel`, review `quickReference` to ensure
      the tactical reminders reflect the character's newly available feats,
      stances, reactions, and combos.
    - **Level gating is automatic**: `build_render_character_sheet`
      renders `quickReference` pages when generating a sheet for the active
      `currentLevel`, and omits it when previewing other levels.

    **Notes about the _tools_ do not go in the character file at all.** A
    calculator that got something wrong or a gap in the rules data belongs in
    this repo's GitHub issues, where it can be fixed and closed. See AGENTS.md.

13. **Validate the file, not just the build.** `build_validate_build` checks
    the _rules_ -- prerequisites, feat budgets, skill caps.
    `build_validate_character` checks the _file_: schema conformance, plan
    levels in order and covering the current level, no two boosts from one
    source landing on the same attribute, every slug resolving to a real
    entry, dependencies that still point at something. Run both. Findings from
    the second name the offending field as a JSON pointer, so fix what it
    points at rather than guessing.

14. **Sheets and exports are outputs, generated on demand.** Nothing needs
    storing per level any more:
    - `build_render_character_sheet` takes a `level`, so one file produces the
      sheet the character had at 1st, has now, or will have at 20th. Write it
      into the character's own folder.
    - `build_export_pathbuilder` produces a Pathbuilder file at any level, for
      when the user wants to open the build in Pathbuilder or Foundry. Offer
      it; don't write one by default.
    - `build_import_pathbuilder` goes the other way, for a build the user made
      in Pathbuilder first. Read its `_import` block before saving: it reports
      names that resolved to nothing, proficiencies it had to state outright
      because the plan could not reach them, and attributes that disagree with
      their own boost list. Then delete that block -- it is a report on the
      conversion, not part of the character.

    **Do not write a companion `.md`.** That document existed because the
    Pathbuilder format had nowhere to put a plan or a reason. Both now have
    fields, and a second file would drift from the first.

## Known limits (say so, don't paper over them)

- `build_calculate_derived_stats` uses worn armor for AC and does apply its
  potency and Resilient runes, but still doesn't add a raised shield's bonus
  (#8) or item bonuses from other worn gear -- AC may read slightly low for a
  fully-kitted-out character.
- **Replaying a plan cannot derive four things**, all of which the character
  file states outright instead. Say so rather than letting a number stand
  unexplained:
  - Training granted by a feat rather than by level -- a dedication that
    trains "a skill of your choice". Goes in `proficiencyOverrides` with the
    granting feat named as its `source`. `build_character_at_level` reports
    any override that derivation has since caught up with, so they can be
    deleted as the data improves.
  - Hit Points per level from Toughness or Mountain's Stoutness -- the
    ingestion drops the rule element that would supply them (#61), so a
    curated list stands in.
  - Skills a subclass option grants in prose rather than in a rule element,
    such as an Animist apparition's two Lores (#62). Record them as
    `skillTraining` choices with a note saying what granted them.
  - An apex item's attribute bonus (#63). A character wearing one comes out a
    modifier short.
- `build_validate_build` checks skill ranks against the fixed level-3/7/15
  expert/master/legendary caps, and now also warns if a save/Perception/
  weapon/armor/class-DC proficiency rank is lower than the character's
  class progression should have granted by their level. It does NOT check
  spellcasting proficiency specifically -- Pathbuilder keys that one by the
  character's magic tradition (e.g. `'divine'`), not a fixed name, so it's
  deliberately skipped rather than guessed at.
- `build_list_available_feats` with `feat_category="archetype"` finds any
  eligible archetype/dedication feat but doesn't let you browse "only this
  one archetype's feats" -- chaining still works correctly (e.g. a feat
  requiring "Medic Dedication" only shows as available once you've taken
  it), you just can't filter the _browse_ to one archetype by name yet.
- `build_list_available_spells` now resolves max rank and actual per-rank
  spell slot counts (`spell_slots` in the result) from each caster class's
  real level-by-level table, correct for Magus/Summoner's genuinely
  different (delayed, non-monotonic) progression too -- no need to pass
  `max_rank` explicitly for a character whose `class` field is itself the
  caster. Still pass it explicitly for a caster archetype/dedication (e.g.
  a Fighter with Wizard Dedication), since `class` there isn't the caster
  and `spell_slots` comes back `None` in that case.
- No retraining/respec tool -- to change an earlier choice, edit the choice in
  the character file, mark it `status: retrained`, move the old pick into
  `alternatives`, and re-run both validators. Check `dependsOn` first: another
  choice may exist only to serve the one being removed.
- Of the 8 character-building variant rules `rules_list_variant_rules`
  surfaces, only **Free Archetype** and **Ancestry Paragon** have real
  mechanical support (extra slots, feat-count budget checks). Proficiency
  without Level, Gradual Attribute Boosts, Automatic Bonus Progression,
  Stamina, Mythic Characters, and Level 0 Characters are recognized names
  but don't change any tool's computed output -- if the user is using one of
  those, track its effects by hand and say so rather than letting a tool
  result imply it's already accounted for.
- PFS legality (`pfs_legal_only` / the `pfs` field on feat/rules results) is
  a rarity-based heuristic plus a hand-curated override file, **not**
  authoritative -- there's no structured feed for Paizo's actual Additional
  Resources document in the data this project ingests. Always say so when
  it comes up, and point the user at the current Additional Resources
  document for anything that actually matters to their table.

- Character files are gitignored and nothing else holds a copy. There is no
  history to recover a broken one from, so validate after writing rather than
  before, and don't overwrite a file you have not read.

If the user runs into one of these, say what's not covered rather than
guessing at an answer the tools can't yet back up.
