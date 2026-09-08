"""Pathbuilder import and export.

The centrepiece is `test_every_real_character_round_trips`, which runs the real
`characters/` corpus through import and back out again. Those files are the
only large body of genuine builds this project has -- twenty-odd characters
across a dozen classes, levels 1 to 20, several with archetypes, two written by
hand rather than exported -- and they exercise combinations no fixture would
think to invent. It skips when the directory is absent, since `characters/` is
gitignored personal data and will not exist in a fresh clone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf2e_mcp.server import character, character_import, character_replay
from pf2e_mcp.server.build_tools import _feat_slot_bucket

CHARACTERS = Path("characters")

#: Picks in the real corpus that genuinely do not resolve, because the source
#: file is wrong rather than because the importer is. Listed so that a *new*
#: unresolvable name still fails the suite.
#:
#: One entry so far: a character recording "Chosen One" -- a background -- in an
#: ancestry feat slot. That character's own notes already flag it under "the two
#: bad ancestry-feat picks", so import surfacing it is the tooling agreeing with
#: a conclusion a human had already reached by hand.
KNOWN_BAD_PICKS: dict[str, set[str]] = {
    "Tag.json": {"chosen one"},
}


def _corpus() -> list[Path]:
    if not CHARACTERS.is_dir():
        return []
    return sorted(
        path for path in CHARACTERS.glob("*.json")
        if ".chronicles" not in path.name and " - Level " not in path.name
    )


@pytest.fixture
def export() -> dict:
    """A small Pathbuilder export, in the envelope Pathbuilder actually emits."""
    return {
        "success": True,
        "build": {
            "name": "Test Subject",
            "class": "Animist",
            "level": 3,
            "ancestry": "Dwarf",
            "heritage": "Death Warden Dwarf",
            "background": "Field Medic",
            "deity": "Pharasma",
            "languages": ["Common", "Dwarven"],
            "abilities": {
                "str": 10, "dex": 14, "con": 16, "int": 12, "wis": 18, "cha": 8,
                "breakdown": {
                    "ancestryBoosts": ["Con", "Wis"],
                    "ancestryFree": ["Dex"],
                    "ancestryFlaws": ["Cha"],
                    "backgroundBoosts": ["Wis", "Con"],
                    "classBoosts": ["Wis"],
                    "mapLevelledBoosts": {"1": ["Con", "Dex", "Int", "Wis"]},
                },
            },
            "proficiencies": {"religion": 2, "medicine": 4},
            "feats": [
                ["Death Warden Dwarf", None, "Heritage", 1],
                ["Battle Medicine", None, "Awarded Feat", 1],
                ["Stonemason's Eye", None, "Ancestry Feat", 1],
                ["Soul Warden Dedication", "Why: the concept", "Class Feat", 2],
                ["Recognize Spell", None, "Skill Feat", 2],
                ["Incredible Initiative", None, "General Feat", 3],
            ],
            "lores": [["Warfare", 2]],
            "equipment": [["Healer's Toolkit", 1, "Invested"]],
            "weapons": [{"name": "Mace", "qty": 1}],
            "armor": [{"name": "Studded Leather Armor", "qty": 1, "worn": True}],
            "money": {"gp": 2, "sp": 5, "cp": 0, "pp": 0},
        },
    }


# ------------------------------------------------------------------ import


def test_import_produces_a_valid_document(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    document.pop("_import")
    assert character.validate_document(document, conn)["valid"]


def test_identity_and_build_are_slugs_not_names(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    assert document["identity"]["name"] == "Test Subject"
    assert document["identity"]["currentLevel"] == 3
    assert document["build"] == {
        "ancestry": "dwarf",
        "heritage": "death-warden-dwarf",
        "background": "field-medic",
        "class": "animist",
        "deity": "pharasma",
        "languages": ["common", "dwarven"],
    }


def test_the_plan_is_reconstructed_level_by_level(export, conn):
    """The feat tuples carry their level, so this is recovered, not guessed."""
    document = character_import.from_pathbuilder(export, conn)
    by_level = {entry["level"]: entry for entry in document["plan"]}
    assert set(by_level) == {1, 2, 3}
    picks = {
        entry["level"]: {c["pick"] for c in entry.get("choices") or []}
        for entry in document["plan"]
    }
    assert picks[1] == {"stonemasons-eye"}
    assert picks[2] == {"soul-warden-dedication", "recognize-spell"}
    assert picks[3] == {"incredible-initiative"}


def test_awarded_feats_become_grants_not_choices(export, conn):
    """Battle Medicine came from the background; it spent no feat slot."""
    document = character_import.from_pathbuilder(export, conn)
    level_one = document["plan"][0]
    assert "battle-medicine" in (level_one.get("automatic") or [])
    assert "battle-medicine" not in {c["pick"] for c in level_one.get("choices") or []}


def test_heritage_is_not_imported_as_a_feat(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    picks = {
        c["pick"] for entry in document["plan"] for c in entry.get("choices") or []
    }
    assert "death-warden-dwarf" not in picks
    assert document["build"]["heritage"] == "death-warden-dwarf"


def test_prose_in_the_choice_slot_is_rescued_into_a_note(export, conn):
    """Agent-authored builds put reasoning there for want of anywhere better."""
    document = character_import.from_pathbuilder(export, conn)
    dedication = next(
        c for entry in document["plan"] for c in entry.get("choices") or []
        if c["pick"] == "soul-warden-dedication"
    )
    assert dedication["note"] == "Why: the concept"


def test_reached_levels_are_locked(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    statuses = {
        c.get("status") for entry in document["plan"]
        for c in entry.get("choices") or []
    }
    assert statuses == {"locked"}


def test_boost_breakdown_becomes_ordered_boosts(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    boosts = document["plan"][0]["attributeBoosts"]
    assert boosts["ancestry"] == {
        "boosts": ["con", "wis"], "free": ["dex"], "flaw": ["cha"],
    }
    assert boosts["background"] == ["wis", "con"]
    assert boosts["free"] == ["con", "dex", "int", "wis"]


def test_older_breakdown_spelling_is_accepted(export, conn):
    """Real exports use two names for the same fields depending on version."""
    breakdown = export["build"]["abilities"]["breakdown"]
    breakdown["backgroundAbilities"] = breakdown.pop("backgroundBoosts")
    breakdown["lvl1"] = breakdown.pop("mapLevelledBoosts")["1"]
    document = character_import.from_pathbuilder(export, conn)
    boosts = document["plan"][0]["attributeBoosts"]
    assert boosts["background"] == ["wis", "con"]
    assert boosts["free"] == ["con", "dex", "int", "wis"]


def test_gear_collapses_into_one_carried_list(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    carried = {item["item"]: item for item in document["gear"]["carried"]}
    assert set(carried) == {"mace", "studded-leather-armor", "healers-toolkit"}
    assert carried["studded-leather-armor"]["worn"] is True
    assert carried["healers-toolkit"]["invested"] is True
    assert document["gear"]["currency"] == {"gp": 2, "sp": 5}


def test_placeholder_deity_is_absence_not_a_failed_lookup(export, conn):
    """Pathbuilder writes "Not set" rather than omitting the field."""
    export["build"]["deity"] = "Not set"
    document = character_import.from_pathbuilder(export, conn)
    assert "deity" not in document["build"]
    assert document["_import"]["unresolved"] == []


def test_a_parenthetical_that_is_part_of_the_name_survives(conn):
    """"Shattering Strike (Monk)" is the entry's name, not a parameter.

    Stripping it would look for "Shattering Strike", find nothing, and lose the
    feat -- which is what happened before the whole string was tried first.
    """
    name, parameter = character_import._split_parameter(
        conn, "Shattering Strike (Monk)", ("feats",)
    )
    assert (name, parameter) == ("Shattering Strike (Monk)", None)


def test_a_parenthetical_that_is_a_parameter_is_split(conn):
    name, parameter = character_import._split_parameter(
        conn, "Assurance (Medicine)", ("feats",)
    )
    assert (name, parameter) == ("Assurance", "Medicine")


def test_underivable_proficiency_becomes_an_attributable_override(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    overrides = document.get("proficiencyOverrides") or {}
    assert all(o["source"] == "pathbuilder-import" for o in overrides.values())


def test_attributes_that_disagree_with_their_boosts_are_reported(export, conn):
    """An export's scores and its boost list are two claims about one thing."""
    export["build"]["abilities"]["dex"] = 20
    document = character_import.from_pathbuilder(export, conn)
    mismatch = document["_import"]["attribute_mismatch"]
    assert mismatch["dex"]["export"] == 20
    assert mismatch["dex"]["modifier_difference"] == 3


