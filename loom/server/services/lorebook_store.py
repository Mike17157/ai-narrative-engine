"""Lorebook storage on libSQL (the Turso engine) with native FTS5/BM25 retrieval.

ALL lorebooks live in one libSQL database — the craft lorebook, the workshop's world
lore, and (dynamically) the simulation's per-character + world knowledge. Storage is a
local file by default (``configs/lorebooks.db``); set ``TURSO_DATABASE_URL`` (+
``TURSO_AUTH_TOKEN``) to use a Turso cloud DB as an embedded replica (local-speed reads,
synced to the cloud). Retrieval uses FTS5's built-in ``bm25()`` ranking — entries can be
upserted/edited at runtime, so the lorebook is fully dynamic.

Entries are keyed by (scope, entry_id). A scope is a namespace, e.g. ``_craft``,
``_global``, a character key, or ``sim-<id>-<character>``.

Each scope is also a first-class *book* with metadata (name, description, rating
sfw/nsfw, category, builtin flag, enabled) stored in the ``books`` table — that's what
the Lorebook manager UI browses and what a chat thread attaches by id. A book row is
auto-created the first time an entry is upserted into a new scope; a handful of starter
books (craft / intimacy / RPG-sim / worldbuilding) are seeded on first init.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import libsql

from ...config.schema import LoreEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lore (
  scope     TEXT NOT NULL,
  entry_id  TEXT NOT NULL,
  title     TEXT DEFAULT '',
  keywords  TEXT DEFAULT '[]',   -- JSON array
  content   TEXT DEFAULT '',
  enabled   INTEGER DEFAULT 1,
  priority  INTEGER DEFAULT 0,
  facet     TEXT DEFAULT '',
  source    TEXT DEFAULT '',     -- '' = authored, 'auto' = written back by the state engine
  trig      TEXT DEFAULT 'input',-- 'input' (transcript/BM25) | 'output' (guard scan of the reply)
  script    TEXT DEFAULT '',     -- named action from the registry ('' = inject text only)
  embedding F32_BLOB(384),       -- semantic vector (bge-small) for hybrid retrieval; NULL = not embedded
  updated   REAL DEFAULT 0,
  PRIMARY KEY (scope, entry_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS lore_fts USING fts5(
  title, keywords, content, content='lore', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS lore_ai AFTER INSERT ON lore BEGIN
  INSERT INTO lore_fts(rowid, title, keywords, content)
  VALUES (new.rowid, new.title, new.keywords, new.content);
END;
CREATE TRIGGER IF NOT EXISTS lore_ad AFTER DELETE ON lore BEGIN
  INSERT INTO lore_fts(lore_fts, rowid, title, keywords, content)
  VALUES ('delete', old.rowid, old.title, old.keywords, old.content);
END;
CREATE TRIGGER IF NOT EXISTS lore_au AFTER UPDATE ON lore BEGIN
  INSERT INTO lore_fts(lore_fts, rowid, title, keywords, content)
  VALUES ('delete', old.rowid, old.title, old.keywords, old.content);
  INSERT INTO lore_fts(rowid, title, keywords, content)
  VALUES (new.rowid, new.title, new.keywords, new.content);
END;
CREATE TABLE IF NOT EXISTS books (
  id          TEXT PRIMARY KEY,
  name        TEXT DEFAULT '',
  description TEXT DEFAULT '',
  rating      TEXT DEFAULT 'sfw',    -- 'sfw' | 'nsfw'
  category    TEXT DEFAULT 'world',  -- world|story|rpg|character|craft|intimacy|guard|function
  builtin     INTEGER DEFAULT 0,     -- seeded/reserved; UI guards destructive edits
  enabled     INTEGER DEFAULT 1,
  preset      TEXT DEFAULT '',       -- bound model PRESET id (Function→Lorebook→Preset); '' = none
  scope       TEXT DEFAULT 'global', -- 'global' = attachable anywhere | 'local' = system-native or
                                      -- belongs to one place (function/guard/craft + character books)
  archived    INTEGER DEFAULT 0,     -- soft-deleted into the recycle bin (hidden from lists/pickers/retrieval)
  updated     REAL DEFAULT 0
);
"""

# Categories whose books are LOCAL by default — not offered in the general "attach a
# lorebook" picker (they're system-native or belong to a single character/flow).
_LOCAL_CATEGORIES = {"function", "guard", "craft", "character"}


def _default_scope(category: str | None) -> str:
    return "local" if (category or "") in _LOCAL_CATEGORIES else "global"

