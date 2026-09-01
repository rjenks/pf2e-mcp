-- Rebuilt from scratch on every ingestion run (see build.py) -- this schema
-- has no migration path by design, since a full rebuild + atomic swap is
-- simpler and more correct than incremental upsert/diff logic for a dataset
-- that's entirely regenerable from upstream JSON plus the prerequisite
-- override file.

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entries (
    id TEXT PRIMARY KEY,       -- Foundry _id
    pack TEXT NOT NULL,        -- source pack file, e.g. 'feats', 'spells', 'classes'.
                                -- 'variant-rules' is a synthetic pack: individual pages
                                -- from the "Subsystems and Variant Rules" section of the
                                -- foundryvtt/pf2e "GM Screen" journal (Free Archetype,
                                -- Ancestry Paragon, Proficiency without Level, etc.),
                                -- not a real Foundry compendium pack.
    type TEXT NOT NULL,        -- Foundry item type, e.g. 'feat', 'spell', 'ancestry';
                                -- 'variant-rule' for the synthetic pack above
    name TEXT NOT NULL,
    slug TEXT,
    level INTEGER,
    category TEXT,             -- system.category, e.g. feat category (ancestry/class/general/skill/archetype).
                                -- For pack='variant-rules': 'character-building' (affects
                                -- character creation/leveling, e.g. Free Archetype) or
                                -- 'subsystem' (GM-facing downtime/encounter subsystem,
                                -- e.g. Chases, Influence) -- see build.py's
                                -- _VARIANT_RULE_CATEGORY for the exact split.
    traits TEXT,                -- JSON array
    description TEXT,          -- plain description text, for FTS and rules_explain
    raw_json TEXT NOT NULL,     -- full original Foundry item JSON, for get_entry
    rarity TEXT,                -- system.traits.rarity: common/uncommon/rare/unique.
                                -- NULL for entry types that don't carry rarity (e.g.
                                -- variant-rules, some reference packs). Doubles as an
                                -- honest-but-approximate PFS-legality proxy -- see
                                -- server/pfs.py -- since there's no structured
                                -- Organized-Play-legality feed in the ingested data.
    source_book TEXT,           -- system.publication.title
    is_remaster INTEGER,        -- system.publication.remaster (0/1), NULL if unknown
    other_tags TEXT,            -- JSON array, system.traits.otherTags. Marks membership
                                 -- in a subclass-style choice group where present, e.g.
                                 -- 'animist-apparition', 'sorcerer-bloodline',
                                 -- 'druid-order', 'witch-patron', 'cleric-doctrine' --
                                 -- one tag family per class with a subclass mechanic.
                                 -- Empty array (not null) when the item carries no
                                 -- otherTags. See rules_list_subclass_option_groups /
                                 -- rules_list_subclass_options.
    access_text TEXT            -- A feat's "Access" line (narrative gate -- org
                                 -- membership, a specific background, a region --
                                 -- distinct from its mechanical `prerequisites`), NULL
                                 -- if the feat has none. Unlike everything else fixed
                                 -- alongside this column, there's no separate structured
                                 -- `system.access` field to read -- confirmed directly on
                                 -- raw item data (Rivethun Disciple's `system` keys have
                                 -- no `access` at all). This is regex-extracted from the
                                 -- leading `<p><strong>Access</strong> ...</p>` paragraph
                                 -- in the free-text `description` HTML (matched 129/129
                                 -- feats carrying an Access line in the source checked
                                 -- against). Raw text only, never evaluated -- same
                                 -- honesty stance as the ~13% of prerequisite text this
                                 -- project leaves unresolved rather than mis-structure.
);

CREATE INDEX idx_entries_slug ON entries(slug);
CREATE INDEX idx_entries_type ON entries(type);
CREATE INDEX idx_entries_pack_category_level ON entries(pack, category, level);

CREATE VIRTUAL TABLE entries_fts USING fts5(entry_id UNINDEXED, name, description);

