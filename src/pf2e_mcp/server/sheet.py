"""Print-ready HTML character sheet rendering.

Takes a Pathbuilder-shaped character dict (the same shape the rest of this
tool surface consumes) and writes a single self-contained HTML file: fonts
inlined as base64 WOFF2 from `sheet_assets`, every ornament drawn as inline
SVG, no stylesheet, script, image or font fetched at view time. The file can
be mailed to a player and printed as-is.

Everything on the sheet is derived from the character dict plus this
project's own ingested rules data. Nothing is authored here, which is why
there is no tactical commentary or roleplay section -- if it isn't in the
character data or the rulebook, it isn't on the sheet.

Two derivations deliberately trust the caller's data over the database:

* A weapon's damage die comes from the character's `weapons[].die`, not the
  base item. Die-size effects (a cleric's Deadly Simplicity, a Rogue's
  sneak attack dice) live outside the item, and the character export is the
  only place they are recorded. Where `weapons[].display` disagrees with
  `die`, the display string is printed beneath the strike verbatim and a
  warning is returned.
* Proficiency ranks come from `proficiencies`, never re-derived from class
  and level, so a sheet always matches the build it was rendered from.

A shield's Hardness, HP and Broken Threshold are the item's own values plus
permanent equipment upgrades only. A stronger base shield resolves as its own
entry, and a reinforcing rune recorded on the shield is applied -- its
increments and caps are parsed from the rune's own text, which is the only
place they exist (the rune items carry no `hardness` field and no rule
elements). Bonuses from spells, feats and other effects are excluded on
purpose: a status bonus such as Emblazon Armament's +1 Hardness may not be
active when the sheet is in use, so printing it would overstate what the
shield reliably blocks.

Unresolvable names (an item the export calls "Repair Kit" where the rules
data has "Repair Toolkit") are reported in the return value's `unresolved`
list rather than silently dropped or guessed at.
"""

from __future__ import annotations

import base64
import html
import json
import math
import re
import zlib
from pathlib import Path
from typing import Any

from . import build_tools, pf2e_math as m
from .db import get_connection
from .sheet_assets import FACES

# --------------------------------------------------------------------------
# Rules-data lookup
# --------------------------------------------------------------------------

# Which packs to search for each kind of name, in preference order. Several
# names collide across packs -- "Shield Block" exists as a class feature stub,
# a general feat carrying the real text, and a monster ability; "Bane" is both
# a spell and a property rune -- so the order here is load-bearing.
_PACKS_BY_KIND: dict[str, tuple[str, ...]] = {
    "ancestry": ("ancestries",),
    "heritage": ("heritages",),
    "background": ("backgrounds",),
    # Exports put more than feats in the `feats` array -- a heritage, sometimes
    # the background, and class features granted by a choice all land there --
    # so the fallback packs matter for anything past a plain Pathbuilder export.
    "feat": ("feats", "class-features", "heritages", "backgrounds",
             "ancestries", "actions"),
    "classfeature": ("class-features", "feats", "actions"),
    "spell": ("spells",),
    "item": ("equipment",),
    "action": ("actions", "feats"),
    # Covers deities proper plus the pantheons, covenants and philosophies that
    # can be followed instead of one.
    "deity": ("deities",),
}

# Values a character export uses to mean "no deity". Pathbuilder writes "Not
# set"; hand-edited files use an empty string or a dash. Treated as absence,
# not as a name that failed to resolve.
_NO_DEITY = {
    "", "-", "--", "—", "n/a", "na", "none", "no deity", "not set", "notset",
    "nothing", "unaffiliated", "atheist", "atheism", "tbd", "?",
}

_ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_NAMES = {
    "str": ("Strength", "STR"), "dex": ("Dexterity", "DEX"),
    "con": ("Constitution", "CON"), "int": ("Intelligence", "INT"),
    "wis": ("Wisdom", "WIS"), "cha": ("Charisma", "CHA"),
}

_CORE_SKILLS = {
    "acrobatics": "dex", "arcana": "int", "athletics": "str", "crafting": "int",
    "deception": "cha", "diplomacy": "cha", "intimidation": "cha", "medicine": "wis",
    "nature": "wis", "occultism": "int", "performance": "cha", "religion": "wis",
    "society": "int", "stealth": "dex", "survival": "wis", "thievery": "dex",
}

_RANK_ABBR = {0: "U", 2: "T", 4: "E", 6: "M", 8: "L"}
# Spelled out where there is room for it -- the armour footer reads better as
# "Trained (+4)" than "T (+4)".
_RANK_NAME = {0: "Untrained", 2: "Trained", 4: "Expert", 6: "Master",
              8: "Legendary"}

# Pathbuilder records striking runes by name; each step adds a damage die.
_STRIKING_DICE = {
    "striking": 2, "greater striking": 3, "major striking": 4,
}

# PF2e's damage die ladder, for effects that step a die up.
_DIE_LADDER = ("d4", "d6", "d8", "d10", "d12")


# Traits that quote a die of their own in a weapon's note -- "deadly d8" is a
# bonus die on a critical hit, not the weapon's damage die.
_TRAIT_DIE_WORDS = ("deadly", "fatal", "fatal aim", "versatile", "jousting",
                    "two-hand", "climbing", "brutal")


def _display_die_conflict(display: str, printed: str,
                          weapon_name: str = "") -> str | None:
    """A damage die named in a weapon's free-text note that disagrees with the
    one being printed, or None.

    Most dice in these notes are not the weapon's own damage die. They belong to
    a trait ("finesse, deadly d8" -- a bonus die on a critical hit) or to a
    different weapon the player weighed up and rejected ("Chosen over Longbow
    (d8)", "shares Longbow's d8"). Both forms appear verbatim in this repo's
    characters, and flagging them teaches a reader to ignore the warnings list.

    So a die counts only when nothing nearby attributes it elsewhere: no trait
    word immediately before it, and no capitalised name in the run-up other than
    this weapon's own.
    """
    own = {w.lower().strip(",.;:'s") for w in weapon_name.split() if len(w) > 2}
    for match in re.finditer(r"\bd(\d+)\b", display):
        die = f"d{match.group(1)}"
        if die == printed:
            continue
        before = display[:match.start()].rstrip()
        if any(before.lower().endswith(word) for word in _TRAIT_DIE_WORDS):
            continue
        others = [
            token for token in re.findall(r"[A-Za-z][\w']*", before[-32:])
            if token[:1].isupper() and len(token) > 2
            and token.lower().strip("'s") not in own
        ]
        if others:
            continue
        return die
    return None


def _step_die(die: str) -> str:
    try:
        return _DIE_LADDER[min(_DIE_LADDER.index(die) + 1, len(_DIE_LADDER) - 1)]
    except ValueError:
        return die

# Conditions worth tracking in pencil, with what each actually does.
_CONDITIONS = [
    ("Off-guard", "−2 AC"), ("Frightened", "−N all checks/DCs"),
    ("Sickened", "−N all checks/DCs"), ("Clumsy", "−N Dex-based"),
    ("Enfeebled", "−N Str-based"), ("Stupefied", "−N Int/Wis/Cha"),
    ("Drained", "−N Con, lose HP"), ("Slowed", "−N actions"),
    ("Prone", "off-guard, must Crawl"), ("Grabbed", "off-guard, immobilized"),
    ("Deafened", "−2 auditory Perc."), ("Fleeing", "must flee source"),
]

_PAPER = {"letter": "letter portrait", "a4": "A4 portrait"}

# Image types a browser can render from a data: URI. EPS and AI are in Paizo's
# Community Use Package alongside the PNGs but are print-workflow formats no
# browser will display, so they are rejected with an explanation rather than
# silently embedded as a broken image.
_LOGO_MIME = {
    ".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
}
# Beyond this a logo dominates the file size for no visible gain at 30px tall.
_LOGO_SOFT_LIMIT = 400 * 1024
# Target height of the logo's visible ink, chosen to sit alongside the 23pt
# character name without out-shouting it or deepening the header.
_LOGO_INK_HEIGHT = 27.0


def _logo_data_uri(path: str) -> tuple[str, list[str]]:
    """Read an image and return it as a data: URI, plus any warnings.

    The bytes are embedded verbatim -- not resized, recompressed, recoloured or
    cropped -- because Paizo's Community Use Policy forbids altering the colour,
    typography, design or proportions of a logo from its package. The sheet
    scales it by height in CSS with `width: auto`, which is proportional
    resizing and is explicitly allowed.
    """
    src = Path(path).expanduser()
    if not src.is_file():
        raise ValueError(f"logo file not found: {src}")
    mime = _LOGO_MIME.get(src.suffix.lower())
    if mime is None:
        raise ValueError(
            f"logo format {src.suffix!r} can't be embedded in HTML. Use PNG or "
            f"SVG (the Community Use Package ships transparent PNGs); EPS and AI "
            f"are print formats browsers can't display."
        )
    data = src.read_bytes()
    warnings: list[str] = []
    if len(data) > _LOGO_SOFT_LIMIT:
        warnings.append(
            f"Logo is {len(data) // 1024} KB, which will dominate the sheet's "
            f"file size; a transparent PNG a few hundred pixels tall is plenty "
            f"at the size it prints."
        )
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", warnings


