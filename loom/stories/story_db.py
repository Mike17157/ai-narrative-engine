"""Per-story persistence on libSQL — one self-contained ``configs/stories/<key>.db`` per story.

A story is a natural aggregate, so its whole world lives in ONE file: meta, the EMBEDDED
characters, cast, relationships, scenes, places, arcs, locations and connections. The
relational-and-mutable parts (cast, **relationships**) are NORMALIZED rows indexed by
character, so the play runtime can pull "who relates to X" as a real query
(``relationships_for``) instead of scanning a JSON list, and a single edit is one UPSERT
instead of a whole-file rewrite. Everything else is row-per-entity JSON.

pydantic (``config/schema.Story`` + ``Character``) stays the schema/validator — this module
only maps Story<->rows. ``save_story``/``load_story`` round-trip a story; the loader and the
save paths call these instead of YAML. See [[conversational-edit-loop]] and GENESIS.md §8.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import libsql

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, json TEXT DEFAULT '{}', updated REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS characters (key TEXT PRIMARY KEY, json TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cast_members (character TEXT PRIMARY KEY, is_primary INTEGER DEFAULT 0,
                                         outfit TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS relationships (
  id TEXT PRIMARY KEY, source TEXT, target TEXT, nature TEXT DEFAULT '', dynamic TEXT DEFAULT '',
  stance TEXT DEFAULT 'neutral', note TEXT DEFAULT '', value INTEGER);
CREATE INDEX IF NOT EXISTS rel_src ON relationships(source);
CREATE INDEX IF NOT EXISTS rel_tgt ON relationships(target);
CREATE TABLE IF NOT EXISTS locations (id TEXT PRIMARY KEY, parent TEXT DEFAULT '', json TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS places (id TEXT PRIMARY KEY, json TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS scenes (id TEXT PRIMARY KEY, json TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS arcs (id TEXT PRIMARY KEY, json TEXT, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, source TEXT, target TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, json TEXT, updated REAL DEFAULT 0);
"""

# Story fields normalized into their own tables; everything else rides in the `meta` json row.
_NORMALIZED = ("cast", "relationships", "locations", "places", "scenes", "arcs", "connections")
_JSON_LISTS = ("places", "scenes", "arcs")   # row-per-entity, opaque JSON payload
_inited: set[str] = set()


def _conn(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = libsql.connect(str(p))
    if str(p) not in _inited:
        con.executescript(SCHEMA)
        con.commit()
        _inited.add(str(p))
    return con


def exists(path) -> bool:
    return Path(path).is_file()


def save_story(path, story: dict, characters: dict | None = None) -> None:
    """Write the whole story + its embedded characters to <path> — replace-all, in one transaction."""
    story = dict(story or {})
    characters = characters or {}
    con = _conn(path)
    now = time.time()
    meta = {k: v for k, v in story.items() if k not in _NORMALIZED}
    con.execute("INSERT INTO meta(k,json,updated) VALUES('story',?,?) "
                "ON CONFLICT(k) DO UPDATE SET json=excluded.json, updated=excluded.updated",
                (json.dumps(meta, ensure_ascii=False), now))
    for tbl in ("characters", "cast_members", "relationships", "locations",
                "places", "scenes", "arcs", "connections"):
        con.execute(f"DELETE FROM {tbl}")
    for i, (k, c) in enumerate(characters.items()):
        con.execute("INSERT INTO characters(key,json,ord) VALUES(?,?,?)",
                    (k, json.dumps(c, ensure_ascii=False), i))
    for i, m in enumerate(story.get("cast") or []):
        con.execute("INSERT INTO cast_members(character,is_primary,outfit,ord) VALUES(?,?,?,?)",
                    (m.get("character"), 1 if m.get("primary") else 0, m.get("outfit"), i))
    for r in story.get("relationships") or []:
        con.execute("INSERT INTO relationships(id,source,target,nature,dynamic,stance,note,value) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (r.get("id"), r.get("source"), r.get("target"), r.get("nature", ""),
                     r.get("dynamic", ""), r.get("stance", "neutral"), r.get("note", ""), r.get("value")))
    for i, l in enumerate(story.get("locations") or []):
        con.execute("INSERT INTO locations(id,parent,json,ord) VALUES(?,?,?,?)",
                    (l.get("id"), l.get("parent", ""), json.dumps(l, ensure_ascii=False), i))
    for grp in _JSON_LISTS:
        for i, it in enumerate(story.get(grp) or []):
            con.execute(f"INSERT INTO {grp}(id,json,ord) VALUES(?,?,?)",
                        (it.get("id"), json.dumps(it, ensure_ascii=False), i))
    for c in story.get("connections") or []:
        con.execute("INSERT INTO connections(id,source,target,json) VALUES(?,?,?,?)",
                    (c.get("id"), c.get("source"), c.get("target"), json.dumps(c, ensure_ascii=False)))
    con.commit()


