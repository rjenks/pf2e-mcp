"""Build (or rebuild) the SQLite rules/character-building database.

Idempotent by design: every run does a full rebuild into a temp file, then
atomically replaces the live DB with os.replace. This sidesteps incremental
upsert/diff logic entirely -- renamed, removed, or errata'd entries are
simply not present in the freshly built DB, so there's no stale-row cleanup
to write. The only piece of state that survives across runs deliberately
is `prerequisite_overrides.json`, which is hand-maintained and versioned
in the repo rather than regenerated here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from ..paths import cache_dir as default_cache_dir
from ..paths import db_path as default_db_path
from .prerequisites import PrerequisiteParser, build_name_index
from .source import (
    download_and_extract,
    fetch_source_file,
    get_latest_release,
    prune_cache,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Packs excluded from the general rules-lookup `entries` table: adventure
# journals/macros/tables aren't game rules content.
_EXCLUDED_PACK_SUFFIXES = ("_folders",)
_EXCLUDED_PACKS = {
    "journals", "macros", "rollable-tables", "action-macros", "criticaldeck",
    "iconics", "paizo-pregens", "npc-gallery", "vehicles",
}

# Bestiary/monster/NPC/full-adventure packs: excluded on licensing grounds,
# not scope. This content is almost entirely proper nouns, unique named
# creatures, and adventure-specific narrative -- exactly what OGL calls
# "Product Identity" and the ORC License calls "Reserved Material" (see
# NOTICE.md), as opposed to the generic game mechanics those licenses
# freely permit reproducing. None of it is used by this project's
# character-building tools either, so excluding it is a pure reduction in
# license-compliance surface area with no functional cost. Matched by
# suffix rather than an exhaustive per-adventure-path list, since Paizo
# publishes a new one roughly every two months and a static list would
# immediately start missing new ones (same reasoning as NOTICE.md's
# decision not to freeze-copy the upstream attribution lists).
_PRODUCT_IDENTITY_PACK_SUFFIX = "-bestiary"
_PRODUCT_IDENTITY_PACKS = {
    "pathfinder-bestiary-2", "pathfinder-bestiary-3",
    "pathfinder-monster-core", "pathfinder-monster-core-2",
    "pathfinder-npc-core", "fall-of-plaguestone", "standalone-adventures",
}


def _load_packs(data_dir: Path) -> dict[str, list[dict]]:
    packs: dict[str, list[dict]] = {}
    for path in sorted((data_dir / "packs").glob("*.json")):
        name = path.stem
        if name.endswith(_EXCLUDED_PACK_SUFFIXES) or name in _EXCLUDED_PACKS:
            continue
        if name.endswith(_PRODUCT_IDENTITY_PACK_SUFFIX) or name in _PRODUCT_IDENTITY_PACKS:
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            packs[name] = data
    return packs


def _entry_description(item: dict) -> str:
    desc = item.get("system", {}).get("description", {}).get("value", "")
    return desc


_ACCESS_LINE_RE = re.compile(r"<p><strong>Access</strong>\s*(.*?)</p>", re.DOTALL)


def _entry_access_text(description: str) -> str | None:
    """A feat's "Access" line (narrative gate, distinct from its mechanical
    `prerequisites`), extracted from the leading
    `<p><strong>Access</strong> ...</p>` paragraph in the description HTML
    -- there's no separate structured field for this anywhere in the
    source (confirmed directly against raw item data), unlike everything
    else this ingestion reads. Raw matched text, HTML markup (including
    any @UUID references) left intact rather than stripped, matching how
    `description` itself is stored -- not evaluated, just surfaced."""
    match = _ACCESS_LINE_RE.search(description)
    return match.group(1).strip() if match else None


def _insert_entries(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    for pack_name, items in packs.items():
        for item in items:
            item_id = item.get("_id")
            name = item.get("name")
            if not item_id or not name:
                continue
            system = item.get("system", {})
            level = system.get("level", {})
            level_value = level.get("value") if isinstance(level, dict) else None
            traits_block = system.get("traits", {})
            traits = traits_block.get("value", [])
            other_tags = traits_block.get("otherTags", [])
            publication = system.get("publication", {})
            description = _entry_description(item)
            conn.execute(
                "INSERT OR REPLACE INTO entries "
                "(id, pack, type, name, slug, level, category, traits, description, raw_json, "
                " rarity, source_book, is_remaster, other_tags, access_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    pack_name,
                    item.get("type", "unknown"),
                    name,
                    system.get("slug"),
                    level_value,
                    system.get("category"),
                    json.dumps(traits),
                    description,
                    json.dumps(item),
                    traits_block.get("rarity"),
                    publication.get("title"),
                    int(publication["remaster"]) if "remaster" in publication else None,
                    json.dumps(other_tags),
                    _entry_access_text(description),
                ),
            )
            conn.execute(
                "INSERT INTO entries_fts (entry_id, name, description) VALUES (?, ?, ?)",
                (item_id, name, _entry_description(item)),
            )


# The "Subsystems and Variant Rules" section of the foundryvtt/pf2e "GM
# Screen" journal (Pathfinder GM Core / Gamemastery Guide content), split
# into which pages actually affect character creation/leveling versus
# GM-facing downtime/encounter subsystems. Hand-curated because the source
# journal has no machine-readable tag for this distinction -- unlisted page
# names default to "subsystem" (see _insert_variant_rules).
_VARIANT_RULE_CATEGORY = {
    "Automatic Bonus Progression": "character-building",
    "Free Archetype": "character-building",
    "Level 0 Characters": "character-building",
    "Gradual Attribute Boosts": "character-building",
    "Proficiency without Level": "character-building",
    "Ancestry Paragon": "character-building",
    "Stamina": "character-building",
    "Mythic Characters": "character-building",
    "Victory Points": "subsystem",
    "Influence": "subsystem",
    "Research": "subsystem",
    "Chases": "subsystem",
    "Infiltration": "subsystem",
    "Reputation": "subsystem",
    "Duels": "subsystem",
    "Hexploration": "subsystem",
    "Leadership": "subsystem",
}


def _slugify(name: str) -> str:
    return "-".join(name.lower().replace("'", "").split())


def _insert_variant_rules(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Journals are excluded from the general entries ingestion (see
    _EXCLUDED_PACKS) since most are adventure/reference content, not rules.
    The "GM Screen" journal's "Subsystems and Variant Rules" section is the
    one exception worth pulling in on its own: it's the only place
    foundryvtt/pf2e carries the actual text of optional character-building
    rules like Free Archetype and Ancestry Paragon, which this project's
    character-building tools need to reason about (see build_tools.py's
    variant_rules parameter). Read directly from journals.json rather than
    via _load_packs, since that function's exclusion list intentionally
    skips this file for every other purpose."""
    journals_path = data_dir / "packs" / "journals.json"
    try:
        journals = json.loads(journals_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    gm_screen = next((j for j in journals if j.get("name") == "GM Screen"), None)
    if not gm_screen:
        return

    pages = sorted(gm_screen.get("pages", []), key=lambda p: p.get("sort", 0))
    in_variant_section = False
    for page in pages:
        title_level = page.get("title", {}).get("level")
        name = page.get("name", "")
        if title_level == 1:
            # Top-level headers delimit sections (e.g. "Playing the Game",
            # "Subsystems and Variant Rules") -- only pages between this
            # header and the next one belong to it.
            in_variant_section = name == "Subsystems and Variant Rules"
            continue
        if not in_variant_section:
            continue
        page_id = page.get("_id")
        if not page_id or not name:
            continue
        content = page.get("text", {}).get("content", "")
        category = _VARIANT_RULE_CATEGORY.get(name, "subsystem")
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(id, pack, type, name, slug, level, category, traits, description, raw_json, "
            " rarity, source_book, is_remaster, other_tags, access_text) "
            "VALUES (?, 'variant-rules', 'variant-rule', ?, ?, NULL, ?, '[]', ?, ?, NULL, NULL, NULL, '[]', NULL)",
            (page_id, name, _slugify(name), category, content, json.dumps(page)),
        )
        conn.execute(
            "INSERT INTO entries_fts (entry_id, name, description) VALUES (?, ?, ?)",
            (page_id, name, content),
        )


def _resolve_uuid_to_id(uuid: str) -> str | None:
    """Foundry item UUIDs are 'Compendium.<system>.<pack>.Item.<id>' (or, for a
    world item, just the bare id) -- the trailing segment after the last '.'
    is the item's own _id, which is exactly what this project uses as
    `entries.id`. Doesn't guarantee the id is actually in this database
    (could point at a licensing-excluded pack, e.g. bestiary) -- callers
    resolve that with a lookup against `entries`, this just extracts the
    candidate id from the UUID string."""
    if not uuid:
        return None
    return uuid.rsplit(".", 1)[-1]


# Which `foundryvtt/pf2e` TypeScript source file each resolvable
# CONFIG.PF2E lookup dictionary lives in, as of when this was written --
# fetched fresh at ingestion time (see `fetch_source_file`), pinned to the
# same release tag as the data being ingested, specifically so this
# doesn't silently drift from a hand-copied snapshot if the source moves
# these around or the dictionary's contents change (new weapon group added
# in a future book, etc.). Deliberately narrow: only the standalone
# top-level `const <name> = {...}` dictionaries relevant to character
# building are covered, not every CONFIG.PF2E key -- see
# KNOWN_ISSUES.md for what's out of scope and why (`baseWeaponTypes`
# needs a second, localization-JSON source file; `creatureTraits` is
# composed from several spread sources and mostly monster-flavor, not
# character-building-relevant; `saves` is nested inside a much larger
# object rather than its own const, and is hardcoded below instead since
# it's a fixed 3-value set that hasn't changed across this system's
# history).
_CONFIG_TS_LOOKUPS = {
    "skills": ("src/scripts/config/index.ts", "skills"),
    "weaponGroups": ("src/scripts/config/index.ts", "weaponGroups"),
    "magicTraditions": ("src/scripts/config/traits.ts", "magicTraditions"),
}

_CONFIG_HARDCODED_LOOKUPS: dict[str, dict[str, str]] = {
    "saves": {
        "fortitude": "PF2E.SavesFortitude",
        "reflex": "PF2E.SavesReflex",
        "will": "PF2E.SavesWill",
    },
}


def _extract_ts_object_body(source: str, const_name: str) -> str | None:
    """Find a top-level `const <const_name> = {...}` (or `: Type = {...}`)
    declaration and return its object-literal body via brace-balance
    matching. Returns None if no such declaration is found -- callers
    treat that as "can't resolve," not an error, since source layout is
    outside this project's control and can change."""
    match = re.search(rf"\bconst\s+{re.escape(const_name)}\b[^={{]*=\s*\{{", source)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : i]
    return None


