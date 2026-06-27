"""Loader for the story chat agent's declarative config (`configs/story_agent.json`) — the single
source for the agent's authored instructions: base system prompt, per-mode personas + triggers,
craft section-anchors, grounding rules, tool policy. Deep-merged over `_DEFAULTS` so the app still
runs (degraded) if the file is missing. No heavy imports → both `agent.py` and `grounding.py` use it.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

# Minimal safety-net defaults. The RICH config lives in configs/story_agent.json (the editable source);
# these only keep the agent functional if that file is deleted.
_DEFAULTS: dict = {
    "system": "You are the writer's story collaborator. Discuss the story, and when the writer asks "
              "for a change, make it by calling the right tool. Always reply with something.",
    "tool_rules": "Fill every tool param using exact ids from the document; set all fields when adding.",
    "story_context_fields": ["title", "premise", "tone", "themes", "logline", "heart",
                             "intended_ending", "arcs", "cast"],
    "craft": {"scope": "_craft", "k": 5, "section_anchors": {}},
    "psyche": {"scope": "_psyche", "k": 4},
    "tool_policy": {"all_tools_cap": 40, "attached_cap": 12},
    # Mode routing: exact keyword triggers first (deterministic, precise); when none fire, fall back
    # to semantic similarity of the request against each mode (recall for unanticipated phrasing).
    # `semantic_floor` is the min cosine to accept a mode — below it, route to no mode (general chat).
    "routing": {"semantic_floor": 0.55},
    "modes": {},
}

_cache: dict[str, dict] = {}
_vec_cache: dict[str, dict] = {}  # str(root) -> {mode_id: passage_vector}; same lifecycle as _cache


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root: Path, *, fresh: bool = False) -> dict:
    """The merged agent config (cached per root). `fresh=True` re-reads the file (after an edit)."""
    key = str(root)
    if fresh:
        _vec_cache.pop(key, None)
    if not fresh and key in _cache:
        return _cache[key]
    cfg = dict(_DEFAULTS)
    p = Path(root) / "configs" / "story_agent.json"
    if p.is_file():
        try:
            data = {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                    if not k.startswith("_")}
            cfg = _deep_merge(_DEFAULTS, data)
        except Exception:  # noqa: BLE001 — a malformed file falls back to defaults, never crashes
            cfg = dict(_DEFAULTS)
    _cache[key] = cfg
    return cfg


def section_anchors(root: Path) -> dict:
    """The per-part craft anchor map (entry ids per section) — single source for pipeline + chat."""
    return (load_config(root).get("craft") or {}).get("section_anchors") or {}


def modes_list(root: Path) -> list[dict]:
    """Selectable modes for the UI dropdown: [{id, label}] from the config."""
    return [{"id": mid, "label": m.get("label", mid)}
            for mid, m in (load_config(root).get("modes") or {}).items()]


def _keyword_modes(root: Path, text: str) -> list[str]:
    """Mode ids whose trigger keywords fire on `text` (word-boundary), in config order."""
    t = (text or "").lower()
    return [mid for mid, m in (load_config(root).get("modes") or {}).items()
            if any(re.search(rf"\b{re.escape(str(k).lower())}\b", t) for k in (m.get("triggers") or []) if k)]


def _cos(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def _mode_vectors(root: Path, modes: dict) -> dict:
    """Passage embedding per mode, cached per root. {} if embeddings are unavailable — caller then
    has nothing to match and the keyword result stands.

    We embed the TRIGGER terms only, not the persona prose: measured on bge-small, the abstract
    persona text ('character', 'story', 'world', 'concrete') is shared across modes and collapses the
    cosine separation; the concrete trigger nouns are the discriminative signal."""
    key = str(root)
    if key in _vec_cache:
        return _vec_cache[key]
    try:
        from ..server.services import embeddings as _emb
    except Exception:  # noqa: BLE001
        return {}
    ids, texts = [], []
    for mid, m in modes.items():
        ids.append(mid)
        texts.append(", ".join(m.get("triggers") or []))
    vecs = _emb.embed_passages(texts) if texts else None
    out = dict(zip(ids, vecs)) if vecs else {}
    if out:
        _vec_cache[key] = out
    return out


def _semantic_mode(root: Path, text: str) -> list[str]:
    """Best mode by cosine similarity of `text` to each mode, if it clears `semantic_floor`; else []."""
    cfg = load_config(root)
    modes = cfg.get("modes") or {}
    if not modes or not (text or "").strip():
        return []
    try:
        from ..server.services import embeddings as _emb
    except Exception:  # noqa: BLE001
        return []
    qv = _emb.embed_query(text)
    if qv is None:
        return []
    mvs = _mode_vectors(root, modes)
    if not mvs:
        return []
    score, mid = max(((_cos(qv, v), m) for m, v in mvs.items()), default=(0.0, ""))
    floor = (cfg.get("routing") or {}).get("semantic_floor", 0.55)
    return [mid] if mid and score >= floor else []


def match_modes(root: Path, text: str) -> list[str]:
    """Route the request to mode(s): exact keyword triggers first (precise, deterministic); if none
    fire, fall back to semantic similarity (recall for phrasing the triggers didn't anticipate).
    Returns [] when nothing is confident enough — the agent then runs as the general collaborator."""
    return _keyword_modes(root, text) or _semantic_mode(root, text)


def demo() -> None:
    # _cos: the only non-trivial math here. (keyword routing is regex, exercised live.)
    assert _cos([1, 0], [1, 0]) == 1.0
    assert _cos([1, 0], [0, 1]) == 0.0
    assert round(_cos([1, 0], [-1, 0]), 6) == -1.0
    assert _cos([], []) == 0.0 and _cos([0, 0], [1, 1]) == 0.0  # zero-vector guard
    # keyword path still works against the live config; semantic only if fastembed is installed.
    root = Path(__file__).resolve().parents[2]
    assert "_wardrobe_fns" in _keyword_modes(root, "design her outfit")
    try:
        from ..server.services import embeddings as _emb
        sem_ok = _emb.available()
    except Exception:  # noqa: BLE001
        sem_ok = False
    if sem_ok:
        # phrasing with NO trigger word should still reach wardrobe via semantics
        hit = match_modes(root, "what should she put on for the gala")
        print("semantic routing live — 'what should she put on…' ->", hit)
    else:
        print("semantic routing unavailable (no fastembed) — keyword path only, graceful fallback ok")
    print("agent_config demo ok")


if __name__ == "__main__":
    demo()