def _png_alpha_bounds(data: bytes) -> tuple[float, float] | None:
    """Fraction of a PNG's height that is fully transparent at the top and at
    the bottom, or None if the file isn't a shape this can read.

    Logo art is usually delivered on a generous transparent canvas -- the
    Pathfinder wordmark's own artwork is only 54% of its file's height, evenly
    padded -- and a browser aligns the *box*, not the ink. Left uncorrected the
    logo floats above the text it should sit beside and inflates the header by
    the padding. Measuring the ink lets the caller cancel it out with negative
    margins, which moves the image without rescaling or cropping it.

    Handles 8-bit non-interlaced RGBA/greyscale-alpha, which is what the
    Community Use PNGs are; anything else returns None and is placed as-is.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        tag = data[pos + 4:pos + 8]
        if tag == b"IHDR":
            ihdr = data[pos + 8:pos + 8 + length]
        elif tag == b"IDAT":
            idat.append(data[pos + 8:pos + 8 + length])
        elif tag == b"IEND":
            break
        pos += 12 + length
    if ihdr is None or len(ihdr) < 13 or not idat:
        return None
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    depth, colour, _, _, interlace = ihdr[8], ihdr[9], ihdr[10], ihdr[11], ihdr[12]
    channels = {4: 2, 6: 4}.get(colour)          # grey+alpha, RGBA
    if depth != 8 or interlace != 0 or channels is None or not width or not height:
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < (stride + 1) * height:
        return None

    prev = bytearray(stride)
    first_ink = last_ink = None
    at = 0
    for y in range(height):
        filt = raw[at]; at += 1
        line = bytearray(raw[at:at + stride]); at += stride
        if filt == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filt == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif filt == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif filt == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if (pa <= pb and pa <= pc)
                                      else b if pb <= pc else c)) & 0xFF
        if any(v > 8 for v in line[channels - 1::channels]):
            if first_ink is None:
                first_ink = y
            last_ink = y
        prev = line

    if first_ink is None:
        return None
    return first_ink / height, (height - last_ink - 1) / height


def _find_symbol(symbol_dir: str, deity_name: str) -> Path | None:
    """Locate a deity's symbol image in a directory of them.

    Matches on the deity's name against the file stem, tolerating the
    underscores Paizo's Community Use "Pathfinder Religious Symbols" pack uses
    in place of spaces ("Sun_Wukong.png").
    """
    root = Path(symbol_dir).expanduser()
    if not root.is_dir():
        raise ValueError(f"symbol_dir is not a directory: {root}")
    wanted = {deity_name.strip().lower(),
              deity_name.strip().lower().replace(" ", "_"),
              deity_name.strip().lower().replace(" ", "-")}
    for path in sorted(root.iterdir()):
        if (path.is_file() and path.suffix.lower() in _LOGO_MIME
                and path.stem.lower() in wanted):
            return path
    return None


def _name_variants(name: str) -> list[str]:
    """Candidate spellings to try for one recorded name, most-faithful first.

    Character exports and the rules data disagree in a handful of systematic
    ways, and every one of these was observed in real files in this repo:
    a qualifier the player appended ("Wooden Shield (emblazoned)"), Pathbuilder's
    inverted catalogue ordering ("Clothing (Explorer's)" for "Explorer's
    Clothing"), Tools where the rules say Toolkit ("Thieves' Tools"), and a
    pluralised class feature ("Debilitating Strikes" for "Debilitating
    Strike"). Anything still unmatched after this is reported to the caller
    rather than guessed at.
    """
    name = name.strip()
    out = [name]

    def push(candidate: str) -> None:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and candidate not in out:
            out.append(candidate)

    bare = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    push(bare)

    inverted = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    if inverted:
        push(f"{inverted.group(2)} {inverted.group(1)}")

    for base in list(out):
        if re.search(r"\bTools\b", base):
            push(re.sub(r"\bTools\b", "Toolkit", base))
        elif re.search(r"\bKit\b", base):
            push(re.sub(r"\bKit\b", "Toolkit", base))
        elif re.search(r"\bToolkit\b", base):
            push(re.sub(r"\bToolkit\b", "Tools", base))
            push(re.sub(r"\bToolkit\b", "Kit", base))

    for base in list(out):
        if base.endswith("s") and not base.endswith("ss"):
            push(base[:-1])

    return out


class _Library:
    """Batched, cached name -> rules-entry resolution for one render."""

    def __init__(self, conn, class_name: str = "") -> None:
        self._conn = conn
        # Used to disambiguate a qualified name that exists for several
        # classes -- "Spirit Familiar" is both (Animist) and (Witch).
        self.class_name = (class_name or "").strip()
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        self.unresolved: list[dict[str, str]] = []
        self.aliased: list[dict[str, str]] = []
        # Every entry actually hydrated during a render, whatever route it came
        # in by. The attribution page credits exactly these sourcebooks.
        self.used: list[dict[str, Any]] = []

    def get(self, name: str, kind: str, quiet: bool = False) -> dict[str, Any] | None:
        """Resolve one recorded name. `quiet` suppresses the `unresolved`
        report -- used for a deity, where not finding a match usually means a
        home-game pantheon rather than a mistake in the character data."""
        if not name:
            return None
        key = (name.lower(), kind)
        if key in self._cache:
            return self._cache[key]
        entry = None
        for i, variant in enumerate(_name_variants(name)):
            entry = self._query(variant, kind)
            if entry is not None:
                if i:
                    # Matched on something other than the recorded name; say so
                    # rather than letting a near-miss pass for an exact hit.
                    self.aliased.append({"recorded": name, "matched": entry["name"],
                                         "kind": kind})
                break
        if entry is None:
            # Last resort, the inverse of stripping a qualifier: the rules data
            # may qualify a name the character records bare -- "Spirit Familiar"
            # is stored as "Spirit Familiar (Animist)", "Spellbook" as
            # "Spellbook (Blank)", "Oil" as "Oil (1 pint)".
            entry = self._query_qualified(name, kind)
            if entry is not None:
                self.aliased.append({"recorded": name, "matched": entry["name"],
                                     "kind": kind})
        if entry is None and not quiet:
            self.unresolved.append({"name": name, "kind": kind})
        self._cache[key] = entry
        return entry

    def _query_qualified(self, name: str, kind: str) -> dict[str, Any] | None:
        """Find entries named "<name> (<qualifier>)".

        Accepts a single candidate outright. Where several exist the qualifier
        must match the character's class, otherwise this gives up rather than
        guess -- attaching a Witch's feat to an Animist because both have a
        "Spirit Familiar" would be worse than reporting it unresolved.
        """
        packs = _PACKS_BY_KIND.get(kind)
        if not packs or not name:
            return None
        # `_` and `%` are LIKE wildcards; a name containing either would
        # otherwise match far too much.
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        placeholders = ",".join("?" for _ in packs)
        order = " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(packs))
        rows = self._conn.execute(
            f"SELECT * FROM entries WHERE name LIKE ? ESCAPE '\\' "
            f"AND pack IN ({placeholders}) "
            f"ORDER BY CASE pack {order} ELSE 99 END, level, name",
            (f"{escaped} (%)", *packs, *packs),
        ).fetchall()
        if not rows:
            return None
        if len(rows) == 1:
            return self._hydrate(rows[0])
        if self.class_name:
            wanted = f"{name} ({self.class_name})".lower()
            for row in rows:
                if row["name"].lower() == wanted:
                    return self._hydrate(row)
        return None

    def by_id(self, entry_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return self._hydrate(row)

    def _query(self, name: str, kind: str) -> dict[str, Any] | None:
        packs = _PACKS_BY_KIND.get(kind)
        if not packs or not name:
            return None
        placeholders = ",".join("?" for _ in packs)
        # ORDER BY over a CASE expression preserves the pack preference order
        # declared above; without it SQLite is free to hand back the
        # monster-ability "Shield Block" instead of the feat with the real text.
        order = " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(packs))
        row = self._conn.execute(
            f"SELECT * FROM entries WHERE name = ? COLLATE NOCASE "
            f"AND pack IN ({placeholders}) "
            f"ORDER BY CASE pack {order} ELSE 99 END LIMIT 1",
            (name, *packs, *packs),
        ).fetchone()
        return self._hydrate(row)

    def _hydrate(self, row) -> dict[str, Any] | None:
        if row is None:
            return None
        raw = json.loads(row["raw_json"]).get("system", {})
        entry = {
            "name": row["name"],
            "pack": row["pack"],
            "type": row["type"],
            "level": row["level"],
            "traits": json.loads(row["traits"] or "[]"),
            "rarity": row["rarity"],
            "source_book": row["source_book"],
            "is_remaster": row["is_remaster"],
            "desc_html": raw.get("description", {}).get("value", ""),
            "system": {k: v for k, v in raw.items()
                       if k not in ("description", "rules", "publication")},
        }
        self.used.append(entry)
        return entry

    def class_features(self, class_name: str, level: int) -> list[dict[str, Any]]:
        """Auto-granted class features at or below `level`, read from the
        ingested `class_progression.granted_items`. A character export does
        not list these -- it only records choices -- so without this the
        sheet would silently omit the features doing the most work (a
        cleric's Divine Font, a magus's Spellstrike)."""
        row = self._conn.execute(
            "SELECT granted_items FROM class_progression WHERE class_slug = ?",
            (class_name.lower().replace(" ", "-"),),
        ).fetchone()
        if row is None:
            return []
        out = []
        for grant in sorted(json.loads(row["granted_items"] or "[]"),
                            key=lambda g: (g.get("level") or 0, g.get("name") or "")):
            if (grant.get("level") or 0) > level:
                continue
            # Resolve by the Foundry id in the grant's uuid, not by its name.
            # Several grants are recorded under a bare name the compendium
            # qualifies -- "Deity" is stored as "Deity (Cleric)", "First
            # Doctrine" as "First Doctrine (Warpriest)" -- so a name lookup
            # silently drops exactly the features that matter most.
            entry = None
            uuid = str(grant.get("uuid") or "")
            if uuid:
                entry = self.by_id(uuid.rsplit(".", 1)[-1])
            if entry is None:
                entry = self.get(grant.get("name", ""), "classfeature")
            if entry:
                out.append({**entry, "granted_level": grant.get("level")})
        return out

    def subclass_selections(self, class_name: str,
                            character: dict[str, Any]) -> list[dict[str, Any]]:
        """Best-effort recovery of a subclass choice (cleric doctrine, druid
        order, sorcerer bloodline). Character exports record these only in
        free-text `specials` prose, so this matches the class's own tagged
        option names against that prose. Reported in the result's `resolved`
        list so a caller can see what it decided."""
        tag = f"{class_name.lower().replace(' ', '-')}-"
        rows = self._conn.execute(
            "SELECT name FROM entries WHERE pack = 'class-features' "
            "AND other_tags LIKE ?", (f'%"{tag}%',),
        ).fetchall()
        haystack = " ".join(
            str(s) for s in character.get("specials", []) or []
        ) + " " + " ".join(
            str(f[0]) for f in character.get("feats", []) or [] if f
        )
        found = []
        for row in rows:
            if re.search(rf"\b{re.escape(row['name'])}\b", haystack):
                entry = self.get(row["name"], "classfeature")
                if entry:
                    found.append(entry)
        return found


# --------------------------------------------------------------------------
# Foundry markup -> printable HTML
# --------------------------------------------------------------------------

def _template_text(match: re.Match) -> str:
    body = match.group(1)
    shape = body.split("|")[0]
    dist = re.search(r"distance:(\d+)", body)
    return f"{dist.group(1)}-foot {shape}" if dist else shape


def _rules_html(raw: str, spell_rank: int | None = None) -> str:
    """Reduce Foundry's enricher syntax to readable prose, keeping the
    structural HTML (paragraphs, lists, <hr> rule breaks, <strong> labels)
    that the rules text uses to separate degrees of success."""
    s = raw or ""
    # A paragraph that is nothing but "Effect: X" / "Spell Effect: X" links a
    # Foundry automation item and means nothing on paper.
    s = re.sub(r"<p>\s*@UUID\[[^\]]+\]\{(?:Spell\s+)?Effect:[^}]*\}\s*</p>", "", s)
    s = re.sub(r"@UUID\[[^\]]+\]\{([^}]*)\}", r'<span class="xref">\1</span>', s)
    s = re.sub(r"@Template\[([^\]]+)\]", _template_text, s)
    # Persistent-damage formulas keyed to spell rank, e.g. Needle Darts'
    # "@Damage[(@item.level)[bleed]]". Resolve when we know the rank.
    if spell_rank is not None:
        s = re.sub(r"@Damage\[\(@item\.level\)\[(\w+)\]\]",
                   lambda mo: f"{spell_rank} persistent {mo.group(1)}", s)
    s = re.sub(r"@\w+\[[^\]]*\]\{([^}]*)\}", r"\1", s)
    s = re.sub(r"@\w+\[[^\]]*\]", "", s)
    s = re.sub(r'<span class="action-glyph">\s*([123RrFf]+)\s*</span>',
               lambda mo: _glyph(mo.group(1)), s)
    return s.strip()


# --------------------------------------------------------------------------
# Original inline-SVG ornament. Plain geometric constructions -- nothing here
# is traced from or derived from any publisher's artwork.
# --------------------------------------------------------------------------

def _glyph(spec: str) -> str:
    """Action-cost glyph: a filled lozenge per action, an open lozenge for a
    free action, a hooked arrow for a reaction."""
    spec = str(spec).strip().lower()
    filled = '<path d="M5 0.6 L9.4 5 L5 9.4 L0.6 5 Z" fill="currentColor"/>'
    hollow = ('<path d="M5 0.9 L9.1 5 L5 9.1 L0.9 5 Z" fill="none" '
              'stroke="currentColor" stroke-width="1.5"/>')
    arrow = ('<path d="M8.6 2.2 A3.9 3.9 0 1 0 5 8.9" fill="none" '
             'stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>'
             '<path d="M8.9 0.1 L8.9 3.6 L5.5 1.9 Z" fill="currentColor"/>')

    def svg(inner: str) -> str:
        return f'<svg class="glyph" viewBox="0 0 10 10" aria-hidden="true">{inner}</svg>'

    if spec == "r":
        return svg(arrow)
    if spec == "f":
        return svg(hollow)
    if spec.isdigit():
        return "".join(svg(filled) for _ in range(min(int(spec), 3)))
    return ""


def _cost(spec: Any) -> str:
    """Glyph plus words for an action cost."""
    if spec in (None, "", {}):
        return ""
    s = str(spec).strip()
    if s == "1 to 3":
        return (f'{_glyph("1")}<span class="cost-txt" style="margin:0 3px">to</span>'
                f'{_glyph("3")}<span class="cost-txt">1 to 3 actions</span>')
    if s.isdigit():
        word = "action" if s == "1" else "actions"
        return f'{_glyph(s)}<span class="cost-txt">{s} {word}</span>'
    if s.lower() == "reaction":
        return f'{_glyph("r")}<span class="cost-txt">reaction</span>'
    if s.lower() == "free":
        return f'{_glyph("f")}<span class="cost-txt">free action</span>'
    return f'<span class="cost-txt">{html.escape(s)}</span>'


def _spiral(size: int = 22) -> str:
    """A logarithmic spiral from its parametric equation, used as this
    sheet's own section mark."""
    pts = []
    for i in range(141):
        t = i / 140 * (4.2 * math.pi)
        r = 0.55 * math.exp(0.245 * t)
        pts.append(f"{16 + r * math.cos(t):.2f},{16 + r * math.sin(t):.2f}")
    return (f'<svg class="spiral" viewBox="0 0 32 32" width="{size}" '
            f'height="{size}" aria-hidden="true"><polyline points="{" ".join(pts)}" '
            f'fill="none" stroke="currentColor" stroke-width="1.05" '
            f'stroke-linecap="round"/></svg>')


def _pips(rank: int) -> str:
    return ('<span class="pips">' + "".join(
        f'<span class="pip{" filled" if rank >= i else ""}"></span>'
        for i in (2, 4, 6, 8)) + "</span>")


def _circles(n: int, cls: str = "") -> str:
    n = max(0, min(int(n), 24))
    return (f'<span class="circles {cls}">'
            + '<span class="circ"></span>' * n + "</span>")


def _mod(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _traits(traits, rarity=None) -> str:
    out = []
    if rarity and rarity != "common":
        out.append(f'<span class="trait rarity">{html.escape(rarity)}</span>')
    out += [f'<span class="trait">{html.escape(str(t).replace("-", " "))}</span>'
            for t in traits or []]
    return f'<span class="traits">{"".join(out)}</span>' if out else ""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


# --------------------------------------------------------------------------
# Mechanical derivation
# --------------------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    """Lead clause of a free-text feature, trimmed to `limit` characters on a
    word boundary. Character exports write these as full sentences with the
    mechanics after a dash, and only the name is wanted on page 1."""
    lead = text.split(" -- ")[0].split(" — ")[0].split(" (")[0].strip()
    lead = lead.rstrip(".")
    if len(lead) <= limit:
        return lead
    cut = lead[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def _strip_to_qualifier(recorded: str, resolved: str) -> str:
    """What the character's own spelling of an item adds over the real item
    name -- "Wooden Shield (emblazoned)" against "Wooden Shield" gives
    "emblazoned". Empty when the difference isn't a trailing parenthetical
    (a Tools/Toolkit style rename, say), since there is nothing useful to
    surface in that case."""
    trailing = re.search(r"\(([^)]*)\)\s*$", recorded.strip())
    if not trailing:
        return ""
    base = recorded.strip()[:trailing.start()].strip()
    if base.lower() != resolved.strip().lower():
        return ""
    return trailing.group(1).strip()


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _sanctification(value: Any) -> str:
    """Foundry stores this as {modal: can|must, what: [holy|unholy]}, or null
    for a deity that grants no sanctification. Wording follows the game's own
    labels ("can choose holy", "must choose unholy")."""
    if not isinstance(value, dict):
        return "none"
    what = [str(w) for w in (value.get("what") or [])]
    if not what:
        return "none"
    modal = "must" if value.get("modal") == "must" else "can"
    return f"{modal} choose {' or '.join(what)}"


def _titles(values: Any, joiner: str = " or ") -> str:
    return joiner.join(str(v).replace("-", " ").title() for v in (values or []))


def _deity_info(character: dict[str, Any], lib: _Library) -> dict[str, Any]:
    """Resolve the character's deity and flatten its mechanics into printable
    label/value pairs.

    Three outcomes, deliberately distinguished: `none` when the character
    follows no deity (the export's own placeholder counts), `resolved` when the
    name matches the rules data, and `unrecognized` when a name is recorded but
    isn't in the database -- a home-game or non-standard pantheon, which is not
    an error in the character data and so is never reported as `unresolved`.
    """
    recorded = str(character.get("deity") or "").strip()
    if recorded.lower() in _NO_DEITY:
        return {"recorded": recorded, "status": "none", "entry": None, "facts": []}

    entry = lib.get(recorded, "deity", quiet=True)
    if entry is None:
        return {"recorded": recorded, "status": "unrecognized", "entry": None,
                "facts": []}

    s = entry["system"]
    facts: list[tuple[str, str]] = []

    category = str(s.get("category") or "deity")
    if category != "deity":
        # A pantheon, covenant or philosophy can be followed in place of a
        # single deity, and often leaves the deity-only fields empty.
        facts.append(("Followed as", category.title()))

    font = [str(f).title() for f in (s.get("font") or [])]
    if font:
        facts.append(("Divine Font", " or ".join(sorted(font, reverse=True))))
    facts.append(("Divine Sanctification", _sanctification(s.get("sanctification"))))
    if s.get("skill"):
        facts.append(("Divine Skill", _titles(s["skill"])))
    if s.get("weapons"):
        facts.append(("Favored Weapon", _titles(s["weapons"])))

    domains = s.get("domains") or {}
    if domains.get("primary"):
        facts.append(("Domains", _titles(domains["primary"], ", ")))
    if domains.get("alternate"):
        facts.append(("Alternate Domains", _titles(domains["alternate"], ", ")))
    if s.get("attribute"):
        # Only reachable through the Raised by Belief background, per the
        # game's own hint text for this field -- labelled so it isn't mistaken
        # for a cleric's key attribute.
        facts.append(("Divine Attribute",
                      " or ".join(str(a).upper() for a in s["attribute"])))

    spells = s.get("spells") or {}
    if isinstance(spells, dict) and spells:
        parts = []
        for rank, uuid in sorted(spells.items(), key=lambda kv: int(kv[0])):
            spell = lib.by_id(str(uuid).rsplit(".", 1)[-1])
            parts.append(f"{_ordinal(int(rank))} {spell['name'] if spell else '?'}")
        # Plain separator, not an HTML entity: `facts` is returned to the
        # caller as data as well as being rendered into the page.
        facts.append(("Cleric Spells", ", ".join(parts)))

    return {"recorded": recorded, "status": "resolved", "entry": entry,
            "facts": facts, "category": category,
            # Slugs, for matching a carried weapon against the favored one --
            # Deadly Simplicity turns on exactly that comparison.
            "favored_weapons": [str(w).lower() for w in (s.get("weapons") or [])]}


# A reinforcing rune states its own increments and caps in its description
# ("Hardness increases by 3, it gains an additional 44 Hit Points, and its BT
# increases by 22 (maximum 8 Hardness, 64 HP, and 32 BT)"). Parsing that beats
# hardcoding a table: the numbers stay correct through errata, and they are not
# available in structured form -- the rune items carry no `hardness` field and
# no rule elements at all.
_REINFORCING_RE = re.compile(
    r"Hardness increases by (\d+).*?additional (\d+) Hit Points.*?"
    r"BT increases by (\d+).*?maximum (\d+) Hardness, (\d+) HP, and (\d+) BT",
    re.IGNORECASE | re.DOTALL,
)


def _reinforcing_bonus(entry: dict[str, Any]) -> dict[str, int] | None:
    text = re.sub(r"<[^>]+>", " ", entry.get("desc_html") or "")
    match = _REINFORCING_RE.search(re.sub(r"\s+", " ", text))
    if not match:
        return None
    hard, hp, bt, max_hard, max_hp, max_bt = (int(g) for g in match.groups())
    return {"hardness": hard, "hp": hp, "bt": bt,
            "max_hardness": max_hard, "max_hp": max_hp, "max_bt": max_bt}


def _shield_stats(character: dict[str, Any], lib: _Library) -> dict[str, Any] | None:
    """First item in the character's gear that resolves to a shield, with any
    etched runes applied.

    Shields live in different lists depending on which exporter produced the
    file -- Pathbuilder's own `armor` list, or the loose `equipment` list -- so
    both are searched.

    Hardness, HP and Broken Threshold are the item's own values plus permanent
    equipment upgrades: a stronger base shield resolves as its own entry
    (Sturdy Shield, Broadleaf Shield (Greater)), and a reinforcing rune
    recorded on the shield is applied here. Bonuses from spells, feats and
    other effects are deliberately *not* included -- a status bonus such as
    Emblazon Armament's +1 Hardness may not be active when the sheet is used,
    so printing it would overstate what the shield reliably blocks. Note that
    a shield recorded as a bare ``[name, quantity]`` pair has nowhere to carry
    runes; only a dict-shaped record can.
    """
    candidates: list[tuple[str, list[str]]] = []

    def runes_of(item: dict[str, Any]) -> list[str]:
        return [str(r) for r in (item.get("runes") or []) if r]

    for item in character.get("armor", []) or []:
        if isinstance(item, dict) and item.get("name"):
            candidates.append((item["name"], runes_of(item)))
    for item in character.get("equipment", []) or []:
        if isinstance(item, (list, tuple)) and item:
            candidates.append((str(item[0]), []))
        elif isinstance(item, dict) and item.get("name"):
            candidates.append((item["name"], runes_of(item)))

    for name, rune_names in candidates:
        entry = lib.get(name, "item")
        if not entry or entry["type"] != "shield":
            continue
        sysd = entry["system"]
        hardness = sysd.get("hardness") or 0
        hp = (sysd.get("hp") or {}).get("max") or 0
        bt = hp // 2
        applied: list[str] = []

        # A shield takes one reinforcing rune; if several are recorded, the
        # strongest wins rather than stacking.
        best, best_rune = None, None
        for rune_name in rune_names:
            rune = lib.get(rune_name, "item")
            bonus = _reinforcing_bonus(rune) if rune else None
            if bonus and (best is None or bonus["hardness"] > best["hardness"]):
                best, best_rune = bonus, rune["name"]
        if best:
            hardness = min(hardness + best["hardness"], best["max_hardness"])
            hp = min(hp + best["hp"], best["max_hp"])
            bt = min(bt + best["bt"], best["max_bt"])
            applied.append(best_rune)

        return {
            # The resolved item's name, not the one recorded on the character.
            # A player's annotation ("Wooden Shield (emblazoned)") would imply a
            # conditional effect is active in a block that deliberately reports
            # only the shield's permanent values. A qualifier that is genuinely
            # part of an item's name survives, because such an item resolves to
            # it exactly -- "Sturdy Shield (Minor)" stays as it is.
            "display_name": entry["name"],
            "ac_bonus": sysd.get("acBonus") or 0,
            "hardness": hardness,
            "hp": hp,
            "broken_threshold": bt,
            "runes": applied,
        }
    return None


def _strikes(character: dict[str, Any], abilities: dict[str, int], level: int,
             prof: dict[str, Any], lib: _Library,
             deity: dict[str, Any] | None = None) -> tuple[list[dict], list[str]]:
    """Attack bonus and damage for each carried weapon.

    The attack ability and the damage ability are decided separately, because
    the rules do not use the same one for both:

    * **Attack roll** -- Dexterity for any ranged attack, and a thrown attack
      *is* a ranged attack. Melee uses Strength, or the better of Strength and
      Dexterity for a finesse weapon.
    * **Damage** -- Strength for melee and thrown weapons; nothing for other
      ranged weapons, except `propulsive`, which adds half Strength (or the
      full modifier when it is negative).

    A weapon carrying a `thrown-N` trait but no range of its own is a melee
    weapon with a throwing option, so it produces two strikes: the melee one
    on Strength/finesse, and the throw on Dexterity. Conflating the two is how
    a Javelin ended up quoting a Strength-based attack bonus.
    """
    rows: list[dict] = []
    warnings: list[str] = []

    # Deadly Simplicity steps up the damage die of the deity's favored weapon,
    # permanently and whenever it's wielded, so it belongs in the printed
    # damage rather than in a footnote. Both halves are derivable now: the feat
    # from the character, the favored weapon from the deity's own entry.
    has_deadly_simplicity = m.has_feat(character, "Deadly Simplicity")
    favored = set((deity or {}).get("favored_weapons") or [])

    for weapon in character.get("weapons", []) or []:
        if not isinstance(weapon, dict) or not weapon.get("name"):
            continue
        entry = lib.get(weapon["name"], "item")
        traits = [str(t) for t in (entry["traits"] if entry else [])]
        has = lambda t: any(x.startswith(t) for x in traits)  # noqa: E731

        thrown = has("thrown")
        # A weapon with its own range entry is fundamentally ranged (Javelin,
        # Shortbow). One with only a `thrown-N` trait is a melee weapon that
        # can also be thrown (Dagger, Trident).
        native_range = (entry or {}).get("system", {}).get("range")
        ranged_weapon = bool(native_range)
        str_mod, dex_mod = m.ability_mod(abilities["str"]), m.ability_mod(abilities["dex"])

        if ranged_weapon:
            atk_mod = dex_mod
        elif has("finesse"):
            atk_mod = max(str_mod, dex_mod)
        else:
            atk_mod = str_mod

        category = weapon.get("prof") or "simple"
        rank = prof.get(category, 0) or 0
        potency = weapon.get("pot") or 0
        # Not m.total_bonus(), which folds in an ability modifier of its own --
        # which ability applies is decided above.
        bonus = (level + rank if rank else 0) + potency
        attack = atk_mod + bonus

        runes = [str(r).lower() for r in (weapon.get("runes") or [])]
        dice = max((_STRIKING_DICE[r] for r in runes if r in _STRIKING_DICE),
                   default=1)
        die = weapon.get("die") or "d4"
        base_die = ((entry or {}).get("system", {}).get("damage") or {}).get("die")
        slug = ((entry or {}).get("system", {}).get("slug")
                or weapon["name"].lower().replace(" ", "-"))
        stepped_by = ""
        # Only step a die the export left at the base item's value. If the two
        # already disagree the export has applied some effect of its own, and
        # stepping again would double-count it.
        if (has_deadly_simplicity and slug in favored
                and base_die is not None and die == base_die):
            unarmed = weapon.get("prof") == "unarmed" or has("unarmed")
            die = ("d6" if unarmed and _DIE_LADDER.index(die) < 1
                   else _step_die(die))
            stepped_by = "Deadly Simplicity"

        def damage_mod(is_thrown_or_melee: bool) -> int:
            if is_thrown_or_melee:
                return str_mod
            if has("propulsive"):
                # Half Strength when positive, the full modifier when negative.
                return str_mod // 2 if str_mod > 0 else str_mod
            return 0

        def damage_text(mod_value: int) -> str:
            return f"{dice}{die}" + (_mod(mod_value) if mod_value else "")

        dmg_mod = damage_mod(not ranged_weapon or thrown)
        damage = damage_text(dmg_mod)

        display = str(weapon.get("display") or "")
        # The export's `die` is authoritative (it carries die-size effects the
        # base item can't know about), but a display string that names a
        # different die means the two have drifted apart.
        # Compared against the die actually printed, so a step-up applied above
        # no longer reads as a discrepancy.
        conflict = _display_die_conflict(display, die, weapon["name"])
        if conflict:
            warnings.append(
                f"{weapon['name']}: printing {die} damage"
                + (f" ({stepped_by} applied to the base {base_die})"
                   if stepped_by else " from the character data")
                + f", but its note names {conflict}. Die-size effects other "
                f"than Deadly Simplicity are not applied automatically — "
                f"check this one."
            )

        row = {
            "name": weapon["name"],
            "qty": weapon.get("qty") or 1,
            "attack": attack,
            "damage": damage,
            "type": (weapon.get("damageType") or "").upper()[:1],
            "traits": traits,
            "agile": has("agile"),
            "display": display,
            "category": category,
            "rank": rank,
            "stepped_by": stepped_by,
        }
        rows.append(row)

        # Melee weapon with a throwing option: the throw is a separate strike
        # on Dexterity, so it gets its own row rather than being hidden behind
        # the melee number.
        if thrown and not ranged_weapon:
            distance = next(
                (t.split("-", 1)[1] for t in traits
                 if t.startswith("thrown-") and t.split("-", 1)[1].isdigit()),
                None,
            )
            label = (f"{weapon['name']} (thrown {distance} ft.)" if distance
                     else f"{weapon['name']} (thrown)")
            rows.append({
                **row,
                "name": label,
                "attack": dex_mod + bonus,
                "damage": damage_text(damage_mod(True)),
                # Finesse governs a melee attack; it has no bearing on a throw.
                "traits": [t for t in traits if t != "finesse"],
                "display": "",
            })
    return rows, warnings


def _skill_rows(character: dict[str, Any], abilities: dict[str, int],
                level: int) -> list[dict[str, Any]]:
    prof = character.get("proficiencies", {}) or {}
    rows = []
    for skill, key in sorted(_CORE_SKILLS.items()):
        rank = prof.get(skill, 0) or 0
        rows.append({
            "name": skill.title(),
            "key": _ABILITY_NAMES[key][1],
            "rank": rank,
            "total": m.total_bonus(abilities[key], rank, level),
        })
    for lore in character.get("lores", []) or []:
        if not (isinstance(lore, (list, tuple)) and lore):
            continue
        name = str(lore[0]).strip()
        rank = int(lore[1]) if len(lore) > 1 and lore[1] else 0
        label = name if name.lower().endswith("lore") else f"{name} Lore"
        rows.append({
            "name": re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label),
            "key": "INT", "rank": rank,
            "total": m.total_bonus(abilities["int"], rank, level),
        })
    return rows


def _spellcasting(character: dict[str, Any], level: int,
                  lib: _Library) -> list[dict[str, Any]]:
    """One block per spellcasting entry on the character, with each spell's
    full rules text resolved. Reads Pathbuilder's `spellCasters` shape, plus
    a synthesized block for its `focus` shape (best-effort: this project has
    no real populated export with focus spells to confirm the exact field
    names against, so any shape that doesn't match simply contributes no
    block rather than raising)."""
    blocks = []
    max_rank = min((level + 1) // 2, 10)
    for caster in character.get("spellCasters", []) or []:
        if not isinstance(caster, dict):
            continue
        per_day = caster.get("perDay") or []
        ranks = []
        for entry in caster.get("spells", []) or []:
            rank = int(entry.get("spellLevel", 0) or 0)
            names = [str(n) for n in (entry.get("list") or [])]
            resolved = []
            for name in names:
                found = lib.get(name, "spell")
                if found:
                    # A cantrip is always heightened to the caster's own
                    # highest rank, which is what rank-scaled formulas in its
                    # text should resolve against.
                    found = {**found,
                             "effective_rank": max_rank if rank == 0 else rank}
                    resolved.append(found)
            ranks.append({
                "rank": rank,
                "slots": per_day[rank] if rank < len(per_day) else None,
                "spells": resolved,
            })
        blocks.append({
            "name": caster.get("name") or "Spellcasting",
            "tradition": (caster.get("magicTradition") or "").title(),
            "kind": (caster.get("spellcastingType") or "").title(),
            "ability": (caster.get("ability") or "").lower(),
            "proficiency": caster.get("proficiency") or 0,
            "ranks": sorted(ranks, key=lambda r: r["rank"]),
            "focus_points": None,
        })

    focus = character.get("focus")
    if isinstance(focus, dict) and focus:
        # (name, at_will). Composition cantrips and other focus *cantrips*
        # cost no Focus Point -- "Composition cantrips are special composition
        # spells that don't cost Focus Points, so you can use them as often as
        # you like" -- so they must not be shown drawing on the pool.
        names: list[tuple[str, bool]] = []
        tradition = ""
        for trad, entry in focus.items():
            if not isinstance(entry, dict):
                continue
            tradition = tradition or str(trad)
            names.extend((str(n), True)
                         for n in (entry.get("focusCantrips") or []))
            names.extend((str(n), False)
                         for n in (entry.get("focusSpells") or []))
        resolved = []
        for name, at_will in names:
            found = lib.get(name, "spell")
            if found:
                resolved.append({**found, "effective_rank": max_rank,
                                 "at_will": at_will})
        if resolved:
            # Focus spells are cast using whichever tradition's casting
            # statistic granted them -- borrow ability/proficiency from a
            # matching spellCasters entry when one exists, since that's
            # almost always the class's own casting stat; otherwise fall
            # back to the character's key ability at trained.
            source = next((b for b in blocks
                            if b["tradition"].lower() == tradition.lower()), None)
            blocks.append({
                "name": "Focus Spells",
                "tradition": source["tradition"] if source else tradition.title(),
                "kind": "Focus",
                "ability": (source["ability"] if source
                            else str(character.get("keyability") or "").lower()),
                "proficiency": source["proficiency"] if source else 2,
                "ranks": [{"rank": max_rank, "slots": None, "spells": resolved}],
                "focus_points": character.get("focusPoints") or 0,
            })
    return blocks


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------

def _stylesheet(paper: str) -> str:
    faces = "".join(
        "@font-face{font-family:'%s';font-style:%s;font-weight:100 900;"
        "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2');}"
        % (family, style, b64)
        for family, style, b64 in FACES.values()
    )
    return faces + r"""
:root{
  --ink:#201d24; --ink-soft:#4a4550; --muted:#736c78; --faint:#9a939f;
  --paper:#fffefb; --rule:#c9c2b8; --hair:#e2ddd4;
  --accent:#4d3f6d; --accent-soft:#efecf5; --accent-line:#b3a9c9;
  --warm:#f7f4ee; --hp:#8c3a3a; --hp-soft:#f6ecec;
}
*{box-sizing:border-box;}
html{-webkit-text-size-adjust:100%;}
body{margin:0;background:#e8e6e1;color:var(--ink);
  font-family:'SheetSerif',Georgia,serif;font-size:10pt;line-height:1.42;
  font-variant-numeric:tabular-nums;}
.sheet{max-width:8.5in;margin:0 auto;padding:16px 8px 48px;}
.page{background:var(--paper);padding:0.34in 0.4in 0.4in;
  box-shadow:0 2px 14px rgba(0,0,0,.16);margin:0 auto 22px;min-height:10.2in;}
.page + .page{break-before:page;}
.lbl{font-family:'SheetSans',Helvetica,Arial,sans-serif;font-weight:650;
  font-size:5.9pt;letter-spacing:.085em;text-transform:uppercase;
  color:var(--muted);line-height:1.2;}
h1,h2,h3,h4{margin:0;font-weight:640;}
.xref{font-style:italic;}
.sec{display:flex;align-items:center;gap:9px;margin:0 0 9px;
  border-bottom:1.4px solid var(--accent);padding-bottom:4px;
  break-after:avoid;break-inside:avoid;}
.sec .spiral{color:var(--accent-line);flex:none;}
.sec h2{font-size:12.5pt;letter-spacing:.045em;text-transform:uppercase;
  color:var(--accent);font-weight:700;}
.sec .sec-note{margin-left:auto;font-family:'SheetSans',sans-serif;
  font-size:6.6pt;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);text-align:right;line-height:1.3;}
.sub{font-family:'SheetSans',sans-serif;font-weight:650;font-size:7.2pt;
  letter-spacing:.1em;text-transform:uppercase;color:var(--accent);
  margin:13px 0 6px;break-after:avoid;}
.sub:first-child{margin-top:0;}
.glyph{width:.72em;height:.72em;vertical-align:-.02em;margin-right:1.5px;
  color:var(--accent);}
.cost-txt{font-family:'SheetSans',sans-serif;font-size:6.6pt;font-weight:650;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-left:3px;}
.pips{display:inline-flex;gap:1.6px;vertical-align:middle;}
.pip{width:5.4px;height:5.4px;border:.8px solid var(--accent-line);
  display:inline-block;}
.pip.filled{background:var(--accent);border-color:var(--accent);}
.circles{display:inline-flex;gap:3px;flex-wrap:wrap;vertical-align:middle;}
.circ{width:8.5px;height:8.5px;border:1px solid var(--rule);border-radius:50%;
  display:inline-block;}
.circles.hp .circ{border-color:#c9a3a3;}
.traits{display:inline-flex;flex-wrap:wrap;gap:3px;vertical-align:middle;}
.trait{font-family:'SheetSans',sans-serif;font-size:5.9pt;font-weight:650;
  letter-spacing:.06em;text-transform:uppercase;border:.7px solid var(--accent-line);
  color:var(--accent);padding:1.2px 4px;
  border-radius:1px;white-space:nowrap;}
.trait.rarity{border-color:#c3ab84;color:#7a5c26;}

/* ---- page 1 ---- */
.nameplate{display:flex;align-items:flex-end;gap:12px;
  border-bottom:2.2px solid var(--ink);padding-bottom:5px;}
.nameplate .spiral{color:var(--accent);flex:none;margin-bottom:1px;}
/* Height-only sizing with automatic width: proportional scaling, which the
   Community Use Policy permits. No filter, opacity or blend mode is applied --
   altering a logo's colour or proportions is not allowed. */
/* Height is normally set inline, computed from the file's own ink bounds; this
   is the fallback for formats whose transparency can't be measured (SVG, JPEG).
   Height-only sizing keeps the artwork's proportions, which the Community Use
   Policy requires. */
.nameplate .logo{height:34px;width:auto;flex:none;max-width:34%;
  object-fit:contain;}
.nameplate h1{font-size:23pt;line-height:.98;font-weight:700;}
.nameplate .cls{margin-left:auto;text-align:right;font-size:11pt;
  font-weight:640;color:var(--accent);line-height:1.15;}
.nameplate .cls small{display:block;font-family:'SheetSans',sans-serif;
  font-size:6.4pt;font-weight:650;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);margin-top:2px;}
.identity{display:grid;grid-template-columns:repeat(6,1fr);
  border-bottom:1px solid var(--rule);}
.identity .f{padding:4px 7px 5px;border-right:1px solid var(--hair);}
.identity .f:last-child{border-right:0;}
.identity .v{font-size:8.6pt;font-weight:620;line-height:1.25;margin-top:1px;}
.identity .v.sm{font-size:7.4pt;font-weight:600;}
/* The left column only has to fit a skill name, its key attribute and a
   modifier, so it gives width to the middle and right columns, which carry
   the wordier blocks. */
.cols{display:grid;grid-template-columns:1.24fr 1.3fr 1.62fr;}
.col{padding:8px 9px 0;border-right:1px solid var(--hair);}
.col:first-child{padding-left:0;}
.col:last-child{border-right:0;padding-right:0;}
.block{margin-bottom:9px;}
.block-hd{font-family:'SheetSans',sans-serif;font-weight:700;font-size:6.6pt;
  letter-spacing:.11em;text-transform:uppercase;color:var(--ink);
  border-bottom:1.2px solid var(--ink);padding-bottom:2.5px;margin-bottom:5px;
  display:flex;align-items:baseline;gap:6px;}
.block-hd .hint{margin-left:auto;font-weight:650;font-size:5.7pt;
  letter-spacing:.055em;color:var(--faint);text-transform:uppercase;}
.abils{display:grid;grid-template-columns:1fr 1fr;gap:4px;}
.abil{border:1px solid var(--rule);padding:3.5px 7px 4px;background:var(--warm);}
.abil .hd{display:flex;justify-content:space-between;align-items:baseline;}
.abil .k{font-family:'SheetSans',sans-serif;font-weight:700;font-size:6.4pt;
  letter-spacing:.1em;color:var(--muted);}
.abil .s{font-size:6.4pt;color:var(--faint);font-weight:600;
  font-variant-numeric:tabular-nums;}
.abil .m{font-size:19.5pt;font-weight:700;line-height:1.05;color:var(--ink);
  text-align:center;margin-top:1px;}
table.sk{width:100%;border-collapse:collapse;}
/* The skills table is the tallest fixed block on page 1 and grows with every
   Lore the character has, so its row padding is the lever that keeps a
   many-Lore build on one sheet. */
table.sk td{padding:1.05px 0;border-bottom:.6px dotted var(--hair);
  vertical-align:middle;}
table.sk tr:last-child td{border-bottom:0;}
table.sk .n{font-size:8pt;font-weight:600;}
table.sk .n.untr{color:var(--muted);font-weight:400;}
table.sk .ka{font-family:'SheetSans',sans-serif;font-size:5.7pt;font-weight:650;
  color:var(--faint);letter-spacing:.05em;padding-left:4px;}
table.sk .p{text-align:right;padding-right:5px;white-space:nowrap;}
table.sk .t{text-align:right;font-size:9pt;font-weight:700;width:26px;}
table.sk .t.untr{font-weight:500;color:var(--muted);}
.sk-foot{font-size:6.4pt;color:var(--muted);margin-top:4px;line-height:1.35;}
.tile{border:1.4px solid var(--ink);display:flex;align-items:stretch;
  background:var(--warm);margin-bottom:6px;}
.tile .big{min-width:52px;padding:5px 4px 4px;text-align:center;
  border-right:1.4px solid var(--ink);display:flex;flex-direction:column;
  justify-content:center;}
.tile .big .n{font-size:21pt;font-weight:700;line-height:.95;}
.tile .big .k{font-family:'SheetSans',sans-serif;font-weight:700;font-size:5.9pt;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-top:1px;}
.tile .body{padding:4px 7px;flex:1;font-size:7.2pt;line-height:1.4;
  color:var(--ink-soft);}
.tile .body b{font-weight:650;color:var(--ink);}
/* The item is named above its own stat block, so a reader meets the thing
   before its numbers. */
.itemcap{font-size:7.5pt;font-weight:640;line-height:1.3;margin-bottom:2px;
  color:var(--ink);}
.itemcap .capmeta{font-weight:400;font-size:7pt;color:var(--muted);}
/* Deliberately its own class, not `.meta`: that name is the spell card's
   metadata strip, which carries a tinted background, padding and borders --
   inherited here it boxed the armour details and shrank their numbers to
   uppercase labels. */
.itemcap .capmeta b{font-family:'SheetSerif',Georgia,serif;font-size:7pt;
  font-weight:700;letter-spacing:0;text-transform:none;color:var(--ink-soft);}
.tile + .itemcap,.writerow + .itemcap{margin-top:6px;}

/* Labelled two-column stat grid inside a tile: the defence values that always
   exist, each pinned to its own label, instead of a sentence that wraps
   unpredictably in a narrow column. */
.tile .grid2{display:grid;grid-template-columns:1fr 1fr;column-gap:9px;
  padding:3.5px 7px;flex:1;align-content:center;}
.mstat{display:flex;align-items:baseline;gap:4px;padding:1.2px 0;
  border-bottom:.6px dotted var(--hair);}
.mstat .k{font-family:'SheetSans',sans-serif;font-size:5.3pt;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  white-space:nowrap;}
.mstat .v{font-size:8.2pt;font-weight:700;margin-left:auto;}

/* A labelled box to write a changing value into (shield HP mid-combat). */
.writerow{display:flex;align-items:stretch;border:1.4px solid var(--rule);
  margin-top:5px;}
.writerow .k{font-family:'SheetSans',sans-serif;font-size:5.5pt;font-weight:700;
  letter-spacing:.085em;text-transform:uppercase;color:var(--muted);
  padding:4px 7px;background:var(--warm);display:flex;align-items:center;
  border-right:1.4px solid var(--rule);white-space:nowrap;}
.writerow .box{flex:1;min-height:24px;background:var(--paper);}

/* Hit points as three equal boxes: the derived maximum printed, and two left
   open. The earlier ruled blanks were a few millimetres wide -- not enough to
   write a three-digit number into mid-session, which is the one number on the
   sheet that changes constantly. Labels sit under each box so the writing area
   stays clear. */
.hpboxes{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;}
.hpbox{border:1.4px solid var(--hp);background:var(--hp-soft);
  display:flex;flex-direction:column;align-items:center;
  padding:3px 3px 2.5px;min-height:44px;}
.hpbox .v{flex:1;display:flex;align-items:center;justify-content:center;
  font-size:17pt;font-weight:700;line-height:1;color:var(--hp);
  min-height:26px;}
.hpbox .k{font-family:'SheetSans',sans-serif;font-size:5.5pt;font-weight:700;
  letter-spacing:.085em;text-transform:uppercase;color:var(--muted);
  margin-top:1px;}
/* Left blank for pen: plain paper reads better under ink than a tint. */
.hpbox.write{background:var(--paper);}
.statrow{display:flex;align-items:center;gap:6px;padding:3.4px 0;
  border-bottom:.6px dotted var(--hair);}
.statrow:last-of-type{border-bottom:0;}
.statrow .nm{font-size:8.2pt;font-weight:620;min-width:60px;}
.statrow .tot{font-size:11pt;font-weight:700;min-width:30px;text-align:right;
  margin-left:auto;}
.statrow .rk{font-family:'SheetSans',sans-serif;font-size:5.8pt;font-weight:700;
  color:var(--muted);letter-spacing:.06em;min-width:14px;text-align:center;}
.note{font-size:6.5pt;color:var(--muted);line-height:1.38;margin-top:3px;}
.note b{color:var(--ink-soft);font-weight:650;}
table.str{width:100%;border-collapse:collapse;}
table.str th{font-family:'SheetSans',sans-serif;font-size:5.7pt;font-weight:700;
  letter-spacing:.075em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 3px 2.5px 0;border-bottom:.9px solid var(--rule);}
table.str th.r,table.str td.r{text-align:right;padding-right:3px;}
table.str td{padding:3px 3px 3px 0;border-bottom:.6px dotted var(--hair);
  font-size:7.8pt;}
table.str .wn{font-weight:640;}
table.str .bn{font-weight:700;font-size:9pt;}
table.str .tr{font-size:6.2pt;color:var(--muted);line-height:1.35;}
.track{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:2px;}
.track .grp{display:flex;align-items:center;gap:4px;}
.track .grp .lbl{font-size:5.8pt;}
.conds{display:grid;grid-template-columns:1fr;}
.cond{display:flex;align-items:baseline;gap:5px;padding:2.1px 0;
  border-bottom:.6px dotted var(--hair);}
.cond:last-child{border-bottom:0;}
.cond .box{width:7.5px;height:7.5px;border:.9px solid var(--rule);flex:none;
  display:inline-block;position:relative;top:-1px;}
.cond .cn{font-size:7.2pt;font-weight:640;white-space:nowrap;}
.cond .ce{font-size:6.1pt;color:var(--muted);line-height:1.3;margin-left:auto;
  text-align:right;padding-left:6px;}

/* Full-width region below the three columns. Feats and class features are the
   one part of page 1 that grows without limit as a character levels -- a
   20th-level build carries dozens -- so they get the entire page width and
   whatever vertical space the columns leave over, flowed into three text
   columns rather than trapped in one narrow column where they were truncating. */
.growth{border-top:1.6px solid var(--ink);margin-top:8px;padding-top:6px;}
.growth .flow{column-count:3;column-gap:16px;
  column-rule:.7px solid var(--hair);}
.growth .fcat{break-inside:avoid;font-size:7.3pt;line-height:1.45;
  margin-bottom:3.5px;}
.growth .fcat b{font-family:'SheetSans',sans-serif;font-size:6pt;
  font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--accent);display:block;}
.growth .sp{break-inside:avoid;font-size:7.2pt;line-height:1.4;
  margin-bottom:2.6px;padding-left:8px;position:relative;color:var(--ink-soft);}
.growth .sp:before{content:"";position:absolute;left:0;top:.42em;width:3px;
  height:3px;background:var(--accent-line);}
.growth .more{font-size:6.6pt;color:var(--faint);margin-top:2px;}
.growth .subhd{font-family:'SheetSans',sans-serif;font-size:6pt;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  margin:5px 0 3px;border-top:.7px dotted var(--rule);padding-top:4px;}
/* Deliberately NOT inside a `column-count` container. Chrome's print engine
   treats a multi-column box as unfragmentable, so a two-line run would jump
   whole to the next sheet rather than filling the space left at the foot of
   page 1 -- which is exactly how this block came to be "cut off". A plain
   full-width paragraph breaks normally and uses whatever room is there. */
.growth .sprun{font-size:7.2pt;line-height:1.5;color:var(--ink-soft);
  text-align:left;}
.growth .sprun .more{color:var(--faint);}

/* ---- rules cards ---- */
.card{break-inside:avoid;page-break-inside:avoid;border:1px solid var(--rule);
  border-left:2.6px solid var(--accent);padding:6px 9px 7px;margin-bottom:7px;}
.card.plain{border-left-color:var(--rule);}
.card.tight{padding:5px 9px 6px;}
.card-hd{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;
  margin-bottom:2px;}
.card-hd h3{font-size:10.6pt;font-weight:700;}
.card-hd .rank{font-family:'SheetSans',sans-serif;font-size:6.4pt;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--accent);border:.8px solid var(--accent-line);
  padding:1.3px 4.5px;}
.card-hd .cost{margin-left:auto;white-space:nowrap;display:flex;
  align-items:center;}
.card-traits{margin:3px 0 4px;}
.meta{font-size:7.3pt;color:var(--ink-soft);line-height:1.5;margin:0 0 4px;
  padding:3.5px 6px;border-top:.7px solid var(--hair);
  border-bottom:.7px solid var(--hair);}
.meta b{font-family:'SheetSans',sans-serif;font-size:6.1pt;font-weight:700;
  letter-spacing:.07em;text-transform:uppercase;color:var(--muted);}
.meta .sep{color:var(--faint);margin:0 5px;}
.rules{font-size:8.4pt;line-height:1.47;}
.rules p{margin:0 0 4.5px;}
.rules p:last-child{margin-bottom:0;}
.rules ul,.rules ol{margin:3px 0 5px;padding-left:16px;}
.rules li{margin-bottom:2px;}
.rules hr{border:0;border-top:.8px solid var(--hair);margin:5px 0;}
.rules strong{font-weight:700;}
.rules h1,.rules h2,.rules h3,.rules h4,.rules h5{
  font-family:'SheetSans',sans-serif;font-size:6.8pt;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;color:var(--accent);
  margin:7px 0 3px;break-after:avoid;}
.rules table{border-collapse:collapse;margin:4px 0 2px;}
.rules table th{font-family:'SheetSans',sans-serif;font-size:5.9pt;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);text-align:left;padding:0 12px 2.5px 0;
  border-bottom:.9px solid var(--rule);}
.rules table td{padding:2.5px 12px 2.5px 0;font-size:8pt;}
/* Deity block: the one card with its own stat table, since a deity's mechanics
   are label/value pairs rather than prose. */
.card.deity{border-left-width:3.4px;}
/* Symbol beside the stat table. Height-only sizing keeps the artwork's own
   proportions, which the Community Use Policy requires. */
.deitybody{display:flex;align-items:flex-start;gap:12px;}
.deitybody .symbol{height:104px;width:auto;flex:none;margin:4px 0 4px 2px;
  object-fit:contain;}
.deitybody table.deityfacts{flex:1;min-width:0;}
table.deityfacts{width:100%;border-collapse:collapse;margin:4px 0 6px;}
table.deityfacts td{padding:2.6px 0;border-bottom:.6px dotted var(--hair);
  vertical-align:baseline;font-size:8.2pt;}
table.deityfacts tr:last-child td{border-bottom:0;}
table.deityfacts td.dl{font-family:'SheetSans',sans-serif;font-size:6.2pt;
  font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);white-space:nowrap;width:122px;padding-right:10px;}
