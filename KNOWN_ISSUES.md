# Known issues

Tracked gaps in the current implementation, for picking back up later. Not
urgent/blocking for normal use — the tools degrade honestly (return
"unconfirmed"/omit rather than silently guess) everywhere these apply.

Resolved issues are removed from this file once fixed, not kept as
strikethrough history — check `git log` on this file for that.

## Rules data

- **~12.7% of feat prerequisites are unresolved** (591/4636 rows).
  `prerequisite_overrides.json` covers a handful of hand-curated
  disambiguations (e.g. `'Counterspell'`, genuinely ambiguous across 6
  real Counterspell-named feats, and `'class_hp_threshold'`, resolved
  against `class_progression.hp` for the 7 "X Resiliency" feats gated on
  "class granting no more Hit Points per level than N + your Constitution
  modifier"); the rest fall into a few categories left unattempted rather
  than risk mis-structuring them: skill-list texts with an embedded AND
  (`'trained in Society, and Thievery'`) or a trailing qualifier clause
  (`'...depending on your chosen tradition'`); spellcasting-ability
  sub-cases ("ability to cast a ritual"/"ability to cast fireball");
  cleric font checks; alignment/deity; and narrative/in-play prerequisites
  ("brought to 0 HP by an enemy with the fire trait") — either genuinely
  unautomatable or needing character-schema fields that don't exist yet.

- **Passive-trait prerequisites beyond vision/familiar aren't covered.**
  `check_single_prerequisite` now resolves "darkvision"/"low-light vision"
  (via `derived["vision"]`, sourced from `ancestry_boosts.vision` +
  heritage `Sense` rule elements) and "familiar" (via
  `derived["has_familiar"]`, sourced from `item_grants` -- does the
  character have any feat that `GrantItem`s the "Pet" item every
  familiar-granting path was confirmed to funnel through). Other passive
  traits (resistances, other senses, languages) aren't covered by the same
  mechanism yet. Also, **Familiar Master Dedication specifically won't be
  detected** even by the working `has_familiar` check -- confirmed its own
  ingested rules don't include an unconditional `GrantItem` of Pet at all,
  only a conditional Enhanced Familiar grant gated on a runtime-only
  `self:has-familiar` flag this project has no equivalent for. A character
  whose only route to a familiar is that specific dedication won't be
  detected.

- **A prerequisite naming an automatic, unconditionally-granted class
  feature is checked the same way as a literal owned feat/ancestry/
  heritage/background/class name, which is never true for an automatic
  feature -- and unlike an unparseable prerequisite, this fails *silently*
  (a hard False, dropping the feat entirely) rather than surfacing as
  "unconfirmed."** This project's character-JSON convention records
  automatic class features (a class's core, always-granted mechanics -- not
  a hybrid-study/doctrine/subclass choice) in `specials`, not `feats`, so
  `has_feat` can never find them. Confirmed live and fixed for the two
  instances that surfaced building a Magus (`Spellstrike`, `Arcane Cascade`
  -- every Magus has both unconditionally from 1st level): this silently
  dropped **28 Magus feats spanning every level tier, 1 through 20**,
  including `Magus's Analysis` at 1st level, from every
  `build_list_available_feats` call for every Magus character ever built
  with this tool -- caught only because a user cross-checked against a real
  Pathbuilder screenshot. Fixed with a new `class_feature_reference`
  prerequisite kind (`pf2e_math.py`'s override branch), registered for
  those two exact strings in `prerequisite_overrides.json`. **Not a general
  fix** -- only Magus's two automatic features are covered; any other
  class's feats prerequisite'd on that class's own core automatic feature
  (Barbarian's Rage, Rogue's Sneak Attack, Sorcerer's Bloodline, a Witch's
  Patron, etc.) will hit the exact same silent-drop bug until a matching
  override is added for that term, or this gets a general fix instead of
  per-string overrides (e.g. cross-referencing a prerequisite name against
  each class's own `classfeature`-category entries that carry no
  `ChoiceSet`, i.e. aren't a player pick).

- **A prerequisite naming a specific hybrid study / subclass option has the
  same silent-drop shape one level deeper: the raw prerequisite text
  carries a category-word suffix the parser's existing single-word
  suffix-stripper (`_SUFFIX_STRIP_WORDS`) doesn't cover, so it never matches
  the actual feat name recorded on the character.** E.g. "starlit span
  hybrid study" (two trailing words) vs. the real feat name "Starlit Span";
  `_strip_suffix_word` only strips one trailing word, so "hybrid study"
  never comes off. Confirmed live: this hid `Meteoric Spellstrike` (a
  Starlit-Span-specific Magus feat) from a Starlit Span character that
  genuinely qualified for it. Fixed for all 10 Magus hybrid studies (22
  affected prerequisite rows) by reusing the existing `named_reference`
  evaluator with the correctly-stripped name via
  `prerequisite_overrides.json`, rather than a general two-word suffix
  fix in the parser itself -- the same "not generalized, only Magus's
  hybrid studies are covered" caveat as the class-feature-reference item
  above applies here too; any other class's subclass-option-gated feats
  (a Cleric doctrine, a Sorcerer bloodline, etc.) with a multi-word
  category suffix in the prerequisite text could have the same gap.

- **`is_remaster` is a publication/licensing-line flag, not a Pathbuilder-
  availability predictor -- there is no discoverable signal for that in
  `foundryvtt/pf2e` data at all.** `system.publication.remaster` (schema:
  `src/module/model.ts`) marks whether an entry belongs to the ORC-licensed
  Remaster line specifically (`license: 'ORC'`) versus OGL (legacy or
  explicitly dual-compatible content) -- confirmed correct against the full
  per-book breakdown: essentially every Remaster-line book (Player Core,
  Player Core 2, GM Core, Monster/NPC Core, War of Immortals, Howl of the
  Wild, ...) is cleanly `is_remaster: 1` for nearly all its entries, and
  every pre-2023 book is cleanly `0`. *Pathfinder Book of the Dead*'s
  uniform `is_remaster: 0` (226/226), which an earlier pass here mistook
  for "the flag is broken, modern content reads as old," is actually
  correct: its own data carries `license: 'OGL'`, deliberately -- Paizo
  published it as edition-agnostic, compatible with both legacy Pathfinder
  and the Remaster (unlike Player Core/GM Core, which are Remaster-
  exclusive replacements). A prior version of this entry treated Book of
  the Dead content (Soul Warden Dedication, Psychopomp Familiar) showing up
  in a real Pathbuilder install as a contradiction of the flag; it isn't --
  dual-compatible content being present in Pathbuilder is exactly what
  dual-compatible means, and doesn't say anything about the flag's
  correctness one way or the other. Pathbuilder is a separately-maintained,
  independent application with its own content-coverage timeline; nothing
  in `foundryvtt/pf2e` -- this flag or any other -- can predict what it has
  or hasn't implemented yet. No heuristic here is safe to lean on; treat
  any option as needing a manual Pathbuilder check if it matters.

## Character math / validation

- **A raised shield's AC bonus isn't modeled.** (Potency runes are --
  `armor_ac` takes a `potency` parameter sourced from the character's own
  armor entry.) Genuinely can't be added without "is the shield currently
  raised" as tracked character state, which doesn't fit a static character
  sheet and isn't a decision to make without asking first (would mean a
  new field in this project's character-JSON conventions).
- **A worn armor's Resilient rune bonus isn't added to saves**, same
  category of gap as the raised-shield one above but on the defense side
  instead of AC -- `calculate_derived_stats`'s save formulas use only
  proficiency + level + ability mod, nothing from `armor.res`. Confirmed
  live building a level 10 Magus with a +1 Resilient Explorer's Clothing:
  raw tool output (Fort 18/Reflex 19/Will 19) was 1 short on all three
  compared to hand calculation with the rune included.
- **`calculate_derived_stats`'s spellcasting attack/DC uses
  `character.keyability` as the casting ability, not the casting
  tradition's actual ability.** For most classes these are the same value
  so this never surfaces, but for any class where they differ (confirmed
  live: a Magus with `keyability: "dex"` for its martial/AC side but
  Intelligence-based spellcasting per `Magus Spellcasting`/`Expert
  Spellcaster`/`Master Spellcaster`) the calculator silently uses the wrong
  ability. Confirmed by holding Int fixed and varying Dex between two calls
  -- the returned spellcasting attack/DC moved with Dex, not Int. The
  `spellCasters[].ability` field the character JSON already carries (e.g.
  `"int"`) is the correct per-caster value and is what should drive this
  instead of falling back to `keyability`.
- **`list_classes`' `trained_skills.additional` is the class's flat
  baseline only -- it does not include the Intelligence-modifier bonus to
  additional trained skills that every class grants at 1st level** ("...
  becomes trained in a number of skills equal to [class value] plus your
  Intelligence modifier" -- Player Core, Skills step of character
  creation). Confirmed live: a level-10 Magus build (Int 14 at the point
  in the 1st-level boost sequence this matters) was first built with only
  2 additional trained skills picked instead of the correct 4 (class's
  `additional: 2` + Int mod +2), because the field's docstring didn't say
  this term was excluded. Partially mitigated now (see
  `_validate_trained_skill_count` below), but `list_classes` itself still
  just returns the class's raw number -- callers must add
  `max(0, ability_mod(level-1 Int))` themselves.

- `list_ability_boost_options` only checks legality within one source at a
  time (ancestry/background/class/free). No cross-source check exists for
  e.g. two different sources both landing a boost on the same ability at
  the same tier.
- `validate_build` doesn't fully cross-check proficiency counts/ranks
  against what class + background + level should actually produce. What's
  now covered: `_validate_fixed_trained_skills` (fixed/unconditional
  trained skills and Lores from `system.trainedSkills`),
  `_validate_trained_skill_count` (a *floor* check on the character's
  total trained-or-better skill/lore count against class fixed+additional
  + background fixed/lore/choice + the character's *level-1* Intelligence
  modifier if positive -- reconstructing level-1 Int from
  `abilities.breakdown` specifically, since a later ability boost at 5/10/
  15/20 doesn't retroactively grant more 1st-level trained skills; skipped
  entirely, rather than guessing, for a character above level 1 whose
  `abilities.breakdown` is missing or malformed), and
  `_validate_proficiency_ranks` (save/Perception/weapon/armor/class-DC
  *rank* floor from the class's level-1 baseline plus every granted class
  feature/taken feat's `system.subfeatures.proficiencies` up to the
  character's level — a structured field distinct from `system.rules`,
  confirmed live that features like Juggernaut/Weapon Legend/Perception
  Mastery carry an *empty* rules array; the previous assumption that these
  ranks live only in unstructured flavor text was wrong, they're just not
  a rule element). Still open: *which specific skills* the player's free
  "additional" choices landed on isn't validated -- only the aggregate
  count has a floor check now, deliberately, since individual picks can't
  be cleanly told apart from a later Skill Increase without risking a
  false positive at higher levels; and spellcasting proficiency
  specifically is skipped by `_validate_proficiency_ranks` (Pathbuilder
  keys it by the character's magic tradition, e.g. `'divine'`, not a fixed
  `'spellcasting'` key, and
  nothing ingested ties a class/dedication reliably enough to that tradition
  to guess right in every case).
- `list_available_spells`'s `spell_slots` is per-class-at-level slot
  *capacity* only (from `class_spell_slots`), not a full spellcasting-entry
  model — nothing tracks which specific spells are actually prepared/known
  against that capacity, or spontaneous vs. prepared repertoire differences
  within it. `spell_slots` is `None` for a class with no row in the table
  (a non-caster class, or a caster archetype/dedication where
  `character.class` itself isn't the caster identity) — `max_rank` still
  needs to be passed explicitly for those.

## Optional/variant rules and PFS legality

- Of the 8 character-building variant rules ingested (`rules_list_variant_rules`,
  category `'character-building'`), only **Free Archetype** and **Ancestry
  Paragon** have real mechanical support: extra feat-level unlocks in
  `build_get_level_up_choices`, feat-count budget checks in
  `build_validate_build`. **Proficiency without Level**, **Gradual Attribute
  Boosts**, **Automatic Bonus Progression**, **Stamina**, **Mythic
  Characters**, and **Level 0 Characters** are recognized names (full text
  ingested, queryable) but don't change any computed output. Proficiency
  without Level in particular would need a rework of `pf2e_math`'s whole
  proficiency-bonus formula, not a small addition — not attempted.
- The Free Archetype budget check in `build_validate_build` is a **soft
  warning, not a hard error**, unlike the Ancestry Paragon one. Reason: this
  project doesn't track class/skill-feat-slot usage at all (feats are a flat
  list, not slotted), so there's no way to tell a normal-slot archetype feat
  apart from one using the bonus Free Archetype slot with certainty. Ancestry
  gets a real error-level check only because 'ancestry' is otherwise never
  used as a normal class/skill/general feat slot, making the count
  unambiguous.
- PFS (Pathfinder Society / Organized Play) legality has **no structured feed**
  in the `foundryvtt/pf2e` data this project ingests — it's Paizo's own
  policy document, on its own update cadence, and doesn't map cleanly onto
  rarity (some common options are PFS-banned for balance; some uncommon ones
  are unlocked without a boon). The `pfs` field surfaced on every entry (and
  `build_validate_build`'s `pfs_legal_only` flag) is a best-effort heuristic
  only: common rarity = legal, uncommon/rare/unique = flagged restricted,
  plus `server/pfs_overrides.json` for hand-curated exceptions (starts
  empty). Explicitly not authoritative -- see `server/pfs.py`'s docstring.
  A more accurate implementation would need to fetch and parse Paizo's
  "Additional Resources" document, which isn't a structured API (Google
  Doc/PDF) and would need periodic re-sync; not attempted, flagged as a
  possible future improvement if accuracy beyond the rarity heuristic
  becomes important.

## Licensing

- **Bestiary, Monster Core, NPC Core, and full-adventure packs are
  excluded from ingestion entirely** (`ingestion/build.py`), not available
  through any tool. This is deliberate, not an oversight: that content is
  almost entirely proper nouns/narrative ("Product Identity"/"Reserved
  Material" under OGL/ORC -- see NOTICE.md), none of it is used by this
  project's character-building tools, and excluding it meaningfully
  shrinks the project's license-compliance exposure. If a future use case
  needs monster stat blocks (e.g. encounter-building tools), this
  exclusion will need deliberate reconsideration, not just removal --
  it's the single largest concentration of Product Identity/Reserved
  Material in the upstream data.
- The `license` field (`server/licensing.py`) surfaced on every `rules_*`
  result is a **pack-level heuristic**, not a per-entry legal
  determination. It can't tell a deity entry's genuine game mechanics (a
  domain's initial spell, a favored weapon) apart from the same entry's
  Product Identity flavor text (title, dogma, edicts) -- the whole entry
  gets one classification. Fixing that properly would need per-field
  tagging that doesn't exist anywhere in the ingested data (Foundry's own
  compendium doesn't structurally separate "this specific sentence is
  Product Identity" from "this one is OGC" either).
- **The vendored font subsets no longer carry their own licence metadata.**
  `pyftsubset`'s default `--name-IDs=0,1,2,3,4,5,6` dropped name IDs 13 and
  14 (the licence notice and URL) when the Noto subsets were generated;
  name ID 0, the copyright notice, survives. This is not a compliance gap
  in practice -- OFL Condition 2 is satisfied by a human-readable copy, and
  both the repository (`LICENSES/OFL-1.1.txt`) and every rendered sheet
  (`_OFL_PARAS` in `server/sheet.py`) carry the full licence text. It's
  recorded only because anyone extracting a WOFF2 blob *out* of this
  project and redistributing it on its own would be shipping a font whose
  internal metadata no longer names its licence, and would need to carry
  the text alongside it themselves. Regenerating with `--name-IDs+=13,14`
  would close it at the source; the command and `SUBSET_UNICODES` are in
  `sheet_assets.py`'s docstring.

## Tooling gaps

- **The working character dict is never validated against any schema.**
  `pydantic` is a declared dependency but never actually imported anywhere
  in this project's own code -- it's pulled in transitively by the
  `mcp[cli]` SDK for its own tool-parameter schema generation, not used
  here. `to_pathbuilder_export` does nothing but strip `_`-prefixed keys
  and wrap the envelope -- no structural check at all. What exists instead
  is defensive `character.get(key, default)` calls scattered across every
  individual function, plus `validate_build`, which checks *game-rules*
  legality (prerequisites, skill caps, feat budgets) assuming the dict is
  already well-formed -- neither is schema validation, and nothing catches
  a genuinely malformed character (wrong field types, a wrong-arity feats
  tuple, an unrecognized key) before it either crashes downstream or
  silently computes a wrong result. Checked whether a real schema exists
  anywhere to validate against, rather than write one from a guess --
  it doesn't, from any source: Pathbuilder itself is closed-source with no
  published export-format docs, and the two real Foundry import modules
  this project's schema was originally built against (`pathmuncher`,
  `foundry-pathbuilder2e-import`) both turned out to be plain, untyped
  JavaScript with no schema file anywhere. **Explicit decision: not
  building a hand-authored schema now** -- validating against this
  project's own reverse-engineered understanding would add a false sense
  of authority without real backing; revisit if a conversation with
  FoundryVTT/Pathbuilder-ecosystem developers produces something
  authoritative to validate against instead.

- **`ChoiceSet` rule elements: predicated and owned-item shapes are
  unresolved.** Of the 4 shapes a `ChoiceSet` can take, a plain hardcoded
  array and simple compendium-tag/`CONFIG.PF2E` queries are ingested (with
  localized labels). Two shapes are not, for different reasons:
  - **Predicated queries** (the majority of compendium-query and
    `CONFIG.PF2E`-lookup `ChoiceSet`s) filter by the character's *current*
    state at selection time (e.g. "only skills you're not yet trained in,"
    "only feats at or below your level") -- inherently character-state-
    dependent, not a fixed answer this project's static ingestion can
    precompute. ~126 compendium queries and ~60 config-lookup `ChoiceSet`s
    fall here.
  - **Owned-items `ChoiceSet`s** (18) draw their options from the
    character's currently-owned items (e.g. "choose one of your prepared
    scrolls") -- same reasoning, inherently dynamic.

  Separately, one `CONFIG.PF2E` dictionary remains unresolved even for the
  no-predicate case: `creatureTraits` (composed from five separate spread
  sources in the source TypeScript, and mostly monster-flavor rather than
  character-building-relevant) -- low-instance-count enough that the added
  parsing complexity wasn't judged worth it yet. And a small residual of
  option labels (~25 of 3691 array-shape labels, ~0.7%) stay as raw i18n
  keys rather than localized text: 16 are `PF2E.Kingmaker.Skills.*` (the
  Kingmaker AP's kingdom-building subsystem, out of scope) and 8 are
  `PF2E.TraitChaotic`/`Evil`/`Good`/`Lawful` plus one
  `PF2E.Actions.Swim.Modifier.SwimSpeed` key that don't exist in any of the
  5 lang files Foundry ships -- a genuine upstream data quirk, not a gap in
  this project's fetching.

- No retraining/respec tool. Changing an earlier choice means hand-editing
  the character JSON and re-running `build_validate_build`.
- Multiclass feat-count-tracking flags (e.g.
  `flags.system.barbarian.archetypeFeatCount`) aren't implemented as
  actual tracked character state -- the archetype-dedication-exclusivity
  check computes membership counts on the fly from prerequisite-reference
  relationships instead, which covers *validation* but not a queryable
  "how many Barbarian archetype feats does this character have" count in
  its own right.
- `build_list_available_feats` with `feat_category="archetype"` finds any
  eligible archetype feat but can't filter the browse down to one named
  archetype (chaining via prerequisites, e.g. requiring "Medic Dedication,"
  already works correctly).
- **No Foundry VTT actor export, only Pathbuilder JSON -- and Pathbuilder
  JSON is a dead end for getting a character into either platform, not
  just Pathbuilder itself.** Pathbuilder itself can't import its own
  export format (export is one-directional). The two real Foundry import
  modules this project's schema was built against (Pathmuncher, the
  current actively-maintained one; and the older, now-unsupported
  `foundry-pathbuilder2e-import`) both require a 6-digit reference code
  that Pathbuilder's own live server generates for a character that
  genuinely exists in a real pathbuilder2e.com account -- neither accepts
  a standalone JSON file. A character this project builds (never entered
  into an actual Pathbuilder account) has no such code, so neither module
  can import it either.

  Foundry does have a real, separate answer, unrelated to Pathbuilder
  entirely: its **core application** (not the pf2e system, not any
  community module) has a built-in "Import Data" feature on any
  Actors-directory entry, accepting a raw Actor document JSON in Foundry's
  own native schema. That schema is a materially larger shape than
  Pathbuilder's flat export because **Foundry's Actor document format is
  deliberately generic across every game system it supports** (D&D5e,
  PF2e, dozens of others) -- the system-specific payload lives nested
  inside a `system` key on a generic document envelope, and every
  ancestry/background/class/feat/spell/equipment choice has to exist as a
  full embedded Item document in its own right, not a name in a flat list.
  Confirmed the real scope directly: `src/module/actor/character/data.ts`
  alone is 542 lines of nested type definitions for just the character's
  own system data, before any embedded items.

  Real advantage to design around when this gets built: this project's
  ingestion already stores each item's exact real Foundry `raw_json`,
  keyed by the item's genuine Foundry `_id` -- building a character's
  embedded Items array should mean assembling from data already in this
  project's own database (deep-copy each taken choice's real `raw_json`,
  fresh id per embedded copy) rather than inventing a parallel schema by
  hand the way the Pathbuilder export originally had to be.

  **Deliberately not implemented -- documented and deferred per explicit
  direction.** Real follow-on work, not a small fix: a second export
  pipeline mapping the working character schema to a genuine
  Actor-plus-embedded-Items document tree.
- No equipment purchasing/inventory/bulk tracking. `build_list_equipment`
  browses and reports the fixed 15 gp starting-wealth constant, nothing
  more.

## Process

- No automated tests. Everything so far was verified with one-off scripts
  during development (see chat history / `scripts/spike_prerequisites.py`
  for the one that's checked in), not durable `pytest` coverage in the
  repo — future changes have nothing automatic to catch regressions.
