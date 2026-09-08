"""The native character format: schema, YAML I/O, and both validation layers."""

from __future__ import annotations

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from pf2e_mcp.server import character, chronicle


# ------------------------------------------------------------------ schema


def test_schema_is_itself_valid():
    Draft202012Validator.check_schema(character.load_schema())


def test_every_field_is_documented():
    """A description on every property, because the schema *is* the manual.

    It is served to a model through `build_character_schema` and is the only
    thing telling a caller what `proficiencyOverrides` is for or why a slug is
    not a name. An undocumented field is a field nobody will fill in correctly.
    """
    undocumented: list[str] = []

    def walk(node, path):
        if not isinstance(node, dict):
            return
        for name, prop in (node.get("properties") or {}).items():
            here = f"{path}/{name}"
            if isinstance(prop, dict) and "$ref" not in prop and not prop.get("description"):
                undocumented.append(here)
            walk(prop, here)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    schema = character.load_schema()
    walk(schema, "")
    for name, definition in schema["$defs"].items():
        walk(definition, f"#/$defs/{name}")

    assert not undocumented, f"Undocumented fields: {undocumented}"


def test_cross_file_refs_resolve():
    """`organizedPlay` references the chronicle schema rather than restating it."""
    raw = json.dumps(character.load_schema())
    assert "chronicle_schema.json#/$defs/chronicle" in raw
    assert "chronicle_schema.json#/$defs/purchase" in raw
    # Resolvable, not merely present.
    character.check_structure({
        "schemaVersion": 1,
        "identity": {"name": "X", "currentLevel": 1},
        "build": {"ancestry": "dwarf", "background": "field-medic", "class": "animist"},
        "plan": [],
        "organizedPlay": {"chronicles": []},
    })


# ---------------------------------------------------------------- YAML I/O


def test_round_trip_preserves_comments_and_order(tmp_path):
    """The reason ruamel is a dependency rather than PyYAML.

    These files are hand-edited as well as tool-written, and a player's own
    annotation must survive the next time the server saves the file.
    """
    source = (
        "schemaVersion: 1\n"
        "identity:\n"
        "  name: Test Subject\n"
        "  # reached 3rd at the January game day\n"
        "  currentLevel: 3\n"
        "build:\n"
        "  ancestry: dwarf\n"
        "  background: field-medic\n"
        "  class: animist\n"
        "plan:\n"
        "  - level: 1\n"
    )
    path = tmp_path / "subject.yaml"
    path.write_text(source, encoding="utf-8")

    document = character.load(path)
    character.save(path, document)
    written = path.read_text(encoding="utf-8")

    assert "# reached 3rd at the January game day" in written
    assert written.index("identity") < written.index("build") < written.index("plan")


