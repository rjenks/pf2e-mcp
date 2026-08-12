"""Rules-lookup tools: structured SQL/FTS queries over the ingested PF2e
database, returning small precise records instead of dumping whole tables
or requiring the model to hold rules text in context."""

from __future__ import annotations

import json
from typing import Any

from . import licensing, pfs
from .db import get_connection

_GLOSSARY_PACKS = ("conditions", "bestiary-ability-glossary-srd", "actions", "boons-and-curses")


def _fts_query(text: str) -> str:
    """Treat caller input as a literal phrase rather than FTS5 query syntax
    -- callers (an LLM, ultimately) shouldn't need to know that raw hyphens,
    colons, or asterisks are FTS5 operators. Quoting sidesteps syntax
    errors entirely; unicode61 tokenization still splits on hyphens inside
    the phrase, so "flat-footed" still matches adjacent "flat" "footed"
    tokens."""
    return '"' + text.replace('"', '""') + '"'


def _row_summary(row, overrides: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    overrides = overrides if overrides is not None else pfs.load_overrides()
    return {
        "id": row["id"],
        "name": row["name"],
        "pack": row["pack"],
        "type": row["type"],
        "level": row["level"],
        "category": row["category"],
        "rarity": row["rarity"],
        # Best-effort, not authoritative -- see pfs.py module docstring.
        "pfs": pfs.pfs_status(row["name"], row["rarity"], overrides),
        # Best-effort, not authoritative -- see licensing.py module
        # docstring and NOTICE.md for what this is and isn't.
        "license": licensing.classify(row["pack"], row["is_remaster"]),
    }


def _row_full(row, overrides: dict[str, dict[str, str]] | None = None, conn=None) -> dict[str, Any]:
    result = {
        **_row_summary(row, overrides),
        "slug": row["slug"],
        "traits": json.loads(row["traits"]) if row["traits"] else [],
        "description": row["description"],
        "source_book": row["source_book"],
        "is_remaster": bool(row["is_remaster"]) if row["is_remaster"] is not None else None,
        "other_tags": json.loads(row["other_tags"]) if row["other_tags"] else [],
        # Narrative gate (org membership, a specific background, a region),
        # distinct from mechanical `prerequisites` -- raw text, never
        # evaluated, since it's GM-adjudicated by nature. None if this
        # entry has no Access line.
        "access": row["access_text"],
    }
    if conn is not None:
        choice_row = conn.execute(
            "SELECT flag, choices FROM item_choice_sets WHERE entry_id = ?", (row["id"],)
        ).fetchone()
        if choice_row:
            result["choices"] = {"flag": choice_row["flag"], "options": json.loads(choice_row["choices"])}
    return result


def rules_data_version() -> dict[str, str]:
    """Report which foundryvtt/pf2e release the current rules database was built from."""
    conn = get_connection()
    try:
        rows = dict(conn.execute("SELECT key, value FROM meta"))
        return rows
    finally:
        conn.close()


def rules_search(
    query: str,
    pack: str | None = None,
    type: str | None = None,
    limit: int = 10,
    include_legacy: bool = False,
) -> list[dict[str, Any]]:
    """Full-text search over PF2e rules content. Returns compact summaries
    (id/name/pack/type/level/category) -- call rules_get_entry with an id
    for the full description and raw data of a specific result.

    `include_legacy` (default False): excludes pre-Remaster/OGL-flagged
    content unless set -- but `is_remaster: false` does NOT mean "retired,"
    just "not (yet) reprinted under ORC" (54% of backgrounds, 38% of
    ancestries are legacy-flagged and still fully playable), so set this
    True whenever the user wants the full catalog. See
    `licensing.legacy_filter_sql`."""
    conn = get_connection()
    try:
        sql = (
            "SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.entry_id "
            "WHERE entries_fts MATCH ?" + licensing.legacy_filter_sql(include_legacy, "e.is_remaster")
        )
        params: list[Any] = [_fts_query(query)]
        if pack:
            sql += " AND e.pack = ?"
            params.append(pack)
        if type:
            sql += " AND e.type = ?"
            params.append(type)
        # Exact (case-insensitive) name matches first -- bm25's length
        # normalization can otherwise rank a longer, tangentially-related
        # description above a short, literal name match, which is the
        # opposite of what a game-data lookup tool should do.
        sql += " ORDER BY (LOWER(e.name) != LOWER(?)), f.rank LIMIT ?"
        params.append(query)
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        overrides = pfs.load_overrides()
        return [_row_summary(r, overrides) for r in rows]
    finally:
        conn.close()


def rules_get_entry(
    id: str | None = None,
    name: str | None = None,
    slug: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single PF2e entry's full detail (description, traits, raw
    data) by id, exact name, or slug. Provide exactly one of the three."""
    if not any([id, name, slug]):
        raise ValueError("Provide one of: id, name, slug")
    conn = get_connection()
    try:
        if id:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (id,)).fetchone()
        elif slug:
            row = conn.execute("SELECT * FROM entries WHERE slug = ?", (slug,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM entries WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
        return _row_full(row, conn=conn) if row else None
    finally:
        conn.close()


def list_variant_rules(category: str | None = None) -> list[dict[str, Any]]:
    """Browse the optional/variant character-building and subsystem rules
    ingested from GM Core's "Subsystems and Variant Rules" section (what
    Pathbuilder and most tables call "optional rules") -- Free Archetype,
    Ancestry Paragon, Proficiency without Level, Gradual Attribute Boosts,
    Automatic Bonus Progression, Stamina, Mythic Characters, and several
    GM-facing downtime/encounter subsystems (Chases, Influence, etc.).
    Returns compact summaries; call rules_get_entry with an id/name for the
    full official rule text and book/page citation.

    `category` filters to 'character-building' (affects what a character
    gets at creation/level-up -- ask about these when starting a new
    character) or 'subsystem' (GM-facing, doesn't change character
    creation) -- omit to see both. Only Free Archetype and Ancestry Paragon
    currently have mechanical support elsewhere in this server (extra feat
    slots in build_get_level_up_choices, feat-count checks in
    build_validate_build, both via their `variant_rules` parameter) -- the
    rest are reference text only for now."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM entries WHERE pack = 'variant-rules'"
        params: tuple = ()
        if category:
            sql += " AND category = ?"
            params = (category,)
        sql += " ORDER BY category, name"
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "slug": r["slug"],
                "category": r["category"],
                "source_book": r["source_book"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def rules_related(entry_id: str) -> dict[str, Any]:
    """Cross-reference traversal for a feat: what it requires (its own
    parsed prerequisites) and what requires it (other feats naming it as a
    prerequisite, directly or as one option in an OR-list). Also includes:
    `grants`/`granted_by` (structured GrantItem relationships -- e.g.
    Animist's Shaman practice `grants` Spirit Familiar, so Spirit
    Familiar's own `granted_by` would list Shaman); `stat_modifiers`
    (structured ActiveEffectLike changes the item makes, e.g. Druid's Leaf
    Order upgrading Diplomacy to trained). All exact data sourced from the
    item's own rule elements, not a guess from description prose."""
    conn = get_connection()
    try:
        entry = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not entry:
            return {"error": f"No entry with id {entry_id}"}

        requires = conn.execute(
            "SELECT raw_text, kind, structured FROM prerequisites WHERE entry_id = ?",
            (entry_id,),
        ).fetchall()

        grants = _resolved_grants(conn, entry_id)
        granted_by_rows = conn.execute(
            "SELECT g.granter_id, g.predicate, e.name AS granter_name "
            "FROM item_grants g JOIN entries e ON e.id = g.granter_id "
            "WHERE g.granted_id = ?",
            (entry_id,),
        ).fetchall()
        granted_by = [
            {
                "id": r["granter_id"],
                "name": r["granter_name"],
                "predicate": json.loads(r["predicate"]) if r["predicate"] else None,
            }
            for r in granted_by_rows
        ]
        stat_modifiers = _resolved_stat_modifiers(conn, entry_id)

        name_lower = entry["name"].lower()
        # skill_rank_any/override/etc. don't reference a named entry, so
        # they're correctly excluded here, not by oversight.
        candidates = conn.execute(
            "SELECT p.entry_id, p.structured, e.name, e.id "
            "FROM prerequisites p JOIN entries e ON e.id = p.entry_id "
            "WHERE p.kind IN ('named_reference', 'compound_named')"
        ).fetchall()
        required_by = []
        for c in candidates:
            structured = json.loads(c["structured"]) if c["structured"] else {}
            names = structured.get("any_of") or [structured.get("name")]
            if any(n and n.lower() == name_lower for n in names):
                required_by.append({"id": c["id"], "name": c["name"]})

        return {
            "entry": {"id": entry["id"], "name": entry["name"]},
            "requires": [
                {"raw_text": r["raw_text"], "kind": r["kind"],
                 "structured": json.loads(r["structured"]) if r["structured"] else None}
                for r in requires
            ],
            "required_by": required_by,
            "grants": grants,
            "granted_by": granted_by,
            "stat_modifiers": stat_modifiers,
        }
    finally:
        conn.close()


def list_subclass_option_groups() -> list[dict[str, Any]]:
    """List every subclass-style choice-group tag found in the ingested
    data -- e.g. 'animist-apparition', 'sorcerer-bloodline', 'druid-order',
    'witch-patron', 'cleric-doctrine', 'barbarian-instinct' -- one tag
    family per class that has this kind of choice (order/practice/
    doctrine/patron/instinct/mystery/muse/racket/style/...). Each of these
    is a real level-1-or-later pick with mechanical consequences (e.g. a
    Druid Order or Animist Practice can outright grant a bonus feat), but
    nothing else in this server can enumerate them -- `build_list_classes`
    only returns a class's flat level-1 baseline, with no concept of
    sub-choices. Call `rules_list_subclass_options` with one of the tags
    returned here to get its actual member options, their full mechanical
    description text, and a structured `grants` field for anything each
    option automatically grants (e.g. Shaman practice granting Spirit
    Familiar) -- sourced from exact GrantItem data, not parsed from
    prose."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT other_tags FROM entries WHERE other_tags IS NOT NULL AND other_tags != '[]'"
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            for tag in json.loads(row["other_tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        return [
            {"tag": tag, "option_count": count}
            for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    finally:
        conn.close()


def _resolved_grants(conn, entry_id: str) -> list[dict[str, Any]]:
    """Structured 'what does taking this item automatically grant' --
    sourced from GrantItem rule elements (see item_grants table), which is
    exact, not a guess from description prose. `predicate` is carried
    through unevaluated (e.g. a grant conditional on a specific ChoiceSet
    selection on the same item) -- a non-null predicate means "granted
    only when this condition holds," not "always granted.\""""
    rows = conn.execute(
        "SELECT g.granted_uuid, g.granted_id, g.predicate, e.name AS granted_name "
        "FROM item_grants g LEFT JOIN entries e ON e.id = g.granted_id "
        "WHERE g.granter_id = ?",
        (entry_id,),
    ).fetchall()
    return [
        {
            "name": r["granted_name"],  # None if granted_id didn't resolve (e.g. excluded pack)
            "id": r["granted_id"],
            "uuid": r["granted_uuid"],
            "predicate": json.loads(r["predicate"]) if r["predicate"] else None,
        }
        for r in rows
    ]


def _resolved_stat_modifiers(conn, entry_id: str) -> list[dict[str, Any]]:
    """Structured 'what numeric/proficiency change does taking this item
    make' -- sourced from ActiveEffectLike rule elements (see
    item_stat_modifiers table), e.g. Druid's Leaf Order upgrading Diplomacy
    to trained. `path` is Foundry's own dotted actor-property convention
    (e.g. 'system.skills.diplomacy.rank'), not reinterpreted here -- a
    'rank' path with mode 'upgrade'/'override' is a proficiency grant, but
    this covers far more than skills (HP, defenses, spellcasting
    proficiency, bonus languages, ...), so callers should filter `path`
    for what they actually care about rather than assume every row is a
    skill."""
    rows = conn.execute(
        "SELECT path, mode, value, predicate FROM item_stat_modifiers WHERE entry_id = ?",
        (entry_id,),
    ).fetchall()
    return [
        {
            "path": r["path"],
            "mode": r["mode"],
            "value": json.loads(r["value"]) if r["value"] else None,
            "predicate": json.loads(r["predicate"]) if r["predicate"] else None,
        }
        for r in rows
    ]


def list_subclass_options(tag: str) -> list[dict[str, Any]]:
    """List every option in a subclass-style choice group by its exact tag
    (see `rules_list_subclass_option_groups` for valid values -- tag
    matching is exact, not fuzzy, since e.g. Inventor's two groups
    ('weapon-innovation-modification'/'armor-innovation-modification')
    don't share a class-name prefix the way most others do, so guessing a
    class-name-based filter would silently miss real groups). Returns full
    entries (with description, plus `grants` -- anything the option
    automatically grants, e.g. Shaman practice granting Spirit Familiar --
    and `stat_modifiers` -- numeric/proficiency changes, e.g. an order's
    skill training -- both sourced from structured GrantItem/
    ActiveEffectLike data, not parsed from prose) rather than summaries,
    since the point of this tool is seeing what each option actually does
    in one call."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM entries WHERE other_tags LIKE ? ORDER BY level, name",
            (f'%"{tag}"%',),
        ).fetchall()
        overrides = pfs.load_overrides()
        results = []
        for r in rows:
            entry = _row_full(r, overrides, conn=conn)
            entry["grants"] = _resolved_grants(conn, r["id"])
            entry["stat_modifiers"] = _resolved_stat_modifiers(conn, r["id"])
            results.append(entry)
        return results
    finally:
        conn.close()


def rules_explain(topic: str, limit: int = 5) -> list[dict[str, Any]]:
    """Explain a rules topic (e.g. a condition or general rule) by full-text
    searching content most likely to hold rules text (conditions, actions,
    glossary entries) and returning descriptions inline, since the point is
    a direct answer rather than a lookup requiring a follow-up call."""
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in _GLOSSARY_PACKS)
        order_clause = "ORDER BY (LOWER(e.name) != LOWER(?)), f.rank LIMIT ?"
        sql = (
            "SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.entry_id "
            f"WHERE entries_fts MATCH ? AND e.pack IN ({placeholders}) {order_clause}"
        )
        fts_topic = _fts_query(topic)
        rows = conn.execute(sql, [fts_topic, *_GLOSSARY_PACKS, topic, limit]).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.entry_id "
                f"WHERE entries_fts MATCH ? {order_clause}",
                [fts_topic, topic, limit],
            ).fetchall()
        overrides = pfs.load_overrides()
        return [_row_full(r, overrides, conn=conn) for r in rows]
    finally:
        conn.close()
