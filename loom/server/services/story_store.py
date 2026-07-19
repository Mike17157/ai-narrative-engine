"""Relational story store — decomposed libSQL tables, the source of truth for story data.

Replaces the per-story JSON aggregate (``configs/stories/<key>/story.json``). A story's
queryable entities (characters, locations, relationships, cast, conditions, arcs, chapters,
connections) become real tables with columns; its slow-changing, read-whole frame
(``world``, ``storyboard``, ``premise_parts``, arc VN-graph nodes…) stays in JSON columns.

Conventions mirror ``lorebook_store.py`` exactly: local libSQL file at
``configs/stories.db`` (optional Turso embedded-replica via env), a module-level ``_SCHEMA``
string, an ``_inited`` guard, additive ``ALTER`` migrations, and ``_conn(root)`` opening a
fresh connection each call.

ROUND-TRIP CONTRACT (the load-bearing invariant): ``load_story(key)`` must reconstruct a dict
byte-identical to what ``save_story`` was given (for the JSON-column fields, canonical
``json.dumps(sort_keys=True, ensure_ascii=False)``; for the column fields, exact values). The
``Story`` pydantic model and ``anchors.node_hash`` both depend on this — if reconstruction
reorders keys or drifts a value, validators and hash-anchored edits break. The ``__main__``
self-test locks it.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import libsql

# ── Connection (mirrors lorebook_store) ───────────────────────────────────────
_inited: set[str] = set()


def _db_path(root: Path) -> Path:
    return root / "configs" / "stories.db"


def _connect(root: Path):
    """Open a libSQL connection. Turso cloud (embedded replica) if env is set, else local."""
    local = str(_db_path(root))
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    url = os.environ.get("TURSO_DATABASE_URL")
    if url:
        con = libsql.connect(local, sync_url=url, auth_token=os.environ.get("TURSO_AUTH_TOKEN", ""))
        try:
            con.sync()
        except Exception:  # noqa: BLE001
            pass
    else:
        con = libsql.connect(local)
    # WAL + busy_timeout so two concurrent browser windows queue instead of hitting
    # "database is locked" on Windows' default rollback-journal exclusive-lock behavior.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _conn(root: Path):
    con = _connect(root)
    key = str(_db_path(root))
    if key not in _inited:
        con.executescript(_SCHEMA)
        _migrate_schema(con)
        con.commit()
        _inited.add(key)
    return con


# ── JSON (de)serialization helpers ────────────────────────────────────────────
def _jdumps(v) -> str:
    """Canonical JSON for a column: sorted keys, non-ASCII preserved. Stable across runs so
    node_hash stays byte-identical after a round-trip."""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _jloads(s: str | None, default):
    if not s:
        return default() if callable(default) else default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default() if callable(default) else default


def _dedup_by_id(items: list) -> list:
    """Drop duplicate-id items, keeping the LAST occurrence (last-write-wins). Prevents a UNIQUE
    constraint violation when an input list re-adds an existing id (e.g. an agent tool-call that
    re-emits a relationship already in the story). Preserves original order of first appearance."""
    seen: set = set()
    out: list = []
    for it in reversed(items or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get("id", ""))
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return list(reversed(out))


def _dedup_by_key(items: list, key: str) -> list:
    """Same as _dedup_by_id but for lists keyed by a different field (e.g. cast by 'character')."""
    seen: set = set()
    out: list = []
    for it in reversed(items or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get(key, ""))
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return list(reversed(out))


# ── Schema ────────────────────────────────────────────────────────────────────
# Design: every table is partitioned by `story_key` (the story's slug). The queryable
# entities get real columns; nested/read-whole structures get JSON columns. List order is
# preserved by `ord` where it matters (locations, arcs, chapters, cast). FKs are enforced
# in application code (the Story validator is the gate), not as SQL constraints — libsql
# embedded mode has uneven FK support and the validators already check every ref.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    key              TEXT PRIMARY KEY,
    name             TEXT NOT NULL DEFAULT '',
    type             TEXT NOT NULL DEFAULT 'novel',
    premise          TEXT NOT NULL DEFAULT '',
    tone             TEXT NOT NULL DEFAULT '',
    themes           TEXT NOT NULL DEFAULT '[]',        -- JSON array
    art_style        TEXT NOT NULL DEFAULT '',
    premise_parts    TEXT NOT NULL DEFAULT '{}',        -- JSON object
    world            TEXT NOT NULL DEFAULT '{}',        -- JSON object
    time_system      TEXT NOT NULL DEFAULT '{}',        -- JSON object (entity activity windows)
    storyboard       TEXT NOT NULL DEFAULT '{}',        -- JSON object (Storyboard)
    lorebook         TEXT NOT NULL DEFAULT '{}',        -- JSON object
    start            TEXT,
    background       TEXT,
    fields           TEXT NOT NULL DEFAULT '{}',        -- JSON object
    start_scene      TEXT NOT NULL DEFAULT '',
    default_personas TEXT NOT NULL DEFAULT '[]',        -- JSON array
    recent_window    INTEGER NOT NULL DEFAULT 8,
    updated          REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS characters (
    story_key  TEXT NOT NULL,
    char_key   TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    system     TEXT NOT NULL DEFAULT '',
    greeting   TEXT,
    fields     TEXT NOT NULL DEFAULT '{}',      -- JSON object
    image      TEXT NOT NULL DEFAULT '{}',      -- JSON object (CharacterImage)
    playable   INTEGER NOT NULL DEFAULT 0,
    ord        INTEGER NOT NULL DEFAULT 0,
    updated    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, char_key)
);
CREATE INDEX IF NOT EXISTS idx_characters_story ON characters(story_key);

CREATE TABLE IF NOT EXISTS cast_members (
    story_key  TEXT NOT NULL,
    character  TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    outfit     TEXT,
    home       TEXT NOT NULL DEFAULT '',
    ord        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, character)
);

CREATE TABLE IF NOT EXISTS locations (
    story_key        TEXT NOT NULL,
    id               TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    history          TEXT NOT NULL DEFAULT '',
    background_prompt TEXT NOT NULL DEFAULT '',
    background       TEXT,
    parent           TEXT NOT NULL DEFAULT '',
    scenes           TEXT NOT NULL DEFAULT '[]',   -- JSON array of Scene
    ord              INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS relationships (
    story_key      TEXT NOT NULL,
    id             TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    target         TEXT NOT NULL DEFAULT '',
    nature         TEXT NOT NULL DEFAULT '',
    dynamic        TEXT NOT NULL DEFAULT '',
    stance         TEXT NOT NULL DEFAULT 'neutral',
    target_dynamic TEXT NOT NULL DEFAULT '',
    target_stance  TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    potential      TEXT NOT NULL DEFAULT '',
    trajectory     TEXT NOT NULL DEFAULT '',
    ord            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS conditions (
    story_key   TEXT NOT NULL,
    id          TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    effect      TEXT NOT NULL DEFAULT '',
    ord         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS arcs (
    story_key        TEXT NOT NULL,
    id               TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    mini_ending      TEXT NOT NULL DEFAULT '',
    dramatic_function TEXT NOT NULL DEFAULT '',
    themes           TEXT NOT NULL DEFAULT '[]',      -- JSON array
    premise          TEXT NOT NULL DEFAULT '',
    cast             TEXT NOT NULL DEFAULT '[]',      -- JSON array
    owner            TEXT NOT NULL DEFAULT '',
    pressures        TEXT NOT NULL DEFAULT '[]',      -- JSON array
    conditions       TEXT NOT NULL DEFAULT '[]',      -- JSON array
    nodes            TEXT NOT NULL DEFAULT '{}',      -- JSON object (ArcBeat map)
    start            TEXT NOT NULL DEFAULT '',
    timelines        TEXT NOT NULL DEFAULT '[]',      -- JSON array
    transitions      TEXT NOT NULL DEFAULT '[]',      -- JSON array
    divergence_axis  TEXT NOT NULL DEFAULT '',
    ord              INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS chapters (
    story_key TEXT NOT NULL,
    id        TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    purpose   TEXT NOT NULL DEFAULT '',
    pov       TEXT NOT NULL DEFAULT '',
    setting   TEXT NOT NULL DEFAULT '',
    beats     TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status    TEXT NOT NULL DEFAULT '',
    draft     TEXT NOT NULL DEFAULT '',
    recap     TEXT NOT NULL DEFAULT '',
    ord       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS scene_harnesses (
    story_key TEXT NOT NULL,
    id        TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    goal      TEXT NOT NULL DEFAULT '',
    setting   TEXT NOT NULL DEFAULT '',
    tone      TEXT NOT NULL DEFAULT '',
    on_stage  TEXT NOT NULL DEFAULT '[]',   -- JSON array of OnStage
    triggers  TEXT NOT NULL DEFAULT '[]',   -- JSON array of DivergenceTrigger
    ord       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS connections (
    story_key TEXT NOT NULL,
    id        TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT '',
    target    TEXT NOT NULL DEFAULT '',
    label     TEXT NOT NULL DEFAULT '',
    ord       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

CREATE TABLE IF NOT EXISTS features (
    story_key TEXT NOT NULL,
    id        TEXT NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    initial   REAL NOT NULL DEFAULT 0,
    ord       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, id)
);

-- Per-playthrough living cards. These never rewrite the authored story tables above:
-- one row is the latest mutable state, and card_history is the evidence ledger behind it.
CREATE TABLE IF NOT EXISTS play_cards (
    story_key  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- character | arc | story
    card_key   TEXT NOT NULL,
    foundation TEXT NOT NULL DEFAULT '{}', -- immutable baseline copied on first sight
    current    TEXT NOT NULL DEFAULT '{}', -- model-mutated present state
    updated    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, session_id, kind, card_key)
);
CREATE INDEX IF NOT EXISTS idx_play_cards_session ON play_cards(story_key, session_id, kind);

CREATE TABLE IF NOT EXISTS card_history (
    story_key  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    card_key   TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    turn       INTEGER NOT NULL DEFAULT 0,
    event      TEXT NOT NULL DEFAULT '',
    evidence   TEXT NOT NULL DEFAULT '[]',
    created    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (story_key, session_id, kind, card_key, seq)
);
CREATE INDEX IF NOT EXISTS idx_card_history_session ON card_history(story_key, session_id, kind, card_key, seq);
"""