def _rel_row(row) -> dict:
    i, s, t, n, d, st, nt, v = row
    out = {"id": i, "source": s, "target": t, "nature": n, "dynamic": d, "stance": st, "note": nt}
    if v is not None:
        out["value"] = v
    return out


def load_story(path) -> tuple[dict, dict]:
    """Assemble (story_dict, characters_dict) from <path>. story_dict is ready for Story(**…)."""
    con = _conn(path)
    row = con.execute("SELECT json FROM meta WHERE k='story'").fetchone()
    story = json.loads(row[0]) if row and row[0] else {}
    story["cast"] = [{"character": c, "primary": bool(p), **({"outfit": o} if o else {})}
                     for (c, p, o) in con.execute(
                         "SELECT character,is_primary,outfit FROM cast_members ORDER BY ord").fetchall()]
    story["relationships"] = [_rel_row(r) for r in con.execute(
        "SELECT id,source,target,nature,dynamic,stance,note,value FROM relationships").fetchall()]
    story["locations"] = [json.loads(j) for (j,) in con.execute(
        "SELECT json FROM locations ORDER BY ord").fetchall()]
    for grp in _JSON_LISTS:
        story[grp] = [json.loads(j) for (j,) in con.execute(
            f"SELECT json FROM {grp} ORDER BY ord").fetchall()]
    story["connections"] = [json.loads(j) for (j,) in con.execute(
        "SELECT json FROM connections").fetchall()]
    characters = {k: json.loads(j) for (k, j) in con.execute(
        "SELECT key,json FROM characters ORDER BY ord").fetchall()}
    return story, characters


def relationships_for(path, keys) -> list[dict]:
    """The indexed pull: every relationship touching any key in `keys` — the narrator's per-scene
    slice as a real query, not a JSON scan. This is the point of normalizing relationships."""
    keys = list(keys or [])
    if not keys:
        return []
    qs = ",".join("?" * len(keys))
    rows = _conn(path).execute(
        f"SELECT id,source,target,nature,dynamic,stance,note,value FROM relationships "
        f"WHERE source IN ({qs}) OR target IN ({qs})", keys + keys).fetchall()
    return [_rel_row(r) for r in rows]


def db_path_for(configs_dir, key: str) -> Path:
    return Path(configs_dir) / "stories" / f"{key}.db"


# ── Play sessions (Slice 2) ── A playthrough's whole record (messages + leveled State doc +
# world_state + draft + attached lorebooks) rides as ONE JSON blob per sid, in the OWNING story's DB.
# Mutated wholesale each turn, never cross-queried, so a blob is right (cf. normalized relationships).

def save_session(path, sid: str, payload: dict) -> None:
    con = _conn(path)
    con.execute("INSERT INTO sessions(sid,json,updated) VALUES(?,?,?) "
                "ON CONFLICT(sid) DO UPDATE SET json=excluded.json, updated=excluded.updated",
                (sid, json.dumps(payload, ensure_ascii=False), time.time()))
    con.commit()