CREATE TABLE prerequisites (
    entry_id TEXT NOT NULL REFERENCES entries(id),
    raw_text TEXT NOT NULL,
    kind TEXT NOT NULL,        -- skill_rank | skill_rank_any | ability_score |
                                -- character_level | named_reference | compound_named |
                                -- override | unresolved
    structured TEXT            -- JSON, NULL if unresolved. For kind='override', structured
                                -- carries its own inner "kind" field for evaluator dispatch
                                -- (see pf2e_math.check_single_prerequisite).
);

CREATE INDEX idx_prerequisites_entry ON prerequisites(entry_id);

CREATE TABLE class_progression (
    class_slug TEXT PRIMARY KEY,
    class_feat_levels TEXT,        -- JSON array of level numbers
    ancestry_feat_levels TEXT,
    general_feat_levels TEXT,
    skill_feat_levels TEXT,
    skill_increase_levels TEXT,
    granted_items TEXT,            -- JSON array of {level, uuid, name} auto-granted class features
    key_ability TEXT,              -- JSON array of eligible key ability options

    -- Level-1 initial proficiency baseline, straight from the class item's
    -- system dict (foundryvtt/pf2e). Proficiency ranks below are stored as
    -- Foundry's own 0-4 encoding (untrained/trained/expert/master/legendary)
    -- -- NOT this project's 0/2/4/6/8 convention used in a character's
    -- `proficiencies` dict elsewhere. Conversion (rank * 2) happens in the
    -- tool layer (build_tools.list_classes), not here, so this table stays
    -- a faithful copy of the source rather than a pre-interpreted one.
    hp INTEGER,                    -- HP per level from the class (before Constitution)
    perception_rank INTEGER,
    fortitude_rank INTEGER,
    reflex_rank INTEGER,
    will_rank INTEGER,
    class_dc_rank INTEGER,         -- null for most classes -- class DC proficiency
                                    -- comes from a named class feature (e.g. Champion
                                    -- Expertise), not this base table, when null
    trained_skills TEXT,           -- JSON {"additional": int, "fixed": [skill slugs],
                                    --       "custom_lore": str | null}
                                    -- "additional" is the X in "X + Int modifier"
                                    -- additional skills of the player's choice; "fixed"
                                    -- are skills every member of the class starts
                                    -- trained in regardless of choice (e.g. Cleric/
                                    -- Champion's "religion"); "custom_lore" is a bonus
                                    -- Lore some classes grant (e.g. Thaumaturge's
                                    -- Esoteric Lore).
    attacks TEXT,                  -- JSON {"simple": rank, "martial": rank,
                                    --       "unarmed": rank, "advanced": rank,
                                    --       "other": {"name": str, "rank": int}}
                                    -- "other" covers a class-specific weapon category
                                    -- outside the four standard ones (e.g. Cleric's
                                    -- "Deity's favored weapon").
    defenses TEXT                  -- JSON {"unarmored": rank, "light": rank,
                                    --       "medium": rank, "heavy": rank}
);

-- Per-class, per-level spell slot counts, sourced from the "Spells per Day"
-- HTML table embedded in each caster class's own page in the "Classes"
-- journal (foundryvtt/pf2e's own source for these numbers -- there's no
-- structured field for this anywhere else; the class compendium item's own
-- `system.spellcasting` is just a starting proficiency-rank integer, not a
-- slot table). Only classes whose journal page has this table get a row
-- here -- auto-detected by table header, not a hardcoded class list, so a
-- newly-added caster class in a future release picks this up automatically
-- without a code change.
CREATE TABLE class_spell_slots (
    class_slug TEXT NOT NULL,
    level INTEGER NOT NULL,
    slots TEXT NOT NULL,   -- JSON {"cantrips": int, "1": int, ..., "10": int}.
                            -- "cantrips" is a *count* of cantrips prepared/
                            -- known at this level, not a rank (cantrips are
                            -- always heightened to the character's own
                            -- highest spell rank, computed elsewhere, not
                            -- stored per-level here). A numbered rank key is
                            -- present only once that rank's column shows a
                            -- real slot count at this level (source table
                            -- uses "-" before that point) -- absence means
                            -- "no slots of that rank yet", not zero underneath
                            -- a name. Any trailing footnote marker on a cell
                            -- (e.g. Wizard's "1*" for the Archwizard's
                            -- Spellcraft 10th-rank slot) is stripped to its
                            -- plain integer; the footnote text itself isn't
                            -- carried through.
    PRIMARY KEY (class_slug, level)
);

-- Which skill each action belongs to, and whether using it requires being
-- trained, is not recoverable from the action items themselves: an item of
-- type='action' carries only actionType/actions/category/traits/frequency and
-- its description. Nothing names the governing skill -- Treat Wounds mentions
-- Medicine only in prose and never says "trained" at all -- and not one action
-- in the whole database has a row in `prerequisites`. The single place
-- foundryvtt/pf2e records this is the "GM Screen" journal's "Skill Actions"
-- page (Player Core pg. 227): a table of Skill | Key Attribute | Untrained
-- Actions | Trained Actions whose cells are @UUID links to the action items.
-- This table is a parse of that page -- see _insert_skill_actions.
--
-- Scope is core skill actions only (50 of them). An action granted by a feat
-- (Battle Medicine, Bon Mot) is absent, and belongs elsewhere anyway: those are
-- type='feat' and already carry a structured `prerequisites` row of
-- kind='skill_rank'.
CREATE TABLE skill_actions (
    action_id TEXT NOT NULL REFERENCES entries(id),
    skill TEXT NOT NULL,           -- lowercase core skill name ('acrobatics', ...),
                                    -- plus the generic 'lore' row, which stands for
                                    -- whichever Lore subskills a character actually
                                    -- has rather than for any one named Lore.
    min_proficiency TEXT NOT NULL, -- 'untrained' | 'trained'. Only those two values
                                    -- exist because the source is a two-column table,
                                    -- not a rank ladder -- no core skill action is
                                    -- gated at expert or higher, so this isn't a
                                    -- lossy encoding of one. Deliberately a word and
                                    -- not an integer: two different numeric rank
                                    -- conventions are already in play in this project
                                    -- (Foundry's 0-4 in class_progression, 0/2/4/6/8
                                    -- in a character's `proficiencies`), and a bare 1
                                    -- here would be ambiguous between them.
    PRIMARY KEY (action_id, skill)
);
CREATE INDEX idx_skill_actions_skill ON skill_actions(skill);

CREATE TABLE ancestry_boosts (
    ancestry_slug TEXT PRIMARY KEY,
    hp INTEGER,
    size TEXT,
    boosts TEXT,        -- JSON
    flaws TEXT,         -- JSON
    vision TEXT         -- 'normal' | 'low-light-vision' | 'darkvision', straight from
                         -- the Ancestry item's own `system.vision` field (a dedicated
                         -- enum field, not a rule element -- distinct from
                         -- `item_senses` below, which covers heritage-level
                         -- vision upgrades layered on top of this base).
);

-- Sense rule elements (darkvision, low-light vision, scent, etc.) -- a
-- third `system.rules` element type alongside GrantItem/ChoiceSet/
-- ActiveEffectLike, found via the same source-reading approach. Mainly
-- relevant here for heritage-level vision upgrades (e.g. Duskwalker's
-- "low-light vision, or darkvision if your ancestry already has low-light
-- vision") layered on top of `ancestry_boosts.vision`.
CREATE TABLE item_senses (
    entry_id TEXT NOT NULL REFERENCES entries(id),
    selector TEXT NOT NULL,  -- sense type, e.g. 'darkvision', 'low-light-vision', 'scent'
    acuity TEXT,             -- 'precise' | 'imprecise' | 'vague', NULL if not specified
    predicate TEXT           -- JSON; condition(s) under which this sense is granted.
                              -- Not generally evaluated by ingestion (see pf2e_math for
                              -- the one narrow shape this project does resolve: a
                              -- heritage's own "self:<vision-level>:from-ancestry"
                              -- self-referential predicate, checked against
                              -- `ancestry_boosts.vision` at query time, not baked in
                              -- here as a resolved value -- unlike item_grants/
                              -- item_choice_sets, this table stays raw).
);

CREATE INDEX idx_item_senses_entry ON item_senses(entry_id);

CREATE TABLE background_boosts (
    background_slug TEXT PRIMARY KEY,
    boosts TEXT,         -- JSON
    trained_skills TEXT, -- JSON {"fixed": [skill slugs], "lore": [Lore skill names]} --
                          -- straight from the background item's own structured
                          -- `system.trainedSkills` field (same shape/field name
                          -- classes already expose via `class_progression`) --
                          -- e.g. Field Medic: {"fixed": ["medicine"], "lore":
                          -- ["Warfare Lore"]}. Previously not read at all; a
                          -- background's granted skill training was wrongly
                          -- assumed to be free-text only.
    granted_items TEXT   -- JSON array of {level, name, uuid} -- the background's
                          -- own `system.items` dict (same mechanism
                          -- `class_progression.granted_items` already reads for
                          -- classes' auto-granted features), e.g. Field Medic's
                          -- Battle Medicine skill feat grant. `level` is null for
                          -- a background grant (always at character creation).
);

-- All three tables below are sourced from each item's `system.rules` array
-- (the Foundry "rule element" list) -- previously never read by this
-- ingestion at all, only `traits`/`category`/`level`/`description`/
-- `publication` were. This is a genuinely different, richer layer of
-- structured data: it's how the actual game system computes "what does
-- taking this item DO" (grant another item, set a skill rank, present a
-- choice, etc.), as opposed to prose that has to be parsed to guess the
-- same thing. See KNOWN_ISSUES.md for the discovery writeup and what's
-- deliberately still NOT covered (2 of 4 ChoiceSet shapes: CONFIG.PF2E
-- lookup paths, and the actor's-currently-owned-items form, the latter
-- inherently character-state-dependent and not fixable via static
-- ingestion at all).

CREATE TABLE item_grants (
    granter_id TEXT NOT NULL REFERENCES entries(id),  -- the item carrying the GrantItem rule
    granted_uuid TEXT NOT NULL,  -- raw Foundry UUID of the granted item, e.g.
                                  -- 'Compendium.pf2e.feats-srd.Item.7y1BCJLrdk9mKXlc'
    granted_id TEXT,             -- resolved entries.id when granted_uuid points at an
                                  -- ingested item; NULL if it points outside the
                                  -- ingested packs (e.g. a licensing-excluded pack) or
                                  -- couldn't be resolved
    predicate TEXT                -- JSON; condition(s) under which this grant actually
                                   -- applies (e.g. only once a specific ChoiceSet
                                   -- selection is made on the same item). Carried
                                   -- through structured, not evaluated by ingestion --
                                   -- see pf2e_math for where prerequisite-shaped
                                   -- predicates get evaluated elsewhere in this project.
);

CREATE INDEX idx_item_grants_granter ON item_grants(granter_id);
CREATE INDEX idx_item_grants_granted ON item_grants(granted_id);

CREATE TABLE item_choice_sets (
    entry_id TEXT NOT NULL REFERENCES entries(id),
    flag TEXT,           -- the ChoiceSet's flag name (cross-references other rule
                          -- elements on the same item that key off this choice)
    choices TEXT NOT NULL, -- JSON array of {label, value}
    source TEXT NOT NULL   -- how `choices` was populated:
                            --  'array' -- a plain hardcoded list on the item itself,
                            --    e.g. Fighter's Acrobatics-or-Athletics.
                            --  'tag_query' -- a compendium-query ChoiceSet whose
                            --    `filter` used only item:tag:X / item:trait:X /
                            --    item:level:N predicates, resolved by ingestion
                            --    against this project's own `entries` (same result
                            --    a live compendium search would give), e.g.
                            --    Sorcerer's Bloodline via `item:tag:sorcerer-bloodline`.
                            --  'config_lookup' -- a ChoiceSet referencing a
                            --    CONFIG.PF2E dictionary by name (e.g. "skills"),
                            --    resolved by fetching and parsing the relevant
                            --    `foundryvtt/pf2e` TypeScript source file at the
                            --    exact release tag being ingested (see
                            --    `fetch_source_file` in ingestion/source.py) --
                            --    stays in sync with future releases rather than
                            --    depending on a hand-copied snapshot.
                            -- ChoiceSets with compound/comparison compendium-query
                            -- predicates (and/or/not/lte/gte), CONFIG.PF2E paths
                            -- this project doesn't resolve (see KNOWN_ISSUES.md for
                            -- which and why), and actor-owned-items choices are NOT
                            -- ingested at all.
);

