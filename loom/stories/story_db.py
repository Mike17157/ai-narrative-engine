"""Per-story persistence — one self-contained ``configs/stories/<key>.json`` per story.

A story is a natural aggregate, so its whole world lives in ONE file: the full Story
dict plus its EMBEDDED characters (the file is the whole self-contained story). Plain
JSON: load = ``json.load``, save = atomic write of the whole document. No relational
schema, no per-table rewrite — the editor (hashline ops) and the loader both read the
same reassembled dict, so storage now matches what every consumer actually reads.

pydantic (``config/schema.Story`` + ``Character``) stays the schema/validator — this
module is pure IO. Callers validate BEFORE calling ``save_story`` (see context.py).

Sessions (playthrough state) NO LONGER live here — they moved to their own per-sid
files in ``configs/story_sessions/<sid>.json`` (see server/services/story_sessions.py).
The ``save_session``/``load_session``/``delete_session`` functions are GONE.

On-disk shape::

    {"story": { <full Story dict> },
     "characters": { "<key>": { <Character dict> }, ... }}
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _doc(path) -> dict:
    """Load the raw {story, characters} document; defensive against partial shapes."""
    p = Path(path)
    if not p.is_file():
        return {"story": {}, "characters": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupt file surfaces as empty, callers validate
        return {"story": {}, "characters": {}}
    if not isinstance(doc, dict):
        return {"story": {}, "characters": {}}
    doc.setdefault("story", {})
    doc.setdefault("characters", {})
    return doc


def _atomic_write(path, doc: dict) -> None:
    """Write JSON atomically: temp file + os.replace, so a crash mid-write can't
    corrupt the story (the durability SQLite gave us, preserved)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def exists(path) -> bool:
    return Path(path).is_file()


def save_story(path, story: dict, characters: dict | None = None) -> None:
    """Write the whole story + its embedded characters to <path> as one JSON document."""
    _atomic_write(path, {"story": dict(story or {}), "characters": dict(characters or {})})


def load_story(path) -> tuple[dict, dict]:
    """Assemble (story_dict, characters_dict) from <path>. story_dict is ready for Story(**…)."""
    doc = _doc(path)
    return doc["story"], doc["characters"]


def relationships_for(path, keys) -> list[dict]:
    """Every relationship touching any key in `keys` — the narrator's per-scene slice.
    A plain in-memory scan (the SQL index this replaced served a 0-to-tiny-row table;
    keys is a scene's cast, ≤8; not a hot path)."""
    keys = set(keys or [])
    if not keys:
        return []
    story, _ = load_story(path)
    return [r for r in (story.get("relationships") or [])
            if r.get("source") in keys or r.get("target") in keys]


def character_keys(path) -> list[str]:
    """The keys of the characters embedded in this story file (for ownership routing)."""
    return list(_doc(path)["characters"].keys())


def get_character(path, key: str) -> dict | None:
    return _doc(path)["characters"].get(key)


def upsert_character(path, key: str, char: dict) -> None:
    """Write ONE embedded character (the story file owns its cast). Read-modify-write
    on the whole file — characters are small and this is a cold path."""
    doc = _doc(path)
    doc["characters"][key] = char
    _atomic_write(path, doc)


def delete_character(path, key: str) -> None:
    doc = _doc(path)
    if doc["characters"].pop(key, None) is not None:
        _atomic_write(path, doc)


def delete_db(path) -> None:
    """Delete the story file (the router's delete_story + genesis rollback call this)."""
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


# ── Play sessions MOVED to configs/story_sessions/<sid>.json ──────────────────
# The save_session / load_session / delete_session functions that used to live here
# (storing session blobs inside the story DB) are REMOVED. Session storage is now
# exclusively in loom/server/services/story_sessions.py as standalone JSON files.


if __name__ == "__main__":   # round-trip self-check (one runnable check)
    import tempfile
    d = tempfile.mkdtemp()
    p = Path(d) / "t.json"

    story = {
        "name": "Test", "type": "novel", "premise": "a premise", "tone": "warm",
        "themes": ["a", "b"], "intended_ending": "they reconcile",
        "cast": [{"character": "leo", "primary": True},
                 {"character": "mara", "primary": False, "outfit": "casual", "home": "l1"}],
        "relationships": [{"id": "r1", "source": "leo", "target": "mara", "nature": "rival",
                           "dynamic": "old grudge", "stance": "strained", "note": "leo owes mara",
                           "target_stance": "warm", "target_dynamic": "teases him about it",
                           "potential": "mara forgave the debt years ago and never said",
                           "trajectory": "from grudge to shame to repair"}],
        "locations": [{"id": "l1", "name": "Library", "parent": ""}],
        "scenes": [{"id": "s1", "name": "Opening"}], "places": [], "arcs": [{"id": "a1", "name": "Arc 1"}],
        "connections": [{"id": "c1", "source": "l1", "target": "l1", "label": "loop"}],
    }
    chars = {"leo": {"name": "Leo", "system": "shy"}, "mara": {"name": "Mara", "system": "sharp"}}

    save_story(p, story, chars)
    s2, c2 = load_story(p)
    assert s2["name"] == "Test" and s2["themes"] == ["a", "b"], s2
    assert s2["cast"][0] == {"character": "leo", "primary": True}, s2["cast"]
    assert s2["cast"][1]["outfit"] == "casual" and s2["cast"][1]["home"] == "l1", s2["cast"]
    # DEPTH fields survive the round-trip
    r0 = s2["relationships"][0]
    assert r0["nature"] == "rival" and r0["stance"] == "strained"
    assert r0["target_stance"] == "warm" and r0["potential"].startswith("mara forgave"), r0
    assert r0["trajectory"] == "from grudge to shame to repair", r0
    assert s2["scenes"][0]["name"] == "Opening" and s2["arcs"][0]["id"] == "a1"
    assert c2["leo"]["name"] == "Leo" and c2["mara"]["system"] == "sharp", c2

    # the indexed pull (now an in-memory scan)
    rel = relationships_for(p, {"mara"})
    assert len(rel) == 1 and rel[0]["source"] == "leo", rel
    assert relationships_for(p, {"nobody"}) == []

    # idempotent re-save (whole-file replace) doesn't duplicate
    save_story(p, story, chars)
    s3, _ = load_story(p)
    assert len(s3["relationships"]) == 1 and len(s3["cast"]) == 2

    # character CRUD on the file
    assert character_keys(p) == ["leo", "mara"]
    assert get_character(p, "leo")["name"] == "Leo"
    upsert_character(p, "leo", {"name": "Leo2", "system": "brave"})
    assert get_character(p, "leo")["name"] == "Leo2"
    delete_character(p, "mara")
    assert get_character(p, "mara") is None and character_keys(p) == ["leo"]
    # story survived the character edits
    s4, _ = load_story(p)
    assert s4["name"] == "Test" and s4["cast"][0]["character"] == "leo"

    # exists + delete
    assert exists(p)
    delete_db(p)
    assert not exists(p)
    delete_db(p)   # idempotent

    print("ok — story_db JSON: round-trip + relationships scan + character CRUD + idempotent re-save")