# Books that exist by convention even before the manager writes metadata for them, so
# the manager can present them with a friendly name/rating instead of a bare scope.
RESERVED_BOOKS = {
    "_craft":     {"name": "Storytelling Craft", "category": "craft",     "rating": "sfw",
                   "description": "Modern storytelling theory the AI reasons with. Always-on for the workshop."},
    "_global":    {"name": "Global Lore",        "category": "world",     "rating": "sfw",
                   "description": "World facts shared across every story and chat."},
    "_nsfw":      {"name": "Intimacy & NSFW",    "category": "intimacy",  "rating": "nsfw",
                   "description": "Adult intimacy guidance, injected when the scene calls for it."},
    "_nsfw_acts": {"name": "NSFW — Act Guides",  "category": "intimacy",  "rating": "nsfw",
                   "description": "Specific act/position guidance, triggered by keyword."},
    "_refusal":   {"name": "Refusal Triggers",   "category": "guard",     "rating": "sfw",
                   "description": "Out-of-character / AI-voice phrases that mark a refusal. The "
                                  "state engine watches output for these and re-runs on the fallback model."},
}

_inited: set[str] = set()


def _db_path(root: Path) -> Path:
    return root / "configs" / "lorebooks.db"


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
        return con
    return libsql.connect(local)


def _conn(root: Path):
    con = _connect(root)
    key = str(_db_path(root))
    if key not in _inited:
        con.executescript(_SCHEMA)
        # Additive migrations for DBs created before these columns existed.
        for _ddl in ("ALTER TABLE lore ADD COLUMN source TEXT DEFAULT ''",
                     "ALTER TABLE lore ADD COLUMN trig TEXT DEFAULT 'input'",
                     "ALTER TABLE lore ADD COLUMN script TEXT DEFAULT ''",
                     "ALTER TABLE lore ADD COLUMN embedding F32_BLOB(384)",
                     "ALTER TABLE books ADD COLUMN preset TEXT DEFAULT ''",
                     "ALTER TABLE books ADD COLUMN archived INTEGER DEFAULT 0"):
            try:
                con.execute(_ddl)
            except Exception:  # noqa: BLE001 — already present
                pass
        # scope column + a ONE-TIME category backfill: the ALTER throws once the column
        # exists, so the UPDATE only runs the first time the column is added (never clobbers
        # a user's later global/local override).
        try:
            con.execute("ALTER TABLE books ADD COLUMN scope TEXT DEFAULT 'global'")
            con.execute("UPDATE books SET scope='local' WHERE category IN "
                        "('function','guard','craft','character')")
        except Exception:  # noqa: BLE001 — already present
            pass
        con.commit()
        _inited.add(key)
        _migrate_json(root, con)
        _seed_books(root, con)
    return con


def _row_to_entry(r) -> LoreEntry:
    try:
        kw = json.loads(r[3]) if r[3] else []
    except Exception:  # noqa: BLE001
        kw = []
    return LoreEntry(id=r[1], title=r[2] or "", keywords=kw if isinstance(kw, list) else [],
                     content=r[4] or "", enabled=bool(r[5]), priority=int(r[6] or 0),
                     facet=r[7] or "", source=(r[8] if len(r) > 8 else "") or "",
                     trigger=(r[9] if len(r) > 9 else "input") or "input",
                     script=(r[10] if len(r) > 10 else "") or "")


# ── One-time migration of the legacy JSON lorebooks ─────────────────────────────

def _migrate_json(root: Path, con) -> None:
    d = root / "configs" / "lorebooks"
    if not d.is_dir():
        return
    cur = con.execute("SELECT COUNT(*) FROM lore")
    if (cur.fetchone() or [0])[0]:
        return  # already populated
    for p in sorted(d.glob("*.json")):
        scope = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for e in (data.get("entries") or []):
            _insert(con, scope, LoreEntry(**e))
    con.commit()


# ── Book metadata + seeding ──────────────────────────────────────────────────────

def _book_upsert(con, book_id: str, *, builtin: bool = False, **f) -> None:
    """Insert a book row, or fill in only the columns the caller supplied (COALESCE so
    a re-seed never clobbers user edits to name/description/rating/etc.)."""
    scope = f.get("scope") or _default_scope(f.get("category", "world"))
    con.execute(
        "INSERT INTO books(id,name,description,rating,category,builtin,enabled,preset,scope,updated) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "name=COALESCE(?,name), description=COALESCE(?,description), "
        "rating=COALESCE(?,rating), category=COALESCE(?,category), preset=COALESCE(?,preset), "
        "scope=COALESCE(?,scope)",
        (book_id, f.get("name", ""), f.get("description", ""), f.get("rating", "sfw"),
         f.get("category", "world"), 1 if builtin else 0, 1, f.get("preset", ""), scope, time.time(),
         f.get("name"), f.get("description"), f.get("rating"), f.get("category"), f.get("preset"),
         f.get("scope")),
    )


