"""One-time migration: convert legacy ``<key>.db`` (libSQL) story files to ``<key>.json``.

The story store moved from SQLite to plain JSON (see story_db.py). This migrator reads each
legacy ``.db`` with a VENDORED minimal libSQL reader (story_db.py no longer imports libsql),
writes the equivalent ``.json`` via the new ``save_story``, extracts any embedded sessions to
``configs/story_sessions/<sid>.json``, and renames the ``.db`` to ``.db.migrated`` so it's
reversible but no longer picked up by the loader's ``*.json`` glob.

The loader calls ``migrate_dir`` lazily on startup, so the user just restarts and it happens
transparently. Or run it manually: ``python -m loom.stories.migrate_db_to_json``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# The on-disk sessions live in configs/story_sessions/, resolved relative to the stories dir.
_SESSIONS_SUB = ("..", "story_sessions")

_REL_COLS = ("id,source,target,nature,dynamic,stance,note,value,"
             "target_stance,target_dynamic,potential,trajectory")
_JSON_LISTS = ("scenes", "arcs")   # legacy per-table JSON lists (places collapsed into locations)


def _rel_row(row) -> dict:
    i, s, t, n, d, st, nt, v, ts, td, po, tr = row
    out = {"id": i, "source": s, "target": t, "nature": n, "dynamic": d, "stance": st, "note": nt,
           "target_stance": ts or "", "target_dynamic": td or "",
           "potential": po or "", "trajectory": tr or ""}
    if v is not None:
        out["value"] = v
    return out


def _read_legacy_db(path: Path) -> tuple[dict, dict, dict]:
    """Vendor the OLD libSQL read: (story_dict, characters_dict, sessions_dict).
    Sessions are {sid: payload} so the caller can write each to its own file."""
    import libsql  # local import — only the migrator needs it now
    con = libsql.connect(str(path))
    row = con.execute("SELECT json FROM meta WHERE k='story'").fetchone()
    story = json.loads(row[0]) if row and row[0] else {}
    story["cast"] = [{"character": c, "primary": bool(p), **({"outfit": o} if o else {}),
                      **({"home": h} if h else {})}
                     for (c, p, o, h) in con.execute(
                         "SELECT character,is_primary,outfit,home FROM cast_members ORDER BY ord").fetchall()]
    story["relationships"] = [_rel_row(r) for r in con.execute(
        f"SELECT {_REL_COLS} FROM relationships").fetchall()]
    story["locations"] = [json.loads(j) for (j,) in con.execute(
        "SELECT json FROM locations ORDER BY ord").fetchall()]
    for grp in _JSON_LISTS:
        story[grp] = [json.loads(j) for (j,) in con.execute(
            f"SELECT json FROM {grp} ORDER BY ord").fetchall()]
    # Legacy DBs had a separate `places` table. Collapse: each place's scenes move onto the
    # matching location (place.id was `p_<loc_id>`), then places are dropped. Defensive — the
    # one real .db was already migrated, but this covers any stragglers.
    try:
        places = [json.loads(j) for (j,) in con.execute("SELECT json FROM places ORDER BY ord").fetchall()]
    except Exception:  # noqa: BLE001 — pre-places DBs had no such table
        places = []
    if places:
        loc_by_id = {l.get("id"): l for l in story["locations"] if isinstance(l, dict)}
        for p in places:
            pid = (p.get("id") or "")
            loc = loc_by_id.get(pid[2:] if pid.startswith("p_") else pid)
            if loc is not None and p.get("scenes"):
                loc["scenes"] = p["scenes"]
    story["connections"] = [json.loads(j) for (j,) in con.execute(
        "SELECT json FROM connections").fetchall()]
    characters = {k: json.loads(j) for (k, j) in con.execute(
        "SELECT key,json FROM characters ORDER BY ord").fetchall()}
    sessions = {sid: json.loads(j) for (sid, j) in con.execute(
        "SELECT sid,json FROM sessions").fetchall()}
    return story, characters, sessions


def _safe_sid(sid: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(sid or "")).strip("_")


def migrate_dir(story_dir: Path, *, verbose: bool = False) -> int:
    """Migrate every ``<key>.db`` in ``story_dir`` to ``<key>.json``. Idempotent: skips a key
    whose ``.json`` already exists. Returns the count migrated. Sessions go to
    ``story_dir/../story_sessions/<sid>.json``."""
    from ...stories.records import store as SDB
    story_dir = Path(story_dir)
    sess_dir = story_dir.joinpath(*_SESSIONS_SUB)
    migrated = 0
    for db_path in sorted(story_dir.glob("*.db")):
        # Skip already-renamed backups (.db.migrated) and anything that isn't a plain .db
        if db_path.suffix != ".db":
            continue
        json_path = db_path.with_suffix(".json")
        if json_path.is_file():
            continue                       # already migrated — leave the .db alone
        try:
            story, characters, sessions = _read_legacy_db(db_path)
        except Exception as exc:  # noqa: BLE001 — don't let one bad DB sink the load
            if verbose:
                print(f"  ✗ {db_path.name}: read failed ({exc}) — leaving as-is")
            continue
        SDB.save_story(json_path, story, characters)
        # Extract any embedded sessions to their own files.
        if sessions:
            sess_dir.mkdir(parents=True, exist_ok=True)
            for sid, payload in sessions.items():
                safe = _safe_sid(sid)
                if not safe:
                    continue
                (sess_dir / f"{safe}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # Rename the .db so it's reversible but invisible to the loader's *.json glob.
        db_path.rename(db_path.with_suffix(".db.migrated"))
        migrated += 1
        if verbose:
            n_sess = len(sessions)
            print(f"  ✓ {db_path.stem}: story + {len(characters)} char(s)"
                  + (f" + {n_sess} session(s)" if n_sess else ""))
    return migrated


if __name__ == "__main__":
    import sys
    # Default to configs/stories under the repo root (two levels up from this file).
    repo = Path(__file__).resolve().parents[2]
    sdir = repo / "configs" / "stories"
    if len(sys.argv) > 1:
        sdir = Path(sys.argv[1])
    if not sdir.is_dir():
        print(f"no stories dir at {sdir}")
        sys.exit(1)
    print(f"migrating legacy .db → .json in {sdir}")
    n = migrate_dir(sdir, verbose=True)
    print(f"done — {n} story file(s) migrated (.db renamed to .db.migrated for rollback)")
