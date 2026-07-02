"""Sources DB — raw harvested reference material (configs/sources.db, stdlib sqlite).

The exemplar decks (`_*_exemplars` lorebooks) hold DISTILLED study cards; this DB holds the
RAW harvested substance they're distilled from — wiki pages, AniList descriptions, plot
summaries, quote pages — with provenance (url, series, fetched_at). Distillation compresses
source FACTS into card form (a model can't invent substance that must come from the source),
and `distilled_at` tracks what's been processed so harvest/distill are independently rerunnable.

Kinds: character | location | premise | arc | voice | plot.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def _db(root: Path) -> sqlite3.Connection:
    p = Path(root) / "configs" / "sources.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE IF NOT EXISTS sources(
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        series TEXT DEFAULT '',
        title TEXT DEFAULT '',
        url TEXT DEFAULT '',
        raw TEXT DEFAULT '',
        meta TEXT DEFAULT '{}',
        fetched_at REAL,
        distilled_at REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sources_kind ON sources(kind, distilled_at)")
    return con


def upsert_source(root: Path, *, id: str, kind: str, series: str = "", title: str = "",
                  url: str = "", raw: str = "", meta: dict | None = None) -> None:
    """Insert or refresh one raw source (re-harvest updates raw, PRESERVES distilled_at)."""
    with closing(_db(root)) as con, con:
        con.execute(
            """INSERT INTO sources(id, kind, series, title, url, raw, meta, fetched_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET series=excluded.series, title=excluded.title,
                 url=excluded.url, raw=excluded.raw, meta=excluded.meta,
                 fetched_at=excluded.fetched_at""",
            (id, kind, series, title, url, raw,
             json.dumps(meta or {}, ensure_ascii=False), time.time()))


def undistilled(root: Path, kind: str, limit: int = 20) -> list[dict]:
    with closing(_db(root)) as con, con:
        rows = con.execute(
            """SELECT id, kind, series, title, url, raw, meta FROM sources
               WHERE kind=? AND distilled_at IS NULL AND length(raw) > 80
               ORDER BY fetched_at LIMIT ?""", (kind, limit)).fetchall()
    return [{"id": r[0], "kind": r[1], "series": r[2], "title": r[3], "url": r[4],
             "raw": r[5], "meta": json.loads(r[6] or "{}")} for r in rows]


def mark_distilled(root: Path, ids: list[str]) -> None:
    if not ids:
        return
    with closing(_db(root)) as con, con:
        con.executemany("UPDATE sources SET distilled_at=? WHERE id=?",
                        [(time.time(), i) for i in ids])


def stats(root: Path) -> dict:
    with closing(_db(root)) as con, con:
        rows = con.execute(
            """SELECT kind, COUNT(*), SUM(CASE WHEN distilled_at IS NULL THEN 0 ELSE 1 END)
               FROM sources GROUP BY kind""").fetchall()
    return {r[0]: {"total": r[1], "distilled": r[2]} for r in rows}


def demo() -> None:
    """Self-check on a temp root. Run: python -m loom.server.services.sources"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        upsert_source(root, id="character:test:erin", kind="character", series="test",
                      title="Erin", url="http://x", raw="x" * 100)
        upsert_source(root, id="character:test:erin", kind="character", series="test",
                      title="Erin2", url="http://x", raw="y" * 100)   # refresh, same id
        assert len(undistilled(root, "character")) == 1
        assert undistilled(root, "character")[0]["title"] == "Erin2"
        mark_distilled(root, ["character:test:erin"])
        assert undistilled(root, "character") == []
        assert stats(root)["character"] == {"total": 1, "distilled": 1}
    print("sources demo ok")


if __name__ == "__main__":
    demo()