def _seed_books(root: Path, con) -> None:
    """Register reserved books + seed a few starter content books on first init.
    Idempotent: metadata is only filled where missing; example entries are inserted
    only into scopes that have none, so user edits/deletions are never resurrected."""
    starter_ids = {b[0] for b in _STARTER_BOOKS}
    # Reserved books always get friendly metadata (builtin) — except ones the starter
    # loop owns (it seeds their example content + metadata together).
    for bid, meta in RESERVED_BOOKS.items():
        if bid not in starter_ids:
            _book_upsert(con, bid, builtin=True, **meta)

    # Starter content books seed once per book — guarded by the book row's existence,
    # so they appear on existing DBs too but are never resurrected after the user
    # deletes/empties them.
    for bid, meta, entries in _STARTER_BOOKS:
        first_time = not con.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone()
        is_reserved = bid in RESERVED_BOOKS
        _book_upsert(con, bid, builtin=is_reserved, **meta)
        has_entries = (con.execute("SELECT COUNT(*) FROM lore WHERE scope=?", (bid,)).fetchone() or [0])[0]
        if first_time and not has_entries:
            for e in entries:
                _insert(con, bid, LoreEntry(**e))

    # Keep the managed `_refusal` floor entry current: convert the original one-phrase-per-
    # entry format to the trigger→action model AND refresh the phrase set when it changes
    # (e.g. the high-precision retune). Only the floor entry + legacy input rows are touched;
    # user-added OUTPUT rules with other ids are preserved.
    canon = next((e for bid, _m, es in _STARTER_BOOKS if bid == "_refusal" for e in es
                  if e.get("id") == "refusal-floor"), None)
    if canon:
        row = con.execute(
            "SELECT keywords FROM lore WHERE scope='_refusal' AND entry_id='refusal-floor'").fetchone()
        try:
            cur_kw = json.loads(row[0]) if row and row[0] else None
        except Exception:  # noqa: BLE001
            cur_kw = None
        legacy = con.execute(
            "SELECT 1 FROM lore WHERE scope='_refusal' AND (trig IS NULL OR trig='input') LIMIT 1").fetchone()
        if cur_kw != canon["keywords"] or legacy:
            con.execute("DELETE FROM lore WHERE scope='_refusal' "
                        "AND (entry_id='refusal-floor' OR trig IS NULL OR trig='input')")
            _insert(con, "_refusal", LoreEntry(**canon))

    # Ensure new built-in function entries land in EXISTING DBs (insert by id only when
    # missing, so user edits/deletions of the others are never clobbered or resurrected).
    for bid, entries in (("_graph_fns", _GRAPH_FNS_ENTRIES), ("_location_fns", _LOCATION_FNS_ENTRIES),
                         ("_character_fns", _CHARACTER_FNS_ENTRIES)):
        if con.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone():
            have = {r[0] for r in con.execute("SELECT entry_id FROM lore WHERE scope=?", (bid,)).fetchall()}
            for e in entries:
                if e["id"] not in have:
                    _insert(con, bid, LoreEntry(**e))

    # Bind each built-in function book to its OWN model PRESET (Function→Lorebook→Preset), so
    # every flow uses the right job preset. Set when unset; also migrate the early builds that
    # were all bound to 'story_consultant' to their proper per-function preset.
    for bid, pid in (("_graph_fns", "story_consultant"), ("_location_fns", "location_builder"),
                     ("_character_fns", "character_builder")):
        row = con.execute("SELECT preset FROM books WHERE id=?", (bid,)).fetchone()
        if row is not None and (not (row[0] or "") or row[0] == "story_consultant"):
            con.execute("UPDATE books SET preset=? WHERE id=?", (pid, bid))
    con.commit()


def ensure_book(root: Path, scope: str) -> None:
    """Make sure a book row exists for *scope* (auto-named from the scope). Called
    whenever an entry is upserted into a scope the manager hasn't registered yet."""
    con = _conn(root)
    if con.execute("SELECT 1 FROM books WHERE id=?", (scope,)).fetchone():
        return
    meta = RESERVED_BOOKS.get(scope)
    if meta:
        _book_upsert(con, scope, builtin=True, **meta)
    else:
        name = scope.lstrip("_").replace("-", " ").replace("_", " ").strip().title() or scope
        rating = "nsfw" if "nsfw" in scope.lower() else "sfw"
        cat = "character" if not scope.startswith("_") else "world"
        _book_upsert(con, scope, builtin=False, name=name, rating=rating, category=cat)
    con.commit()