def _migrate_schema(con) -> None:
    """Apply additive migrations for stores created before a card field became a column.

    ``CREATE TABLE IF NOT EXISTS`` intentionally leaves an existing table alone.  Keep
    migrations immediately beside the schema so a new Story field cannot silently
    vanish when an older local database is read and written again.
    """
    columns = {row[1] for row in con.execute("PRAGMA table_info(stories)").fetchall()}
    if "time_system" not in columns:
        con.execute("ALTER TABLE stories ADD COLUMN time_system TEXT NOT NULL DEFAULT '{}'")
    location_columns = {row[1] for row in con.execute("PRAGMA table_info(locations)").fetchall()}
    if "history" not in location_columns:
        con.execute("ALTER TABLE locations ADD COLUMN history TEXT NOT NULL DEFAULT ''")


# ── Story list / existence ───────────────────────────────────────────────────
def story_updated_map(root: Path) -> dict[str, float]:
    """{story_key: last-updated epoch} — the UI's recency sort, now that no per-story
    file exists to stat."""
    con = _conn(root)
    return {k: float(u or 0) for k, u in
            con.execute("SELECT key, updated FROM stories").fetchall()}


def list_stories(root: Path) -> list[str]:
    """All story keys, ordered by name."""
    con = _conn(root)
    rows = con.execute("SELECT key FROM stories ORDER BY name COLLATE NOCASE").fetchall()
    return [r[0] for r in rows]


