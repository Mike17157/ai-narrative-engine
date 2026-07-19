"""Server-side checkpoints for story-building sessions — relational store.

Sessions live in the story domain's libSQL database (``configs/stories.db``), the same
store as stories (``story_store.py``): the ``sessions`` table holds the durable console
state (conversation, development graph, draft, unified State doc) as JSON columns, and
the ``session_beats`` table is the UNBOUNDED episodic record — every scribe ``log`` beat
lands there permanently (see ``apply_deltas(beat_sink=…)``), while the State doc's
capped 20-beat log stays as just the model's working window.

COPACKAGING: like every table in this database, sessions and beats partition by
``story_key`` — a story's playthroughs travel and delete WITH it (story_store.
delete_story cascades here via ``delete_story_sessions``). Builder threads not yet
anchored to a story carry ``story_key=''`` until they are. Conventions mirror
``story_store.py``: shared ``_connect`` (WAL + busy_timeout), a module ``_SCHEMA``, an
``_inited`` guard, additive ``ALTER`` migrations, and a lazy fold on first touch — any
legacy ``configs/story_sessions/<sid>.json`` is migrated in and renamed
``<sid>.json.migrated`` (its surviving capped beats backfilled as history).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import story_store

_inited: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid         TEXT PRIMARY KEY,
    story_key   TEXT NOT NULL DEFAULT '',   -- the story this thread belongs to ('' = unanchored builder)
    character   TEXT NOT NULL DEFAULT '',
    messages    TEXT NOT NULL DEFAULT '[]',   -- JSON array
    graph       TEXT,                          -- JSON object (development graph) or NULL
    draft       TEXT,                          -- JSON object (latest draft) or NULL
    lorebooks   TEXT NOT NULL DEFAULT '[]',   -- JSON array of book ids
    world_state TEXT NOT NULL DEFAULT '{}',   -- JSON object (legacy flat projection)
    state       TEXT NOT NULL DEFAULT '{}',   -- JSON object (unified leveled State doc)
    prologue    TEXT,                          -- JSON object or NULL
    updated     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_story ON sessions(story_key);

CREATE TABLE IF NOT EXISTS session_beats (
    sid       TEXT NOT NULL,
    seq       INTEGER NOT NULL,                    -- monotonic per sid
    story_key TEXT NOT NULL DEFAULT '',           -- copackaged with the story
    step      INTEGER NOT NULL DEFAULT 0,         -- world step when the beat landed
    ts        REAL NOT NULL DEFAULT 0,
    text      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sid, seq)
);
CREATE INDEX IF NOT EXISTS idx_session_beats_story ON session_beats(story_key, seq);
"""


def _conn(root: Path):
    con = story_store._connect(root)          # same db file, same pragmas, same Turso env
    key = str(story_store._db_path(root))
    if key not in _inited:
        con.executescript(_SCHEMA)
        _migrate_schema(con)
        con.commit()
        _migrate_json_sessions(root, con)
        _backfill_story_keys(con)
        con.commit()
        _inited.add(key)
    return con