def _parse_simple_ts_dict(body: str) -> dict[str, str]:
    """Extract `key: "value"` and `key: { label: "value", ... }` pairs from
    a TS object-literal body -- the only two shapes this project's target
    CONFIG.PF2E dictionaries use (a flat string-value dict like
    `weaponGroups`, or a dict of small option-objects like `skills`).
    Deliberately not a general TS parser: doesn't handle spread
    expressions, computed keys, or nesting beyond one level, all of which
    are absent from the specific consts this is used against."""
    result: dict[str, str] = {}
    for m in re.finditer(r'^\s*([a-zA-Z0-9_]+):\s*"([^"]*)"', body, re.MULTILINE):
        result[m.group(1)] = m.group(2)
    for m in re.finditer(r'^\s*([a-zA-Z0-9_]+):\s*\{\s*label:\s*"([^"]*)"', body, re.MULTILINE):
        result[m.group(1)] = m.group(2)
    return result


_LOCALIZATION_FILE = "static/lang/en.json"
_LOCALIZATION_REMASTER_FILE = "static/lang/re-en.json"


def _load_localization_dict(tag: str, cache_dir: Path) -> dict[str, str]:
    """Fetch and flatten `foundryvtt/pf2e`'s own English localization files
    (single static JSON files, not TypeScript -- no regex parser needed,
    just `json.loads` and a flatten pass) into `{"PF2E.Skill.Acrobatics":
    "Acrobatics", ...}`. This is the actual source every raw i18n key
    surfaced elsewhere in this project's output (`"PF2E.Skill.Acrobatics"`
    instead of `"Acrobatics"`) refers to -- previously never fetched, so
    those keys were left unresolved. Foundry's own client merges multiple
    lang files at runtime (declared in the system manifest); `re-en.json`
    is the Remaster-era overlay (renamed/added keys like `PF2E.Dragon.
    Black`, `PF2E.BattleForm.Attack.Antler` that don't exist in the base
    `en.json` at all) and takes priority on any key collision. Returns an
    empty dict (not raises) if both fetches fail, so localization degrades
    to "keys stay raw" rather than failing the whole build."""
    flat: dict[str, str] = {}

    def _flatten(prefix: str, node: object) -> None:
        if isinstance(node, str):
            flat[prefix] = node
        elif isinstance(node, dict):
            for key, value in node.items():
                _flatten(f"{prefix}.{key}" if prefix else key, value)

    for repo_path in (_LOCALIZATION_FILE, _LOCALIZATION_REMASTER_FILE):
        source = fetch_source_file(tag, repo_path, cache_dir)
        if source is None:
            continue
        try:
            raw = json.loads(source)
        except json.JSONDecodeError:
            continue
        _flatten("", raw)

    return flat


