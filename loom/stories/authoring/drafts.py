"""Persistent interview drafts: conversation + evolving Story-card data."""
from __future__ import annotations
import json, re, time, uuid
from pathlib import Path

def _path(root, draft_id: str) -> Path:
    safe = re.sub(r"[^\w-]+", "", draft_id)
    return Path(root) / "configs" / "story_drafts" / f"{safe}.json"

def create(root) -> dict:
    draft = {"id": f"draft-{uuid.uuid4().hex[:12]}", "messages": [], "story": {}, "open_questions": [], "history": [], "created": time.time()}
    save(root, draft)
    return draft

def load(root, draft_id: str) -> dict | None:
    p = _path(root, draft_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

def save(root, draft: dict) -> None:
    p = _path(root, draft["id"]); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