def load_session(path, sid: str) -> dict | None:
    if not exists(path):
        return None
    row = _conn(path).execute("SELECT json FROM sessions WHERE sid=?", (sid,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def delete_session(path, sid: str) -> None:
    if not exists(path):
        return
    con = _conn(path)
    con.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    con.commit()


def character_keys(path) -> list[str]:
    """The keys of the characters embedded in this story DB (for ownership routing)."""
    if not exists(path):
        return []
    return [k for (k,) in _conn(path).execute("SELECT key FROM characters").fetchall()]


def get_character(path, key: str) -> dict | None:
    row = _conn(path).execute("SELECT json FROM characters WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def delete_character(path, key: str) -> None:
    if exists(path):
        con = _conn(path)
        con.execute("DELETE FROM characters WHERE key=?", (key,))
        con.commit()


def upsert_character(path, key: str, char: dict) -> None:
    """Write ONE embedded character (the story DB owns its cast). New keys append at the end."""
    con = _conn(path)
    n = (con.execute("SELECT COUNT(*) FROM characters").fetchone() or [0])[0]
    con.execute("INSERT INTO characters(key,json,ord) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET json=excluded.json",
                (key, json.dumps(char, ensure_ascii=False), n))
    con.commit()


def patch_story(path, fields: dict) -> dict:
    """Apply a top-level field patch to a DB-backed story (the update_story_fields equivalent): load
    the story dict, merge `fields`, save it back. Returns the merged story dict (for validation)."""
    story, chars = load_story(path)
    story.update(fields or {})
    save_story(path, story, chars)
    return story


def delete_db(path) -> None:
    p = Path(path)
    _inited.discard(str(p))
    if p.is_file():
        p.unlink()


def migrate_from_yaml(configs_dir) -> list[str]:
    """One-time: convert every top-level ``configs/stories/<key>.yaml`` into ``<key>.db``, EMBEDDING
    each cast member's ``configs/characters/<key>.yaml`` record. Non-destructive — the .yaml is left
    in place as a backup; the loader prefers the .db once present. Returns the migrated story keys."""
    import yaml

    configs = Path(configs_dir)
    sdir, cdir = configs / "stories", configs / "characters"
    done: list[str] = []
    for ypath in sorted(sdir.glob("*.yaml")):
        story = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
        chars: dict = {}
        for m in story.get("cast") or []:
            ck = (m or {}).get("character")
            cpath = cdir / f"{ck}.yaml" if ck else None
            if cpath and cpath.is_file():
                chars[ck] = yaml.safe_load(cpath.read_text(encoding="utf-8")) or {}
        save_story(sdir / f"{ypath.stem}.db", story, chars)
        done.append(ypath.stem)
    return done


if __name__ == "__main__":   # round-trip self-check (ponytail: one runnable check)
    import tempfile

    d = tempfile.mkdtemp()
    p = Path(d) / "t.db"
    story = {
        "name": "Test", "type": "novel", "premise": "a premise", "tone": "warm",
        "themes": ["a", "b"], "intended_ending": "they reconcile",
        "cast": [{"character": "leo", "primary": True}, {"character": "mara", "primary": False, "outfit": "casual"}],
        "relationships": [{"id": "r1", "source": "leo", "target": "mara", "nature": "rival",
                           "dynamic": "old grudge", "stance": "strained", "note": "leo owes mara"}],
        "locations": [{"id": "l1", "name": "Library", "parent": ""}],
        "scenes": [{"id": "s1", "name": "Opening"}], "places": [], "arcs": [{"id": "a1", "name": "Arc 1"}],
        "connections": [{"id": "c1", "source": "l1", "target": "l1", "label": "loop"}],
    }
    chars = {"leo": {"name": "Leo", "system": "shy"}, "mara": {"name": "Mara", "system": "sharp"}}
    save_story(p, story, chars)
    s2, c2 = load_story(p)
    assert s2["name"] == "Test" and s2["themes"] == ["a", "b"], s2
    assert s2["cast"][0] == {"character": "leo", "primary": True}, s2["cast"]
    assert s2["cast"][1]["outfit"] == "casual", s2["cast"]
    assert s2["relationships"][0]["nature"] == "rival" and s2["relationships"][0]["stance"] == "strained"
    assert s2["scenes"][0]["name"] == "Opening" and s2["arcs"][0]["id"] == "a1"
    assert c2["leo"]["name"] == "Leo" and c2["mara"]["system"] == "sharp"
    # the indexed pull
    rel = relationships_for(p, {"mara"})
    assert len(rel) == 1 and rel[0]["source"] == "leo", rel
    assert relationships_for(p, {"nobody"}) == []
    # idempotent re-save (replace-all) doesn't duplicate
    save_story(p, story, chars)
    s3, _ = load_story(p)
    assert len(s3["relationships"]) == 1 and len(s3["cast"]) == 2
    # play sessions live in the same DB
    save_session(p, "play-x", {"messages": [{"role": "user", "content": "hi"}], "world_state": {"k": 1}})
    sess = load_session(p, "play-x")
    assert sess and sess["messages"][0]["content"] == "hi" and sess["world_state"]["k"] == 1, sess
    save_story(p, story, chars)            # re-saving the story must NOT wipe sessions
    assert load_session(p, "play-x") is not None, "session lost on story re-save"
    delete_session(p, "play-x")
    assert load_session(p, "play-x") is None
    print("ok — story_db round-trip + indexed pull + idempotent re-save + sessions")