def list_books(root: Path, archived: bool = False) -> list[dict]:
    """Books with live entry counts. `archived=False` (default) returns the live library;
    `archived=True` returns the recycle bin. Orphan scopes (legacy data) surface too."""
    con = _conn(root)
    # Backfill any orphan scopes so the manager shows everything.
    orphans = con.execute(
        "SELECT DISTINCT scope FROM lore WHERE scope NOT IN (SELECT id FROM books)").fetchall()
    for (s,) in orphans:
        ensure_book(root, s)
    rows = con.execute(
        "SELECT b.id,b.name,b.description,b.rating,b.category,b.builtin,b.enabled,b.preset,b.scope,"
        "(SELECT COUNT(*) FROM lore l WHERE l.scope=b.id) AS n "
        "FROM books b WHERE COALESCE(b.archived,0)=? ORDER BY b.builtin DESC, b.name",
        (1 if archived else 0,)).fetchall()
    return [{"id": r[0], "name": r[1] or r[0], "description": r[2] or "", "rating": r[3] or "sfw",
             "category": r[4] or "world", "builtin": bool(r[5]), "enabled": bool(r[6]),
             "preset": r[7] or "", "scope": r[8] or "global", "entries": int(r[9] or 0)} for r in rows]


def set_archived(root: Path, book_id: str, archived: bool) -> None:
    """Soft-delete a book into the recycle bin (archived=True) or restore it (False)."""
    con = _conn(root)
    con.execute("UPDATE books SET archived=?, updated=? WHERE id=?",
                (1 if archived else 0, time.time(), book_id))
    con.commit()


def get_book(root: Path, book_id: str) -> dict | None:
    con = _conn(root)
    r = con.execute(
        "SELECT id,name,description,rating,category,builtin,enabled,preset,scope,COALESCE(archived,0) "
        "FROM books WHERE id=?", (book_id,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1] or r[0], "description": r[2] or "", "rating": r[3] or "sfw",
            "category": r[4] or "world", "builtin": bool(r[5]), "enabled": bool(r[6]),
            "preset": r[7] or "", "scope": r[8] or "global", "archived": bool(r[9])}


def upsert_book(root: Path, book_id: str, **fields) -> dict:
    """Create or update a book's metadata (name/description/rating/category/enabled)."""
    con = _conn(root)
    exists = con.execute("SELECT builtin FROM books WHERE id=?", (book_id,)).fetchone()
    if exists:
        sets, vals = [], []
        for col in ("name", "description", "rating", "category", "preset", "scope"):
            if fields.get(col) is not None:
                sets.append(f"{col}=?"); vals.append(fields[col])
        if fields.get("enabled") is not None:
            sets.append("enabled=?"); vals.append(1 if fields["enabled"] else 0)
        sets.append("updated=?"); vals.append(time.time())
        con.execute(f"UPDATE books SET {', '.join(sets)} WHERE id=?", (*vals, book_id))
    else:
        scope = fields.get("scope") or _default_scope(fields.get("category", "world"))
        con.execute(
            "INSERT INTO books(id,name,description,rating,category,builtin,enabled,preset,scope,updated) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (book_id, fields.get("name") or book_id, fields.get("description", ""),
             fields.get("rating", "sfw"), fields.get("category", "world"), 0,
             1 if fields.get("enabled", True) else 0, fields.get("preset", ""), scope, time.time()))
    con.commit()
    return get_book(root, book_id) or {}


def delete_book(root: Path, book_id: str) -> None:
    """Delete a book and ALL its entries. Reserved/builtin books are kept (metadata only)."""
    con = _conn(root)
    con.execute("DELETE FROM lore WHERE scope=?", (book_id,))
    if book_id not in RESERVED_BOOKS:
        con.execute("DELETE FROM books WHERE id=?", (book_id,))
    con.commit()


# ── CRUD ────────────────────────────────────────────────────────────────────────

def _insert(con, scope: str, e: LoreEntry) -> None:
    con.execute(
        "INSERT OR REPLACE INTO lore(scope,entry_id,title,keywords,content,enabled,priority,facet,source,trig,script,updated) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (scope, e.id, e.title, json.dumps(e.keywords, ensure_ascii=False), e.content,
         1 if e.enabled else 0, e.priority, e.facet, getattr(e, "source", "") or "",
         getattr(e, "trigger", "input") or "input", getattr(e, "script", "") or "", time.time()),
    )


def load_lorebook(root: Path, scope: str) -> list[LoreEntry]:
    con = _conn(root)
    rows = con.execute(
        "SELECT scope,entry_id,title,keywords,content,enabled,priority,facet,source,trig,script FROM lore "
        "WHERE scope=? ORDER BY priority DESC", (scope,)).fetchall()
    return [_row_to_entry(r) for r in rows]