CREATE INDEX idx_item_choice_sets_entry ON item_choice_sets(entry_id);

CREATE TABLE item_stat_modifiers (
    entry_id TEXT NOT NULL REFERENCES entries(id),
    path TEXT NOT NULL,   -- actor property path this modifies, e.g.
                           -- 'system.skills.diplomacy.rank',
                           -- 'system.proficiencies.defenses.medium.rank'.
                           -- Foundry's own dotted-path convention, not
                           -- reinterpreted here.
    mode TEXT NOT NULL,   -- upgrade | add | subtract | multiply | downgrade |
                           -- override | remove (Foundry's ActiveEffectLike modes;
                           -- 'upgrade'/'downgrade' only change the value if the
                           -- new one is better/worse than current, which is how
                           -- most skill/proficiency-rank grants are expressed)
    value TEXT,            -- JSON; the value/delta applied. May be a plain number,
                            -- or a resolvable formula/reference string (e.g. tied
                            -- to a ChoiceSet selection on the same item) -- not
                            -- evaluated here, carried through as-is.
    predicate TEXT         -- JSON; condition(s) under which this modifier applies.
                           -- Not evaluated by ingestion.
);

CREATE INDEX idx_item_stat_modifiers_entry ON item_stat_modifiers(entry_id);
CREATE INDEX idx_item_stat_modifiers_path ON item_stat_modifiers(path);

