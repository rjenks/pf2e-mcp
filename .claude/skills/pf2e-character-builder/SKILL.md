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
- `build_*` (`build_list_ancestries`, `build_list_backgrounds`, `build_list_classes`,
  `build_list_available_feats`, `build_list_ability_boost_options`,
  `build_check_prerequisite`, `build_validate_build`, `build_calculate_derived_stats`,
  `build_get_level_up_choices`, `build_to_pathbuilder_export`): character-building
  tools. Every `build_*` tool that takes a `character` argument is a pure
  function -- it reads the character JSON you pass it and returns a result, but
  the server keeps no state between calls. **You are responsible for holding
  the in-progress character JSON across the conversation** and passing the
  current version into each call.

**Also hold the chosen variant rules, PFS mode, and legacy-content setting
across the conversation**, the same way you hold the character JSON -- they
aren't stored in the character JSON itself (keeps it a clean, portable
Pathbuilder export), so you pass them as explicit arguments on every call
where they matter:
- `variant_rules: list[str]` -- on `build_get_level_up_choices` and
  `build_validate_build`. Only `'free-archetype'` and `'ancestry-paragon'`
  currently change computed results; other valid slugs (see
  `rules_list_variant_rules`) are accepted but don't affect output yet.
- `pfs_legal_only: bool` -- on `build_validate_build`, to get a warning for
  any taken option whose best-effort PFS status isn't `'legal'`.
- `include_legacy: bool` -- on every `rules_search`/`build_list_*` discovery
  tool (`rules_search`, `build_list_ancestries`, `build_list_backgrounds`,
  `build_list_classes`, `build_list_available_feats`,
  `build_list_available_spells`, `build_list_equipment`). Defaults to
  `False` (Remaster-only) in every one of them -- pass `True` for the whole
  rest of the build once the user says they want legacy content included,
  don't ask per-tool-call.

The character JSON follows Pathbuilder 2e's own export schema (confirmed
against real Pathbuilder importer source, not invented) -- fields like `name`,
`level`, `ancestry`, `heritage`, `background`, `class`, `keyability`,
`abilities`, `proficiencies`, `feats` (each a `[name, extra, type, level,
featChoiceRef, choiceKind, parentFeatChoiceRef]` tuple), `lores`, `attributes`.
When in doubt about a field, call `build_to_pathbuilder_export` and inspect the
shape of what comes back, or ask the user to paste an existing Pathbuilder
export to continue from.

## Conversational flow

0. **State the defaults up front, once, and offer to customize -- don't
   interrogate the user with a checklist before they've even picked a
   concept.** Pathbuilder itself exposes a large options panel (variant
   rules, legacy/remastered content toggles, Mythic, Automatic Bonus
   Progression, Legacy GMG variants, and a pile of app-level settings this
   project has no equivalent for) -- asking through all of that up front is
   exactly the friction to avoid. Say something like: *"I'll default to
   Remaster-only content, no variant rules, and not PFS-restricted unless
   you tell me otherwise -- want to keep those defaults, or customize
   anything first?"* Only drill into specifics (which variant rule, PFS
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
   source of truth for *legality* even when a guide is the better source
   for *strategy*. Present the build-around to the user as part of the
   concept conversation, so they can redirect before mechanics start rather
   than discovering the anchor after the fact.

3. **One major decision at a time.** Walk ancestry -> heritage -> background ->
   class -> attribute boosts -> skills -> feats, in that order (it's the order
   the boosts/prerequisites actually depend on). Don't ask the user to pick
   five things in one message.

   **Say attribute modifiers, never scores: `+4`, not `18`.** The Remaster
   works in modifiers -- Player Core starts each modifier at +0, a boost adds
   1 to it and a flaw subtracts 1 -- and every rule that consumes an attribute
   reads the modifier. The 10-to-20 score is legacy notation that survives
   because Pathbuilder's export stores it; keep it in the `abilities` dict of
   the JSON (that's the file format, don't "fix" it) and out of everything you
   say to the user, including the companion `.md`. See AGENTS.md.

   Two things this makes harder to get wrong. **Boost arithmetic:** in
   modifiers a boost is simply +1, with the sole exception that an attribute
   at +4 or higher needs two boosts to advance -- no 18-threshold special case
   to forget mid-table.

   **Half-steps only matter at 20th.** A boost that doesn't move the modifier
   looks wasted, but below 20th it is half a step that completes at the next
   milestone -- and it gets there one milestone *sooner* than an even score
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
   *Strength* modifier to damage -- worthless on a Dex-based build with
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
   change whether something is actually good for *this* character, not
   just whether it sounds good. This applies to every recommendation, not
   only weapons -- a spell's or feat's traits are exactly as load-bearing
   as an item's.