def save_lorebook(root: Path, scope: str, entries: list[LoreEntry]) -> None:
    """Replace ALL entries in a scope."""
    con = _conn(root)
    con.execute("DELETE FROM lore WHERE scope=?", (scope,))
    for e in entries:
        _insert(con, scope, e)
    con.commit()


def _embed_text(title: str, keywords, content: str) -> str:
    """The passage text we embed for an entry (title + triggers + body)."""
    return f"{title}. {' '.join(keywords or [])}. {content}".strip()


def _set_embedding(con, scope: str, eid: str, vec: list[float]) -> None:
    from . import embeddings as _emb
    con.execute("UPDATE lore SET embedding=vector32(?) WHERE scope=? AND entry_id=?",
                (_emb.to_sql(vec), scope, eid))


def _embed_entry(con, scope: str, e: LoreEntry) -> None:
    """Compute + store an entry's vector (best-effort; no-op if no embedder)."""
    from . import embeddings as _emb
    if not _emb.available():
        return
    txt = _embed_text(e.title, e.keywords, e.content)
    if not txt:
        return
    vecs = _emb.embed_passages([txt])
    if vecs:
        _set_embedding(con, scope, e.id, vecs[0])


def upsert_entry(root: Path, scope: str, entry: LoreEntry) -> LoreEntry:
    """Dynamic insert-or-update of one entry (used by the lore generator / scribe)."""
    con = _conn(root)
    _insert(con, scope, entry)
    _embed_entry(con, scope, entry)   # keep the semantic vector in sync (best-effort)
    con.commit()
    ensure_book(root, scope)
    return entry


def backfill_embeddings(root: Path, limit: int = 1000) -> int:
    """Embed entries that don't have a vector yet (e.g. seeded/imported/legacy rows).
    Idempotent; safe to run at startup in the background. Returns how many were embedded."""
    from . import embeddings as _emb
    if not _emb.available():
        return 0
    con = _conn(root)
    rows = con.execute(
        "SELECT scope,entry_id,title,keywords,content FROM lore "
        "WHERE embedding IS NULL AND (content<>'' OR title<>'') LIMIT ?", (limit,)).fetchall()
    if not rows:
        return 0
    texts = []
    for r in rows:
        try:
            kw = json.loads(r[3]) if r[3] else []
        except Exception:  # noqa: BLE001
            kw = []
        texts.append(_embed_text(r[2] or "", kw, r[4] or ""))
    vecs = _emb.embed_passages(texts)
    if not vecs:
        return 0
    n = 0
    for r, v in zip(rows, vecs):
        _set_embedding(con, r[0], r[1], v)
        n += 1
    con.commit()
    return n


def delete_entry(root: Path, scope: str, entry_id: str) -> None:
    con = _conn(root)
    con.execute("DELETE FROM lore WHERE scope=? AND entry_id=?", (scope, entry_id))
    con.commit()


def all_scopes(root: Path) -> list[str]:
    con = _conn(root)
    return [r[0] for r in con.execute("SELECT DISTINCT scope FROM lore ORDER BY scope").fetchall()]


# ── Retrieval — HYBRID: FTS5 BM25 (lexical) + native vector cosine (semantic) ────
# BM25 nails exact keywords / proper nouns; vector catches synonyms & paraphrase. We
# fuse the two rank lists with Reciprocal Rank Fusion. Degrades to pure BM25 when no
# embedder is available. `output` (guard) rules are never returned as lore.

_RRF_K = 60          # RRF damping; rank 0 → 1/60, rank 1 → 1/61, …
_COLS = ("l.scope,l.entry_id,l.title,l.keywords,l.content,l.enabled,l.priority,l.facet,l.source,l.trig,l.script")


def _fts_query(text: str) -> str:
    toks = [t for t in dict.fromkeys(re.findall(r"[a-z0-9]+", (text or "").lower())) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in toks[:48])


def _bm25_search(con, query: str, scopes: list[str], ph: str, limit: int, allow_nsfw: bool = True) -> list[LoreEntry]:
    q = _fts_query(query)
    if not q:
        return []
    nsfw_clause = "" if allow_nsfw else "AND (b.rating IS NULL OR b.rating <> 'nsfw') "
    sql = (
        f"SELECT {_COLS}, bm25(lore_fts, 5.0, 10.0, 1.0) AS rank "
        "FROM lore_fts JOIN lore l ON l.rowid = lore_fts.rowid "
        "LEFT JOIN books b ON b.id = l.scope "
        "WHERE l.enabled=1 AND (b.enabled IS NULL OR b.enabled=1) AND COALESCE(b.archived,0)=0 "
        "AND (l.trig IS NULL OR l.trig='input') "
        f"{nsfw_clause}"
        f"AND l.scope IN ({ph}) AND lore_fts MATCH ? ORDER BY rank LIMIT ?"
    )
    try:
        rows = con.execute(sql, (*scopes, q, limit)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_row_to_entry(r) for r in rows]