-- Proficiency-rank grants sourced from `system.subfeatures.proficiencies`,
-- a structured field (not a rule element -- confirmed live that class
-- features like Juggernaut/Evasion/Perception Mastery/Weapon Legend carry
-- an empty `system.rules` array; the actual rank bump lives here instead)
-- present on 345 class-feature/feat items in the pf2e-8.4.0 release. This
-- is what resolves KNOWN_ISSUES.md's previous "proficiency-rank increases
-- can't be validated, they're only in class-feature flavor text" finding --
-- they're not flavor text, they're this field, just not a rule element.
CREATE TABLE item_proficiency_grants (
    entry_id TEXT NOT NULL REFERENCES entries(id),
    key TEXT NOT NULL,     -- one of: 'perception', 'fortitude'/'reflex'/'will',
                            -- 'simple'/'martial'/'advanced'/'unarmed' (weapon
                            -- categories), 'light'/'medium'/'heavy'/'unarmored'
                            -- (armor categories), 'spellcasting', or a class
                            -- slug (e.g. 'rogue') for a classDC rank that only
                            -- applies to a character of that class -- Foundry's
                            -- own key, not reinterpreted here.
    rank INTEGER NOT NULL  -- Foundry's 0-4 scale (untrained..legendary), same
                            -- convention as class_progression's rank columns --
                            -- NOT this project's 0/2/4/6/8 proficiencies-dict
                            -- convention. Conversion happens in the tool layer.
);

CREATE INDEX idx_item_proficiency_grants_entry ON item_proficiency_grants(entry_id);
