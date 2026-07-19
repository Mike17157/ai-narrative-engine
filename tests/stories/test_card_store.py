"""Global character + persona cards in the relational store (configs/stories.db).

card_store.py persists the global character library and the personas as JSON-payload
rows (global_characters / personas tables) — binary assets (PNGs, portraits/) stay
files. Legacy per-card YAMLs (configs/characters/*.yaml, configs/personas/*.yaml) are
folded into the tables on first touch and renamed .yaml.migrated; corrupt files stay
put for manual inspection.
"""
from __future__ import annotations

import yaml

from loom.config.schema import Character, Persona
from loom.server.services import card_store as cs


# ── Round-trips ──────────────────────────────────────────────────────────────

def test_character_roundtrip(tmp_path):
    cs.upsert_character(tmp_path, "mara", {"name": "Mara", "system": "archivist",
                                           "fields": {"role": "lead"}})
    cards = cs.load_characters(tmp_path)
    assert cards == {"mara": {"name": "Mara", "system": "archivist",
                              "fields": {"role": "lead"}}}
    assert Character(**cards["mara"]).name == "Mara"          # payload still validates


def test_persona_roundtrip(tmp_path):
    cs.upsert_persona(tmp_path, "you", {"name": "You", "description": "the user"})
    personas = cs.load_personas(tmp_path)
    assert personas["you"] == {"name": "You", "description": "the user"}
    assert Persona(**personas["you"]).name == "You"


def test_upsert_replaces_in_place(tmp_path):
    cs.upsert_character(tmp_path, "mara", {"name": "Mara"})
    cs.upsert_character(tmp_path, "mara", {"name": "Mara Voss", "system": "x"})
    cards = cs.load_characters(tmp_path)
    assert list(cards) == ["mara"]                            # one row, not two
    assert cards["mara"]["name"] == "Mara Voss"


def test_loads_are_empty_without_anything(tmp_path):
    assert cs.load_characters(tmp_path) == {}
    assert cs.load_personas(tmp_path) == {}


# ── Deletes ────────────────────────────────────────────────────────────────────

def test_delete_removes_the_row(tmp_path):
    cs.upsert_character(tmp_path, "mara", {"name": "Mara"})
    cs.upsert_persona(tmp_path, "you", {"name": "You"})
    cs.delete_character(tmp_path, "mara")
    cs.delete_persona(tmp_path, "you")
    assert cs.load_characters(tmp_path) == {}
    assert cs.load_personas(tmp_path) == {}
    cs.delete_character(tmp_path, "mara")                    # idempotent


# ── Legacy YAML → DB migration ─────────────────────────────────────────────────

def test_legacy_character_yaml_folded_into_db(tmp_path):
    d = tmp_path / "configs" / "characters"
    d.mkdir(parents=True)
    (d / "mara.yaml").write_text(yaml.safe_dump(
        {"name": "Mara", "system": "archivist"}), encoding="utf-8")
    png = d / "mara.png"
    png.write_bytes(b"\x89PNG fake")
    ref = d / "mara.ref.png"
    ref.write_bytes(b"\x89PNG fake ref")
    portraits = d / "portraits" / "mara"
    portraits.mkdir(parents=True)
    (portraits / "manifest.json").write_text("{}", encoding="utf-8")

    cards = cs.load_characters(tmp_path)                     # first touch triggers migration
    assert cards["mara"] == {"name": "Mara", "system": "archivist"}
    assert Character(**cards["mara"]).name == "Mara"
    assert not (d / "mara.yaml").exists()                    # folded away…
    assert (d / "mara.yaml.migrated").exists()               # …but reversible
    assert png.exists() and ref.exists()                     # binary assets never folded
    assert (portraits / "manifest.json").exists()            # portraits/ untouched


def test_legacy_persona_yaml_folded_into_db(tmp_path):
    d = tmp_path / "configs" / "personas"
    d.mkdir(parents=True)
    (d / "you.yaml").write_text(yaml.safe_dump({"name": "You", "description": "hi"}),
                                encoding="utf-8")
    (d / "you.png").write_bytes(b"\x89PNG fake")
    personas = cs.load_personas(tmp_path)
    assert personas["you"] == {"name": "You", "description": "hi"}
    assert (d / "you.yaml.migrated").exists()
    assert (d / "you.png").exists()


def test_corrupt_yaml_stays_put(tmp_path):
    d = tmp_path / "configs" / "characters"
    d.mkdir(parents=True)
    (d / "bad.yaml").write_text("{not: [valid", encoding="utf-8")
    assert "bad" not in cs.load_characters(tmp_path)
    assert (d / "bad.yaml").exists()                         # left for manual inspection
    assert not (d / "bad.yaml.migrated").exists()


def test_nondict_yaml_stays_put(tmp_path):
    d = tmp_path / "configs" / "personas"
    d.mkdir(parents=True)
    (d / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    assert cs.load_personas(tmp_path) == {}
    assert (d / "list.yaml").exists()


def test_db_wins_over_a_straggler_yaml(tmp_path):
    """A YAML left behind (e.g. restored from backup) re-folds on next touch; the
    row it creates is just an upsert over the existing one."""
    cs.upsert_character(tmp_path, "mara", {"name": "Mara DB"})
    d = tmp_path / "configs" / "characters"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mara.yaml").write_text(yaml.safe_dump({"name": "Mara YAML"}), encoding="utf-8")
    # Same root, NEW connection (fresh process simulation): the guard is per-path,
    # so reset it to force the migration pass again.
    cs._inited.discard(str(tmp_path / "configs" / "stories.db"))
    assert cs.load_characters(tmp_path)["mara"]["name"] == "Mara YAML"
    assert (d / "mara.yaml.migrated").exists()


# ── Story partitioning (copackaging) ───────────────────────────────────────────

def test_generated_card_carries_story_key(tmp_path):
    cs.upsert_character(tmp_path, "rina_gen",
                        {"name": "Rina", "fields": {"story": "school", "_generated": True}})
    cs.upsert_character(tmp_path, "mara", {"name": "Mara"})          # shared library card
    pool = cs.characters_for_story(tmp_path, "school")
    assert list(pool) == ["rina_gen"] and pool["rina_gen"]["name"] == "Rina"
    assert cs.characters_for_story(tmp_path, "other") == {}


def test_backfill_stamps_pre_partition_rows(tmp_path):
    cs.upsert_character(tmp_path, "gen1",
                        {"name": "Gen", "fields": {"story": "school", "_generated": True}})
    con = cs._conn(tmp_path)                       # simulate a row written before the column
    con.execute("UPDATE global_characters SET story_key='' WHERE key='gen1'")
    con.commit()
    cs._inited.discard(str(tmp_path / "configs" / "stories.db"))     # force re-init
    assert "gen1" in cs.characters_for_story(tmp_path, "school")


# ── Loader integration ─────────────────────────────────────────────────────────

def test_load_settings_reads_cards_from_db(tmp_path):
    from loom.config import load_settings
    cfg = tmp_path / "configs"
    (cfg / "characters").mkdir(parents=True)
    (cfg / "models.yaml").write_text("models: {}\n", encoding="utf-8")
    (cfg / "characters" / "mara.yaml").write_text(
        yaml.safe_dump({"name": "Mara", "system": "s"}), encoding="utf-8")
    cs.upsert_persona(tmp_path, "you", {"name": "You"})

    settings = load_settings(tmp_path)
    assert settings.characters["mara"].name == "Mara"
    assert settings.characters["mara"].system == "s"
    assert settings.personas["you"].name == "You"
    assert (cfg / "characters" / "mara.yaml.migrated").exists()