def test_save_is_atomic(tmp_path):
    """A failed write must not leave a truncated file or a stray temporary."""
    path = tmp_path / "subject.yaml"
    character.save(path, {"schemaVersion": 1})
    original = path.read_text(encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(Exception):
        character.save(path, {"schemaVersion": 1, "bad": Unserializable()})

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_to_plain_strips_ruamel_types():
    document = character.loads("a: 1\nb:\n  - x\n  - y\n")
    plain = character.to_plain(document)
    assert type(plain) is dict
    assert type(plain["b"]) is list
    json.dumps(plain)


# ------------------------------------------------------- structural layer


def test_minimal_document_validates(minimal):
    assert character.validate_document(minimal)["valid"]


def test_unknown_top_level_key_is_rejected(minimal):
    minimal["backstory"] = "in the wrong place"
    issues = character.check_structure(minimal)
    assert any(i["code"] == "schema" for i in issues)


def test_wrong_schema_version_is_rejected(minimal):
    minimal["schemaVersion"] = 2
    assert character.check_structure(minimal)


def test_a_name_where_a_slug_belongs_is_rejected(minimal):
    """The single most likely authoring mistake."""
    minimal["build"]["ancestry"] = "Dwarf"
    issues = character.check_structure(minimal)
    assert any(i["path"] == "/build/ancestry" for i in issues)


def test_structural_failure_short_circuits_semantics(minimal):
    """A malformed document is reported as malformed, not as a bad build."""
    minimal["plan"] = "levels one through twenty"
    result = character.validate_document(minimal)
    assert not result["valid"]
    assert {i["code"] for i in result["issues"]} == {"schema"}


def test_issues_name_the_offending_field(minimal):
    minimal["plan"] = [{"level": 1, "choices": [{"slot": "nonsense", "pick": "x"}]}]
    issues = character.check_structure(minimal)
    assert any(i["path"].startswith("/plan/0/choices/0/slot") for i in issues)


# --------------------------------------------------------- semantic layer


def test_duplicate_plan_level_is_an_error(minimal):
    minimal["plan"] = [{"level": 1}, {"level": 1}]
    result = character.validate_document(minimal)
    assert not result["valid"]
    assert any(i["code"] == "duplicate_level" for i in result["issues"])


def test_out_of_order_plan_is_an_error(minimal):
    minimal["plan"] = [{"level": 2}, {"level": 1}]
    result = character.validate_document(minimal)
    assert any(i["code"] == "plan_order" for i in result["issues"])


def test_plan_gap_below_current_level_warns(minimal):
    minimal["identity"]["currentLevel"] = 3
    minimal["plan"] = [{"level": 1}, {"level": 3}]
    result = character.validate_document(minimal)
    assert result["valid"]
    assert any(i["code"] == "plan_gap" for i in result["issues"])


def test_same_source_double_boost_is_an_error(minimal):
    """The rule this project has got wrong more than once.

    An ancestry's fixed boosts and its free boost are one source, so a dwarf
    cannot spend the free boost on Constitution to reach +2 from ancestry
    alone.
    """
    minimal["plan"] = [{
        "level": 1,
        "attributeBoosts": {
            "ancestry": {"boosts": ["con", "wis"], "free": ["con"], "flaw": ["cha"]},
        },
    }]
    result = character.validate_document(minimal)
    assert not result["valid"]
    assert any(i["code"] == "same_source_boost" for i in result["issues"])


def test_background_double_boost_is_an_error(minimal):
    minimal["plan"] = [{"level": 1, "attributeBoosts": {"background": ["wis", "wis"]}}]
    result = character.validate_document(minimal)
    assert any(i["code"] == "same_source_boost" for i in result["issues"])


def test_legal_boost_spread_passes(minimal):
    minimal["plan"] = [{
        "level": 1,
        "attributeBoosts": {
            "ancestry": {"boosts": ["con", "wis"], "free": ["dex"], "flaw": ["cha"]},
            "background": ["wis", "con"],
            "class": ["wis"],
            "free": ["con", "dex", "int", "wis"],
        },
    }]
    assert character.validate_document(minimal)["valid"]


def test_free_boosts_off_a_milestone_are_an_error(minimal):
    minimal["identity"]["currentLevel"] = 4
    minimal["plan"] = [
        {"level": n} for n in (1, 2, 3)
    ] + [{"level": 4, "attributeBoosts": {"free": ["str", "dex", "con", "int"]}}]
    result = character.validate_document(minimal)
    assert any(i["code"] == "boosts_off_milestone" for i in result["issues"])


def test_creation_boosts_after_level_one_are_an_error(minimal):
    minimal["identity"]["currentLevel"] = 5
    minimal["plan"] = [
        {"level": n} for n in (1, 2, 3, 4)
    ] + [{"level": 5, "attributeBoosts": {"class": ["wis"], "free": ["con"]}}]
    result = character.validate_document(minimal)
    assert any(i["code"] == "creation_boosts_off_level_one" for i in result["issues"])


def test_override_without_a_source_is_rejected(minimal):
    """An override that does not say what granted it is indistinguishable from a typo."""
    minimal["proficiencyOverrides"] = {"religion": {"rank": "trained"}}
    assert character.check_structure(minimal)


def test_override_with_a_bad_rank_is_rejected(minimal):
    minimal["proficiencyOverrides"] = {
        "religion": {"rank": "proficient", "source": "fighter-dedication"}
    }
    assert character.check_structure(minimal)


def test_level_below_organized_play_start_is_an_error(minimal):
    minimal["identity"]["currentLevel"] = 1
    minimal["organizedPlay"] = {"startingLevel": 3}
    result = character.validate_document(minimal)
    assert any(i["code"] == "level_below_start" for i in result["issues"])


def test_chronicle_above_current_level_warns(minimal):
    """A cross-check that was impossible while the log lived in another file."""
    minimal["identity"]["currentLevel"] = 2
    minimal["organizedPlay"] = {
        "startingLevel": 1,
        "chronicles": [{
            "adventure": "8-02",
            "characterLevel": 5,
            "xp": {"start": 0, "gained": 4, "end": 4},
            "currency": {"start": 15, "gained": 10, "spent": 0, "end": 25},
        }],
    }
    result = character.validate_document(minimal)
    assert any(i["code"] == "chronicle_above_level" for i in result["issues"])


# --------------------------------------------------- slug resolution (DB)


def test_slugs_resolve_against_the_rules_database(minimal, conn):
    assert character.validate_document(minimal, conn)["valid"]


def test_unknown_slug_is_reported_with_its_path(minimal, conn):
    minimal["build"]["class"] = "animsit"
    result = character.validate_document(minimal, conn)
    assert not result["valid"]
    bad = [i for i in result["issues"] if i["code"] == "unknown_slug"]
    assert bad and bad[0]["path"] == "/build/class"


def test_right_slug_wrong_pack_says_so(minimal, conn):
    """'It exists, but not as an ancestry' is a far more useful message."""
    minimal["build"]["ancestry"] = "field-medic"
    result = character.validate_document(minimal, conn)
    message = next(i["message"] for i in result["issues"] if i["code"] == "unknown_slug")
    assert "exists, but not" in message


def test_ambiguous_feat_name_does_not_resolve(minimal, conn):
    """The bug the format exists to prevent.

    'Spirit Familiar' is two different feats -- the Animist's and the Witch's
    -- so no entry carries that slug. Under name matching this silently
    resolved to whichever pack sorted first.
    """
    minimal["plan"] = [{"level": 1, "automatic": ["spirit-familiar"]}]
    result = character.validate_document(minimal, conn)
    assert any(i["code"] == "unknown_slug" for i in result["issues"])

    minimal["plan"] = [{"level": 1, "automatic": ["spirit-familiar-animist"]}]
    assert not [
        i for i in character.validate_document(minimal, conn)["issues"]
        if i["code"] == "unknown_slug"
    ]


# ------------------------------------------- chronicle structural retrofit


def test_chronicle_schema_is_now_enforced():
    """It was served to callers but never applied to anything until now."""
    log = {"schemaVersion": 2, "character": "X", "chronicles": "not a list"}
    assert character.check_chronicle_structure(log)


def test_malformed_chronicle_stops_before_the_arithmetic(conn):
    log = {"schemaVersion": 2, "character": "X", "chronicles": "not a list"}
    result = chronicle.validate_chronicle_log(log, conn)
    assert not result["valid"]
    assert result["journal"] == []
    assert all(i["code"] == "schema" for i in result["issues"])


def test_well_formed_chronicle_still_validates(conn):
    """The retrofit must not reject logs that were fine before it existed."""
    log = {
        "schemaVersion": 2,
        "character": "Test Subject",
        "startingLevel": 1,
        "startingCurrency": 15,
        "chronicles": [{
            "adventure": "8-02",
            "characterLevel": 1,
            "xp": {"start": 0, "gained": 4, "end": 4},
            "currency": {"start": 15, "gained": 10, "spent": 0, "end": 25},
        }],
    }
    assert not character.check_chronicle_structure(log)
    assert chronicle.validate_chronicle_log(log, conn)["errors"] == 0
