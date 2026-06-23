"""Unified world-state engine — the mutable working memory of a playthrough.

A lorebook is the *reference manual* (canon, retrieved). This is the *save file*: the
evolving truth of THIS thread — where characters are, how they feel, what they know,
relationships, inventory, plot flags, the clock, and a rolling episodic log. It's read
into every prompt and rewritten every turn.

Flow each turn: render_state() → prompt → the narrator/scribe emits `state_deltas`
(small, uniform ops) → apply_deltas() mutates the doc, writes `fact` deltas back into a
dynamic lorebook scope (so established facts become retrievable canon), and appends to
the log. The same machinery drives interactive play AND the autonomous simulation.

State is a plain dict (persisted in the session JSON), shape:
    { entities: {name: {kind, location, mood, status, relationships:{target:int}}},
      flags: {key: value}, inventory: [str], location: str, clock: str,
      log: [str], revision: int }
"""
from __future__ import annotations

import re
from pathlib import Path

# ── Delta vocabulary ─────────────────────────────────────────────────────────────
# One uniform envelope for every op (heterogeneous unions are brittle under strict
# json_schema / grammar decoding). Unused fields are "" / []. The model fills only
# what each op needs.
_OPS = ["set_flag", "move", "mood", "rel", "item_add", "item_remove", "fact", "log", "entity"]

STATE_DELTA_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["op", "name", "key", "value", "title", "keywords"],
    "properties": {
        "op": {"type": "string", "enum": _OPS,
               "description": "set_flag(key,value) | move(name->value=location) | mood(name,value) | "
                              "rel(name,key=target,value=±N) | item_add(value) | item_remove(value) | "
                              "fact(title,keywords,value=content) | log(value=text) | entity(name,value=status)"},
        "name": {"type": "string", "description": "character/entity the op concerns ('' if n/a)"},
        "key": {"type": "string", "description": "flag key, or relationship target ('' if n/a)"},
        "value": {"type": "string", "description": "the op's value ('' if n/a)"},
        "title": {"type": "string", "description": "fact title ('' if n/a)"},
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "fact trigger keywords ([] if n/a)"},
    },
}

STATE_DELTAS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["state_deltas"],
    "properties": {"state_deltas": {"type": "array", "items": STATE_DELTA_ITEM}},
}

_LOG_CAP = 20          # keep the doc bounded; compaction trims the oldest beats
_REL_MIN, _REL_MAX = -10, 10


# ── World-op registry ────────────────────────────────────────────────────────────
# The SAME registered-code pattern as stories/scripts.py, applied to the world level:
# each delta op is a named function `op(ws, d, ctx)` that mutates the world-state dict.
# `apply_deltas` becomes a dispatcher over this registry instead of a hardcoded switch —
# so world ops are individually named/testable and live in one place, consistent with the
# graph scripts. (The wire format stays the uniform STATE_DELTA envelope above — robust
# under strict grammar decoding — so this is a dispatch change, not a schema change.)

from dataclasses import dataclass as _dataclass
from typing import Any as _Any, Callable as _Callable

WORLD_OPS: dict[str, _Callable] = {}


@_dataclass
class _WorldCtx:
    root: _Any = None        # for side-effecting ops (fact write-back)
    scope: str | None = None


def world_op(name: str) -> _Callable:
    """Register `fn(ws, d, ctx)` as the handler for delta op `name`."""
    def deco(fn: _Callable) -> _Callable:
        WORLD_OPS[name] = fn
        return fn
    return deco


# ── State doc helpers ────────────────────────────────────────────────────────────

def empty_state() -> dict:
    return {"entities": {}, "flags": {}, "inventory": [], "location": "",
            "clock": "", "log": [], "revision": 0}


def normalize(ws: dict | None) -> dict:
    """Coerce a (possibly partial / legacy) doc into the full shape."""
    ws = dict(ws or {})
    base = empty_state()
    for k, v in base.items():
        ws.setdefault(k, v)
    if not isinstance(ws.get("entities"), dict):
        ws["entities"] = {}
    for ent in ws["entities"].values():
        if isinstance(ent, dict) and not isinstance(ent.get("relationships"), dict):
            ent["relationships"] = {}
    for k in ("flags",):
        if not isinstance(ws.get(k), dict):
            ws[k] = {}
    for k in ("inventory", "log"):
        if not isinstance(ws.get(k), list):
            ws[k] = []
    return ws