def _vector_search(con, query: str, scopes: list[str], ph: str, limit: int, allow_nsfw: bool = True) -> list[LoreEntry]:
    from . import embeddings as _emb
    if not _emb.available():
        return []
    vec = _emb.embed_query(query)
    if not vec:
        return []
    nsfw_clause = "" if allow_nsfw else "AND (b.rating IS NULL OR b.rating <> 'nsfw') "
    sql = (
        f"SELECT {_COLS}, vector_distance_cos(l.embedding, vector32(?)) AS d "
        "FROM lore l LEFT JOIN books b ON b.id = l.scope "
        "WHERE l.enabled=1 AND (b.enabled IS NULL OR b.enabled=1) AND COALESCE(b.archived,0)=0 "
        "AND (l.trig IS NULL OR l.trig='input') AND l.embedding IS NOT NULL "
        f"{nsfw_clause}"
        f"AND l.scope IN ({ph}) ORDER BY d LIMIT ?"
    )
    try:
        rows = con.execute(sql, (_emb.to_sql(vec), *scopes, limit)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_row_to_entry(r) for r in rows]


def retrieve(root: Path, query: str, scopes: list[str], top_k: int = 5,
             one_per_facet: bool = True, allow_nsfw: bool = True) -> list[LoreEntry]:
    """Top-k entries across *scopes*, fusing lexical BM25 + semantic vector ranks (RRF),
    with a small priority nudge and one-entry-per-facet collapsing. `allow_nsfw=False`
    (the global content gate) excludes entries from nsfw-rated books."""
    scopes = [s for s in (scopes or []) if s]
    if not scopes:
        return []
    con = _conn(root)
    ph = ",".join("?" * len(scopes))
    pool = top_k * 5

    bm = _bm25_search(con, query, scopes, ph, pool, allow_nsfw)
    vec = _vector_search(con, query, scopes, ph, pool, allow_nsfw)
    if not bm and not vec:
        return []

    # Reciprocal Rank Fusion over the two lists, keyed by (scope, entry_id).
    scores: dict[tuple, float] = {}
    entries: dict[tuple, LoreEntry] = {}
    for lst in (bm, vec):
        for rank, e in enumerate(lst):
            k = (e.id, e.title)
            scores[k] = scores.get(k, 0.0) + 1.0 / (_RRF_K + rank)
            entries[k] = e
    # tiny priority nudge so authored importance still tips ties
    fused = sorted(entries.values(),
                   key=lambda e: -(scores[(e.id, e.title)] + int(e.priority or 0) * 0.001))

    if one_per_facet:
        seen: set[str] = set()
        kept = []
        for e in fused:
            if e.facet:
                if e.facet in seen:
                    continue
                seen.add(e.facet)
            kept.append(e)
        fused = kept
    return fused[:top_k]


def top_by_priority(root: Path, scope: str, n: int) -> list[LoreEntry]:
    con = _conn(root)
    rows = con.execute(
        "SELECT scope,entry_id,title,keywords,content,enabled,priority,facet,source,trig,script FROM lore "
        "WHERE scope=? AND enabled=1 AND content<>'' ORDER BY priority DESC LIMIT ?",
        (scope, n)).fetchall()
    return [_row_to_entry(r) for r in rows]


# ── Starter content (seeded once, on a fresh DB) ─────────────────────────────────
# Each: (book_id, metadata, [entry dicts]). Entries are keyword-triggered and editable
# in the manager; they're examples to build on, not load-bearing config.

def _gfn(fn: str, keywords: list) -> dict:
    """A function-book entry is now pure TRIGGER metadata: it NAMES a registered code
    script (loom/stories/scripts.py) and supplies the keywords that surface it. The logic,
    params, and description live in CODE — not in this row. Authoring a new built-in ==
    adding an @script function; the entry only decides when it's offered to the model."""
    return {"id": fn, "title": fn, "keywords": keywords, "facet": "fn",
            "content": json.dumps({"fn": fn}, ensure_ascii=False)}


