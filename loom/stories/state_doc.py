"""The **State** primitive — one leveled, mutable document per thread.

This is the third leg of the unified model, distinct from the other two:

  • **Lorebook = Knowledge** — *composed* (authored, retrieved, injected read-only).
  • **Script   = Behavior**  — *emits deltas* (graph-ops / stage functions); it "stacks".
  • **State    = Memory**    — *accumulates* those deltas. The thread's save file.

A chat preset composes static lore; a story accumulates a State doc. That difference
is the whole reason stories felt different — and historically it was smeared across
three separate accumulators (the dev `graph`, the `world_state` engine, the simulation
`sim_state`). This module is the ONE leveled document + ONE delta engine they fold into.

Shape::

    { "levels": { <level>: <json>, ... }, "revision": int }

Default levels (the set is extensible — any key is allowed)::

    canon   authored story snapshot (read-mostly)
    graph   arc / development nodes          (structural scripts write here)
    draft   enriched chapter draft
    world   runtime world-state (entities/flags/inventory/location/clock/log)
    facts   emergent facts (Phase D: mirrored to the thread's `state-<sid>` lore scope)

Scripts declare which level they write; `apply_ops` applies a generic JSON-patch op
scoped to a level, reusing the path dialect already proven in `graph_ops`
(`/nodes/#id/next`, `/flags/key`, `/-` append, …). A script that writes level "world"
simply has its op paths prefixed with `/world`, so existing bare-path graph functions
keep working unchanged once they declare `writes: "graph"`.
"""
from __future__ import annotations

import copy
from typing import Any

from . import graph_ops as _GO   # the JSON-patch dialect (_apply_one / _interp) lives here

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


# ── The ONE delta engine ─────────────────────────────────────────────────────────

def _scope(op: dict, level: str | None) -> dict:
    """Prefix an op's path with `/<level>` unless it already targets that level root.
    A bare `/nodes/-` authored for the dev graph becomes `/graph/nodes/-`."""
    if not level:
        return op
    path = op.get("path") or ""
    root = f"/{level}"
    if path == root or path.startswith(root + "/"):
        return op
    if not path.startswith("/"):
        path = "/" + path
    return {**op, "path": root + path}


def apply_ops(state: dict, ops: list, *, level: str | None = None,
              params: dict | None = None) -> tuple[dict, list[dict]]:
    """Apply generic JSON-patch ops to the State doc's levels (on a deepcopy; returns
    (new_state, log)). Reuses the graph_ops dialect with `state['levels']` as the root
    object. When `level` is given the level container is ensured and each op path is
    scoped under `/<level>`. `params` interpolates `{{placeholders}}` in the ops. Never
    raises — a bad op is skipped and logged."""
    st = normalize(copy.deepcopy(state))
    root = st["levels"]
    if level and not isinstance(root.get(level), (dict, list)):
        root[level] = {}
    log: list[dict] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        op2 = _GO._interp(op, params or {}) if params else op
        op2 = _scope(op2, level)
        try:
            changed = _GO._apply_one(root, op2)
            log.append({"op": op2.get("op"), "path": op2.get("path"), "ok": True, "changed": changed})
        except Exception as exc:  # noqa: BLE001 — one bad op never sinks the batch
            log.append({"op": op2.get("op"), "path": op2.get("path"), "ok": False, "error": str(exc)})
    st["revision"] = int(st.get("revision") or 0) + 1
    return st, log