# ------------------------------------------------------------------ export


def test_export_round_trips_through_the_envelope(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    out = character_import.to_pathbuilder(document, None, conn)
    assert out["success"] is True
    assert out["build"]["name"] == "Test Subject"
    assert out["build"]["level"] == 3


def test_export_drops_provenance_keys(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    out = character_import.to_pathbuilder(document, None, conn)
    assert not [key for key in out["build"] if key.startswith("_")]


def test_export_can_render_any_level(export, conn):
    document = character_import.from_pathbuilder(export, conn)
    assert character_import.to_pathbuilder(document, 1, conn)["build"]["level"] == 1
    assert len(character_import.to_pathbuilder(document, 1, conn)["build"]["feats"]) < \
        len(character_import.to_pathbuilder(document, 3, conn)["build"]["feats"])


# ------------------------------------------------- the real corpus


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
@pytest.mark.parametrize("path", _corpus(), ids=lambda p: p.stem)
def test_every_real_character_round_trips(path, conn):
    """Import a real build, replay it, and check nothing was lost.

    Feats are the assertion that matters: every feat that spends a slot in the
    source must reappear, at the same level. That is what proves the plan was
    reconstructed rather than approximated, and it is what caught the two
    category vocabularies in the corpus -- Pathbuilder's "Class Feat" and the
    hand-written files' "class" -- when nearly forty feats vanished from one
    character.
    """
    source = json.loads(path.read_text())
    build = source.get("build", source)

    document = character_import.from_pathbuilder(source, conn)
    document.pop("_import")
    assert character.validate_document(document, conn)["valid"], (
        f"{path.name} did not import into a valid document"
    )

    replayed = character_replay.at_level(document, None, conn)

    # Compared case-insensitively on purpose. Replay emits the rules
    # database's canonical name, so a file recording "Rock the Boat" comes back
    # as "Rock The Boat" -- a normalisation, not a loss.
    expected = {
        (str(feat[0]).lower(), feat[3] if len(feat) > 3 else 1)
        for feat in build.get("feats") or []
        if len(feat) > 2 and _feat_slot_bucket(str(feat[2]), False)
    }
    actual = {
        (feat[0].lower(), feat[3]) for feat in replayed["feats"]
        if feat[2] != "Heritage"
    }
    missing = sorted(expected - actual)
    known = KNOWN_BAD_PICKS.get(path.name, set())
    unexplained = [feat for feat in missing if feat[0] not in known]
    assert not unexplained, f"{path.name} lost feats: {unexplained}"


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
def test_the_corpus_has_no_unresolvable_names(conn):
    """Every name in every real build maps to a slug.

    Import is the only place this format matches on a name. If a name here
    resolves to nothing, that character cannot be converted without losing
    something, so this is the gate on migration.
    """
    failures = {}
    for path in _corpus():
        document = character_import.from_pathbuilder(
            json.loads(path.read_text()), conn
        )
        unresolved = [
            name for name in document["_import"]["unresolved"]
            if not any(bad in name.lower() for bad in KNOWN_BAD_PICKS.get(path.name, ()))
        ]
        if unresolved:
            failures[path.name] = unresolved
    assert not failures, f"Unresolvable names: {failures}"


def test_a_name_from_the_wrong_pack_is_not_silently_accepted(conn):
    """The failure mode this format exists to stop.

    "Chosen One" is a background. Asked for it as a feat, the lookup must
    return nothing rather than falling back across packs and producing a
    confident, wrong slug -- which is exactly what name matching used to do.
    """
    assert character_import._slug_for(conn, "Chosen One", ("feats",)) is None
    assert character_import._slug_for(conn, "Chosen One", ("backgrounds",)) == "chosen-one"
    assert "exists as backgrounds" in character_import._describe_unresolved(
        conn, "Chosen One"
    )