def story_exists(root: Path, key: str) -> bool:
    con = _conn(root)
    return bool(con.execute("SELECT 1 FROM stories WHERE key=?", (key,)).fetchone())


# ── Living playthrough cards ──────────────────────────────────────────────────
def get_play_cards(root: Path, story_key: str, session_id: str) -> dict[str, dict[str, dict]]:
    """Return the current runtime-card projection, grouped as kind → card_key → card.

    `foundation` is immutable after first insert. `current` is deliberately mutable, while the
    append-only evidence ledger is fetched by ``get_card_history`` only when a caller needs depth.
    This keeps normal per-turn prompt assembly cheap.
    """
    con = _conn(root)
    out: dict[str, dict[str, dict]] = {"character": {}, "arc": {}, "story": {}}
    for kind, card_key, foundation, current, updated in con.execute(
            "SELECT kind, card_key, foundation, current, updated FROM play_cards "
            "WHERE story_key=? AND session_id=?", (story_key, session_id)).fetchall():
        out.setdefault(kind, {})[card_key] = {"foundation": _jloads(foundation, dict),
                                                "current": _jloads(current, dict), "updated": updated}
    return out


def get_card_history(root: Path, story_key: str, session_id: str, kind: str, card_key: str,
                     limit: int = 50) -> list[dict]:
    con = _conn(root)
    rows = con.execute(
        "SELECT seq, turn, event, evidence, created FROM card_history "
        "WHERE story_key=? AND session_id=? AND kind=? AND card_key=? "
        "ORDER BY seq DESC LIMIT ?", (story_key, session_id, kind, card_key, max(1, limit))).fetchall()
    return [{"seq": r[0], "turn": r[1], "event": r[2], "evidence": _jloads(r[3], list),
             "created": r[4]} for r in reversed(rows)]


def apply_play_card_update(root: Path, story_key: str, session_id: str, *, kind: str, card_key: str,
                           foundation: dict | None, current: dict, event: str = "",
                           evidence: list[str] | None = None, turn: int = 0) -> None:
    """Atomically mutate a runtime card's present state and append its evidence event.

    The first non-empty foundation wins (`COALESCE` is not enough because it is JSON), so later
    consolidations cannot silently rewrite what the character/arc/story was at the start of play.
    """
    con = _conn(root)
    now = time.time()
    try:
        con.execute("BEGIN")
        old = con.execute("SELECT foundation FROM play_cards WHERE story_key=? AND session_id=? AND kind=? AND card_key=?",
                          (story_key, session_id, kind, card_key)).fetchone()
        base = _jdumps(foundation or {}) if old is None else old[0]
        con.execute(
            "INSERT INTO play_cards (story_key,session_id,kind,card_key,foundation,current,updated) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(story_key,session_id,kind,card_key) DO UPDATE SET "
            "current=excluded.current, updated=excluded.updated",
            (story_key, session_id, kind, card_key, base, _jdumps(current or {}), now))
        if event.strip():
            seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM card_history "
                              "WHERE story_key=? AND session_id=? AND kind=? AND card_key=?",
                              (story_key, session_id, kind, card_key)).fetchone()[0]
            con.execute("INSERT INTO card_history (story_key,session_id,kind,card_key,seq,turn,event,evidence,created) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (story_key, session_id, kind, card_key, seq, int(turn or 0), event.strip(),
                         _jdumps(evidence or []), now))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