8. **Validate before locking anything in.** After the character has a full
   set of level-1 choices (or after any level-up), call `build_validate_build`
   -- passing `variant_rules` and `pfs_legal_only` per step 0's answers --
   before treating the build as final. Surface `errors` as blocking ("this
   needs to change before we continue") and `warnings` as "worth double
   checking" -- don't silently ignore either.

9. **Backtracking re-runs discovery, it doesn't hand-patch JSON.** If the user
   changes an earlier decision (e.g. swaps ancestry after already picking
   feats), don't just edit the `ancestry` field and leave stale feat choices
   in place -- re-run `build_list_available_feats`/`build_check_prerequisite`
   against the changed character to see what's still valid, and flag anything
   that no longer qualifies via `build_validate_build`.

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

12. **Exporting**: once the user is happy with the build, offer
    `build_to_pathbuilder_export` so they can bring it into Pathbuilder,
    Foundry, or any other Pathbuilder-JSON-compatible tool. Save the result
    to `characters/<Name>.json` (per this project's CLAUDE.md convention).

13. **Always write a companion `characters/<Name>.md` alongside the JSON,
    structured level by level in build order -- not grouped by category
    (all ancestry feats together, all class feats together, etc.).** The
    user will likely be re-entering these choices by hand into Pathbuilder
    or Foundry, one level-up screen at a time -- a category-grouped writeup
    forces them to cross-reference five different lists to figure out what
    level 6 needs, while a level-ordered one reads top to bottom exactly
    like the level-up flow they're clicking through. Concretely, for each
    level from 1 to the character's current level, in order:
    - **Separate what's automatic from what's a choice.** Automatically
      granted class features, proficiency-rank bumps (e.g. "Perception
      Expertise -- Perception trained -> expert"), and background/heritage
      grants (e.g. a background's bonus skill feat) are not something the
      user picks from a list in Pathbuilder -- label them as automatic so
      they aren't mistaken for a decision point. Actual choices (ancestry/
      class/general/skill feats, hybrid-study/subclass-option picks, skill
      increases) get labeled as choices, with which specific option was
      picked and a short reason why.
    - **Attribute boosts get their own explicit, ordered list** at levels
      that grant them (character creation, then every 5th level) --
      ancestry, then background, then class, then each free boost
      individually, in the order Pathbuilder applies them (this matters:
      an attribute crossing +4 mid-list changes whether a later boost on it
      is worth a full step) -- followed by the running attribute array at
      that milestone, not just the final numbers at the very end.
    - **One level = one section** (a markdown heading per level, or a
      clearly bounded block), even for levels with little happening --
      consistency beats brevity here, since the user is scanning for "what
      do I do at level 6" not reading straight through.
    - **Equipment, prepared spells, familiar loadout, final derived stats,
      and any tool caveats/open items belong in their own sections after
      the level-by-level sequence**, not interleaved into it -- they aren't
      part of the level-up click-through and would break the flow.

## Known limits (say so, don't paper over them)

- `build_calculate_derived_stats` uses worn armor (Pathbuilder's `armor` list,
  `worn: true`) for AC, but doesn't yet add potency runes or a raised shield's
  bonus -- AC may read slightly low for a fully-kitted-out character.
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
  it), you just can't filter the *browse* to one archetype by name yet.
- `build_list_available_spells` now resolves max rank and actual per-rank
  spell slot counts (`spell_slots` in the result) from each caster class's
  real level-by-level table, correct for Magus/Summoner's genuinely
  different (delayed, non-monotonic) progression too -- no need to pass
  `max_rank` explicitly for a character whose `class` field is itself the
  caster. Still pass it explicitly for a caster archetype/dedication (e.g.
  a Fighter with Wizard Dedication), since `class` there isn't the caster
  and `spell_slots` comes back `None` in that case.
- No retraining/respec tool -- to change an earlier choice, edit the
  character JSON directly (remove the old feat/proficiency, add the new
  one) and re-run `build_validate_build`.
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

If the user runs into one of these, say what's not covered rather than
guessing at an answer the tools can't yet back up.