table.deityfacts td.dv{font-weight:600;line-height:1.4;}
table.deityfacts .fill{display:block;border-bottom:.7px solid var(--rule);
  height:.95em;}
.chosen{margin-top:5px;padding:4px 7px;
  border-left:2px solid var(--accent-line);font-size:7.5pt;line-height:1.45;
  color:var(--ink-soft);}
.chosen b{font-family:'SheetSans',sans-serif;font-size:6.1pt;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--accent);}
.src{font-family:'SheetSans',sans-serif;font-size:5.7pt;letter-spacing:.05em;
  color:var(--faint);text-transform:uppercase;margin-top:4px;}
table.grid{width:100%;border-collapse:collapse;}
table.grid th{font-family:'SheetSans',sans-serif;font-size:5.9pt;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 6px 3px 0;border-bottom:1px solid var(--rule);}
table.grid td{padding:3.6px 6px 3.6px 0;border-bottom:.6px dotted var(--hair);
  font-size:8pt;vertical-align:top;}
table.grid td.r,table.grid th.r{text-align:right;padding-right:11px;}
table.grid td.last,table.grid th.last{padding-right:0;}
table.grid .nm{font-weight:640;}
table.grid .dsc{font-size:7.2pt;color:var(--muted);line-height:1.4;}
.preptable{margin-bottom:12px;}
.preptable td.r,.preptable th.r{vertical-align:middle;}
/* Spontaneous ranks and focus pools: bubbles live once on a group header
   row, and the spells they can be spent on are listed beneath it. */