# Each book triggers a set of registered scripts. The script CODE (and which artifact it
# mutates — dev graph / locations / cast) lives in stories/scripts.py; here we only choose
# the trigger words. The same engine is artifact-agnostic, so all three share one mechanism.
_GRAPH_FNS_ENTRIES = [
    _gfn("add_beat", ["add a beat", "new beat", "insert beat", "add beat", "another beat"]),
    _gfn("insert_between", ["insert between", "in between", "split the arrow", "between"]),
    _gfn("set_beat_field", ["rename", "retitle", "change the", "edit the beat", "set the", "update beat"]),
    _gfn("connect", ["connect", "branch", "link", "leads to", "arrow", "then", "sequence"]),
    _gfn("disconnect", ["disconnect", "unlink", "remove arrow", "detach"]),
    _gfn("delete_beat", ["delete", "remove beat", "drop the beat", "cut the beat"]),
    _gfn("move_beat", ["move", "reposition", "put after", "relocate", "moved"]),
    _gfn("reorder_beats", ["reorder", "re-order", "order the beats", "sequence them", "rearrange"]),
    _gfn("set_spine", ["wound", "lie", "truth", "logline", "spine", "misbelief"]),
]

_LOCATION_FNS_ENTRIES = [
    _gfn("add_location", ["add a location", "new location", "another place", "add place", "new place"]),
    _gfn("set_location_field", ["rename location", "change the place", "edit location", "set the location", "update place"]),
    _gfn("remove_location", ["remove location", "delete place", "drop the location", "cut the place"]),
    _gfn("set_start", ["start location", "opening location", "begins at", "starts in", "set start"]),
]

_CHARACTER_FNS_ENTRIES = [
    _gfn("add_character", ["add a character", "new character", "another character", "add npc", "new npc",
                           "add a cast member", "introduce a character"]),
    _gfn("set_character_field", ["rename character", "change the character", "edit character", "set the character",
                                 "update character", "change their role", "rewrite the persona"]),
    _gfn("remove_character", ["remove character", "delete character", "drop the character", "cut the character",
                              "remove npc", "kill off"]),
]


