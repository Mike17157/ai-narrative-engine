"""Global character library + personas — relational store.

The global character library (``configs/characters/*.yaml``) and the personas
(``configs/personas/*.yaml``) live in the story domain's libSQL database
(``configs/stories.db``), the same store as stories (``story_store.py``): one row per
card in ``global_characters`` / ``personas``, the full card dict (what the YAML held) as
a JSON ``payload`` column. Binary assets stay files — avatar ``<key>.png``,
``<key>.ref.png``, and the ``portraits/`` subdirectory are never touched here.

COPACKAGING: a card made FOR a story (its ``fields.story`` provenance) carries that
``story_key`` as a real column, so everything a story owns is selectable by one key —
``characters_for_story`` returns the story's generated pool. Shared library cards keep
``story_key=''`` and never cascade with a story (prune_orphan_characters owns their
lifecycle). Conventions mirror ``story_sessions.py``: shared ``story_store._connect``
(WAL + busy_timeout, optional Turso env), a module ``_SCHEMA``, an ``_inited`` guard,
additive ``ALTER`` migrations, and a lazy migration on first touch — any legacy
``configs/characters/<key>.yaml`` / ``configs/personas/<key>.yaml`` still on disk is
folded into the tables and renamed ``<key>.yaml.migrated`` (a corrupt file stays put
for manual inspection).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from . import story_store

_inited: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS global_characters (
    key       TEXT PRIMARY KEY,
    story_key TEXT NOT NULL DEFAULT '',   -- the story this card was generated for ('' = shared library)
    name      TEXT NOT NULL DEFAULT '',
    payload   TEXT NOT NULL DEFAULT '{}',   -- JSON object (the full character dict)
    updated   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_global_characters_story ON global_characters(story_key);

CREATE TABLE IF NOT EXISTS personas (
    key     TEXT PRIMARY KEY,
    name    TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',   -- JSON object (the full persona dict)
    updated REAL NOT NULL DEFAULT 0
);
"""

_TABLES = {"characters": "global_characters", "personas": "personas"}


def _conn(root: Path):
    con = story_store._connect(root)          # same db file, same pragmas, same Turso env
    key = str(story_store._db_path(root))
    if key not in _inited:
        con.executescript(_SCHEMA)
        _migrate_schema(con)
        con.commit()
        _migrate_yaml_cards(root, con)
        _backfill_story_keys(con)
        con.commit()
        _inited.add(key)
    return con


def _migrate_schema(con) -> None:
    """Additive column upgrades for DBs created before story_key existed."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(global_characters)").fetchall()}
    if cols and "story_key" not in cols:
        con.execute("ALTER TABLE global_characters ADD COLUMN story_key TEXT NOT NULL DEFAULT ''")


def _safe(key: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(key or "")).strip("_")


def _jdumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _jloads(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001 — corrupt cell surfaces as the default
        return default


def _card_story(data: dict) -> str:
    """A card's owning story: the `fields.story` provenance prune_orphan_characters uses."""
    fields = data.get("fields")
    return str(fields.get("story") or "") if isinstance(fields, dict) else ""


def _upsert(con, table: str, safe: str, data: dict) -> None:
    data = data if isinstance(data, dict) else {}
    if table == "global_characters":
        con.execute(
            f"INSERT OR REPLACE INTO {table} (key, story_key, name, payload, updated)"
            " VALUES (?,?,?,?,?)",
            (safe, _card_story(data), str(data.get("name", "") or ""), _jdumps(data), time.time()))
    else:
        con.execute(
            f"INSERT OR REPLACE INTO {table} (key, name, payload, updated) VALUES (?,?,?,?)",
            (safe, str(data.get("name", "") or ""), _jdumps(data), time.time()))


def _migrate_yaml_cards(root: Path, con) -> None:
    """Fold legacy per-card YAML files into the tables, then rename them .yaml.migrated.
    Only top-level ``*.yaml`` files — never ``*.png``/``*.ref.png`` or ``portraits/``."""
    for sub, table in _TABLES.items():
        d = root / "configs" / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.yaml")):
            safe = _safe(p.stem)
            if not safe:
                continue
            try:
                raw = yaml.safe_load(p.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("card file is not a mapping")
                _upsert(con, table, safe, raw)
                p.rename(p.with_name(p.name + ".migrated"))
            except Exception:  # noqa: BLE001 — a corrupt file stays put for manual inspection
                continue
    con.commit()


def _backfill_story_keys(con) -> None:
    """Stamp story_key on rows written before the column existed, from payload provenance.
    Idempotent (story_key='' guard)."""
    rows = con.execute("SELECT key, payload FROM global_characters WHERE story_key=''").fetchall()
    for k, payload in rows:
        skey = _card_story(_jloads(payload, {}))
        if skey:
            con.execute("UPDATE global_characters SET story_key=? WHERE key=?", (skey, k))


def _load_table(root: Path, table: str) -> dict[str, dict]:
    rows = _conn(root).execute(f"SELECT key, payload FROM {table} ORDER BY key").fetchall()
    out: dict[str, dict] = {}
    for k, payload in rows:
        data = _jloads(payload, {})
        out[k] = data if isinstance(data, dict) else {}
    return out


def load_characters(root: Path) -> dict[str, dict]:
    """The global character library: {key: card dict} (the keys the YAML stems had)."""
    return _load_table(root, _TABLES["characters"])


def load_personas(root: Path) -> dict[str, dict]:
    """The persona library: {key: persona dict} (the keys the YAML stems had)."""
    return _load_table(root, _TABLES["personas"])


def characters_for_story(root: Path, story_key: str) -> dict[str, dict]:
    """The story's own generated cards (story_key-stamped) — its copackaged pool."""
    rows = _conn(root).execute(
        "SELECT key, payload FROM global_characters WHERE story_key=? ORDER BY key",
        (story_key,)).fetchall()
    return {k: (_jloads(p, {}) or {}) for k, p in rows}


def upsert_character(root: Path, key: str, cdata: dict) -> str:
    """Insert/replace one global character card. Returns the stored (sanitized) key."""
    safe = _safe(key)
    if not safe:
        return ""
    con = _conn(root)
    _upsert(con, _TABLES["characters"], safe, cdata)
    con.commit()
    return safe


def upsert_persona(root: Path, key: str, pdata: dict) -> str:
    """Insert/replace one persona. Returns the stored (sanitized) key."""
    safe = _safe(key)
    if not safe:
        return ""
    con = _conn(root)
    _upsert(con, _TABLES["personas"], safe, pdata)
    con.commit()
    return safe


def delete_character(root: Path, key: str) -> None:
    safe = _safe(key)
    if not safe:
        return
    con = _conn(root)
    con.execute("DELETE FROM global_characters WHERE key=?", (safe,))
    con.commit()


def delete_persona(root: Path, key: str) -> None:
    safe = _safe(key)
    if not safe:
        return
    con = _conn(root)
    con.execute("DELETE FROM personas WHERE key=?", (safe,))
    con.commit()