def _load_config_pf2e_lookups(tag: str, cache_dir: Path) -> dict[str, dict[str, str]]:
    """Resolve every entry in `_CONFIG_TS_LOOKUPS` by fetching each needed
    source file once (deduped) and parsing out the named const, plus the
    hardcoded entries in `_CONFIG_HARDCODED_LOOKUPS`, plus `baseWeaponTypes`
    (built directly from the localization file's `PF2E.Weapon.Base`, which
    is already `{slug: localized name}` with no i18n-key indirection needed
    -- this is exactly what `foundryvtt/pf2e`'s own source does too:
    `R.mapValues(EN_JSON.PF2E.Weapon.Base, (_v, slug) => ...)`). Every
    resolved value across all three sources is then localized through the
    same file -- previously every `config_lookup` value was left as a raw
    `"PF2E.Skill.Acrobatics"`-style key. A dictionary this project expects
    to find but can't (network failure, source moved the const, changed
    its shape) is silently omitted rather than failing the whole build --
    any `ChoiceSet` referencing it just stays unresolved, same
    honest-degradation behavior as everything else in this file."""
    localization = _load_localization_dict(tag, cache_dir)

    lookups: dict[str, dict[str, str]] = dict(_CONFIG_HARDCODED_LOOKUPS)
    file_cache: dict[str, str | None] = {}
    for config_key, (repo_path, const_name) in _CONFIG_TS_LOOKUPS.items():
        if repo_path not in file_cache:
            file_cache[repo_path] = fetch_source_file(tag, repo_path, cache_dir)
        source = file_cache[repo_path]
        if source is None:
            continue
        body = _extract_ts_object_body(source, const_name)
        if body is None:
            continue
        parsed = _parse_simple_ts_dict(body)
        if parsed:
            lookups[config_key] = parsed

    weapon_base_dict = _flat_prefix_lookup(localization, "PF2E.Weapon.Base.")
    if weapon_base_dict:
        lookups["baseWeaponTypes"] = weapon_base_dict

    for config_key, entries in lookups.items():
        lookups[config_key] = {
            slug: localization.get(label, label) for slug, label in entries.items()
        }
    return lookups