# ── LOAD: reconstruct the {story, characters} aggregate from rows ────────────
def load_story(root: Path, key: str, *, _connection=None) -> tuple[dict, dict] | None:
    """Reconstruct the (story_dict, characters_dict) aggregate from rows. Returns None if
    the story key doesn't exist. The story_dict is ready for ``Story(**story_dict)``.

    ``_connection`` is an internal escape hatch for a caller that already owns
    the write transaction.  Public callers continue to receive a fresh store
    connection, preserving the normal read API.
    """
    con = _connection if _connection is not None else _conn(root)
    srow = con.execute(
        "SELECT name, type, premise, tone, themes, art_style, premise_parts, world, time_system, "
        "storyboard, lorebook, \"start\", background, fields, start_scene, default_personas, "
        "recent_window FROM stories WHERE key=?", (key,)
    ).fetchone()
    if srow is None:
        return None
    story = {
        "name": srow[0], "type": srow[1], "premise": srow[2], "tone": srow[3],
        "themes": _jloads(srow[4], list), "art_style": srow[5],
        "premise_parts": _jloads(srow[6], dict), "world": _jloads(srow[7], dict),
        "time_system": _jloads(srow[8], dict), "storyboard": _jloads(srow[9], dict),
        "lorebook": _jloads(srow[10], dict), "start": srow[11], "background": srow[12],
        "fields": _jloads(srow[13], dict), "start_scene": srow[14],
        "default_personas": _jloads(srow[15], list), "recent_window": srow[16],
    }

    # cast
    cast = []
    for c, p, o, h in con.execute(
            "SELECT character, is_primary, outfit, home FROM cast_members "
            "WHERE story_key=? ORDER BY ord", (key,)).fetchall():
        m = {"character": c, "primary": bool(p)}
        if o:
            m["outfit"] = o
        if h:
            m["home"] = h
        cast.append(m)
    story["cast"] = cast

    # locations (with nested scenes)
    locs = []
    for lid, lname, ldesc, lhist, lbp, lbg, lparent, lscenes in con.execute(
            "SELECT id, name, description, history, background_prompt, background, parent, scenes "
            "FROM locations WHERE story_key=? ORDER BY ord", (key,)).fetchall():
        locs.append({"id": lid, "name": lname, "description": ldesc, "history": lhist,
                     "background_prompt": lbp, "background": lbg, "parent": lparent,
                     "scenes": _jloads(lscenes, list)})
    story["locations"] = locs

    # relationships
    story["relationships"] = [
        {"id": rid, "source": s, "target": t, "nature": n, "dynamic": d, "stance": st,
         "target_dynamic": td, "target_stance": ts, "note": nt, "potential": po,
         "trajectory": tr}
        for (rid, s, t, n, d, st, td, ts, nt, po, tr) in con.execute(
            "SELECT id, source, target, nature, dynamic, stance, target_dynamic, "
            "target_stance, note, potential, trajectory FROM relationships "
            "WHERE story_key=? ORDER BY ord", (key,)).fetchall()
    ]

    # conditions
    story["conditions"] = [
        {"id": cid, "name": cn, "kind": ck, "description": cd, "effect": ce}
        for (cid, cn, ck, cd, ce) in con.execute(
            "SELECT id, name, kind, description, effect FROM conditions "
            "WHERE story_key=? ORDER BY ord", (key,)).fetchall()
    ]

    # arcs (with nested JSON fields)
    arcs = []
    for (aid, an, ame, adf, ath, ap, ac, ao, aps, aco, anod, ast, atl, atr, adv) in con.execute(
            "SELECT id, name, mini_ending, dramatic_function, themes, premise, \"cast\", owner, "
            "pressures, conditions, nodes, \"start\", timelines, transitions, divergence_axis "
            "FROM arcs WHERE story_key=? ORDER BY ord", (key,)).fetchall():
        arcs.append({"id": aid, "name": an, "mini_ending": ame, "dramatic_function": adf,
                     "themes": _jloads(ath, list), "premise": ap, "cast": _jloads(ac, list),
                     "owner": ao, "pressures": _jloads(aps, list),
                     "conditions": _jloads(aco, list), "nodes": _jloads(anod, dict),
                     "start": ast, "timelines": _jloads(atl, list),
                     "transitions": _jloads(atr, list), "divergence_axis": adv})
    story["arcs"] = arcs

    # chapters
    story["chapters"] = [
        {"id": cid, "title": ct, "purpose": cp, "pov": cv, "setting": cs, "beats": _jloads(cb, list),
         "status": cstat, "draft": cd, "recap": cr}
        for (cid, ct, cp, cv, cs, cb, cstat, cd, cr) in con.execute(
            "SELECT id, title, purpose, pov, setting, beats, status, draft, recap "
            "FROM chapters WHERE story_key=? ORDER BY ord", (key,)).fetchall()
    ]

    # scene harnesses (VN)
    story["scenes"] = [
        {"id": sid, "title": st, "goal": sg, "setting": sse, "tone": snt,
         "on_stage": _jloads(oss, list), "triggers": _jloads(tr, list)}
        for (sid, st, sg, sse, snt, oss, tr) in con.execute(
            "SELECT id, title, goal, setting, tone, on_stage, triggers "
            "FROM scene_harnesses WHERE story_key=? ORDER BY ord", (key,)).fetchall()
    ]

    # features
    story["features"] = [
        {"id": fid, "label": fl, "initial": float(fi)}
        for (fid, fl, fi) in con.execute(
            "SELECT id, label, initial FROM features WHERE story_key=? ORDER BY ord",
            (key,)).fetchall()
    ]

    # connections
    story["connections"] = [
        {"id": cid, "source": cs, "target": ct, "label": cl}
        for (cid, cs, ct, cl) in con.execute(
            "SELECT id, source, target, label FROM connections "
            "WHERE story_key=? ORDER BY ord", (key,)).fetchall()
    ]

    # characters (embedded)
    chars: dict[str, dict] = {}
    for ck, cn, csys, cg, cf, cimg, cplay in con.execute(
            "SELECT char_key, name, system, greeting, fields, image, playable "
            "FROM characters WHERE story_key=? ORDER BY ord", (key,)).fetchall():
        cdoc = {"name": cn, "system": csys, "fields": _jloads(cf, dict),
                "image": _jloads(cimg, dict), "playable": bool(cplay)}
        if cg is not None:
            cdoc["greeting"] = cg
        chars[ck] = cdoc
    return story, chars