_STARTER_BOOKS = [
    ("_graph_fns", {"name": "Graph Functions", "category": "function", "rating": "sfw",
                    "description": "Functions the story workshop can call to edit the development graph. "
                                   "Each entry IS a function (its content is a JSON op-spec); its keywords "
                                   "are trigger terms. Attach this book to a step and author your own."},
     _GRAPH_FNS_ENTRIES),
    ("_location_fns", {"name": "Location Functions", "category": "function", "rating": "sfw",
                       "description": "Functions for editing the story's locations as a chat flow "
                                      "(operate on a {start, locations:[…]} document)."},
     _LOCATION_FNS_ENTRIES),
    ("_character_fns", {"name": "Character Functions", "category": "function", "rating": "sfw",
                        "description": "Functions for editing the story's cast as a chat flow "
                                       "(operate on a {cast:[…]} document)."},
     _CHARACTER_FNS_ENTRIES),
    ("rpg-sim", {"name": "RPG Simulation", "category": "rpg", "rating": "sfw",
                 "description": "Turn the chat into a lightweight tabletop RPG: skill checks, "
                                "combat turns, inventory, and consequences."}, [
        {"id": "resolution", "title": "Resolving uncertain actions", "priority": 3,
         "keywords": ["roll", "check", "attempt", "try to", "dice", "d20", "skill check"],
         "content": "When the player attempts something with a real chance of failure, resolve it as a "
                    "d20 check: roll 1d20, add the relevant ability modifier (-1 to +5), and compare to "
                    "a difficulty (Easy 8, Medium 13, Hard 17, Heroic 22). State the roll and the result "
                    "in-fiction. On a success the action works; on a failure it goes wrong in an "
                    "interesting way (a complication, not just 'nothing happens'). Never roll for trivial "
                    "or guaranteed actions."},
        {"id": "abilities", "title": "Ability scores", "priority": 2,
         "keywords": ["strength", "dexterity", "intelligence", "charisma", "stat", "ability", "modifier"],
         "content": "Characters use six abilities: Might, Agility, Wits, Resolve, Presence, Insight. Each "
                    "has a modifier from -1 (poor) to +5 (legendary), defaulting to +1. Pick the ability "
                    "that best fits the action being attempted and use its modifier on the check."},
        {"id": "combat", "title": "Combat turns", "priority": 2,
         "keywords": ["attack", "fight", "combat", "strike", "initiative", "enemy", "battle"],
         "content": "In combat, alternate turns: the player declares one action, you resolve it (attack = "
                    "Might or Agility check vs the foe's defense), then each present foe acts. Track rough "
                    "health as Healthy → Hurt → Bloodied → Down for everyone in the fight; describe wounds "
                    "narratively rather than with exact numbers."},
        {"id": "inventory", "title": "Inventory & items", "priority": 1,
         "keywords": ["inventory", "item", "loot", "equipment", "carry", "backpack", "pick up"],
         "content": "Track what the player is carrying. When they loot, buy, craft, or are given something, "
                    "add it; when they use a consumable, remove it. If asked, list the current inventory. "
                    "Keep it grounded — encumbrance matters and rare items should feel earned."},
        {"id": "consequences", "title": "Consequences & stakes", "priority": 1,
         "keywords": ["die", "death", "wound", "damage", "consequence", "fail", "risk"],
         "content": "Choices have lasting consequences: failures cost something (time, resources, trust, a "
                    "wound), and danger is real — the player can be hurt, captured, or killed if they take "
                    "reckless risks. Foreshadow stakes before they trigger so outcomes feel fair, not arbitrary."},
    ]),
    ("story-world", {"name": "Worldbuilding (template)", "category": "story", "rating": "sfw",
                     "description": "Starter world facts — factions, places, history, systems. Edit these "
                                    "to your setting; they trigger by keyword and keep the AI consistent."}, [
        {"id": "faction-example", "title": "Faction — The Ashen Concord", "priority": 1, "facet": "faction",
         "keywords": ["concord", "ashen", "guild", "order", "faction"],
         "content": "EXAMPLE — replace with your own. The Ashen Concord is a guild of memory-keepers who "
                    "trade in the recovered recollections of the dead. Respected and feared; their grey "
                    "robes mean a debt is being collected. Rivalry with the city watch runs deep."},
        {"id": "place-example", "title": "Location — Hollowmere", "priority": 1, "facet": "location",
         "keywords": ["hollowmere", "mere", "lake", "town"],
         "content": "EXAMPLE — replace with your own. Hollowmere is a lakeside town built on stilts above "
                    "black water. Fog never fully lifts; lanterns burn day and night. The locals don't "
                    "speak of what surfaces in the mere on the longest nights of winter."},
        {"id": "system-example", "title": "Magic — Resonance", "priority": 2, "facet": "system",
         "keywords": ["magic", "resonance", "spell", "power", "cast"],
         "content": "EXAMPLE — replace with your own. Magic ('Resonance') is sung, not spoken: a caster "
                    "matches the hidden pitch of a thing to bend it. Power scales with the singer's lung "
                    "and nerve, and a cracked note rebounds on the caster. There are no silent spells."},
        {"id": "history-example", "title": "History — The Sundering", "priority": 1, "facet": "history",
         "keywords": ["sundering", "history", "war", "ago", "fall"],
         "content": "EXAMPLE — replace with your own. Two centuries ago the Sundering split the old empire "
                    "when the sky-roads collapsed. Cities that once traded by air are now isolated; the "
                    "ruins of the roads still hang overhead, and salvaging them is lucrative and lethal."},
    ]),
    ("_nsfw", {"name": "Intimacy & NSFW", "category": "intimacy", "rating": "nsfw",
               "description": "Injected when the recent transcript hits these triggers. Edit the guidance "
                              "and triggers freely; adult-only and consent-first by design."}, [
        {"id": "tone", "title": "Intimate-scene craft", "priority": 2, "facet": "intimacy",
         "keywords": ["kiss", "kisses", "intimate", "bedroom", "undress", "touch", "embrace",
                      "make love", "naked", "aroused", "seduce"],
         "content": "When a scene turns intimate, slow down and write it with the same care as any other "
                    "beat: focus on sensation, emotion, and the shifting dynamic between the characters "
                    "rather than a clinical play-by-play. Keep everyone's voice and wants consistent with "
                    "who they are. All participants are consenting adults; enthusiastic, ongoing consent is "
                    "assumed and a 'no' or hesitation is always respected in the fiction."},
    ]),
    # Refusal triggers — a trigger→action rule: many keyword phrases, matched against the
    # START of model OUTPUT (AI-voice / out-of-character, so in-fiction "I can't" never trips
    # it), firing the `fallback` script (re-run on the fallback model). Add more entries with
    # different keyword sets → different scripts. Edit freely in the manager.
    ("_refusal", {"name": "Refusal Triggers", "category": "guard", "rating": "sfw",
                  "description": "Keyword phrases that mark a refusal in the reply; on a hit the engine "
                                 "runs the entry's script (fallback)."}, [
        {"id": "refusal-floor", "title": "Refusal phrases → fallback", "trigger": "output",
         "script": "fallback", "priority": 1, "content": "",
         # HIGH-PRECISION (AI-refusal register) so an in-fiction "I can't stay" never trips it.
         "keywords": ["as an ai", "as a language model", "i can't assist with", "i cannot assist with",
                      "i can't help with that request", "i'm not able to continue", "i'm unable to provide",
                      "i cannot provide", "i cannot fulfill", "against my guidelines", "content policy",
                      "i won't be able to help with"]},
    ]),
]
