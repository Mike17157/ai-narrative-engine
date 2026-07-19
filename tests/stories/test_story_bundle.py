"""Story bundle round-trips: export packs the aggregate + copackaged cards + personas +
sessions (beats, play cards, history) + image assets; import restores them into a fresh
root verbatim, and into the SAME root with every collision remapped rather than clobbered."""
from __future__ import annotations

from pathlib import Path

import pytest

from loom.server.services import card_store, story_bundle, story_sessions, story_store

PNG = b"\x89PNG\r\n\x1a\nfake"


def _root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "configs").mkdir(parents=True)
    return root


def _seed(root: Path, key: str = "harbour") -> None:
    story_store.save_story(root, key, {
        "name": "Harbour",
        "type": "vn",
        "premise": "A ferry town keeps its dead.",
        "world": {"setting": "Grey water, low sky."},
        "locations": [{"id": "dock", "name": "The Dock", "description": "Rope and gulls.",
                       "background": f"/api/stories/{key}/bg/dock.png"}],
        "start": "dock",
        "background": f"/api/stories/{key}/bg/_cover.png",
        "default_personas": ["you"],
        "fields": {"status": "playing"},
    }, {
        "mara": {"name": "Mara", "system": "You are Mara, the harbormaster.",
                 "image": {"avatar": f"/api/stories/{key}/chars/mara.png"}},
    })
    card_store.upsert_character(root, "npc_gull", {"name": "Gull", "fields": {"story": key}})
    card_store.upsert_persona(root, "you", {"name": "You", "description": "A traveller."})
    story_sessions.save_session(root, "sess1", {
        "character": "mara",
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "rain again"}],
        "prologue": {"text": "The ferry horns."},
    }, story_key=key)
    story_sessions.append_beats(root, "sess1", ["The ferry horns.", "Mara counts the rope."],
                                step=3, story_key=key)
    con = story_sessions._conn(root)
    con.execute("INSERT INTO play_cards (story_key, session_id, kind, card_key, foundation,"
                " current, updated) VALUES (?,?,?,?,?,?,?)",
                (key, "sess1", "character", "mara", "{}", '{"mood": "wary"}', 1.0))
    con.execute("INSERT INTO card_history (story_key, session_id, kind, card_key, seq, turn,"
                " event, evidence, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (key, "sess1", "character", "mara", 0, 1, "mood shifted", '["turn 1"]', 1.0))
    con.commit()
    bg = root / "configs" / "stories" / key / "bg"
    bg.mkdir(parents=True)
    (bg / "dock.png").write_bytes(PNG + b"bg")
    (bg / "_cover.png").write_bytes(PNG + b"cover")
    chars = root / "configs" / "stories" / key / "chars"
    chars.mkdir(parents=True)
    (chars / "mara.png").write_bytes(PNG + b"avatar")
    (root / "configs" / "characters").mkdir(exist_ok=True)
    (root / "configs" / "characters" / "npc_gull.png").write_bytes(PNG + b"gull")
    (root / "configs" / "personas").mkdir(exist_ok=True)
    (root / "configs" / "personas" / "you.png").write_bytes(PNG + b"you")


def test_export_import_round_trip_into_fresh_root(tmp_path: Path):
    src = _root(tmp_path, "src")
    _seed(src)
    bundle = story_bundle.export_story(src, "harbour")
    assert bundle["format"] == story_bundle.FORMAT
    assert bundle["key"] == "harbour"
    assert set(bundle["assets"]) == {
        "bg/dock.png", "bg/_cover.png", "chars/mara.png",
        "shared/characters/npc_gull.png", "shared/personas/you.png"}
    assert len(bundle["sessions"]) == 1
    assert len(bundle["sessions"][0]["beats"]) == 2

    dst = _root(tmp_path, "dst")
    summary = story_bundle.import_story(dst, bundle)
    assert summary["key"] == "harbour"           # no collision in a fresh root
    assert summary["renamed_from"] == ""
    assert summary["sessions"] == 1 and summary["beats"] == 2
    assert summary["play_cards"] == 1 and summary["card_history"] == 1
    assert summary["assets"] == 5

    story, characters = story_store.load_story(dst, "harbour")
    assert story["name"] == "Harbour"
    assert story["premise"] == "A ferry town keeps its dead."
    assert story["default_personas"] == ["you"]
    assert story["locations"][0]["background"] == "/api/stories/harbour/bg/dock.png"
    assert characters["mara"]["system"].startswith("You are Mara")

    # copackaged library card + persona made the trip, provenance intact
    lib = card_store.characters_for_story(dst, "harbour")
    assert lib["npc_gull"]["name"] == "Gull"
    assert card_store.load_personas(dst)["you"]["description"] == "A traveller."

    # session + chronicle + play-through state, verbatim
    sess = story_sessions.load_session(dst, "sess1")
    assert sess["character"] == "mara"
    assert sess["messages"][1]["content"] == "rain again"
    assert sess["prologue"] == {"text": "The ferry horns."}
    beats = story_sessions.beats_for(dst, "sess1", limit=10)
    assert [b["text"] for b in beats] == ["The ferry horns.", "Mara counts the rope."]
    assert beats[0]["step"] == 3
    play = story_store.get_play_cards(dst, "harbour", "sess1")
    assert play["character"]["mara"]["current"]["mood"] == "wary"
    hist = story_store.get_card_history(dst, "harbour", "sess1", "character", "mara")
    assert hist[0]["event"] == "mood shifted"

    # assets landed byte-identical, in the right roots
    assert (dst / "configs/stories/harbour/bg/dock.png").read_bytes() == PNG + b"bg"
    assert (dst / "configs/stories/harbour/chars/mara.png").read_bytes() == PNG + b"avatar"
    assert (dst / "configs/characters/npc_gull.png").read_bytes() == PNG + b"gull"
    assert (dst / "configs/personas/you.png").read_bytes() == PNG + b"you"


def test_import_into_same_root_remaps_every_collision(tmp_path: Path):
    src = _root(tmp_path, "src")
    _seed(src)
    bundle = story_bundle.export_story(src, "harbour")

    summary = story_bundle.import_story(src, bundle)
    assert summary["key"] == "harbour_2"
    assert summary["renamed_from"] == "harbour"
    assert summary["remapped"]["sessions"] == {"sess1": "sess1_2"}

    # the original is untouched
    orig, _ = story_store.load_story(src, "harbour")
    assert orig["locations"][0]["background"] == "/api/stories/harbour/bg/dock.png"
    assert story_sessions.load_session(src, "sess1") is not None

    # the copy is fully rewired to its new key
    copy, copy_chars = story_store.load_story(src, "harbour_2")
    assert copy["name"] == "Harbour"
    assert copy["locations"][0]["background"] == "/api/stories/harbour_2/bg/dock.png"
    assert copy["background"] == "/api/stories/harbour_2/bg/_cover.png"
    assert copy_chars["mara"]["image"]["avatar"] == "/api/stories/harbour_2/chars/mara.png"
    assert (src / "configs/stories/harbour_2/bg/dock.png").read_bytes() == PNG + b"bg"

    dup = story_sessions.load_session(src, "sess1_2")
    assert dup is not None
    assert dup["messages"][0]["content"] == "hi"
    assert [b["text"] for b in story_sessions.beats_for(src, "sess1_2", limit=10)] == [
        "The ferry horns.", "Mara counts the rope."]
    play = story_store.get_play_cards(src, "harbour_2", "sess1_2")
    assert play["character"]["mara"]["current"]["mood"] == "wary"

    # identical persona/library card were REUSED, not duplicated
    assert summary["remapped"]["personas"] == {}
    assert summary["remapped"]["cards"] == {}
    assert card_store.load_personas(src)["you"]["description"] == "A traveller."


def test_import_rejects_non_bundles(tmp_path: Path):
    root = _root(tmp_path, "src")
    with pytest.raises(ValueError):
        story_bundle.import_story(root, {"not": "a bundle"})
    with pytest.raises(ValueError):
        story_bundle.import_story(root, {"format": story_bundle.FORMAT, "version": 99})


def test_export_missing_story_raises(tmp_path: Path):
    root = _root(tmp_path, "src")
    with pytest.raises(KeyError):
        story_bundle.export_story(root, "nope")