def _flat_prefix_lookup(flat: dict[str, str], prefix: str) -> dict[str, str]:
    """Pull `{slug: value}` out of a flattened localization dict for all
    keys under `prefix` (e.g. 'PF2E.Weapon.Base.' -> {'adze': 'Adze', ...}),
    slug being the key's final dotted segment."""
    return {
        key[len(prefix):]: value
        for key, value in flat.items()
        if key.startswith(prefix)
    }


_SIMPLE_PREDICATE_RE = re.compile(r"^item:(tag|trait|level):(.+)$")


def _resolve_tag_query_choices(
    conn: sqlite3.Connection, filter_list: list, item_type: str | None
) -> list[dict[str, str]] | None:
    """Statically resolve a ChoiceSet compendium query's `choices.filter`
    against this project's own already-ingested `entries` -- the same
    result a live Foundry compendium search would produce, for the common
    case where every predicate is a plain `item:tag:X` / `item:trait:X` /
    `item:level:N` string (an AND of simple conditions, no nested
    and/or/not/comparison predicates). Returns None (caller skips
    ingesting this ChoiceSet) if any predicate doesn't match that shape,
    rather than guessing at a partial/wrong result."""
    conditions: list[str] = []
    params: list[str | int] = []
    for predicate in filter_list:
        if not isinstance(predicate, str):
            return None
        match = _SIMPLE_PREDICATE_RE.match(predicate)
        if not match:
            return None
        kind, target = match.groups()
        if kind == "tag":
            conditions.append("other_tags LIKE ?")
            params.append(f'%"{target}"%')
        elif kind == "trait":
            conditions.append("traits LIKE ?")
            params.append(f'%"{target}"%')
        else:  # level
            if not target.isdigit():
                return None
            conditions.append("level = ?")
            params.append(int(target))

    sql = "SELECT id, name FROM entries WHERE " + " AND ".join(conditions)
    if item_type:
        sql += " AND type = ?"
        params.append(item_type)
    rows = conn.execute(sql, params).fetchall()
    return [{"label": name, "value": id_} for id_, name in rows]