def _migrate_schema(con) -> None:
    """Additive column upgrades for DBs created before story_key existed."""
    for table in ("sessions", "session_beats"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and "story_key" not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN story_key TEXT NOT NULL DEFAULT ''")


def _backfill_story_keys(con) -> None:
    """Stamp story_key on pre-partition rows. Conventions: play-<key> is structural; probe-/
    bench- prefixes are only trusted when the remainder matches a real story key. Anything
    undeterminable stays '' rather than guessing wrong. Idempotent (story_key='' guard).
    The key-dependent rules no-op when this DB has no stories table yet (e.g. a sessions-only
    scratch root) — the play- rule stands alone."""
    have_stories = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stories'").fetchone()
    keys = {r[0] for r in con.execute("SELECT key FROM stories").fetchall()} if have_stories else set()
    for table in ("sessions", "session_beats"):
        con.execute(f"UPDATE {table} SET story_key=substr(sid,6)"
                    f" WHERE story_key='' AND sid LIKE 'play-%'")
        for k in keys:
            con.execute(f"UPDATE {table} SET story_key=? WHERE story_key='' AND sid=?",
                        (k, f"probe-{k}"))
            con.execute(f"UPDATE {table} SET story_key=? WHERE story_key='' AND sid LIKE ?",
                        (k, f"bench-{k}-%"))


def _safe(sid: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(sid or "")).strip("_")


def _jdumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _jloads(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001 — corrupt cell surfaces as the default
        return default


def _derive_story_key(con, safe_sid: str, explicit: str) -> str:
    """Resolve a thread's story: an explicit key wins; then the play-<key> sid convention;
    then whatever an earlier save already stamped (re-saves must not un-anchor)."""
    if explicit:
        return explicit
    if safe_sid.startswith("play-"):
        return safe_sid[len("play-"):]
    row = con.execute("SELECT story_key FROM sessions WHERE sid=?", (safe_sid,)).fetchone()
    return (row[0] or "") if row else ""


def _payload(safe: str, data: dict) -> dict:
    """The canonical session payload (identical shape to the legacy JSON file's)."""
    from ...stories.runtime import state as _SD

    data = data or {}
    # The canonical mutable record is the State doc; write BOTH it and the projected flat
    # fields so readers on either side of the phased migration agree.
    state = _SD.from_session(data)
    legacy = _SD.to_session_fields(state)   # {state, graph?, draft?, world_state?}
    return {
        "id": safe,
        "character": data.get("character", ""),
        "messages": data.get("messages") or [],
        "graph": legacy.get("graph", data.get("graph")),
        "draft": legacy.get("draft", data.get("draft")),
        "lorebooks": data.get("lorebooks") or [],     # book ids attached to this thread
        "world_state": legacy.get("world_state", data.get("world_state") or {}),
        "state": state,                                # the unified leveled State doc
        "prologue": data.get("prologue"),              # kept losslessly (the JSON file dropped it)
    }


def _upsert(con, payload: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO sessions"
        " (sid, story_key, character, messages, graph, draft, lorebooks, world_state, state,"
        " prologue, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (payload["id"], payload.get("story_key", ""), payload["character"],
         _jdumps(payload["messages"]), _jdumps(payload["graph"]), _jdumps(payload["draft"]),
         _jdumps(payload["lorebooks"]), _jdumps(payload["world_state"]), _jdumps(payload["state"]),
         None if payload.get("prologue") is None else _jdumps(payload["prologue"]),
         time.time()))


def _migrate_json_sessions(root: Path, con) -> None:
    """Fold legacy per-sid JSON files into the tables, then rename them .json.migrated."""
    d = root / "configs" / "story_sessions"
    if not d.is_dir():
        return
    for p in sorted(d.glob("*.json")):
        safe = _safe(p.stem)
        if not safe:
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("session file is not an object")
            payload = _payload(safe, raw)
            payload["story_key"] = _derive_story_key(con, safe, "")
            _upsert(con, payload)
            # Backfill the surviving (capped) beats as history — once, and only when the
            # archive has nothing for this thread yet.
            have = con.execute("SELECT 1 FROM session_beats WHERE sid=? LIMIT 1", (safe,)).fetchone()
            ws = raw.get("world_state")
            log = ws.get("log") if isinstance(ws, dict) else None
            if not have and isinstance(log, list):
                ts = p.stat().st_mtime
                for i, line in enumerate(log):
                    con.execute(
                        "INSERT INTO session_beats (sid, seq, story_key, step, ts, text)"
                        " VALUES (?,?,?,?,?,?)",
                        (safe, i, payload["story_key"], 0, ts, str(line)))
            p.rename(p.with_name(p.name + ".migrated"))
        except Exception:  # noqa: BLE001 — a corrupt file stays put for manual inspection
            continue
    con.commit()


def load_session(root: Path, sid: str) -> dict | None:
    safe = _safe(sid)
    if not safe:
        return None
    row = _conn(root).execute(
        "SELECT story_key, character, messages, graph, draft, lorebooks, world_state, state,"
        " prologue FROM sessions WHERE sid=?", (safe,)).fetchone()
    if row is None:
        return None
    raw = {"id": safe, "story_key": row[0] or "", "character": row[1] or "",
           "messages": _jloads(row[2], []), "graph": _jloads(row[3], None),
           "draft": _jloads(row[4], None), "lorebooks": _jloads(row[5], []),
           "world_state": _jloads(row[6], {}), "state": _jloads(row[7], {})}
    if row[8] is not None:
        raw["prologue"] = _jloads(row[8], None)
    # Surface the unified State doc alongside the legacy flat fields (lazy + lossless).
    if not isinstance(raw.get("state"), dict):
        from ...stories.runtime import state as _SD
        raw["state"] = _SD.from_session(raw)
    return raw


def save_session(root: Path, sid: str, data: dict, story_key: str = "") -> dict:
    safe = _safe(sid)
    con = _conn(root)
    payload = _payload(safe, data)
    payload["story_key"] = _derive_story_key(con, safe, story_key)
    _upsert(con, payload)
    con.commit()
    return payload


def delete_session(root: Path, sid: str) -> None:
    safe = _safe(sid)
    if not safe:
        return
    con = _conn(root)
    con.execute("DELETE FROM sessions WHERE sid=?", (safe,))
    con.execute("DELETE FROM session_beats WHERE sid=?", (safe,))
    con.commit()


# ── Story partitioning (copackaging) ───────────────────────────────────────────

def sessions_for_story(root: Path, story_key: str) -> list[str]:
    """Every thread anchored to this story (play sessions, sims, its bench runs)."""
    rows = _conn(root).execute(
        "SELECT sid FROM sessions WHERE story_key=? ORDER BY sid", (story_key,)).fetchall()
    return [r[0] for r in rows]


def beats_for_story(root: Path, story_key: str, limit: int = 500) -> list[dict]:
    """The story's whole beat chronicle across all its threads, oldest→newest."""
    rows = _conn(root).execute(
        "SELECT sid, seq, step, ts, text FROM session_beats WHERE story_key=?"
        " ORDER BY ts DESC, sid, seq DESC LIMIT ?",
        (story_key, int(limit))).fetchall()
    return [{"sid": s, "seq": q, "step": st, "ts": ts, "text": t}
            for s, q, st, ts, t in reversed(rows)]


def delete_story_sessions(root: Path, story_key: str, con=None) -> tuple[int, int]:
    """Cascade half of story deletion: remove the story's sessions AND their beats.
    Pass `con` to join a caller's transaction (story_store.delete_story does); otherwise
    a fresh connection commits on its own. Returns (sessions, beats) removed."""
    own = con is None
    if own:
        con = _conn(root)
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sessions','session_beats')").fetchall()}
    ns = nb = 0
    if "session_beats" in have:
        nb = con.execute("SELECT COUNT(*) FROM session_beats WHERE story_key=?", (story_key,)).fetchone()[0]
        con.execute("DELETE FROM session_beats WHERE story_key=?", (story_key,))
    if "sessions" in have:
        ns = con.execute("SELECT COUNT(*) FROM sessions WHERE story_key=?", (story_key,)).fetchone()[0]
        con.execute("DELETE FROM sessions WHERE story_key=?", (story_key,))
    if own:
        con.commit()
    return ns, nb


# ── The permanent beat chronicle ───────────────────────────────────────────────

def append_beats(root: Path, sid: str, lines, *, step: int = 0, story_key: str = "") -> int:
    """Append new episodic-log lines to the session's permanent record. Returns the
    number stored. This table is never trimmed — the State doc's capped log is the
    model's window; THIS is the chronicle."""
    safe = _safe(sid)
    lines = [str(x or "").strip() for x in (lines or [])]
    lines = [x for x in lines if x]
    if not safe or not lines:
        return 0
    con = _conn(root)
    skey = _derive_story_key(con, safe, story_key)
    row = con.execute("SELECT COALESCE(MAX(seq), -1) FROM session_beats WHERE sid=?", (safe,)).fetchone()
    seq = int(row[0] if row and row[0] is not None else -1) + 1
    ts = time.time()
    for text in lines:
        con.execute(
            "INSERT INTO session_beats (sid, seq, story_key, step, ts, text) VALUES (?,?,?,?,?,?)",
            (safe, seq, skey, int(step or 0), ts, text))
        seq += 1
    con.commit()
    return len(lines)


def beats_for(root: Path, sid: str, limit: int = 200) -> list[dict]:
    """The session's stored beats, oldest→newest: [{"seq", "step", "ts", "text"}, …]."""
    safe = _safe(sid)
    if not safe:
        return []
    rows = _conn(root).execute(
        "SELECT seq, step, ts, text FROM session_beats WHERE sid=? ORDER BY seq DESC LIMIT ?",
        (safe, int(limit))).fetchall()
    return [{"seq": s, "step": st, "ts": ts, "text": t} for s, st, ts, t in reversed(rows)]