# ── SAVE: fan the validated aggregate to rows in one transaction ─────────────
def save_story(root: Path, key: str, story: dict, characters: dict | None = None, *, _connection=None) -> None:
    """Persist the whole story + its embedded characters. REPLACEs every row for `key` —
    callers MUST validate (``Story(**story)`` + cross-aggregate check) BEFORE calling, then
    this fans the validated dict to rows atomically. Mirrors the old story_db.save_story
    contract: whole-document write, atomic via transaction.

    ``_connection`` is internal-only: it lets ``update_story_atomically`` save
    inside the transaction that loaded and validated the aggregate.  Ordinary
    callers keep the existing self-contained transaction behavior.
    """
    characters = characters or {}
    now = time.time()
    con = _connection if _connection is not None else _conn(root)
    owns_transaction = _connection is None
    try:
        if owns_transaction:
            con.execute("BEGIN")
        # upsert the stories row
        con.execute(
            "INSERT INTO stories (key, name, type, premise, tone, themes, art_style, "
            "premise_parts, world, time_system, storyboard, lorebook, start, background, fields, "
            "start_scene, default_personas, recent_window, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, type=excluded.type, "
            "premise=excluded.premise, tone=excluded.tone, themes=excluded.themes, "
            "art_style=excluded.art_style, premise_parts=excluded.premise_parts, "
            "world=excluded.world, time_system=excluded.time_system, storyboard=excluded.storyboard, "
            "lorebook=excluded.lorebook, "
            "start=excluded.start, background=excluded.background, fields=excluded.fields, "
            "start_scene=excluded.start_scene, default_personas=excluded.default_personas, "
            "recent_window=excluded.recent_window, updated=excluded.updated",
            (key, story.get("name", ""), story.get("type", "novel"), story.get("premise", ""),
             story.get("tone", ""), _jdumps(story.get("themes", [])), story.get("art_style", ""),
             _jdumps(story.get("premise_parts", {})), _jdumps(story.get("world", {})),
             _jdumps(story.get("time_system", {})), _jdumps(story.get("storyboard", {})),
             _jdumps(story.get("lorebook", {})),
             story.get("start"), story.get("background"), _jdumps(story.get("fields", {})),
             story.get("start_scene", ""), _jdumps(story.get("default_personas", [])),
             int(story.get("recent_window", 8)), now)
        )
        # wipe + rewrite every child table (whole-document replace, like the JSON form did).
        # Each list is deduped by id first (last-write-wins) — a duplicate id in the input (e.g. an
        # agent re-adding an existing relationship) must never crash the write with a UNIQUE violation.
        for tbl in ("cast_members", "locations", "relationships", "conditions", "arcs",
                    "chapters", "scene_harnesses", "connections", "features", "characters"):
            con.execute(f"DELETE FROM {tbl} WHERE story_key=?", (key,))

        for i, m in enumerate(_dedup_by_key(story.get("cast", []), "character")):
            con.execute(
                "INSERT INTO cast_members (story_key, character, is_primary, outfit, home, ord) "
                "VALUES (?,?,?,?,?,?)",
                (key, m.get("character", ""), 1 if m.get("primary") else 0,
                 m.get("outfit"), m.get("home", ""), i))

        for i, l in enumerate(_dedup_by_id(story.get("locations", []))):
            con.execute(
                "INSERT INTO locations (story_key, id, name, description, history, background_prompt, "
                "background, parent, scenes, ord) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, l.get("id", ""), l.get("name", ""), l.get("description", ""), l.get("history", ""),
                 l.get("background_prompt", ""), l.get("background"), l.get("parent", ""),
                 _jdumps(l.get("scenes", [])), i))

        for i, r in enumerate(_dedup_by_id(story.get("relationships", []))):
            con.execute(
                "INSERT INTO relationships (story_key, id, source, target, nature, dynamic, "
                "stance, target_dynamic, target_stance, note, potential, trajectory, ord) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, r.get("id", ""), r.get("source", ""), r.get("target", ""),
                 r.get("nature", ""), r.get("dynamic", ""), r.get("stance", "neutral"),
                 r.get("target_dynamic", ""), r.get("target_stance", ""), r.get("note", ""),
                 r.get("potential", ""), r.get("trajectory", ""), i))

        for i, c in enumerate(_dedup_by_id(story.get("conditions", []))):
            con.execute(
                "INSERT INTO conditions (story_key, id, name, kind, description, effect, ord) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, c.get("id", ""), c.get("name", ""), c.get("kind", ""),
                 c.get("description", ""), c.get("effect", ""), i))

        for i, a in enumerate(_dedup_by_id(story.get("arcs", []))):
            con.execute(
                "INSERT INTO arcs (story_key, id, name, mini_ending, dramatic_function, themes, "
                "premise, \"cast\", owner, pressures, conditions, nodes, \"start\", timelines, "
                "transitions, divergence_axis, ord) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, a.get("id", ""), a.get("name", ""), a.get("mini_ending", ""),
                 a.get("dramatic_function", ""), _jdumps(a.get("themes", [])),
                 a.get("premise", ""), _jdumps(a.get("cast", [])), a.get("owner", ""),
                 _jdumps(a.get("pressures", [])), _jloads_dict_list(a.get("conditions")),
                 _jdumps(a.get("nodes", {})), a.get("start", ""),
                 _jdumps(a.get("timelines", [])), _jdumps(a.get("transitions", [])),
                 a.get("divergence_axis", ""), i))

        for i, ch in enumerate(_dedup_by_id(story.get("chapters", []))):
            con.execute(
                "INSERT INTO chapters (story_key, id, title, purpose, pov, setting, beats, "
                "status, draft, recap, ord) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (key, ch.get("id", ""), ch.get("title", ""), ch.get("purpose", ""),
                 ch.get("pov", ""), ch.get("setting", ""), _jdumps(ch.get("beats", [])),
                 ch.get("status", ""), ch.get("draft", ""), ch.get("recap", ""), i))

        for i, sc in enumerate(_dedup_by_id(story.get("scenes", []))):
            con.execute(
                "INSERT INTO scene_harnesses (story_key, id, title, goal, setting, tone, "
                "on_stage, triggers, ord) VALUES (?,?,?,?,?,?,?,?,?)",
                (key, sc.get("id", ""), sc.get("title", ""), sc.get("goal", ""),
                 sc.get("setting", ""), sc.get("tone", ""), _jdumps(sc.get("on_stage", [])),
                 _jdumps(sc.get("triggers", [])), i))

        for i, cn in enumerate(_dedup_by_id(story.get("connections", []))):
            con.execute(
                "INSERT INTO connections (story_key, id, source, target, label, ord) "
                "VALUES (?,?,?,?,?,?)",
                (key, cn.get("id", ""), cn.get("source", ""), cn.get("target", ""),
                 cn.get("label", ""), i))

        for i, f in enumerate(_dedup_by_id(story.get("features", []))):
            con.execute(
                "INSERT INTO features (story_key, id, label, initial, ord) VALUES (?,?,?,?,?)",
                (key, f.get("id", ""), f.get("label", ""), float(f.get("initial", 0)), i))

        # embedded characters (whole-map replace, mirroring the JSON form)
        for i, (ck, cdoc) in enumerate(characters.items()):
            con.execute(
                "INSERT INTO characters (story_key, char_key, name, system, greeting, fields, "
                "image, playable, ord, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, ck, cdoc.get("name", ""), cdoc.get("system", ""), cdoc.get("greeting"),
                 _jdumps(cdoc.get("fields", {})), _jdumps(cdoc.get("image", {})),
                 1 if cdoc.get("playable") else 0, i, now))
        if owns_transaction:
            con.execute("COMMIT")
    except Exception:
        if owns_transaction:
            con.execute("ROLLBACK")
        raise


