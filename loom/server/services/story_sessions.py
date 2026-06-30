"""Server-side checkpoints for story-building sessions.

A session is the durable state of a console: the conversation, the working development
graph, and the latest draft — persisted as JSON under configs/story_sessions/{id}.json
so a consultation resumes across reloads/devices instead of relying on client memory.
Keyed by an opaque id the client owns (e.g. a wizard uuid, or `story-<key>`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _dir(root: Path) -> Path:
    return root / "configs" / "story_sessions"


def _safe(sid: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(sid or "")).strip("_")


def _story_db(root: Path, sid: str):
    """The owning story's DB for a story-bound sid (``play-<key>`` / ``story-<key>``), if it's
    DB-backed. Other sids (wizard uuids, un-migrated stories) → None → legacy JSON file. So a
    playthrough's session lives INSIDE its story's .db. See loom/stories/story_db.py."""
    m = re.match(r"^(?:play|story)-(.+)$", str(sid or ""))
    if not m:
        return None
    from ...stories import story_db as _SDB
    key = re.sub(r"[^\w\-]+", "", m.group(1))
    p = root / "configs" / "stories" / f"{key}.db"
    return p if _SDB.exists(p) else None


def load_session(root: Path, sid: str) -> dict | None:
    safe = _safe(sid)
    if not safe:
        return None
    from ...stories import story_db as _SDB
    db = _story_db(root, sid)
    raw = _SDB.load_session(db, safe) if db is not None else None
    if raw is None:                                    # file fallback (legacy / lazy-migrate)
        p = _dir(root) / f"{safe}.json"
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                raw = None
    if not isinstance(raw, dict):
        return None
    # Surface the unified State doc alongside the legacy flat fields (lazy + lossless).
    if not isinstance(raw.get("state"), dict):
        from ...stories import state_doc as _SD
        raw["state"] = _SD.from_session(raw)
    return raw


def save_session(root: Path, sid: str, data: dict) -> dict:
    from ...stories import state_doc as _SD, story_db as _SDB

    safe = _safe(sid)
    data = data or {}
    # The canonical mutable record is the State doc; write BOTH it and the projected flat fields so
    # readers on either side of the phased migration agree.
    state = _SD.from_session(data)
    legacy = _SD.to_session_fields(state)   # {state, graph?, draft?, world_state?}
    payload = {
        "id": safe,
        "character": data.get("character", ""),
        "messages": data.get("messages") or [],
        "graph": legacy.get("graph", data.get("graph")),
        "draft": legacy.get("draft", data.get("draft")),
        "lorebooks": data.get("lorebooks") or [],     # book ids attached to this thread
        "world_state": legacy.get("world_state", data.get("world_state") or {}),
        "state": state,                                # the unified leveled State doc
    }
    db = _story_db(root, sid)
    if db is not None:                                 # store inside the owning story's DB
        _SDB.save_session(db, safe, payload)
        try:                                           # lazy-migrate: drop any legacy JSON file
            (_dir(root) / f"{safe}.json").unlink()
        except FileNotFoundError:
            pass
    else:
        d = _dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def delete_session(root: Path, sid: str) -> None:
    safe = _safe(sid)
    if not safe:
        return
    from ...stories import story_db as _SDB
    db = _story_db(root, sid)
    if db is not None:
        _SDB.delete_session(db, safe)
    p = _dir(root) / f"{safe}.json"                    # also drop any legacy file
    try:
        p.unlink()
    except FileNotFoundError:
        pass