.preptable tr.grp td{background:var(--zebra,rgba(0,0,0,.035));
  border-top:.7px solid var(--hair);}
.preptable tr.grp td.nm{font-weight:700;font-size:7.6pt;
  letter-spacing:.04em;text-transform:uppercase;color:var(--ink-soft);}
.preptable tr.sub td{border-top:none;}
.preptable tr.sub td.nm{padding-left:15px;position:relative;}
.preptable tr.sub td.nm::before{content:"·";position:absolute;left:6px;
  color:var(--faint);}
.plainlist{font-size:8.4pt;line-height:1.5;margin:0;padding-left:16px;}
.plainlist li{margin-bottom:3px;}
.pagefoot{margin-top:8px;display:flex;justify-content:space-between;
  align-items:baseline;font-family:'SheetSans',sans-serif;font-size:5.7pt;
  letter-spacing:.07em;text-transform:uppercase;color:var(--faint);
  border-top:.7px solid var(--hair);padding-top:3px;break-inside:avoid;}
.legal{margin-top:0;font-size:7.2pt;line-height:1.5;color:var(--muted);}
.legal h4{font-family:'SheetSans',sans-serif;font-size:6.8pt;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;color:var(--ink-soft);
  margin:9px 0 4px;}
.legal p{margin:0 0 5px;}
.legal .ogl{font-size:5.2pt;line-height:1.42;column-count:2;column-gap:14px;
  margin-top:5px;text-align:justify;}
.legal .ogl p{margin:0 0 3px;}
.warn{border-left:2.6px solid var(--hp);border-top:.7px solid var(--hair);
  border-bottom:.7px solid var(--hair);
  padding:5px 9px;margin-bottom:7px;font-size:8pt;line-height:1.45;}
