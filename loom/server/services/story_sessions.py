"""Server-side checkpoints for story-building sessions.

A session is the durable state of a console: the conversation, the working development
graph, and the latest draft — persisted as JSON under configs/story_sessions/{id}.json
so a consultation resumes across reloads/devices instead of relying on client memory.
Keyed by an opaque id the client owns (e.g. a wizard uuid, or `story-<key>`).

Sessions NO LONGER live inside the story file — they moved to their own per-sid JSON
files here. (They used to co-reside in the story's SQLite DB; that DB is gone.)
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _dir(root: Path) -> Path:
    return root / "configs" / "story_sessions"


def _safe(sid: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(sid or "")).strip("_")


def _path(root: Path, sid: str) -> Path:
    return _dir(root) / f"{_safe(sid)}.json"


def load_session(root: Path, sid: str) -> dict | None:
    safe = _safe(sid)
    if not safe:
        return None
    p = _dir(root) / f"{safe}.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupt file surfaces as "no session"
        return None
    if not isinstance(raw, dict):
        return None
    # Surface the unified State doc alongside the legacy flat fields (lazy + lossless).
    if not isinstance(raw.get("state"), dict):
        from ...stories import state_doc as _SD
        raw["state"] = _SD.from_session(raw)
    return raw


def save_session(root: Path, sid: str, data: dict) -> dict:
    from ...stories import state_doc as _SD

    safe = _safe(sid)
    data = data or {}
    # The canonical mutable record is the State doc; write BOTH it and the projected flat
    # fields so readers on either side of the phased migration agree.
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
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    # Atomic write: temp + replace, so a crash mid-write can't corrupt the session.
    p = _path(root, sid)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return payload


def delete_session(root: Path, sid: str) -> None:
    safe = _safe(sid)
    if not safe:
        return
    try:
        (_dir(root) / f"{safe}.json").unlink()
    except FileNotFoundError:
        pass