# ── State-doc bridge (this engine OWNS the `world` level) ────────────────────────
# In the unified model the per-thread mutable record is a State doc (loom/stories/
# state_doc.py) with named levels. The world-state engine owns the `world` level; its
# delta vocabulary (set_flag/move/mood/…) is unchanged — these helpers just let callers
# be State-doc-native instead of poking the legacy flat `world_state` field.

WORLD_LEVEL = "world"


def facts_scope(sid: str) -> str:
    """The thread-owned lorebook scope holding engine-established facts — this IS the
    `facts` level (libSQL-backed so the facts stay retrievable). ONE source of truth for
    the name (the play loop + retrieval both use it), so emergent facts never land in an
    authored book."""
    return re.sub(r"[^\w\-]+", "_", f"thread-{sid}")


def world_of(state: dict | None) -> dict:
    """Read the normalized world-state from a State doc's `world` level."""
    from .state_doc import get_level
    return normalize(get_level(state or {}, WORLD_LEVEL) or {})


def with_world(state: dict | None, ws: dict) -> dict:
    """Return the State doc with its `world` level set to `ws` (normalized)."""
    from .state_doc import set_level
    return set_level(state or {}, WORLD_LEVEL, normalize(ws))


def _entity(ws: dict, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {}
    ents = ws["entities"]
    if name not in ents:
        ents[name] = {"kind": "character", "location": "", "mood": "", "status": "",
                      "relationships": {}}
    return ents[name]


def _to_int(s: str) -> int:
    m = re.search(r"-?\d+", str(s or ""))
    return int(m.group()) if m else 0


def _coerce_scalar(v: str):
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


# ── Apply deltas (with lorebook write-back) ──────────────────────────────────────

def apply_deltas(ws: dict, deltas: list[dict], *, root: Path | None = None,
                 scope: str | None = None) -> dict:
    """Apply a list of delta ops to *ws* (mutates + returns) by DISPATCHING each to its
    registered handler in `WORLD_OPS`. `fact` ops are written back into the lorebook
    *scope* (provenance source='auto') so they become retrievable canon. `log` ops append
    to episodic memory. Unknown ops are ignored."""
    ws = normalize(ws)
    ctx = _WorldCtx(root=root, scope=scope)
    for d in (deltas or []):
        if not isinstance(d, dict):
            continue
        op = (d.get("op") or "").strip()
        name = (d.get("name") or "").strip()
        key = (d.get("key") or "").strip()
        # Guard against a common misfill: the model echoing the op verb (or a field name)
        # into `name`/`key` instead of leaving them blank.
        if name in _OPS:
            name = ""
        if key in _OPS or key in ("flags", "key", "value", "name"):
            key = ""
        handler = WORLD_OPS.get(op)
        if handler is None:
            continue
        nd = {"op": op, "name": name, "key": key, "value": (d.get("value") or "").strip(),
              "title": d.get("title") or "", "keywords": d.get("keywords") or []}
        handler(ws, nd, ctx)

    if len(ws["log"]) > _LOG_CAP:
        ws["log"] = ws["log"][-_LOG_CAP:]
    ws["revision"] = int(ws.get("revision", 0)) + 1
    return ws


# ── The registered world ops (one named function per delta verb) ─────────────────

@world_op("set_flag")
def _op_set_flag(ws, d, ctx):
    if d["key"] and d["value"]:        # ignore empty/echoed flags
        ws["flags"][d["key"]] = _coerce_scalar(d["value"])


@world_op("move")
def _op_move(ws, d, ctx):
    if d["name"]:
        _entity(ws, d["name"])["location"] = d["value"]


@world_op("mood")
def _op_mood(ws, d, ctx):
    if d["name"]:
        _entity(ws, d["name"])["mood"] = d["value"]


@world_op("rel")
def _op_rel(ws, d, ctx):
    if not d["name"]:
        return
    target = d["key"] or "you"
    rels = _entity(ws, d["name"])["relationships"]
    rels[target] = max(_REL_MIN, min(_REL_MAX, int(rels.get(target, 0)) + _to_int(d["value"])))


@world_op("item_add")
def _op_item_add(ws, d, ctx):
    if d["value"] and d["value"] not in ws["inventory"]:
        ws["inventory"].append(d["value"])


@world_op("item_remove")
def _op_item_remove(ws, d, ctx):
    if d["value"]:
        ws["inventory"] = [i for i in ws["inventory"] if i.lower() != d["value"].lower()]


@world_op("entity")
def _op_entity(ws, d, ctx):
    if d["name"]:
        _entity(ws, d["name"])["status"] = d["value"]


@world_op("log")
def _op_log(ws, d, ctx):
    if d["value"]:
        ws["log"].append(d["value"])


@world_op("fact")
def _op_fact(ws, d, ctx):
    value = d["value"]
    if value and ctx and ctx.root is not None and ctx.scope:
        _write_fact(ctx.root, ctx.scope, title=(d["title"] or value[:60]),
                    keywords=[k for k in (d["keywords"] or []) if k], content=value)
        ws["log"].append(f"(established: {(d['title'] or value)[:80]})")


def _write_fact(root: Path, scope: str, *, title: str, keywords: list[str], content: str) -> None:
    """Upsert an engine-established fact into a dynamic lorebook scope (deduped by title)."""
    try:
        from ..config.schema import LoreEntry
        from ..server.services import lorebook_store as LS
        eid = "auto-" + re.sub(r"[^\w\-]+", "-", title.lower()).strip("-")[:40] or "auto-fact"
        if not keywords:  # derive crude triggers from the title so it's retrievable
            keywords = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']{2,}", title)][:6]
        LS.upsert_entry(root, scope, LoreEntry(
            id=eid, title=title[:70], keywords=keywords, content=content,
            priority=1, source="auto"))
    except Exception:  # noqa: BLE001 — write-back is best-effort, never break the turn
        pass


# ── Render for the prompt ────────────────────────────────────────────────────────

def render_state(ws: dict) -> str:
    """The WORLD STATE block injected into the narrator/scribe prompt."""
    ws = normalize(ws)
    if not (ws["entities"] or ws["flags"] or ws["inventory"] or ws["log"] or ws["location"]):
        return ""
    lines = ["WORLD STATE — the current, evolving truth of this playthrough. Honor it; "
             "do not contradict it. Report any changes via state_deltas."]
    if ws["location"]:
        lines.append(f"Location: {ws['location']}")
    if ws["clock"]:
        lines.append(f"Time: {ws['clock']}")
    if ws["entities"]:
        lines.append("Characters:")
        for nm, e in ws["entities"].items():
            bits = []
            if e.get("location"):
                bits.append(f"at {e['location']}")
            if e.get("mood"):
                bits.append(f"feeling {e['mood']}")
            if e.get("status"):
                bits.append(e["status"])
            rels = e.get("relationships") or {}
            if rels:
                bits.append("relations: " + ", ".join(f"{t} {v:+d}" for t, v in rels.items()))
            lines.append(f"- {nm}" + (f" — {'; '.join(bits)}" if bits else ""))
    if ws["inventory"]:
        lines.append("Inventory: " + ", ".join(ws["inventory"]))
    if ws["flags"]:
        lines.append("Flags: " + "; ".join(f"{k}={v}" for k, v in ws["flags"].items()))
    if ws["log"]:
        lines.append("Recently: " + " | ".join(ws["log"][-6:]))
    return "\n".join(lines)


def summary(ws: dict) -> dict:
    """Compact, UI-friendly snapshot (counts + the doc) for the state panel / telemetry."""
    ws = normalize(ws)
    return {"entities": len(ws["entities"]), "flags": len(ws["flags"]),
            "inventory": len(ws["inventory"]), "revision": ws["revision"], "state": ws}