@dataclass(frozen=True)
class AtomicStoryUpdate:
    """Outcome from :func:`update_story_atomically`.

    ``result`` is the caller-owned value returned by its updater.  The separate
    booleans keep a deliberately aborted update distinct from a missing story
    and from a successful no-op write.
    """

    found: bool
    committed: bool
    result: Any = None


def update_story_atomically(
    root: Path,
    key: str,
    updater: Callable[[dict, dict], tuple[dict, dict | None, Any] | None],
) -> AtomicStoryUpdate:
    """Load, validate/build, and replace one Story aggregate under one lock.

    The callback runs after ``BEGIN IMMEDIATE`` has acquired the database write
    lock and receives the current ``(story, embedded_characters)`` aggregate.
    It returns ``(validated_story, validated_characters, result)`` to commit,
    or ``None`` to abandon the transaction without writing.  This lets a
    capability endpoint check its supplied revision *inside* the same
    transaction in which it rebuilds and saves the candidate, rather than
    losing a private concurrent edit between a read and a whole-aggregate save.

    The callback must not perform slow external work while the lock is held;
    model calls belong before this function.
    """
    con = _conn(root)
    transaction_open = False
    try:
        # A deferred BEGIN would allow a second writer to change the aggregate
        # after this read but before the replacement write.  IMMEDIATE makes
        # the snapshot and whole-aggregate commit one serialized operation.
        con.execute("BEGIN IMMEDIATE")
        transaction_open = True
        loaded = load_story(root, key, _connection=con)
        if loaded is None:
            con.execute("ROLLBACK")
            transaction_open = False
            return AtomicStoryUpdate(found=False, committed=False)

        update = updater(*loaded)
        if update is None:
            con.execute("ROLLBACK")
            transaction_open = False
            return AtomicStoryUpdate(found=True, committed=False)

        story, characters, result = update
        save_story(root, key, story, characters, _connection=con)
        con.execute("COMMIT")
        transaction_open = False
        return AtomicStoryUpdate(found=True, committed=True, result=result)
    except Exception:
        if transaction_open:
            con.execute("ROLLBACK")
        raise


def _jloads_dict_list(v):
    """conditions can be either a list (current) — serialize as JSON regardless."""
    return _jdumps(v if v is not None else [])


