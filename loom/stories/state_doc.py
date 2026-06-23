"""The **State** primitive — one leveled, mutable document per thread.

This is the third leg of the unified model, distinct from the other two:

  • **Lorebook = Knowledge** — *composed* (authored, retrieved, injected read-only).
  • **Script   = Behavior**  — registered code (stories/scripts.py) the model calls.
  • **State    = Memory**    — *accumulates* those mutations. The thread's save file.

A chat preset composes static lore; a story accumulates a State doc. That difference
is the whole reason stories felt different — and historically it was smeared across
three separate accumulators (the dev `graph`, the `world_state` engine, the simulation
`sim_state`). This module is the ONE leveled document they fold into.

Shape::

    { "levels": { <level>: <json>, ... }, "revision": int }

Default levels (the set is extensible — any key is allowed)::

    canon   authored story snapshot (read-mostly)
    graph   arc / development nodes          (structural scripts write here)
    draft   enriched chapter draft
    world   runtime world-state (entities/flags/inventory/location/clock/log)
    sim     simulation cast + scenes
    facts   emergent facts (mirrored to the thread's `thread-<sid>` lore scope)

This module owns the doc SHAPE and migration. The MUTATIONS are applied by the
registered code scripts: `graph_ops.apply_calls` runs a script's `impl` against its
`writes` level; `state_engine.apply_deltas` dispatches world ops to `WORLD_OPS`.
"""
from __future__ import annotations

from typing import Any

# The canonical level names. Not enforced — a State doc may carry any levels — but these
# are what the migrations seed and what the engines target.
DEFAULT_LEVELS = ("canon", "graph", "draft", "world", "facts", "log")

# Legacy flat session field → State level. The session JSON historically stored these as
# top-level fields; the State doc folds them into `levels` losslessly.
_LEGACY_MAP = {"graph": "graph", "draft": "draft", "world_state": "world"}


# ── Doc helpers ──────────────────────────────────────────────────────────────────

def empty_state() -> dict:
    return {"levels": {}, "revision": 0}


def normalize(state: dict | None) -> dict:
    """Coerce a (possibly partial / legacy) value into the full {levels, revision} shape."""
    state = dict(state or {})
    lv = state.get("levels")
    state["levels"] = lv if isinstance(lv, dict) else {}
    state["revision"] = int(state.get("revision") or 0)
    return state


def get_level(state: dict, level: str, default: Any = None) -> Any:
    return normalize(state)["levels"].get(level, default)


def level_summary(state: dict) -> dict:
    """A compact, UI-friendly description of which levels carry data and how much —
    powers the Player's State viewer chips. Per-level `size` is the most meaningful count
    for that level (graph→nodes, world→entities+flags, sim→cast, lists→len)."""
    st = normalize(state)
    out: dict = {}
    for name, val in st["levels"].items():
        if isinstance(val, dict):
            if name == "world":
                size = len(val.get("entities") or {}) + len(val.get("flags") or {})
            elif name == "sim":
                size = len(val.get("characters") or [])
            elif isinstance(val.get("nodes"), list):     # graph / arc levels
                size = len(val["nodes"])
            else:
                size = len(val)
            out[name] = {"size": size}
        elif isinstance(val, list):
            out[name] = {"size": len(val)}
    return out


def set_level(state: dict, level: str, value: Any) -> dict:
    state = normalize(state)
    state["levels"][level] = value
    return state


# ── Migration (session flat fields ⇄ State doc) ──────────────────────────────────

def from_session(sess: dict | None) -> dict:
    """Build (or read) the State doc for a session. If the session already carries a
    `state`, normalize and return it; otherwise fold the legacy flat fields
    (graph/draft/world_state) into levels. Idempotent + lossless."""
    sess = sess or {}
    st = sess.get("state")
    if isinstance(st, dict) and isinstance(st.get("levels"), dict):
        return normalize(st)

    st = empty_state()
    for fld, level in _LEGACY_MAP.items():
        v = sess.get(fld)
        if v not in (None, {}, []):
            st["levels"][level] = v
    # Carry the world-state engine's own revision as the doc revision (best-effort).
    ws = sess.get("world_state") or {}
    if isinstance(ws, dict) and ws.get("revision"):
        st["revision"] = int(ws.get("revision") or 0)
    return st


def to_session_fields(state: dict) -> dict:
    """Project the State doc BACK to the legacy flat session fields so existing readers
    (the world_state endpoints, graph consumers) keep working through the phased
    migration. The canonical `state` is always saved alongside."""
    state = normalize(state)
    lv = state["levels"]
    out: dict = {"state": state}
    if "graph" in lv:
        out["graph"] = lv["graph"]
    if "draft" in lv:
        out["draft"] = lv["draft"]
    if "world" in lv:
        out["world_state"] = lv["world"]
    return out


# Mutations are applied by the registered code scripts (stories/scripts.py) via
# graph_ops.apply_calls / state_engine.apply_deltas — each runs against the relevant
# level's doc. This module owns the doc SHAPE (levels) and migration, not a delta engine.
