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
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    # Surface the unified State doc alongside the legacy flat fields. Lazy + lossless:
    # legacy graph/draft/world_state are folded into `state.levels` in memory and
    # persisted on the next save_session. Existing readers keep using the flat fields.
    if isinstance(raw, dict) and not isinstance(raw.get("state"), dict):
        from ...stories import state_doc as _SD
        raw["state"] = _SD.from_session(raw)
    return raw


def save_session(root: Path, sid: str, data: dict) -> dict:
    from ...stories import state_doc as _SD

    safe = _safe(sid)
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    # The canonical mutable record is the State doc. Build it from whatever the caller
    # passed — an explicit `state` (new callers) or the legacy flat fields (everyone
    # else) — then write BOTH the doc and the projected flat fields so readers on either
    # side of the phased migration agree.
    data = data or {}
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