def _insert_item_grants_and_choices(
    conn: sqlite3.Connection,
    packs: dict[str, list[dict]],
    config_lookups: dict[str, dict[str, str]],
    localization: dict[str, str],
) -> None:
    """Read each item's `system.rules` array (previously never read by this
    ingestion at all) for GrantItem, ChoiceSet, and ActiveEffectLike rule
    elements -- see schema.sql's comments on `item_grants`/
    `item_choice_sets`/`item_stat_modifiers` for what each covers and what's
    deliberately left out. `config_lookups` (from `_load_config_pf2e_lookups`,
    already localized) resolves the subset of `ChoiceSet`s that reference a
    CONFIG.PF2E dictionary by name instead of a literal array;
    `localization` (from `_load_localization_dict`) additionally localizes
    the plain-array `ChoiceSet` shape's own hardcoded labels (e.g. Fighter's
    skill choice ships `"PF2E.Skill.Acrobatics"` as a raw i18n key even in
    the source). Must run after `_insert_entries` so `granted_uuid` ->
    `granted_id` resolution and tag-query choice resolution can query
    against already-populated `entries`."""
    existing_ids = {row[0] for row in conn.execute("SELECT id FROM entries")}
    for items in packs.values():
        for item in items:
            item_id = item.get("_id")
            if not item_id:
                continue
            for rule in item.get("system", {}).get("rules", []):
                key = rule.get("key")
                if key == "GrantItem":
                    uuid = rule.get("uuid")
                    if not uuid:
                        continue
                    candidate_id = _resolve_uuid_to_id(uuid)
                    granted_id = candidate_id if candidate_id in existing_ids else None
                    conn.execute(
                        "INSERT INTO item_grants (granter_id, granted_uuid, granted_id, predicate) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            item_id,
                            uuid,
                            granted_id,
                            json.dumps(rule["predicate"]) if rule.get("predicate") else None,
                        ),
                    )
                elif key == "ChoiceSet":
                    choices = rule.get("choices")
                    if isinstance(choices, list) and choices:
                        localized_choices = [
                            {
                                **c,
                                "label": localization.get(c["label"], c["label"]),
                            }
                            if isinstance(c, dict) and isinstance(c.get("label"), str)
                            else c
                            for c in choices
                        ]
                        conn.execute(
                            "INSERT INTO item_choice_sets (entry_id, flag, choices, source) VALUES (?, ?, ?, 'array')",
                            (item_id, rule.get("flag"), json.dumps(localized_choices)),
                        )
                    elif isinstance(choices, dict) and isinstance(choices.get("filter"), list):
                        resolved = _resolve_tag_query_choices(
                            conn, choices["filter"], choices.get("itemType")
                        )
                        if resolved:
                            conn.execute(
                                "INSERT INTO item_choice_sets (entry_id, flag, choices, source) "
                                "VALUES (?, ?, ?, 'tag_query')",
                                (item_id, rule.get("flag"), json.dumps(resolved)),
                            )
                    elif isinstance(choices, str) and choices in config_lookups:
                        options = [
                            {"label": label, "value": slug}
                            for slug, label in config_lookups[choices].items()
                        ]
                        conn.execute(
                            "INSERT INTO item_choice_sets (entry_id, flag, choices, source) "
                            "VALUES (?, ?, ?, 'config_lookup')",
                            (item_id, rule.get("flag"), json.dumps(options)),
                        )
                    elif (
                        isinstance(choices, dict)
                        and choices.get("config") in config_lookups
                        and not choices.get("predicate")
                    ):
                        # The object form (`{config: "skills", predicate: [...]}`)
                        # almost always carries a predicate filtering by the
                        # character's *current* rank in each candidate (e.g. "only
                        # untrained skills") -- inherently character-state-dependent,
                        # not a fixed answer ingestion can precompute, same reasoning
                        # as the tag_query compound-predicate skip above. Only the
                        # no-predicate case (the full static list) is safe to resolve.
                        options = [
                            {"label": label, "value": slug}
                            for slug, label in config_lookups[choices["config"]].items()
                        ]
                        conn.execute(
                            "INSERT INTO item_choice_sets (entry_id, flag, choices, source) "
                            "VALUES (?, ?, ?, 'config_lookup')",
                            (item_id, rule.get("flag"), json.dumps(options)),
                        )
                elif key == "ActiveEffectLike":
                    path = rule.get("path")
                    if not path:
                        continue
                    conn.execute(
                        "INSERT INTO item_stat_modifiers (entry_id, path, mode, value, predicate) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            item_id,
                            path,
                            rule.get("mode", ""),
                            json.dumps(rule["value"]) if "value" in rule else None,
                            json.dumps(rule["predicate"]) if rule.get("predicate") else None,
                        ),
                    )
                elif key == "Sense":
                    selector = rule.get("selector")
                    if not selector:
                        continue
                    conn.execute(
                        "INSERT INTO item_senses (entry_id, selector, acuity, predicate) VALUES (?, ?, ?, ?)",
                        (
                            item_id,
                            selector,
                            rule.get("acuity"),
                            json.dumps(rule["predicate"]) if rule.get("predicate") else None,
                        ),
                    )