# ── DELETE ────────────────────────────────────────────────────────────────────
def delete_story(root: Path, key: str) -> None:
    """Remove a story and all its child rows (cascade by story_key)."""
    con = _conn(root)
    try:
        con.execute("BEGIN")
        # child tables partition by story_key; the stories table keys on `key`
        for tbl in ("characters", "cast_members", "locations", "relationships",
                    "conditions", "arcs", "chapters", "scene_harnesses", "connections",
                    "features", "play_cards", "card_history"):
            con.execute(f"DELETE FROM {tbl} WHERE story_key=?", (key,))
        # Sessions + beats are copackaged too — same story_key partition, same transaction.
        from . import story_sessions as _SESS   # local import: story_sessions imports story_store
        _SESS.delete_story_sessions(root, key, con=con)
        con.execute("DELETE FROM stories WHERE key=?", (key,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


# ── Character slice ops (the embedded-character access the ctx methods need) ──
def character_keys(root: Path, key: str) -> list[str]:
    con = _conn(root)
    return [r[0] for r in con.execute(
        "SELECT char_key FROM characters WHERE story_key=? ORDER BY ord", (key,)).fetchall()]


def all_character_keys(root: Path) -> set[str]:
    """Return every embedded key so newly generated Story characters stay unique."""
    con = _conn(root)
    return {row[0] for row in con.execute("SELECT DISTINCT char_key FROM characters").fetchall()}


def duplicate_character_keys(root: Path) -> dict[str, list[str]]:
    """Report cast keys shared by more than one Story, deterministically.

    Even a library-backed reference is relevant: once one Story embeds and
    edits that key, older flat consumers can otherwise resolve it for another
    Story too.
    """
    con = _conn(root)
    owners: dict[str, list[str]] = {}
    for char_key, story_key in con.execute(
        "SELECT character, story_key FROM cast_members ORDER BY character, story_key"
    ).fetchall():
        owners.setdefault(char_key, []).append(story_key)
    return {key: stories for key, stories in owners.items() if len(stories) > 1}


def get_character(root: Path, story_key: str, char_key: str) -> dict | None:
    con = _conn(root)
    row = con.execute(
        "SELECT name, system, greeting, fields, image, playable FROM characters "
        "WHERE story_key=? AND char_key=?", (story_key, char_key)).fetchone()
    if row is None:
        return None
    out = {"name": row[0], "system": row[1], "fields": _jloads(row[3], dict),
           "image": _jloads(row[4], dict), "playable": bool(row[5])}
    if row[2] is not None:
        out["greeting"] = row[2]
    return out


def story_owner(root: Path, char_key: str) -> str | None:
    """Which story owns this character? Indexed lookup (replaces the O(stories) JSON scan)."""
    con = _conn(root)
    row = con.execute("SELECT story_key FROM characters WHERE char_key=? LIMIT 1",
                      (char_key,)).fetchone()
    return row[0] if row else None


def upsert_character(root: Path, story_key: str, char_key: str, cdata: dict) -> None:
    """Upsert ONE embedded character without rewriting the whole story (used by write_npc +
    _write_character_data for surgical character edits). Preserves ord by appending at the end
    if new. Mirrors story_db.upsert_character's read-modify-write, but as a single-table op."""
    now = time.time()
    con = _conn(root)
    exists = con.execute(
        "SELECT 1 FROM characters WHERE story_key=? AND char_key=?", (story_key, char_key)
    ).fetchone()
    try:
        con.execute("BEGIN")
        if exists:
            con.execute(
                "UPDATE characters SET name=?, system=?, greeting=?, fields=?, image=?, "
                "playable=?, updated=? WHERE story_key=? AND char_key=?",
                (cdata.get("name", ""), cdata.get("system", ""), cdata.get("greeting"),
                 _jdumps(cdata.get("fields", {})), _jdumps(cdata.get("image", {})),
                 1 if cdata.get("playable") else 0, now, story_key, char_key))
        else:
            # append at the end (ord = current max + 1)
            maxord = con.execute(
                "SELECT COALESCE(MAX(ord), -1) FROM characters WHERE story_key=?",
                (story_key,)).fetchone()[0]
            con.execute(
                "INSERT INTO characters (story_key, char_key, name, system, greeting, fields, "
                "image, playable, ord, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (story_key, char_key, cdata.get("name", ""), cdata.get("system", ""),
                 cdata.get("greeting"), _jdumps(cdata.get("fields", {})),
                 _jdumps(cdata.get("image", {})), 1 if cdata.get("playable") else 0,
                 maxord + 1, now))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def delete_character(root: Path, story_key: str, char_key: str) -> None:
    """Remove ONE embedded character from a story (the ctx._char_owner-routed delete path)."""
    con = _conn(root)
    con.execute("DELETE FROM characters WHERE story_key=? AND char_key=?", (story_key, char_key))
    con.commit()


# ── Migration: JSON → relational tables ──────────────────────────────────────
def migrate_from_json(root: Path, *, verbose: bool = False) -> int:
    """One-time migration: read each ``configs/stories/<key>/story.json`` (and legacy flat
    ``<key>.json``) into the relational tables, then rename it ``.json.migrated`` so it's not
    re-read. Idempotent: a story whose key already exists in the DB is skipped (the JSON is
    still renamed to .migrated so it stops appearing in the glob). Returns the count migrated.

    Run lazily on startup (see loader.load_settings) so the user just restarts. Reversible:
    rename the ``.json.migrated`` files back to ``.json`` and they'll re-migrate.
    """
    import json as _json
    story_dir = root / "configs" / "stories"
    if not story_dir.is_dir():
        return 0
    # Inline the legacy per-story JSON read (records/store.py is gone): the folder form
    # <key>/story.json wins, the legacy flat <key>.json is the fallback; the document is
    # {story, characters}. Only this migration read path needs it.
    def _iter_legacy_json(sdir):
        out = {}
        for p in sorted(sdir.glob("*/story.json")):
            out[p.parent.name] = p
        for p in sorted(sdir.glob("*.json")):
            out.setdefault(p.stem, p)
        return list(out.items())
    migrated = 0
    for skey, path in _iter_legacy_json(story_dir):
        # Skip a key already in the DB (already migrated) — but still rename the leftover JSON.
        already = story_exists(root, skey)
        if not already:
            try:
                _doc = _json.loads(path.read_text(encoding="utf-8"))
                sdata, embedded = (_doc.get("story") or {}), (_doc.get("characters") or {})
            except Exception as exc:  # noqa: BLE001 — don't let one bad file sink the rest
                if verbose:
                    print(f"  ✗ {path.name}: read failed ({exc}) — leaving as-is")
                continue
            try:
                save_story(root, skey, sdata, embedded)
            except Exception as exc:  # noqa: BLE001
                if verbose:
                    print(f"  ✗ {path.name}: save failed ({exc}) — leaving as-is")
                continue
            migrated += 1
            if verbose:
                print(f"  ✓ {skey}: migrated to relational store")
        # Mark migrated: rename so the legacy-file scan stops seeing it. Reversible by renaming back.
        try:
            path.rename(path.with_suffix(".json.migrated"))
        except Exception:  # noqa: BLE001 — best-effort; the data is already in the DB
            pass
    return migrated


# ── Self-test ────────────────────────────────────────────────────────────────
def _self_test() -> None:
    """Round-trip: build a story dict, save, reload, assert the JSON-column fields and the
    queryable columns both survive byte-identical. Uses an explicit temp dir (not
    TemporaryDirectory) because libSQL holds the file open on Windows and the auto-cleanup
    would hit a PermissionError. Cleans up manually at the end."""
    import tempfile
    root = Path(tempfile.mkdtemp())
    dbp = _db_path(root)
    _inited.discard(str(dbp))   # force schema setup on the temp DB
    try:
        _self_test_body(root)
    finally:
        # best-effort cleanup; the .db file may remain locked on Windows, which is fine for a temp dir
        try:
            if dbp.exists():
                dbp.unlink()
        except Exception:
            pass


def _self_test_body(root: Path) -> None:
    key = "test_story"
    story = {
        "name": "Test", "type": "vn", "premise": "A test premise.", "tone": "wry",
        "themes": ["grief", "home"], "art_style": "cel-shaded",
        "premise_parts": {"root": "the drought", "question": "stay or go"},
        "world": {"genre": "low fantasy", "forces": [{"name": "Rootbound", "stance": "endure"}]},
        "time_system": {"slots": ["morning", "evening", "night"], "entity_periods": [
            {"id": "night-watch", "slots": ["night"], "state": "hunting",
             "capabilities": ["mimicry"], "constraint": "cannot act by day"}]},
        "storyboard": {"heart": "the heart", "logline": "the logline", "beats": []},
        "lorebook": {"x": 1}, "start": "home", "background": "/img/cover.png",
        "fields": {"creator": "tester"}, "start_scene": "scene-1",
        "default_personas": ["eli"], "recent_window": 12,
        "cast": [{"character": "eli", "primary": True, "home": "home"},
                 {"character": "mara", "outfit": "librarian"}],
        "locations": [{"id": "home", "name": "Home", "description": "a cottage",
                       "background_prompt": "cozy", "parent": "",
                       "scenes": [{"id": "s1", "character": "eli", "backstory": "reads"}]},
                      {"id": "square", "name": "The Square", "description": "the village square",
                       "background_prompt": "", "parent": "", "scenes": []}],
        "relationships": [{"id": "r1", "source": "eli", "target": "mara", "nature": "mentor",
                           "dynamic": "wary respect", "stance": "neutral", "note": "old debt"}],
        "conditions": [{"id": "flood", "name": "Flood season", "kind": "seasonal",
                        "effect": "the square floods"}],
            "arcs": [{"id": "arc-1", "name": "First arc", "cast": ["eli"], "owner": "eli",
                      "nodes": {"n1": {"id": "n1", "title": "beat 1"}}, "timelines": [], "transitions": []}],
        "chapters": [{"id": "ch1", "title": "Morning", "beats": ["she woke"], "status": "drafted"}],
        "scenes": [{"id": "scene-1", "title": "Opening", "on_stage": [{"char": "eli"}]}],
        "connections": [{"id": "c1", "source": "home", "target": "square", "label": "path"}],
        "features": [{"id": "trust", "label": "Trust", "initial": 0.5}],
    }
    chars = {"eli": {"name": "Eli", "system": "a herbalist",
                     "fields": {"role": "protagonist"},
                     "image": {"checkpoint": "animagine"},
                     "playable": True, "greeting": "hi"},
             "mara": {"name": "Mara", "system": "a teacher", "fields": {}, "image": {}}}
    save_story(root, key, story, chars)

    loaded = load_story(root, key)
    assert loaded is not None, "load returned None"
    s2, c2 = loaded

    # JSON-column fields must round-trip exactly
    for fld in ("name", "type", "premise", "tone", "art_style", "start", "background",
                "start_scene", "recent_window"):
        assert s2[fld] == story[fld], f"{fld}: {s2[fld]!r} != {story[fld]!r}"
    for fld in ("themes", "premise_parts", "world", "time_system", "storyboard", "lorebook", "fields",
                "default_personas"):
        assert s2[fld] == story[fld], f"{fld}: {s2[fld]!r} != {story[fld]!r}"

    # queryable entities — normalize both sides through the pydantic models, because the
    # relational columns store defaults (e.g. primary=False) that the input dict omitted.
    # The real contract is: Story(**loaded) == Story(**original), Character(**c2)==Character(**orig)
    from ...config.schema import Story, Character
    norm = Story(**story).model_dump()
    norm2 = Story(**s2).model_dump()
    for fld in ("cast", "locations", "relationships", "conditions", "connections",
                "features", "arcs", "chapters", "scenes"):
        assert norm2[fld] == norm[fld], f"{fld}: {norm2[fld]!r} != {norm[fld]!r}"
    for ck, cdoc in chars.items():
        assert Character(**c2[ck]).model_dump() == Character(**cdoc).model_dump(), \
            f"character {ck}: {c2[ck]!r}"

    # slice ops
    assert story_exists(root, key)
    assert character_keys(root, key) == ["eli", "mara"]
    assert get_character(root, key, "eli") == chars["eli"]
    assert story_owner(root, "eli") == key
    assert story_owner(root, "nobody") is None

    # Living cards keep an immutable baseline, mutate their present projection, and append
    # evidence without touching the authored character/story rows.
    apply_play_card_update(root, key, "play-test", kind="character", card_key="eli",
                           foundation={"wound": "old loss"}, current={"mood": "guarded"},
                           event="Eli refused Mara's help.", evidence=["scene 1"], turn=3)
    apply_play_card_update(root, key, "play-test", kind="character", card_key="eli",
                           foundation={"wound": "must not replace"}, current={"mood": "less guarded"},
                           event="Eli accepted the lantern.", evidence=["scene 2"], turn=8)
    live = get_play_cards(root, key, "play-test")["character"]["eli"]
    assert live["foundation"] == {"wound": "old loss"} and live["current"]["mood"] == "less guarded"
    assert [h["turn"] for h in get_card_history(root, key, "play-test", "character", "eli")] == [3, 8]

    # node_hash stability: the reconstructed world dict must hash the same as the original.
    # (anchors.py hashes the live dict; if reconstruction drifts, every anchor breaks.)
    from ...stories.records.anchors import node_hash
    assert node_hash(s2["world"]) == node_hash(story["world"]), "world hash drifted"
    assert node_hash(s2["premise_parts"]) == node_hash(story["premise_parts"]), \
        "premise_parts hash drifted"

    # delete cascade
    delete_story(root, key)
    assert load_story(root, key) is None
    assert not story_exists(root, key)
    print("story_store self-test OK — round-trip, slices, hash stability, delete cascade")


if __name__ == "__main__":
    _self_test()
