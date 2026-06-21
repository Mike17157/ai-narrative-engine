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


def load_session(root: Path, sid: str) -> dict | None:
    safe = _safe(sid)
    if not safe:
        return None
    p = _dir(root) / f"{safe}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_session(root: Path, sid: str, data: dict) -> dict:
    safe = _safe(sid)
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": safe,
        "character": (data or {}).get("character", ""),
        "messages": (data or {}).get("messages") or [],
        "graph": (data or {}).get("graph"),
        "draft": (data or {}).get("draft"),
        "lorebooks": (data or {}).get("lorebooks") or [],   # book ids attached to this thread
        "world_state": (data or {}).get("world_state") or {},  # mutable engine state for this thread
    }
    (d / f"{safe}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def delete_session(root: Path, sid: str) -> None:
    safe = _safe(sid)
    if not safe:
        return
    p = _dir(root) / f"{safe}.json"
    try:
        p.unlink()
    except FileNotFoundError:
        pass