def _insert_item_proficiency_grants(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    """Read `system.subfeatures.proficiencies` -- a structured field
    distinct from `system.rules` (confirmed live: Juggernaut, Evasion,
    Weapon Legend, Perception Mastery etc. all carry an *empty* rules
    array; Foundry's own actor-preparation code reads this field directly
    instead of an ActiveEffectLike rule element for these specific grants).
    Present on feats and class-features across both packs. Faithful
    passthrough of Foundry's own rank scale (0-4) and key names -- no
    interpretation of which character-facing proficiency slot a given key
    maps to happens here (that's the tool layer's job, since e.g. a
    class-slug key like 'rogue' only applies to a character whose own
    class matches, and 'spellcasting' needs the character's tradition to
    resolve to a real proficiency-dict key)."""
    for items in packs.values():
        for item in items:
            item_id = item.get("_id")
            if not item_id:
                continue
            proficiencies = item.get("system", {}).get("subfeatures", {}).get("proficiencies")
            if not isinstance(proficiencies, dict):
                continue
            for prof_key, detail in proficiencies.items():
                if not isinstance(detail, dict) or "rank" not in detail:
                    continue
                conn.execute(
                    "INSERT INTO item_proficiency_grants (entry_id, key, rank) VALUES (?, ?, ?)",
                    (item_id, prof_key, detail["rank"]),
                )


def _insert_prerequisites(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    name_index = build_name_index(packs)
    parser = PrerequisiteParser.load(name_index)
    for item in packs.get("feats", []):
        item_id = item.get("_id")
        prereqs = item.get("system", {}).get("prerequisites", {}).get("value", [])
        for p in prereqs:
            raw_text = p.get("value", "").strip()
            if not raw_text:
                continue
            result = parser.parse(raw_text)
            conn.execute(
                "INSERT INTO prerequisites (entry_id, raw_text, kind, structured) VALUES (?, ?, ?, ?)",
                (
                    item_id,
                    raw_text,
                    result["kind"],
                    json.dumps(result["structured"]) if result["structured"] is not None else None,
                ),
            )


def _insert_class_progression(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    for cls in packs.get("classes", []):
        system = cls.get("system", {})
        slug = system.get("slug") or cls.get("name", "").lower().replace(" ", "-")
        granted = [
            {"level": v.get("level"), "name": v.get("name"), "uuid": v.get("uuid")}
            for v in system.get("items", {}).values()
        ]

        def levels(key: str) -> list[int]:
            return system.get(key, {}).get("value", [])

        saving_throws = system.get("savingThrows", {}) or {}
        trained_skills_raw = system.get("trainedSkills", {}) or {}
        attacks = system.get("attacks", {}) or {}
        defenses = system.get("defenses", {}) or {}

        trained_skills = {
            "additional": trained_skills_raw.get("additional", 0),
            "fixed": trained_skills_raw.get("value", []),
            "custom_lore": trained_skills_raw.get("custom"),
        }

        conn.execute(
            "INSERT OR REPLACE INTO class_progression "
            "(class_slug, class_feat_levels, ancestry_feat_levels, general_feat_levels, "
            " skill_feat_levels, skill_increase_levels, granted_items, key_ability, "
            " hp, perception_rank, fortitude_rank, reflex_rank, will_rank, class_dc_rank, "
            " trained_skills, attacks, defenses) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slug,
                json.dumps(levels("classFeatLevels")),
                json.dumps(levels("ancestryFeatLevels")),
                json.dumps(levels("generalFeatLevels")),
                json.dumps(levels("skillFeatLevels")),
                json.dumps(levels("skillIncreaseLevels")),
                json.dumps(granted),
                json.dumps(system.get("keyAbility", {}).get("value", [])),
                system.get("hp"),
                system.get("perception"),
                saving_throws.get("fortitude"),
                saving_throws.get("reflex"),
                saving_throws.get("will"),
                system.get("classDC"),
                json.dumps(trained_skills),
                json.dumps(attacks),
                json.dumps(defenses),
            ),
        )


class _TableExtractor(HTMLParser):
    """Collects every `<table>...</table>` block in a chunk of HTML into
    `{"header": [str, ...], "rows": [[str, ...], ...]}`, using the stdlib
    parser rather than regex since journal page HTML is real (if simple)
    markup, not a shape worth hand-rolling a brittle pattern for. Only
    `<thead>` cells become the header; a footnote row using a single wide
    `<td colspan=...>` cell (seen on e.g. the Wizard page, for the
    Archwizard's Spellcraft note) naturally produces a short row rather
    than one cell per column, which the caller filters on."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict[str, list]] = []
        self._in_thead = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._current_row: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self.tables.append({"header": [], "rows": []})
        elif tag == "thead" and self.tables:
            self._in_thead = True
        elif tag == "tbody" and self.tables:
            self._in_thead = False
        elif tag == "tr" and self.tables:
            self._current_row = []
        elif tag in ("td", "th") and self.tables:
            self._in_cell = True
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self._in_thead = False
        elif tag == "tr" and self.tables:
            if self._in_thead:
                self.tables[-1]["header"] = self._current_row
            else:
                self.tables[-1]["rows"].append(self._current_row)
            self._current_row = []
        elif tag in ("td", "th") and self._in_cell:
            self._current_row.append("".join(self._cell_text).strip())
            self._in_cell = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)


_SPELL_RANK_COLUMNS = [str(n) for n in range(1, 11)]


def _parse_spell_slot_table(html: str) -> dict[int, dict[str, int]] | None:
    """Finds the "Spells per Day" table in a class journal page's HTML --
    the one whose header row includes "Cantrips" (every class page also has
    an unrelated "Class Features" table, which this skips) -- and parses it
    into `{level: {"cantrips": count, "1": slots, ..., "10": slots}}`.
    Returns None if the page has no such table (a non-caster class)."""
    parser = _TableExtractor()
    parser.feed(html)
    for table in parser.tables:
        header = table["header"]
        if "Cantrips" not in header:
            continue
        # header is ["Your Level", "Cantrips", "1st", "2nd", ...] -- the
        # first two columns are fixed, the rest map 1:1 onto rank numbers
        # in order (however many ranks this class's table goes up to).
        col_keys = ["cantrips"] + _SPELL_RANK_COLUMNS[: len(header) - 2]
        progression: dict[int, dict[str, int]] = {}
        for row in table["rows"]:
            if not row or not row[0].isdigit():
                # Not a level row -- either empty or a footnote (single wide
                # colspan cell whose text doesn't start with a bare level
                # number).
                continue
            level = int(row[0])
            slots: dict[str, int] = {}
            for key, cell in zip(col_keys, row[1:]):
                cleaned = cell.rstrip("*").strip()
                if cleaned.isdigit():
                    slots[key] = int(cleaned)
            progression[level] = slots
        return progression
    return None


def _insert_class_spell_progression(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Reads the "Spells per Day" table off each caster class's own page in
    the "Classes" journal (see `class_spell_slots`' schema comment for why
    -- no compendium item anywhere carries this as structured data; the
    class item's own `system.spellcasting` is just a starting-proficiency
    rank integer). Auto-detects which class pages have the table rather
    than hardcoding a class list, so a newly-added caster class in a future
    release picks this up without a code change. Must run after
    `_insert_entries` so each page's real slug can be resolved from
    `entries` rather than naively derived."""
    journals_path = data_dir / "packs" / "journals.json"
    try:
        journals = json.loads(journals_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    classes_journal = next((j for j in journals if j.get("name") == "Classes"), None)
    if not classes_journal:
        return

    for page in classes_journal.get("pages", []):
        name = page.get("name", "")
        if not name:
            continue
        content = page.get("text", {}).get("content", "")
        progression = _parse_spell_slot_table(content)
        if not progression:
            continue
        row = conn.execute(
            "SELECT slug FROM entries WHERE pack = 'classes' AND name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if not row:
            continue
        slug = row[0]
        for level, slots in progression.items():
            conn.execute(
                "INSERT OR REPLACE INTO class_spell_slots (class_slug, level, slots) VALUES (?, ?, ?)",
                (slug, level, json.dumps(slots)),
            )


def _insert_ancestry_boosts(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    for anc in packs.get("ancestries", []):
        system = anc.get("system", {})
        slug = system.get("slug") or anc.get("name", "").lower().replace(" ", "-")
        conn.execute(
            "INSERT OR REPLACE INTO ancestry_boosts (ancestry_slug, hp, size, boosts, flaws, vision) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                slug,
                system.get("hp"),
                system.get("size"),
                json.dumps(system.get("boosts", {})),
                json.dumps(system.get("flaws", {})),
                system.get("vision"),
            ),
        )


def _insert_background_boosts(conn: sqlite3.Connection, packs: dict[str, list[dict]]) -> None:
    for bg in packs.get("backgrounds", []):
        system = bg.get("system", {})
        slug = system.get("slug") or bg.get("name", "").lower().replace(" ", "-")
        trained_skills_raw = system.get("trainedSkills", {}) or {}
        trained_skills = {
            "fixed": trained_skills_raw.get("value", []),
            "lore": trained_skills_raw.get("lore", []),
        }
        granted = [
            {"level": v.get("level"), "name": v.get("name"), "uuid": v.get("uuid")}
            for v in system.get("items", {}).values()
        ]
        conn.execute(
            "INSERT OR REPLACE INTO background_boosts "
            "(background_slug, boosts, trained_skills, granted_items) VALUES (?, ?, ?, ?)",
            (
                slug,
                json.dumps(system.get("boosts", {})),
                json.dumps(trained_skills),
                json.dumps(granted),
            ),
        )


def _report_changes(old_db: Path, new_conn: sqlite3.Connection) -> None:
    """Lightweight changelog: entry-count deltas per pack, for user visibility
    into what a refresh actually changed. Not a full diff -- just enough to
    say "this refresh touched N feats" rather than staying silent."""
    if not old_db.exists():
        print("No previous database found -- this is the first ingestion run.")
        return
    old_conn = sqlite3.connect(old_db)
    old_counts = dict(old_conn.execute("SELECT pack, COUNT(*) FROM entries GROUP BY pack"))
    new_counts = dict(new_conn.execute("SELECT pack, COUNT(*) FROM entries GROUP BY pack"))
    old_conn.close()

    changed = False
    for pack in sorted(set(old_counts) | set(new_counts)):
        old_n, new_n = old_counts.get(pack, 0), new_counts.get(pack, 0)
        if old_n != new_n:
            changed = True
            print(f"  {pack}: {old_n} -> {new_n} ({new_n - old_n:+d})")
    if not changed:
        print("  No entry-count changes since last ingestion (content may still have been "
              "edited/errata'd in place -- this is a count-level check only).")


def build_database(output_path: Path, cache_dir: Path) -> None:
    release = get_latest_release()
    print(f"Latest foundryvtt/pf2e release: {release.tag}")
    data_dir = download_and_extract(release, cache_dir)
    packs = _load_packs(data_dir)
    print(f"Loaded {len(packs)} packs, {sum(len(v) for v in packs.values())} total entries")

    config_lookups = _load_config_pf2e_lookups(release.tag, cache_dir)
    print(f"Resolved {len(config_lookups)} CONFIG.PF2E lookups from source: {sorted(config_lookups)}")
    localization = _load_localization_dict(release.tag, cache_dir)
    print(f"Resolved {len(localization)} localization keys from source")

    tmp_path = output_path.with_suffix(".tmp.sqlite")
    tmp_path.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_path)
    conn.executescript(SCHEMA_PATH.read_text())

    _insert_entries(conn, packs)
    _insert_variant_rules(conn, data_dir)
    _insert_item_grants_and_choices(conn, packs, config_lookups, localization)
    _insert_item_proficiency_grants(conn, packs)
    _insert_prerequisites(conn, packs)
    _insert_class_progression(conn, packs)
    _insert_class_spell_progression(conn, data_dir)
    _insert_ancestry_boosts(conn, packs)
    _insert_background_boosts(conn, packs)

    conn.execute("INSERT OR REPLACE INTO meta VALUES ('data_version', ?)", (release.tag,))
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('ingested_at', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()

    print("Changes since last ingestion:")
    _report_changes(output_path, conn)
    conn.close()

    tmp_path.replace(output_path)
    print(f"Database ready at {output_path} (data_version={release.tag})")

    pruned = prune_cache(cache_dir, release.tag)
    if pruned:
        print(f"Pruned {len(pruned)} superseded release(s) from {cache_dir}: {', '.join(sorted(pruned))}")


if __name__ == "__main__":
    output = default_db_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    build_database(output, cache)