.toolbar{max-width:8.5in;margin:0 auto;padding:10px 8px 0;display:flex;gap:8px;
  align-items:center;font-family:'SheetSans',sans-serif;font-size:8pt;
  color:#4a4550;flex-wrap:wrap;}
.toolbar button{font:inherit;font-weight:650;padding:5px 11px;
  border:1px solid #b9b3ab;background:#fffefb;color:#201d24;cursor:pointer;
  border-radius:2px;}
.toolbar button:hover{background:#f2efe9;}
.toolbar .tip{color:#6c6660;}
@media print{
  body{background:#fff;}
  .toolbar{display:none !important;}
  .sheet{max-width:none;margin:0;padding:0;}
  .page{box-shadow:none;margin:0;padding:0 0 0.2in;min-height:0;background:#fff;}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
}
@page{size:__PAPER__;margin:0.42in 0.45in 0.4in;}
""".replace("__PAPER__", _PAPER.get(paper, _PAPER["letter"]))


# --------------------------------------------------------------------------
# Section builders
# --------------------------------------------------------------------------

def _section(title: str, note: str = "") -> str:
    tail = f'<div class="sec-note">{note}</div>' if note else ""
    return f'<div class="sec">{_spiral(22)}<h2>{_esc(title)}</h2>{tail}</div>'


def _foot(name: str, label: str) -> str:
    return (f'<div class="pagefoot"><span>{_esc(name)}</span>'
            f'<span>{_esc(label)}</span></div>')


def _card(entry: dict[str, Any], kicker: str = "", chosen: str = "",
          cost: Any = None, plain: bool = False,
          spell_rank: int | None = None, meta: str = "") -> str:
    system = entry.get("system", {})
    if cost is None:
        actions = (system.get("actions") or {}).get("value")
        atype = (system.get("actionType") or {}).get("value") or ""
        cost = str(actions) if actions else (atype if atype in ("reaction", "free") else None)
    cost_html = f'<span class="cost">{_cost(cost)}</span>' if cost else ""
    kick = f'<span class="rank">{_esc(kicker)}</span>' if kicker else ""
    chosen_html = (f'<div class="chosen"><b>As chosen</b> &nbsp;{chosen}</div>'
                   if chosen else "")
    return f"""
<article class="card{' plain' if plain else ''}">
  <div class="card-hd"><h3>{_esc(entry['name'])}</h3>{kick}{cost_html}</div>
  <div class="card-traits">{_traits(entry.get('traits'), entry.get('rarity'))}</div>
  {meta}
  <div class="rules">{_rules_html(entry.get('desc_html', ''), spell_rank)}</div>
  {chosen_html}
  <div class="src">{_esc(entry.get('source_book') or '')}</div>
</article>"""


def _spell_meta(entry: dict[str, Any], dc: int | None) -> str:
    system = entry.get("system", {})
    bits = []

    def add(label: str, value: str) -> None:
        if value:
            bits.append(f"<b>{label}</b> {value}")

    add("Range", _esc((system.get("range") or {}).get("value")))
    area = system.get("area")
    if area:
        add("Area", f'{_esc(area.get("value"))}-foot {_esc(area.get("type"))}')
    add("Targets", _esc((system.get("target") or {}).get("value")))
    add("Duration", _esc((system.get("duration") or {}).get("value")))
    save = ((system.get("defense") or {}).get("save") or {})
    if save.get("statistic"):
        basic = "basic " if save.get("basic") else ""
        vs = f" vs. DC {dc}" if dc is not None else ""
        add("Defense", f'{basic}{_esc(save["statistic"]).title()} save{vs}')
    return f'<div class="meta">{"<span class=\'sep\'>|</span>".join(bits)}</div>' if bits else ""


def _page_core(ctx: dict[str, Any]) -> str:
    ch, d = ctx["character"], ctx["derived"]
    abilities, level = ctx["abilities"], ctx["level"]
    prof = ch.get("proficiencies", {}) or {}
    # Deliberately not called `name`: this function's return is one long
    # f-string evaluated at the end, so any loop variable named `name` in
    # between would silently replace the character's name in the nameplate.
    # That is exactly what happened -- four sheets printed a feat's name as
    # the character's -- so the binding is kept distinct.
    shield, character_name = ctx["shield"], ctx["name"]

    # Score and modifier are printed together, but the modifier is what's
    # actually rolled with -- it leads at nearly triple the size, the raw
    # score demoted to a quiet corner note next to the ability's label.
    abil_html = "".join(
        f'<div class="abil"><div class="hd"><span class="k">{_ABILITY_NAMES[k][1]}</span>'
        f'<span class="s">{abilities[k]}</span></div>'
        f'<div class="m">{_mod(m.ability_mod(abilities[k]))}</div></div>'
        for k in _ABILITY_KEYS
    )

    sk_html = "".join(
        f'<tr><td class="n{"" if r["rank"] else " untr"}">{_esc(r["name"])}'
        f'<span class="ka">{r["key"]}</span></td>'
        f'<td class="p">{_pips(r["rank"])}</td>'
        f'<td class="t{"" if r["rank"] else " untr"}">{_mod(r["total"])}</td></tr>'
        for r in ctx["skills"]
    )

    save_html = "".join(
        f'<div class="statrow"><span class="nm">{label}</span>'
        f'{_pips(prof.get(key, 0) or 0)}'
        f'<span class="rk">{_RANK_ABBR.get(prof.get(key, 0) or 0, "?")}</span>'
        f'<span class="tot">{_mod(d["saves"][key])}</span></div>'
        for key, label in (("fortitude", "Fortitude"), ("reflex", "Reflex"),
                           ("will", "Will"))
    )

    strike_html = ""
    for s in ctx["strikes"]:
        # `display` is a free-text field players use for anything from "d6 via
        # Deadly Simplicity" to several sentences of build rationale. Short
        # ones are usually the mechanical note worth printing; long ones would
        # swamp the column, and a die mismatch is reported as a warning anyway.
        note = s["display"] if len(s["display"]) <= 80 else ""
        extra = f'<div class="tr">{_esc(note)}</div>' if note else ""
        strike_html += (
            f'<tr><td><div class="wn">{_esc(s["name"])}'
            f'{f" &times;{s['qty']}" if s["qty"] > 1 else ""}</div>'
            f'<div class="tr">{_esc(", ".join(t.replace("-", " ") for t in s["traits"]))}</div>'
            f'{extra}</td>'
            f'<td class="r bn">{_mod(s["attack"])}</td>'
            f'<td class="r">{_esc(s["damage"])}<div class="tr">{_esc(s["type"])}</div></td></tr>'
        )
    if not strike_html:
        strike_html = ('<tr><td colspan="3" class="tr">No weapons recorded on '
                       'this character.</td></tr>')

    def mstat(label: str, value: Any) -> str:
        return (f'<div class="mstat"><span class="k">{label}</span>'
                f'<span class="v">{_esc(value)}</span></div>')

    # The armour statistics that always exist, as a labelled grid rather than a
    # sentence: in a column this narrow the prose wrapped mid-value ("Dex cap
    # / +2"), which is exactly where a reader must not have to hunt.
    armor = ctx["armor"]
    if armor:
        armor_rank = prof.get(armor["category"], 0) or 0
        ac_cells = (mstat("Item", _mod(armor["ac_bonus"]))
                    + mstat("Dex cap", _mod(armor["dex_cap"]))
                    + mstat("Check", armor["check_penalty"] or 0)
                    + mstat("Speed", armor["speed_penalty"] or 0))
        ac_cap = (f'<b>{_esc(armor["display_name"])}</b>'
                  f'<span class="capmeta"> &middot; '
                  f'{_RANK_NAME.get(armor_rank, "?")} '
                  f'({_mod(level + armor_rank if armor_rank else 0)})')
    else:
        unarmored_rank = prof.get("unarmored", 0) or 0
        ac_cells = (mstat("Item", "—") + mstat("Dex cap", "—")
                    + mstat("Check", 0) + mstat("Speed", 0))
        ac_cap = (f'<b>Unarmored</b><span class="capmeta"> &middot; '
                  f'{_RANK_NAME.get(unarmored_rank, "?")} '
                  f'({_mod(level + unarmored_rank if unarmored_rank else 0)})')
    if shield:
        ac_cap += f' &middot; <b>{d["ac"] + shield["ac_bonus"]}</b> raised'
    ac_cap += "</span>"

    shield_tile = ""
    if shield:
        # Shield HP is the one defensive number that changes during a fight --
        # Shield Block spends it -- so it gets a box to write in, alongside the
        # fixed values it has to be compared against.
        runes = (f'<span class="capmeta"> &middot; '
                 f'{_esc(", ".join(shield["runes"]))}</span>'
                 if shield.get("runes") else "")
        shield_tile = f"""
        <div class="itemcap"><b>{_esc(shield['display_name'])}</b>{runes}</div>
        <div class="tile">
          <div class="big"><div class="n">{shield['hardness']}</div>
            <div class="k">Hard&shy;ness</div></div>
          <div class="grid2">
            {mstat("AC raised", _mod(shield['ac_bonus']))}
            {mstat("Max HP", shield['hp'])}
            {mstat("Broken at", shield['broken_threshold'])}
            {mstat("Destroyed", 0)}
          </div>
        </div>
        <div class="writerow">
          <span class="k">Shield HP now</span><span class="box"></span>
        </div>"""

    # A character can carry several spellcasting entries (an animist's
    # apparitions, a dual-class build). Only the first gets a full tile;
    # the rest are compact rows, so this block's height stays bounded and
    # page 1 stays a single page.
    cast_tile = ""
    for i, block in enumerate(ctx["spellcasting"]):
        key = abilities.get(block["ability"], 10)
        rank = block["proficiency"] or 0
        dc = 10 + m.total_bonus(key, rank, level)
        attack = m.total_bonus(key, rank, level)
        if i == 0:
            slot_line = " &middot; ".join(
                f'Rank {r["rank"]}: <b>{r["slots"]}</b>' if r["rank"] else
                f'Cantrips <b>{len(r["spells"])}</b>'
                for r in block["ranks"] if r["slots"] or r["rank"] == 0
            )
            cast_tile += f"""
        <div class="tile">
          <div class="big"><div class="n">{dc}</div><div class="k">Spell DC</div></div>
          <div class="body">Spell attack <b>{_mod(attack)}</b> &middot;
            {_RANK_ABBR.get(rank, '?')}<br>
            {_esc(block['tradition'])} {_esc(block['kind'])} &middot;
            {_esc(block['ability']).upper()}<br>{slot_line}</div>
        </div>"""
        else:
            cast_tile += (
                f'<div class="statrow"><span class="nm" style="min-width:0">'
                f'{_esc(block["name"])}</span>'
                f'<span class="rk">{_esc(block["ability"]).upper()}</span>'
                f'<span class="tot">{dc}</span></div>'
            )

    conds = "".join(
        f'<div class="cond"><span class="box"></span>'
        f'<span class="cn">{_esc(n)}</span><span class="ce">{_esc(e)}</span></div>'
        for n, e in _CONDITIONS
    )

    # Feats and features live in a full-width three-column region, so the
    # budget here is generous: a 20th-level character's whole feat list fits.
    # It exists only so a pathological build can't push page 1 onto a second
    # sheet; everything is reproduced complete on the Features page anyway.
    by_cat: dict[str, list[str]] = {}
    for feat in ch.get("feats", []) or []:
        if not (isinstance(feat, (list, tuple)) and feat):
            continue
        by_cat.setdefault(str(feat[2]) if len(feat) > 2 and feat[2] else "Feat",
                          []).append(str(feat[0]))

    feat_html, budget, dropped_cats = "", 1500, 0
    for cat, names in by_cat.items():
        line = ", ".join(names)
        if budget <= 0:
            dropped_cats += 1
            continue
        if len(line) > budget:
            kept: list[str] = []
            for feat_name in names:
                if len(", ".join(kept + [feat_name])) > max(budget, 60):
                    break
                kept.append(feat_name)
            extra = len(names) - len(kept)
            line = ", ".join(kept) + (f" +{extra} more" if extra else "")
        budget -= len(line)
        feat_html += (f'<div class="fcat"><b>{_esc(cat)}</b>'
                      f'{_esc(line)}</div>')
    if dropped_cats:
        feat_html += (f'<div class="fcat more">and {dropped_cats} more '
                      f'categories &mdash; all feats in full on the Features '
                      f'page</div>')

    # Other features are a run-in list rather than one row per item: a
    # 20th-level character can carry thirty of them, and at one row each they
    # pushed page 1 onto a second sheet. Each is reduced to its lead clause --
    # the character data writes these as whole sentences -- and the run is
    # budgeted by total length so the block's height stays bounded whatever the
    # build. Every one appears verbatim on the Features page.
    # Feats take precedence over other features for the space at the foot of
    # page 1: they are what a player reads mid-session, and they grow steadily
    # with level. Each feat category is an unbreakable block with its own
    # heading, so category count drives the flow's depth -- seven or more stack
    # three rows deep across the three columns and leave nothing behind them.
    # At that point the other-features run stands down entirely rather than
    # half-render or orphan its heading; the Features page carries all of it.
    feat_rows = -(-len(by_cat) // 3) if by_cat else 0
    specials = ([s for s in (ch.get("specials", []) or []) if str(s).strip()]
                if feat_rows < 3 else [])
    kept_specials: list[str] = []
    remaining = max(150, 470 - 40 * max(0, len(by_cat) - 5))
    # Reserve room for the "+N more" pointer up front. Adding it after the
    # budget was spent is what pushed its last three words onto a second sheet.
    if len(specials) > 3:
        remaining -= 30
    for s in specials:
        clipped = _clip(str(s), 42)
        if len(clipped) + 3 > remaining:
            break
        remaining -= len(clipped) + 3
        kept_specials.append(clipped)
    special_html = ""
    if kept_specials:
        run = " &middot; ".join(_esc(s) for s in kept_specials)
        omitted = len(specials) - len(kept_specials)
        tail = (f' <span class="more">&middot; +{omitted} more on the Features '
                f'page</span>' if omitted else "")
        special_html = f'<div class="sprun">{run}{tail}</div>'

    return f"""
<section class="page">
  <div class="nameplate">{ctx['logo_html'] or _spiral(30)}
    <h1>{_esc(character_name)}</h1>
    <div class="cls">{_esc(ch.get('class') or '')} {level}
      <small>{_esc(ctx['subclass_label'])}</small></div>
  </div>
  <div class="identity">
    <div class="f"><div class="lbl">Ancestry &amp; Heritage</div>
      <div class="v">{_esc(ch.get('ancestry') or '—')}
        <span style="color:var(--muted)">/</span>
        {_esc(ch.get('heritage') or '—')}</div></div>
    <div class="f"><div class="lbl">Background</div>
      <div class="v">{_esc(ch.get('background') or '—')}</div></div>
    <div class="f"><div class="lbl">Deity</div>
      <div class="v">{_esc(ch.get('deity') or '—')}</div></div>
    <div class="f"><div class="lbl">Size / Speed</div>
      <div class="v">{_esc(ch.get('sizeName') or ctx['size'])} &middot;
        {ctx['speed']} ft.</div></div>
    <div class="f"><div class="lbl">Languages</div>
      <div class="v sm">{_esc(", ".join(ch.get('languages') or []) or '—')}</div></div>
    <div class="f"><div class="lbl">Hero Points</div>
      <div class="v">{_circles(3)}</div></div>
  </div>

  <div class="cols">
    <div class="col">
      <div class="block"><div class="block-hd">Attributes</div>
        <div class="abils">{abil_html}</div></div>
      <div class="block">
        <div class="block-hd">Skills <span class="hint">T &middot; E &middot; M &middot; L</span></div>
        <table class="sk">{sk_html}</table>
        {f'<div class="sk-foot">{ctx["armor_note"]}</div>' if ctx["armor_note"] else ""}
      </div>
    </div>

    <div class="col">
      <div class="block"><div class="block-hd">Defense</div>
        <div class="itemcap">{ac_cap}</div>
        <div class="tile">
          <div class="big"><div class="n">{d['ac']}</div><div class="k">AC</div></div>
          <div class="grid2">{ac_cells}</div>
        </div>{shield_tile}
      </div>
      <div class="block"><div class="block-hd">Saving Throws</div>{save_html}</div>
      <div class="block"><div class="block-hd">Hit Points</div>
        <div class="hpboxes">
          <div class="hpbox"><div class="v">{d['hp']}</div>
            <div class="k">Full</div></div>
          <div class="hpbox write"><div class="v"></div>
            <div class="k">Current</div></div>
          <div class="hpbox write"><div class="v"></div>
            <div class="k">Temporary</div></div>
        </div>
        <div class="track">
          <div class="grp"><span class="lbl">Dying</span>{_circles(4, "hp")}</div>
          <div class="grp"><span class="lbl">Wounded</span>{_circles(3, "hp")}</div>
        </div>
        <div class="note">Dying 4 = dead.</div>
      </div>
      <div class="block"><div class="block-hd">Conditions
        <span class="hint">tick &middot; write N</span></div>
        <div class="conds">{conds}</div>
      </div>
    </div>

    <div class="col">
      <div class="block"><div class="block-hd">Perception &amp; Senses</div>
        <div class="statrow"><span class="nm">Perception</span>
          {_pips(prof.get('perception', 0) or 0)}
          <span class="rk">{_RANK_ABBR.get(prof.get('perception', 0) or 0, '?')}</span>
          <span class="tot">{_mod(d['perception'])}</span></div>
        {f'<div class="note"><b>Senses</b> {_esc(ctx["vision"])}</div>' if ctx["vision"] else ""}
      </div>
      <div class="block"><div class="block-hd">Strikes</div>
        <table class="str">
          <tr><th>Weapon</th><th class="r">Atk</th><th class="r">Damage</th></tr>
          {strike_html}
        </table>
        <div class="note">Multiple attack penalty &minus;5 / &minus;10, or
          &minus;4 / &minus;8 with an agile weapon.</div>
      </div>
      {f'<div class="block"><div class="block-hd">Spellcasting</div>{cast_tile}</div>' if cast_tile else ""}
      <div class="block">
        <div class="statrow"><span class="nm">Class DC</span>
          {_pips(prof.get('classDC', 0) or 0)}
          <span class="rk">{_RANK_ABBR.get(prof.get('classDC', 0) or 0, '?')}</span>
          <span class="tot">{d['class_dc']}</span></div>
      </div>
    </div>
  </div>

  {f'''<div class="growth">
    <div class="block-hd">Feats &amp; Features
      <span class="hint">full text on the Features page</span></div>
    {f'<div class="flow">{feat_html}</div>' if feat_html else ""}
    {f'<div class="subhd">Other features</div>{special_html}'
     if special_html else ""}
  </div>''' if (feat_html or special_html) else ""}
</section>"""
# No footer on page 1 deliberately. Its content is packed to the margins, and a
# 6pt footer -- an unbreakable box -- was enough to spill a dense build onto a
# second sheet even when everything above it fit. The nameplate at the top of
# the page already identifies the character and the section.


def _spell_action(spell: dict[str, Any]) -> str:
    return _cost((spell["system"].get("time") or {}).get("value"))


def _prep_table(blocks: list[dict[str, Any]]) -> str:
    """One consolidated slot-usage table spanning every spellcasting source
    on the character -- a class list, its font, focus spells, an
    archetype's repertoire, whatever's present -- rather than a separate
    table per source, so a player checks one place instead of flipping
    between pages. One row per distinct spell occupying a rank's slots,
    tagged with its source and action cost, and a hollow bubble per slot it
    fills -- a spell prepared into more than one slot (a Font with only
    Heal, prepared 4 times) reads as one row with several bubbles rather
    than a repeated stat block later on the page.

    Every rank (and the focus pool) opens with a **group header row**, then
    lists its spells beneath it -- one consistent shape down the page. What
    differs is *where the bubbles sit*, because that's what encodes how the
    resource actually works:

    - **Spontaneous / innate:** slots aren't tied to named spells. 2 rank-1
      slots against a 3-spell repertoire means *any two castings from those
      three*, not two of each. Bubbles go **on the group header**, once;
      the spell rows beneath carry none. Putting them per-spell would show
      6 uses where the character has 2.
    - **Focus:** all focus spells draw on one shared point pool, so the
      bubbles likewise sit on the header, once.
    - **Prepared:** a slot really is locked to a named spell, so bubbles
      stay **on the spell rows**. The header carries no bubbles -- just the
      rank's slot count as text, which makes an under-prepared rank visible
      (header says 3 slots, only 2 bubbles below).

    Focus *cantrips* (composition cantrips and the like) cost no Focus
    Point at all, so they're split out of the pool group and shown at will.
    """
    rows = []
    for block in blocks:
        source = _esc(block["name"])
        kind = block["kind"].lower()
        for r in block["ranks"]:
            if not r["spells"]:
                continue
            if kind == "focus":
                pool = block.get("focus_points") or 0
                at_will = sorted({s["name"] for s in r["spells"]
                                  if s.get("at_will")})
                pooled = sorted({s["name"] for s in r["spells"]
                                 if not s.get("at_will")})
                by_name = {s["name"]: s for s in r["spells"]}
                if pooled:
                    lbl = ("Focus pool — any combination below"
                           if len(pooled) > 1 else "Focus pool")
                    rows.append(
                        f'<tr class="grp"><td>{source}</td><td>Focus</td>'
                        f'<td class="nm">{lbl}</td><td></td>'
                        f'<td class="r last">{_circles(pool)}</td></tr>')
                    for name in pooled:
                        rows.append(
                            f'<tr class="sub"><td></td><td></td>'
                            f'<td class="nm">{_esc(name)}</td>'
                            f'<td>{_spell_action(by_name[name])}</td>'
                            f'<td class="r last"></td></tr>')
                for name in at_will:
                    rows.append(
                        f'<tr><td>{source}</td><td>Focus</td>'
                        f'<td class="nm">{_esc(name)}</td>'
                        f'<td>{_spell_action(by_name[name])}</td>'
                        f'<td class="r last">at will</td></tr>')
                continue
            if r["rank"] == 0:
                for name in sorted({s["name"] for s in r["spells"]}):
                    spell = next(s for s in r["spells"] if s["name"] == name)
                    heightened = spell.get("effective_rank", r["rank"])
                    rows.append(
                        f'<tr><td>{source}</td><td>Cantrip</td>'
                        f'<td class="nm">{_esc(name)}</td>'
                        f'<td>{_spell_action(spell)}</td>'
                        f'<td class="r last">at will<br>'
                        f'<span class="dsc">(rank {heightened})</span></td></tr>')
                continue
            counts: dict[str, int] = {}
            by_name: dict[str, dict[str, Any]] = {}
            for spell in r["spells"]:
                counts[spell["name"]] = counts.get(spell["name"], 0) + 1
                by_name.setdefault(spell["name"], spell)
            if kind == "prepared":
                # Prepared ranks get the same group header as every other
                # source, for a consistent read down the page -- but the
                # bubbles stay on the spell rows, because here a slot really
                # is locked to a named spell. The header instead states the
                # rank's slot count as text, which makes an under-prepared
                # rank visible (3 slots, only 2 bubbles below).
                n = r["slots"] or sum(counts.values())
                rows.append(
                    f'<tr class="grp"><td>{source}</td><td>{r["rank"]}</td>'
                    f'<td class="nm">Prepared</td><td></td>'
                    f'<td class="r last"><span class="dsc">{n} '
                    f'slot{"" if n == 1 else "s"}</span></td></tr>')
                for name in sorted(counts):
                    spell = by_name[name]
                    rows.append(
                        f'<tr class="sub"><td></td><td></td>'
                        f'<td class="nm">{_esc(name)}</td>'
                        f'<td>{_spell_action(spell)}</td>'
                        f'<td class="r last">{_circles(counts[name])}</td></tr>')
            else:
                n = r["slots"] or 0
                label = ("Any combination below" if len(counts) > 1
                         else "Slots")
                rows.append(
                    f'<tr class="grp"><td>{source}</td><td>{r["rank"]}</td>'
                    f'<td class="nm">{label}</td><td></td>'
                    f'<td class="r last">{_circles(n)}</td></tr>')
                for name in sorted(counts):
                    rows.append(
                        f'<tr class="sub"><td></td><td></td>'
                        f'<td class="nm">{_esc(name)}</td>'
                        f'<td>{_spell_action(by_name[name])}</td>'
                        f'<td class="r last"></td></tr>')
    if not rows:
        return ""
    return f"""
  <table class="grid preptable">
    <thead><tr><th>Source</th><th>Rank</th><th>Spell</th><th>Action</th>
      <th class="r last">Uses</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>"""


def _page_spells(ctx: dict[str, Any]) -> str:
    blocks = ctx["spellcasting"]
    if not blocks:
        return ""
    level, abilities = ctx["level"], ctx["abilities"]
    multi_source = len(blocks) > 1

    # Stat blocks follow the prep table alphabetically by name, across every
    # source combined, not grouped by rank or source -- the table above is
    # where "what's in which slot" lives, so this list is purely for looking
    # a spell up. Each card's kicker still carries its rank/source so it's
    # unambiguous once you're looking at the spell itself.
    source_lines = []
    entries: list[tuple[str, dict[str, Any], int, int, str, int]] = []
    for block in blocks:
        key = abilities.get(block["ability"], 10)
        rank = block["proficiency"] or 0
        dc = 10 + m.total_bonus(key, rank, level)
        attack = m.total_bonus(key, rank, level)
        source_lines.append(
            f"{_esc(block['name'])} ({_esc(block['kind'])}) &mdash; "
            f"DC {dc} &middot; attack {_mod(attack)}")

        # A prepared spell can occupy more than one slot at the same rank --
        # collapse to one entry with a multiplicity count so its stat block
        # prints once.
        for r in block["ranks"]:
            if not r["spells"]:
                continue
            counts: dict[str, int] = {}
            order: list[str] = []
            by_name: dict[str, dict[str, Any]] = {}
            for spell in r["spells"]:
                name = spell["name"]
                if name not in counts:
                    order.append(name)
                    by_name[name] = spell
                counts[name] = counts.get(name, 0) + 1
            for name in order:
                entries.append((name, by_name[name], r["rank"], counts[name],
                                 block["name"], dc))

    cards = ""
    for name, spell, spell_rank, count, source, dc in sorted(
            entries, key=lambda e: e[0].lower()):
        rank_lbl = ("Cantrip" if "cantrip" in spell["traits"]
                    else f"Rank {spell_rank}")
        if count > 1:
            rank_lbl += f" ×{count}"
        kicker = f"{source} · {rank_lbl}" if multi_source else rank_lbl
        cards += _card(
            spell, kicker=kicker,
            cost=(spell["system"].get("time") or {}).get("value"),
            spell_rank=spell.get("effective_rank"),
            meta=_spell_meta(spell, dc),
        )

    return f"""
<section class="page">
  {_section("Spellcasting", "<br>".join(source_lines))}
  {_prep_table(blocks)}
  {cards or '<div class="rules"><p>No spells recorded.</p></div>'}
  {_foot(ctx['name'], "Spellcasting")}
</section>"""


def _deity_card(info: dict[str, Any]) -> str:
    """Full deity block. Omitted entirely for a character who follows no
    deity; for one whose deity isn't in the rules data, a labelled block with
    ruled lines to fill in by hand, since a home-game pantheon is a legitimate
    thing for a sheet to carry."""
    if info["status"] == "none":
        return ""

    if info["status"] == "unrecognized":
        rows = "".join(
            f'<tr><td class="dl">{label}</td>'
            f'<td class="dv"><span class="fill"></span></td></tr>'
            for label in ("Divine Font", "Divine Sanctification", "Divine Skill",
                          "Favored Weapon", "Domains", "Edicts", "Anathema")
        )
        return f"""
  <article class="card deity">
    <div class="card-hd"><h3>{_esc(info['recorded'])}</h3>
      <span class="rank">Deity</span></div>
    <div class="rules"><p>Recorded on this character but not present in the
      rules data &mdash; a home-game or non-standard pantheon. Its details are
      left blank to fill in.</p></div>
    <table class="deityfacts">{rows}</table>
  </article>"""

    entry = info["entry"]
    rows = "".join(
        f'<tr><td class="dl">{label}</td><td class="dv">{value}</td></tr>'
        for label, value in info["facts"]
    )
    symbol = (f'<img class="symbol" src="{info["symbol_uri"]}" alt="">'
              if info.get("symbol_uri") else "")
    return f"""
  <article class="card deity">
    <div class="card-hd"><h3>{_esc(entry['name'])}</h3>
      <span class="rank">{_esc(info.get('category', 'deity').title())}</span></div>
    <div class="deitybody">{symbol}
      <table class="deityfacts">{rows}</table></div>
    <div class="rules">{_rules_html(entry.get('desc_html', ''))}</div>
    <div class="src">{_esc(entry.get('source_book') or '')}</div>
  </article>"""


def _page_features(ctx: dict[str, Any]) -> str:
    ch, lib = ctx["character"], ctx["lib"]
    cards = _deity_card(ctx["deity"])

    ancestry = lib.get(ch.get("ancestry") or "", "ancestry")
    if ancestry:
        stats = ctx["ancestry_stats"]
        if stats:
            cards += f"""
  <article class="card plain tight">
    <div class="card-hd"><h3>{_esc(ancestry['name'])} &mdash; ancestry statistics</h3></div>
    <div class="rules"><p><strong>Hit Points</strong> {stats['hp']}
      &nbsp;&middot;&nbsp; <strong>Size</strong> {_esc(ctx['size'])}
      &nbsp;&middot;&nbsp; <strong>Speed</strong> {ctx['speed']} feet
      &nbsp;&middot;&nbsp; <strong>Senses</strong> {_esc(stats['vision'])}
      &nbsp;&middot;&nbsp; <strong>Languages</strong>
      {_esc(", ".join(ch.get('languages') or []) or '—')}</p></div>
  </article>"""
        cards += _card(ancestry, kicker="Ancestry")

    heritage = lib.get(ch.get("heritage") or "", "heritage")
    if heritage:
        cards += _card(heritage, kicker="Heritage")

    background = lib.get(ch.get("background") or "", "background")
    if background:
        cards += _card(background, kicker="Background")

    for feature in ctx["class_features"]:
        cards += _card(feature, kicker=f"Class, level {feature['granted_level']}")
    for feature in ctx["subclasses"]:
        cards += _card(feature, kicker="Chosen")

    feats = []
    for feat in ch.get("feats", []) or []:
        if not (isinstance(feat, (list, tuple)) and feat):
            continue
        name = str(feat[0])
        note = str(feat[1]) if len(feat) > 1 and feat[1] else ""
        cat = str(feat[2]) if len(feat) > 2 and feat[2] else "Feat"
        lvl = feat[3] if len(feat) > 3 and feat[3] else None
        feats.append((name, note, cat, lvl))
    feats.sort(key=lambda f: (f[3] or 0, f[0]))

    feat_cards = ""
    for name, note, cat, lvl in feats:
        entry = lib.get(name, "feat")
        if entry is None:
            continue
        kicker = f"{cat}{f', level {lvl}' if lvl else ''}"
        feat_cards += _card(entry, kicker=kicker, chosen=_esc(note) if note else "")

    specials = [s for s in (ch.get("specials", []) or []) if str(s).strip()]
    special_html = ""
    if specials:
        special_html = (
            '<div class="sub">Recorded on the character, verbatim</div>'
            '<article class="card plain tight"><div class="rules"><ul class="plainlist">'
            + "".join(f"<li>{_esc(s)}</li>" for s in specials)
            + "</ul></div></article>"
        )

    return f"""
<section class="page">
  {_section("Features", "Ancestry, heritage, background<br>and class features, in full")}
  {cards or '<div class="rules"><p>Nothing resolved.</p></div>'}
  {f'<div class="sub">Feats</div>{feat_cards}' if feat_cards else ""}
  {special_html}
  {_foot(ctx['name'], "Features")}
</section>"""


def _coin(money: Any) -> str:
    if not isinstance(money, dict):
        return "—"
    parts = [f"{money[k]} {k}" for k in ("pp", "gp", "sp", "cp")
             if money.get(k)]
    return " ".join(parts) or "—"


def _page_equipment(ctx: dict[str, Any]) -> str:
    ch, lib = ctx["character"], ctx["lib"]
    rows, seen = "", []

    def add(name: str, qty: Any, note: str = "") -> None:
        nonlocal rows
        entry = lib.get(name, "item")
        if entry and entry["name"] not in seen:
            seen.append(entry["name"])
        # Name the item, and keep a player's own annotation in the notes column
        # where it reads as an annotation. Leaving "(emblazoned)" or "(worn)"
        # attached to the item's name makes a temporary or situational state
        # look like part of the equipment itself.
        display = entry["name"] if entry else name
        if entry and entry["name"].lower() != name.strip().lower():
            recorded = _strip_to_qualifier(name, entry["name"])
            if recorded:
                note = f"{note}; {recorded}" if note else recorded
        system = (entry or {}).get("system", {})
        bulk = (system.get("bulk") or {}).get("value")
        if bulk == 0.1:
            bulk_s = "L"
        elif bulk in (None, 0):
            bulk_s = "—"
        else:
            bulk_s = str(int(bulk) if float(bulk).is_integer() else bulk)
        price = system.get("price", {}).get("value") or {}
        price_s = " ".join(f"{price[k]} {k}" for k in ("pp", "gp", "sp", "cp")
                           if price.get(k)) or "—"
        rows += (f'<tr><td class="nm">{_esc(display)}</td>'
                 f'<td class="r">{_esc(qty)}</td>'
                 f'<td class="r">{bulk_s}</td>'
                 f'<td class="r">{price_s}</td>'
                 f'<td class="dsc last">{_esc(note)}</td></tr>')

    for w in ch.get("weapons", []) or []:
        if isinstance(w, dict) and w.get("name"):
            runes = ", ".join(str(r) for r in (w.get("runes") or []))
            add(w["name"], w.get("qty") or 1, runes)
    for a in ch.get("armor", []) or []:
        if isinstance(a, dict) and a.get("name"):
            add(a["name"], a.get("qty") or 1,
                "Worn" if a.get("worn") else "")
    for item in ch.get("equipment", []) or []:
        if isinstance(item, (list, tuple)) and item:
            add(str(item[0]), item[1] if len(item) > 1 else 1)
        elif isinstance(item, dict) and item.get("name"):
            add(item["name"], item.get("qty") or 1)

    item_cards = ""
    for name in seen:
        entry = lib.get(name, "item")
        if entry and entry.get("desc_html"):
            item_cards += _card(entry, kicker=entry["type"].title(), plain=True)

    return f"""
<section class="page">
  {_section("Equipment", f"Money {_coin(ch.get('money'))}")}
  <table class="grid">
    <tr><th>Item</th><th class="r">Qty</th><th class="r">Bulk</th>
      <th class="r">Price</th><th class="last">Notes</th></tr>
    {rows or '<tr><td colspan="5" class="dsc">Nothing recorded.</td></tr>'}
  </table>
  {f'<div class="sub">Item rules text</div>{item_cards}' if item_cards else ""}
  {_foot(ctx['name'], "Equipment")}
</section>"""


def _page_notes(ctx: dict[str, Any]) -> str:
    d = ctx["derived"]
    lib = ctx["lib"]
    unresolved, warnings, aliased = lib.unresolved, ctx["warnings"], lib.aliased

    warn_html = ""
    if unresolved or warnings:
        items = "".join(
            f"<li>{_esc(u['name'])} &mdash; no {_esc(u['kind'])} of that name in "
            f"the rules data, so its text is not on this sheet.</li>"
            for u in unresolved
        ) + "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        warn_html = (f'<div class="warn"><strong>Check these.</strong>'
                     f'<ul class="plainlist">{items}</ul></div>')
    if aliased:
        rows = "".join(
            f"<li>&ldquo;{_esc(a['recorded'])}&rdquo; was matched to "
            f"<strong>{_esc(a['matched'])}</strong>.</li>" for a in aliased
        )
        warn_html += (
            '<div class="card plain tight"><div class="rules">'
            '<p><strong>Names matched inexactly.</strong> The character data and '
            'the rules data spell these differently; the rules text shown is for '
            'the entry named second.</p>'
            f'<ul class="plainlist">{rows}</ul></div></div>'
        )

    return f"""
<section class="page">
  {_section("Sheet Notes", "How each number was derived")}
  {warn_html}
  <div class="rules">
    <p>Every total on the first page was computed by this project&rsquo;s own
    <code>calculate_derived_stats</code>, from the character data as recorded.
    Proficiency ranks are taken from the character, never re-derived from class
    and level, so this sheet always matches the build it came from. Rules text
    is read from the ingested rules database rather than retyped.</p>
    <ul class="plainlist">
      <li><strong>AC {d['ac']}</strong> &mdash; 10, plus the worn armor&rsquo;s
        item bonus, plus Dexterity capped by the armor, plus level and
        proficiency. A raised shield is added separately where one is carried;
        <code>calculate_derived_stats</code> does not include it.</li>
      <li><strong>HP {d['hp']}</strong> &mdash; ancestry HP, plus level &times;
        (class HP + Constitution modifier), plus any recorded bonus HP.</li>
      <li><strong>Saves</strong> Fortitude {_mod(d['saves']['fortitude'])},
        Reflex {_mod(d['saves']['reflex'])}, Will {_mod(d['saves']['will'])}
        &mdash; ability modifier + level + proficiency.</li>
      <li><strong>Perception {_mod(d['perception'])}</strong> and
        <strong>Class DC {d['class_dc']}</strong> &mdash; same arithmetic,
        Class DC on the key attribute
        ({_esc(ctx['character'].get('keyability', '')).upper()}).</li>
      <li><strong>Untrained skills</strong> are the ability modifier alone.
        Level is added only at trained rank or better.</li>
      <li><strong>Shield</strong> Hardness, HP and Broken Threshold are the
        item&rsquo;s own values plus any etched rune. Bonuses from spells,
        feats and other effects are left out on purpose &mdash; a status bonus
        may not be active when you need the shield, so the printed figure is
        what it reliably blocks.</li>
      <li><strong>Strikes</strong> use the damage die recorded on the character,
        which is where die-size effects live; striking runes add dice. Ability
        choice: Dexterity for non-thrown ranged weapons, the better of Strength
        or Dexterity for finesse, Strength otherwise.</li>
    </ul>
    <p>Not modelled: temporary bonuses, item bonuses beyond armor and weapon
    potency runes, focus points, and anything a rules-glossary entry adds that
    is not recorded in the character data.</p>
  </div>
  {_foot(ctx['name'], "Sheet Notes")}
</section>"""


_OGL_PARAS = [
    "The following text is the property of Wizards of the Coast, Inc. and is Copyright 2000 Wizards of the Coast, Inc. (“Wizards”). All Rights Reserved.",
    "<b>1. Definitions:</b> (a)“Contributors” means the copyright and/or trademark owners who have contributed Open Game Content; (b)“Derivative Material” means copyrighted material including derivative works and translations (including into other computer languages), potation, modification, correction, addition, extension, upgrade, improvement, compilation, abridgment or other form in which an existing work may be recast, transformed or adapted; (c) “Distribute” means to reproduce, license, rent, lease, sell, broadcast, publicly display, transmit or otherwise distribute; (d)“Open Game Content” means the game mechanic and includes the methods, procedures, processes and routines to the extent such content does not embody the Product Identity and is an enhancement over the prior art and any additional content clearly identified as Open Game Content by the Contributor, and means any work covered by this License, including translations and derivative works under copyright law, but specifically excludes Product Identity. (e) “Product Identity” means product and product line names, logos and identifying marks including trade dress; artifacts; creatures characters; stories, storylines, plots, thematic elements, dialogue, incidents, language, artwork, symbols, designs, depictions, likenesses, formats, poses, concepts, themes and graphic, photographic and other visual or audio representations; names and descriptions of characters, spells, enchantments, personalities, teams, personas, likenesses and special abilities; places, locations, environments, creatures, equipment, magical or supernatural abilities or effects, logos, symbols, or graphic designs; and any other trademark or registered trademark clearly identified as Product identity by the owner of the Product Identity, and which specifically excludes the Open Game Content; (f) “Trademark” means the logos, names, mark, sign, motto, designs that are used by a Contributor to identify itself or its products or the associated products contributed to the Open Game License by the Contributor (g) “Use”, “Used” or “Using” means to use, Distribute, copy, edit, format, modify, translate and otherwise create Derivative Material of Open Game Content. (h) “You” or “Your” means the licensee in terms of this agreement.",
    "<b>2. The License:</b> This License applies to any Open Game Content that contains a notice indicating that the Open Game Content may only be Used under and in terms of this License. You must affix such a notice to any Open Game Content that you Use. No terms may be added to or subtracted from this License except as described by the License itself. No other terms or conditions may be applied to any Open Game Content distributed using this License.",
    "<b>3. Offer and Acceptance:</b> By Using the Open Game Content You indicate Your acceptance of the terms of this License.",
    "<b>4. Grant and Consideration:</b> In consideration for agreeing to use this License, the Contributors grant You a perpetual, worldwide, royalty-free, non-exclusive license with the exact terms of this License to Use, the Open Game Content.",
    "<b>5. Representation of Authority to Contribute:</b> If You are contributing original material as Open Game Content, You represent that Your Contributions are Your original creation and/or You have sufficient rights to grant the rights conveyed by this License.",
    "<b>6. Notice of License Copyright:</b> You must update the COPYRIGHT NOTICE portion of this License to include the exact text of the COPYRIGHT NOTICE of any Open Game Content You are copying, modifying or distributing, and You must add the title, the copyright date, and the copyright holder’s name to the COPYRIGHT NOTICE of any original Open Game Content you Distribute.",
    "<b>7. Use of Product Identity:</b> You agree not to Use any Product Identity, including as an indication as to compatibility, except as expressly licensed in another, independent Agreement with the owner of each element of that Product Identity. You agree not to indicate compatibility or co-adaptability with any Trademark or Registered Trademark in conjunction with a work containing Open Game Content except as expressly licensed in another, independent Agreement with the owner of such Trademark or Registered Trademark. The use of any Product Identity in Open Game Content does not constitute a challenge to the ownership of that Product Identity. The owner of any Product Identity used in Open Game Content shall retain all rights, title and interest in and to that Product Identity.",
    "<b>8. Identification:</b> If you distribute Open Game Content You must clearly indicate which portions of the work that you are distributing are Open Game Content.",
    "<b>9. Updating the License:</b> Wizards or its designated Agents may publish updated versions of this License. You may use any authorized version of this License to copy, modify and distribute any Open Game Content originally distributed under any version of this License.",
    "<b>10. Copy of this License:</b> You MUST include a copy of this License with every copy of the Open Game Content You Distribute.",
    "<b>11. Use of Contributor Credits:</b> You may not market or advertise the Open Game Content using the name of any Contributor unless You have written permission from the Contributor to do so.",
    "<b>12. Inability to Comply:</b> If it is impossible for You to comply with any of the terms of this License with respect to some or all of the Open Game Content due to statute, judicial order, or governmental regulation then You may not Use any Open Game Material so affected.",
    "<b>13. Termination:</b> This License will terminate automatically if You fail to comply with all terms herein and fail to cure such breach within 30 days of becoming aware of the breach. All sublicenses shall survive the termination of this License.",
    "<b>14. Reformation:</b> If any provision of this License is held to be unenforceable, such provision shall be reformed only to the extent necessary to make it enforceable.",
    "<b>15. COPYRIGHT NOTICE:</b> Open Game License v 1.0a Copyright 2000, Wizards of the Coast, Inc. System Reference Document © 2000, Wizards of the Coast, Inc.; Authors Jonathan Tweet, Monte Cook, Skip Williams, based on original material by E. Gary Gygax and Dave Arneson. Pathfinder Core Rulebook (Second Edition) © 2019, Paizo Inc.; Designers: Logan Bonner, Jason Bulmahn, Stephen Radney-MacFarland, and Mark Seifter. Additional Open Game Content from the Pathfinder sourcebooks listed on this sheet, © Paizo Inc.",
]


# The sheet embeds the Noto subsets as base64 WOFF2, which makes every
# rendered file a redistribution of the Font Software in its own right. OFL
# Condition 2 wants each such copy to carry the copyright notice *and* the
# licence; naming the licence is not enough, and the subsets no longer carry
# it internally (pyftsubset's default --name-IDs drops name IDs 13/14, the
# licence notice and URL -- see sheet_assets.py). So it is reproduced here,
# the same way the OGL is, and for the same reason.
_OFL_COPYRIGHT = (
    "Copyright 2022 The Noto Project Authors "
    "(https://github.com/notofonts/latin-greek-cyrillic)"
)

_OFL_PARAS = [
    "This Font Software is licensed under the SIL Open Font License, Version 1.1. "
    "This license is copied below, and is also available with a FAQ at: "
    "https://openfontlicense.org",
    "<b>SIL OPEN FONT LICENSE Version 1.1 &mdash; 26 February 2007</b>",
    "<b>PREAMBLE</b> The goals of the Open Font License (OFL) are to stimulate "
    "worldwide development of collaborative font projects, to support the font "
    "creation efforts of academic and linguistic communities, and to provide a "
    "free and open framework in which fonts may be shared and improved in "
    "partnership with others.",
    "The OFL allows the licensed fonts to be used, studied, modified and "
    "redistributed freely as long as they are not sold by themselves. The fonts, "
    "including any derivative works, can be bundled, embedded, redistributed "
    "and/or sold with any software provided that any reserved names are not used "
    "by derivative works. The fonts and derivatives, however, cannot be released "
    "under any other type of license. The requirement for fonts to remain under "
    "this license does not apply to any document created using the fonts or their "
    "derivatives.",
    "<b>DEFINITIONS</b> &ldquo;Font Software&rdquo; refers to the set of files "
    "released by the Copyright Holder(s) under this license and clearly marked as "
    "such. This may include source files, build scripts and documentation.",
    "&ldquo;Reserved Font Name&rdquo; refers to any names specified as such after "
    "the copyright statement(s).",
    "&ldquo;Original Version&rdquo; refers to the collection of Font Software "
    "components as distributed by the Copyright Holder(s).",
    "&ldquo;Modified Version&rdquo; refers to any derivative made by adding to, "
    "deleting, or substituting &mdash; in part or in whole &mdash; any of the "
    "components of the Original Version, by changing formats or by porting the "
    "Font Software to a new environment.",
    "&ldquo;Author&rdquo; refers to any designer, engineer, programmer, technical "
    "writer or other person who contributed to the Font Software.",
    "<b>PERMISSION &amp; CONDITIONS</b> Permission is hereby granted, free of "
    "charge, to any person obtaining a copy of the Font Software, to use, study, "
    "copy, merge, embed, modify, redistribute, and sell modified and unmodified "
    "copies of the Font Software, subject to the following conditions:",
    "<b>1)</b> Neither the Font Software nor any of its individual components, in "
    "Original or Modified Versions, may be sold by itself.",
    "<b>2)</b> Original or Modified Versions of the Font Software may be bundled, "
    "redistributed and/or sold with any software, provided that each copy contains "
    "the above copyright notice and this license. These can be included either as "
    "stand-alone text files, human-readable headers or in the appropriate "
    "machine-readable metadata fields within text or binary files as long as those "
    "fields can be easily viewed by the user.",
    "<b>3)</b> No Modified Version of the Font Software may use the Reserved Font "
    "Name(s) unless explicit written permission is granted by the corresponding "
    "Copyright Holder. This restriction only applies to the primary font name as "
    "presented to the users.",
    "<b>4)</b> The name(s) of the Copyright Holder(s) or the Author(s) of the Font "
    "Software shall not be used to promote, endorse or advertise any Modified "
    "Version, except to acknowledge the contribution(s) of the Copyright Holder(s) "
    "and the Author(s) or with their explicit written permission.",
    "<b>5)</b> The Font Software, modified or unmodified, in part or in whole, "
    "must be distributed entirely under this license, and must not be distributed "
    "under any other license. The requirement for fonts to remain under this "
    "license does not apply to any document created using the Font Software.",
    "<b>TERMINATION</b> This license becomes null and void if any of the above "
    "conditions are not met.",
    "<b>DISCLAIMER</b> THE FONT SOFTWARE IS PROVIDED &ldquo;AS IS&rdquo;, WITHOUT "
    "WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY "
    "WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND "
    "NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT "
    "SHALL THE COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER "
    "LIABILITY, INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR "
    "CONSEQUENTIAL DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, "
    "ARISING FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM "
    "OTHER DEALINGS IN THE FONT SOFTWARE.",
]


def _page_legal(ctx: dict[str, Any]) -> str:
    orc = sorted(ctx["orc_books"])
    ogl = sorted(ctx["ogl_books"])

    # A deity block reproduces more than mechanics: a title, areas of concern,
    # edicts, anathema and iconography are Reserved Material under the ORC
    # License (Product Identity under the OGL), not the generic game content
    # either licence grants freely. Say which permission actually covers it,
    # and only when such a block was rendered.
    deity_note = ""
    if ctx["deity"]["status"] == "resolved":
        deity_note = (
            "<p><strong>Reserved Material.</strong> The mechanical content "
            "above &mdash; divine font, sanctification, skill, favored weapon, "
            "domains and spell lists &mdash; is generic game content under the "
            "ORC License. A deity&rsquo;s name, title, areas of concern, "
            "edicts, anathema, iconography and descriptive text are Reserved "
            "Material under that licence and remain the property of Paizo Inc.; "
            "they appear here as a descriptive reference in a free, "
            "non-commercial character sheet under the Community Use Policy "
            "above, not as an exercise of the ORC License.</p>"
        )
    orc_list = ", ".join(f"<em>{_esc(b)}</em>" for b in orc) or "—"
    ogl_section = ""
    if ogl:
        ogl_section = f"""
    <h4>Open Game License content</h4>
    <p>Rules text on this sheet drawn from {", ".join(f"<em>{_esc(b)}</em>" for b in ogl)}
    is Open Game Content, licensed under the Open Game License v1.0a,
    reproduced in full below. Original material on this sheet &mdash; the
    layout, the artwork, and everything about the character
    {_esc(ctx['name'])} &mdash; is not Open Game Content.</p>
    <h4>Open Game License Version 1.0a</h4>
    <div class="ogl">{"".join(f"<p>{p}</p>" for p in _OGL_PARAS)}</div>"""

    return f"""
<section class="page">
  {_section("Notices & Attribution", "Licence text for the rules<br>content reproduced here")}
  <div class="legal">
    <h4>Paizo Community Use Policy</h4>
    <p>This character sheet uses trademarks and/or copyrights owned by Paizo
    Inc., used under Paizo&rsquo;s Community Use Policy
    (paizo.com/licenses/communityuse). We are expressly prohibited from
    charging you to use or access this content. This character sheet is not
    published, endorsed, or specifically approved by Paizo. For more
    information about Paizo Inc. and Paizo products, visit paizo.com.</p>

    <h4>ORC License attribution notice</h4>
    <p>This work includes material from {orc_list}, &copy; Paizo Inc., used
    under the ORC License. The ORC License is available at paizo.com/orclicense
    and is registered with the U.S. Copyright Office, Library of Congress,
    TX&nbsp;9-307-067.</p>
    {deity_note}
    {ogl_section}
  </div>
  {_foot(ctx['name'], "Notices & Attribution")}
</section>

<section class="page">
  {_section("Typography & Font Licence", "The typeface licence for the<br>fonts embedded in this file")}
  <div class="legal">
    <h4>Artwork and typography</h4>
    <p>Every ornament and icon on this sheet &mdash; the spiral section marks,
    the action-cost lozenges, the proficiency boxes &mdash; is original inline
    SVG generated by this software. No publisher&rsquo;s artwork, logo, map or
    layout file is embedded. Typography is Noto Serif, Noto Serif Italic and
    Noto Sans, embedded as subsetted WOFF2 and licensed under the SIL Open Font
    License 1.1, reproduced in full below.</p>
    <p>{_esc(_OFL_COPYRIGHT)}</p>
    <h4>SIL Open Font License Version 1.1</h4>
    <div class="ogl">{"".join(f"<p>{p}</p>" for p in _OFL_PARAS)}</div>
  </div>
  {_foot(ctx['name'], "Typography & Font Licence")}
</section>"""


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------

def _ancestry_stats(conn, ancestry_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT hp, size, vision FROM ancestry_boosts WHERE ancestry_slug = ?",
        (ancestry_name.lower().replace(" ", "-"),),
    ).fetchone()
    if row is None:
        return None
    return {"hp": row["hp"], "size": row["size"],
            "vision": (row["vision"] or "normal").replace("-", " ")}


_SIZE_NAMES = {"tiny": "Tiny", "sm": "Small", "small": "Small", "med": "Medium",
               "medium": "Medium", "lg": "Large", "large": "Large"}


def _armor_stats(character: dict[str, Any], lib: _Library,
                 abilities: dict[str, int]) -> tuple[dict | None, str]:
    """Worn armor's printable stats, with the Strength-threshold adjustment
    applied: meeting the armor's Strength value removes its check penalty and
    reduces its Speed penalty by 5 feet."""
    for item in character.get("armor", []) or []:
        if not (isinstance(item, dict) and item.get("name")):
            continue
        entry = lib.get(item["name"], "item")
        if not entry or entry["type"] != "armor":
            continue
        system = entry["system"]
        check = system.get("checkPenalty") or 0
        speed = system.get("speedPenalty") or 0
        threshold = system.get("strength")
        str_mod = m.ability_mod(abilities["str"])
        note = ""
        if threshold is not None and str_mod >= threshold:
            # Kept to one line: this sits at the foot of the narrowest column
            # on page 1, where a long sentence costs three lines of height.
            note = (f"Strength {_mod(str_mod)} meets this armor&rsquo;s "
                    f"{threshold} &mdash; no check penalty, Speed penalty "
                    f"reduced by 5 ft.")
            check = 0
            speed = min(speed + 5, 0)
        return {
            # As with the shield above: name the resolved item, so the AC block
            # can't imply a temporary effect is in play.
            "display_name": entry["name"],
            "category": system.get("category") or "light",
            "ac_bonus": system.get("acBonus") or 0,
            "dex_cap": system.get("dexCap") if system.get("dexCap") is not None else 0,
            "check_penalty": check or 0,
            "speed_penalty": speed or 0,
            "potency": item.get("pot") or 0,
        }, note
    return None, ""


def _license_split(lib: _Library) -> tuple[set[str], set[str]]:
    """Which sourcebooks the sheet reproduced text from, split by licence
    regime, so the attribution page lists only what is actually on the page."""
    orc: set[str] = set()
    ogl: set[str] = set()
    for entry in lib.used:
        if not entry.get("source_book"):
            continue
        (orc if entry.get("is_remaster") else ogl).add(entry["source_book"])
    return orc, ogl


def _build_context(character: dict[str, Any], lib: _Library, conn) -> dict[str, Any]:
    level = int(character.get("level") or 1)
    abilities = m.default_character_abilities(character)
    derived = build_tools.calculate_derived_stats(character)
    prof = character.get("proficiencies", {}) or {}

    deity = _deity_info(character, lib)
    armor, armor_note = _armor_stats(character, lib, abilities)
    shield = _shield_stats(character, lib)
    strikes, warnings = _strikes(character, abilities, level, prof, lib, deity)
    ancestry_stats = _ancestry_stats(conn, character.get("ancestry") or "")
    subclasses = lib.subclass_selections(character.get("class") or "", character)

    size = _SIZE_NAMES.get(str(character.get("size") or
                               (ancestry_stats or {}).get("size") or "").lower(),
                           str(character.get("sizeName") or "Medium"))

    return {
        "character": character,
        "lib": lib,
        "level": level,
        "abilities": abilities,
        "derived": derived,
        "name": character.get("name") or "Unnamed character",
        "size": size,
        "speed": (character.get("attributes") or {}).get("speed") or 25,
        # Only worth a line when it beats ordinary sight. A heritage can also
        # upgrade vision (item_senses), which isn't read here -- so this is the
        # ancestry baseline, and the character's own `specials` carry the rest.
        "vision": ((ancestry_stats or {}).get("vision") or "").replace("normal", ""),
        "deity": deity,
        "armor": armor,
        "armor_note": armor_note,
        "shield": shield,
        "strikes": strikes,
        "warnings": warnings,
        "skills": _skill_rows(character, abilities, level),
        "spellcasting": _spellcasting(character, level, lib),
        "class_features": lib.class_features(character.get("class") or "", level),
        "subclasses": subclasses,
        # Repeating the class name under "Fighter 9" tells the reader nothing;
        # classes with no subclass mechanic get their key attribute instead.
        "subclass_label": (
            subclasses[0]["name"] if subclasses
            else f"Key attribute {str(character.get('keyability') or '').upper()}"
            if character.get("keyability") else ""
        ),
        "ancestry_stats": ancestry_stats,
    }


_TOOLBAR = """
<div class="toolbar">
  <button onclick="window.print()">Print / Save as PDF</button>
  <span class="tip">Enable &ldquo;background graphics&rdquo; in the print dialog
    so the tinted panels come through.</span>
</div>
"""


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------

def render_character_sheet(
    character: dict[str, Any],
    output_path: str,
    paper: str = "letter",
    logo_path: str | None = None,
    symbol_dir: str | None = None,
) -> dict[str, Any]:
    """Write a print-ready, fully self-contained HTML character sheet and
    return a summary of what went onto it.

    The rendered file has no external dependencies of any kind -- fonts are
    inlined as base64 WOFF2, all ornament is generated inline SVG, and there
    is no stylesheet, script, or image fetched at view time -- so it can be
    sent to a player as a single attachment and printed unchanged. Open it and
    use the browser's own Print dialog (enable background graphics).

    The HTML is written to `output_path` and deliberately not returned: a
    real sheet runs to 150 KB or more, which would swamp a tool response.

    Sections rendered, each only when the character data supports it:
    core statistics (attributes, skills, AC and shield, saves, HP,
    Perception, strikes, spell DC, conditions tracker); one spellcasting
    page per entry in `spellCasters`, with every spell's complete rules text
    and traits; features, led by a deity block where there is one (see below)
    and then ancestry, heritage, background, level-appropriate auto-granted
    class features, any detected subclass, and every feat; equipment with item
    rules text; a notes page showing how each number was derived; and a licence
    attribution page listing only the sourcebooks actually quoted.

    The deity block distinguishes three cases, reported as `deity.status`:

    * `none` -- the character follows no deity, including when the export
      writes its own placeholder ("Not set", an empty string, a dash). No
      block is rendered.
    * `resolved` -- the recorded name matched the rules data. Renders every
      mechanical field the game defines (divine font, sanctification, skill,
      favored weapon, primary and alternate domains, divine attribute, and the
      deity's cleric spells resolved to names by rank) followed by its full
      descriptive text: title, areas of concern, edicts, anathema, iconography.
      Pantheons, covenants and philosophies resolve here too, labelled as such.
    * `unrecognized` -- a name is recorded but isn't in the database, i.e. a
      home-game or non-standard pantheon. Renders a labelled block with ruled
      lines for the standard fields to be filled in by hand. This is *not*
      treated as a defect in the character data and is deliberately kept out
      of `unresolved`.

    Everything is derived from `character` plus this project's ingested rules
    data -- there is no authored commentary, so a sheet is exactly as complete
    as the character export it came from. Two things follow from that worth
    knowing before you call it:

    * A character with no `spellCasters` block gets no spell pages, even if
      the class is a caster. Pathbuilder exports populate it; hand-written
      character JSON often doesn't.
    * Class features come from the ingested `class_progression.granted_items`
      filtered to the character's level, because exports record only choices.
      A subclass (cleric doctrine, druid order) is recovered by matching the
      class's tagged option names against the character's free-text
      `specials`, which is best-effort -- check `subclass` in the result.

    `paper` accepts 'letter' (default) or 'a4'; it sets the @page size, and
    content reflows rather than being scaled.

    `logo_path` embeds an image top-left on page 1 in place of the sheet's own
    spiral mark -- intended for the Pathfinder logo from Paizo's Community Use
    Package, which the Community Use FAQ permits on free fan material. Accepts
    PNG, SVG, JPEG, GIF or WebP (not EPS or AI, which browsers can't display).
    The bytes are embedded verbatim and scaled by height with automatic width,
    so the logo is never recoloured, cropped or distorted -- the policy forbids
    altering a logo's colour, typography, design or proportions, and permits
    proportional resizing. Section headings keep the spiral, so the sheet still
    has a mark of its own. Omit to use the spiral everywhere.

    `symbol_dir` points at a directory of deity symbol images named after the
    deity ("Pharasma.png"), such as Paizo's Community Use "Pathfinder Religious
    Symbols" pack -- underscores in place of spaces are matched too, so
    "Sun_Wukong.png" resolves. When the character's deity resolves and a
    matching file exists, the symbol is embedded beside the deity block's stat
    table, again scaled by height only. A missing symbol is not an error: the
    block simply renders without one.

    Returns a dict with the output `path` and `bytes`, the `sections` written,
    counts of what was rendered, the detected `subclass`, the sourcebooks
    quoted under each licence, and -- importantly -- `unresolved` names and
    `warnings`. An item the export calls "Repair Kit" where the rules data has
    "Repair Toolkit" is reported there rather than guessed at or dropped
    silently, so treat a non-empty `unresolved` as something to fix in the
    character data.
    """
    if not isinstance(character, dict) or not character:
        raise ValueError("character must be a non-empty character dict")
    character = character.get("build", character)
    if paper not in _PAPER:
        raise ValueError(f"paper must be one of {sorted(_PAPER)}, got {paper!r}")

    out = Path(output_path).expanduser()
    if out.suffix.lower() not in (".html", ".htm"):
        raise ValueError(f"output_path must end in .html, got {out.name!r}")
    if not out.parent.exists():
        raise ValueError(f"directory does not exist: {out.parent}")

    logo_html, logo_warnings, logo_bytes = "", [], 0
    if logo_path:
        uri, logo_warnings = _logo_data_uri(logo_path)
        logo_bytes = len(uri)
        # Size and seat the logo from its actual ink rather than its canvas, so
        # the visible wordmark matches the character name's cap height and sits
        # on its baseline. Negative margins cancel the file's transparent
        # padding: the image is moved, never rescaled non-uniformly or cropped.
        style = ""
        bounds = _png_alpha_bounds(Path(logo_path).expanduser().read_bytes())
        if bounds:
            pad_top, pad_bottom = bounds
            ink = max(1e-3, 1.0 - pad_top - pad_bottom)
            box = _LOGO_INK_HEIGHT / ink
            style = (f'height:{box:.1f}px;'
                     f'margin-top:{-box * pad_top:.1f}px;'
                     # +3px leaves the ink a hair above the text box's bottom,
                     # which puts it about on the name's baseline.
                     f'margin-bottom:{3 - box * pad_bottom:.1f}px;')
        logo_html = (f'<img class="logo" src="{uri}" alt=""'
                     f'{f" style=\"{style}\"" if style else ""}>')

    conn = get_connection()
    try:
        lib = _Library(conn, class_name=str(character.get("class") or ""))
        ctx = _build_context(character, lib, conn)
        ctx["logo_html"] = logo_html
        ctx["warnings"] = ctx["warnings"] + logo_warnings

        if symbol_dir and ctx["deity"]["status"] == "resolved":
            found = _find_symbol(symbol_dir, ctx["deity"]["entry"]["name"])
            if found:
                uri, sym_warnings = _logo_data_uri(str(found))
                ctx["deity"]["symbol_uri"] = uri
                ctx["deity"]["symbol_source"] = str(found)
                ctx["warnings"] = ctx["warnings"] + sym_warnings
        # Pages are built first: rendering is what populates the entry list the
        # licence page reads to decide which sourcebooks to credit.
        pages = (_page_core(ctx) + _page_spells(ctx) + _page_features(ctx)
                 + _page_equipment(ctx) + _page_notes(ctx))
        orc_books, ogl_books = _license_split(lib)
        ctx["orc_books"], ctx["ogl_books"] = orc_books, ogl_books
        pages += _page_legal(ctx)

        doc = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{_esc(ctx["name"])} &mdash; '
            f'{_esc(character.get("ancestry") or "")} '
            f'{_esc(character.get("class") or "")} {ctx["level"]}</title>\n'
            f"<style>{_stylesheet(paper)}</style>\n</head>\n<body>\n"
            f'{_TOOLBAR}<div class="sheet">{pages}</div>\n</body>\n</html>\n'
        )
        out.write_text(doc, encoding="utf-8")

        sections = ["core", "features", "equipment", "notes", "attribution"]
        if ctx["spellcasting"]:
            sections.insert(1, "spellcasting")

        return {
            "path": str(out.resolve()),
            "bytes": len(doc.encode("utf-8")),
            "paper": paper,
            "logo": {"embedded": bool(logo_html),
                     "source": logo_path,
                     "data_uri_bytes": logo_bytes},
            "sections": sections,
            "rendered": {
                "skills": len(ctx["skills"]),
                "strikes": len(ctx["strikes"]),
                "class_features": len(ctx["class_features"]),
                "feats": len([f for f in (character.get("feats") or []) if f]),
                "spells": sum(len(r["spells"]) for b in ctx["spellcasting"]
                              for r in b["ranks"]),
                "spellcasting_entries": len(ctx["spellcasting"]),
            },
            "subclass": ctx["subclass_label"],
            # 'none'       -- character follows no deity, block omitted
            # 'resolved'   -- matched the rules data, full mechanics rendered
            # 'unrecognized' -- a name is recorded but isn't in the database
            #                 (home-game pantheon); a fill-in block is rendered
            #                 and this is NOT reported as a data error
            "deity": {
                "recorded": ctx["deity"]["recorded"],
                "status": ctx["deity"]["status"],
                "matched": (ctx["deity"]["entry"] or {}).get("name"),
                "facts": dict(ctx["deity"]["facts"]),
                "symbol": ctx["deity"].get("symbol_source"),
            },
            "licensing": {
                "orc_sources": sorted(orc_books),
                "ogl_sources": sorted(ogl_books),
            },
            "unresolved": lib.unresolved,
            "aliased": lib.aliased,
            "warnings": ctx["warnings"],
        }
    finally:
        conn.close()
