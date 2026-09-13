"""Pathbuilder import and export.

The centrepiece is `test_every_real_character_survives_a_pathbuilder_round_trip`,
which exports each character in the real `characters/` library to Pathbuilder,
reads it straight back, and checks that nothing changed. Those files are the
only large body of genuine builds this project has -- twenty-odd characters
across a dozen classes, levels 1 to 20, several with archetypes -- and they
exercise combinations no invented fixture would think of. It is also the check
that a player can send a character to Pathbuilder and back without losing
anything.

These tests skip when `characters/` is absent, since it is gitignored personal
data and will not exist in a fresh clone. That also means they are the only
guard on files nothing else holds a copy of: a broken character file cannot be
recovered from history.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf2e_mcp.server import character, character_import, character_replay

CHARACTERS = Path("characters")


def _corpus() -> list[Path]:
    """The real character library, now stored natively rather than as exports."""
    if not CHARACTERS.is_dir():
        return []
    return character.find_all(CHARACTERS)


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


def test_a_known_pathbuilder_ancestry_data_disagreement_still_resolves(export, conn):
    """Pathbuilder's own picker labels the Orc heritage "Battle Ready"; the
    rules data names it "Battle-Ready Orc". Every *other* Orc heritage
    (Badlands, Deep, Grave, Hold-Scarred, Rainfall, Winter) matches
    Pathbuilder's label verbatim, so this needs a specific alias rather than
    a general reformatting rule -- and must not regress to reporting the
    heritage as unresolved."""
    export["build"]["heritage"] = "Battle Ready"
    document = character_import.from_pathbuilder(export, conn)
    assert document["build"]["heritage"] == "battle-ready-orc"
    assert not any("heritage" in u for u in document["_import"]["unresolved"])


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


def test_import_shares_one_replay_between_overrides_and_attribute_mismatch(export, conn):
    """`_recover_lores`, `_overrides_for` and `_attribute_mismatch` each used
    to run their own full `replay.at_level` over the same freshly-imported
    document -- three complete plan replays (proficiency grants, skill
    grants, gear, spellcasting, all of it) for one `from_pathbuilder` call.
    `_recover_lores` genuinely needs its own: it runs first and can still
    mutate `document["plan"]` (adding a Lore's `skillTraining` choice), so
    its replay has to see the plan *before* that mutation while the other
    two need to see it *after*. But `_overrides_for` and `_attribute_mismatch`
    both then diff against the exact same, now-final document -- collapsible
    from two replays into one shared result, down to two total rather than
    three."""
    calls = {"n": 0}
    real_at_level = character_import.replay.at_level

    def counting_at_level(document, level, connection):
        calls["n"] += 1
        return real_at_level(document, level, connection)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(character_import.replay, "at_level", counting_at_level)
        character_import.from_pathbuilder(export, conn)
    assert calls["n"] == 2


def test_a_nonzero_export_rank_lower_than_derived_is_reported():
    """Only the exactly-untrained case (export states 0) used to be reported
    when derivation rates higher -- a nonzero-but-still-too-low export rank
    (trained where replay reaches expert, say) silently passed through
    unflagged, despite the docstring promising to catch any case where
    derivation overreaches ("a bug to chase rather than a fact to record")."""
    build = {"proficiencies": {"religion": 2}}
    overrides, unexplained = character_import._overrides_for(
        build, {"religion": 4})
    assert overrides == {}
    assert unexplained == ["religion: derived 4, export 2"]


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
def test_every_real_character_survives_a_pathbuilder_round_trip(path, conn):
    """Export a real character to Pathbuilder, read it back, and compare.

    The library is stored natively now, so the round trip runs the other way
    round from how it did during migration -- but it exercises the same three
    pieces, and against the same twenty-odd genuine builds across a dozen
    classes and every level from 1 to 20. Those combinations are what caught
    the two feat-category vocabularies, the parentheticals that are part of a
    name rather than a parameter, and the Lores that Pathbuilder keeps outside
    the feat list.

    Attributes and every chosen feat must come back identical. Anything that
    does not survive export and re-import is something a player would lose by
    sending their character to Pathbuilder and back.
    """
    document = character.load(path)
    direct = character_replay.at_level(document, None, conn)

    exported = character_import.to_pathbuilder(document, None, conn)
    reimported = character_import.from_pathbuilder(exported, conn)
    reimported.pop("_import")
    round_tripped = character_replay.at_level(reimported, None, conn)

    assert round_tripped["level"] == direct["level"]

    before = {k: v for k, v in direct["abilities"].items() if k != "breakdown"}
    after = {k: v for k, v in round_tripped["abilities"].items() if k != "breakdown"}
    assert before == after, f"{path.name}: attributes changed"

    # Skill Increases and feat-granted Skill Trainings ride in the same list
    # but are not feats, and they genuinely do not survive the trip: a
    # Pathbuilder export records proficiency *ranks*, not which skill a free
    # pick chose or which level increased it. That loss is a property of
    # Pathbuilder's format, is reported by import as `stated_proficiencies`,
    # and is one of the reasons the native format exists -- so it is not what
    # this test is guarding.
    def real_feats(state):
        return {(f[0].lower(), f[3]) for f in state["feats"]
                if str(f[2] or "").strip().lower()
                not in ("skill increase", "skill training")}

    expected, actual = real_feats(direct), real_feats(round_tripped)
    assert not expected - actual, f"{path.name} lost feats: {sorted(expected - actual)}"

    assert sorted(direct["lores"]) == sorted(round_tripped["lores"]), (
        f"{path.name}: Lores changed"
    )


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
def test_the_whole_library_is_valid(conn):
    """Every stored character passes both validation layers.

    These files are the only copy: they are gitignored, so nothing else holds
    them. A malformed one is not recoverable from history.
    """
    failures = {}
    for path in _corpus():
        result = character.validate_document(character.load(path), conn)
        if not result["valid"]:
            failures[path.name] = [
                i for i in result["issues"] if i["level"] == "error"
            ]
    assert not failures, f"Invalid character files: {failures}"


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
def test_the_whole_library_replays_and_computes(conn):
    """Every character produces a coherent sheet's worth of numbers."""
    from pf2e_mcp.server import build_tools

    for path in _corpus():
        build = character_replay.at_level(character.load(path), None, conn)
        derived = build_tools.calculate_derived_stats(build)
        assert derived["hp"] > 0, f"{path.name}: non-positive Hit Points"
        assert derived["ac"] > 0, f"{path.name}: non-positive Armor Class"


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


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
def test_no_two_characters_share_a_file(conn):
    """Nothing in the library is one save away from overwriting something else."""
    documents = {str(path): character.load(path) for path in _corpus()}
    assert not character.check_collisions(documents)


@pytest.mark.skipif(not _corpus(), reason="no characters/ directory in this checkout")
def test_every_character_sits_where_its_own_document_says_it_should(conn):
    """A file whose folder no longer matches its contents has drifted.

    Renaming a character, or retraining into a different class, changes where
    the document says it belongs -- and a stale folder is how a library starts
    lying about what is in it.
    """
    misplaced = {
        str(path): str(character.storage_path(character.load(path)))
        for path in _corpus()
        if character.storage_path(character.load(path)) != path
    }
    assert not misplaced, f"Files not at their canonical path: {misplaced}"
