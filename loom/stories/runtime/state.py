"""Live-story runtime state.

Owns document levels, world-state deltas, summaries, and turn-by-turn character
perception. This is the canonical mutable-state layer for story play.
"""

from __future__ import annotations

import re
from dataclasses import dataclass as _dataclass
from pathlib import Path
from typing import Any, Callable as _Callable

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

from typing import Any

# The canonical level names. Not enforced — a State doc may carry any levels — but these
# are what the migrations seed and what the engines target.
DEFAULT_LEVELS = ("canon", "graph", "draft", "world", "facts", "log")

# Legacy flat session field → State level. The session JSON historically stored these as
# top-level fields; the State doc folds them into `levels` losslessly.
_LEGACY_MAP = {"graph": "graph", "draft": "draft", "world_state": "world"}


# ── Doc helpers ──────────────────────────────────────────────────────────────────

def document_empty_state() -> dict:
    return {"levels": {}, "revision": 0}


def document_normalize(state: dict | None) -> dict:
    """Coerce a (possibly partial / legacy) value into the full {levels, revision} shape."""
    state = dict(state or {})
    lv = state.get("levels")
    state["levels"] = lv if isinstance(lv, dict) else {}
    state["revision"] = int(state.get("revision") or 0)
    return state


def get_level(state: dict, level: str, default: Any = None) -> Any:
    return document_normalize(state)["levels"].get(level, default)


def level_summary(state: dict) -> dict:
    """A compact, UI-friendly description of which levels carry data and how much —
    powers the Player's State viewer chips. Per-level `size` is the most meaningful count
    for that level (graph→nodes, world→entities+flags, sim→cast, lists→len)."""
    st = document_normalize(state)
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
    state = document_normalize(state)
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
        # This is already the leveled document.  Do not feed it through the
        # world-level normalizer (which would bolt entity/flag keys onto the
        # document root and blur the two state layers).
        return document_normalize(st)

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
    state = document_normalize(state)
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


# --- world-state transitions ---

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

import re
from pathlib import Path

# ── Delta vocabulary ─────────────────────────────────────────────────────────────
# One uniform envelope for every op (heterogeneous unions are brittle under strict
# json_schema / grammar decoding). Unused fields are "" / []. The model fills only
# what each op needs.
_OPS = ["set_flag", "move", "mood", "rel", "item_add", "item_remove", "fact", "log", "entity",
        "detail", "promise", "payoff"]

STATE_DELTA_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["op", "name", "key", "value", "title", "keywords"],
    "properties": {
        "op": {"type": "string", "enum": _OPS,
               "description": "set_flag(key,value) | move(name->value=location) | mood(name,value) | "
                              "rel(name,key=target,value=±N) | item_add(value) | item_remove(value) | "
                              "fact(title,keywords,value=content) | log(value=text) | entity(name,value=status) | "
                              "detail(value=small concrete particular, name=owner if any) | "
                              "promise(value=setup awaiting payoff) | payoff(value=which promise was fulfilled)"},
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
# Continuity ledger caps (the ring buffers behind the CONTINUITY lane — see the harness plan).
_DETAILS_CAP = 30      # small concrete particulars (objects, gestures, scars, phrases)
_PROMISES_CAP = 15     # setups awaiting payoff


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
    # Geography hook (loom/stories/geography.py): (ws, name, target) → allow? Blocks
    # implausible off-screen teleports at the ENGINE level (prompt rules alone leak).
    validate_move: _Callable | None = None


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
    for k in ("inventory", "log", "details", "promises"):
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
    return normalize(get_level(state or {}, WORLD_LEVEL) or {})


def with_world(state: dict | None, ws: dict) -> dict:
    """Return the State doc with its `world` level set to `ws` (normalized)."""
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
                 scope: str | None = None, validate_move=None) -> dict:
    """Apply a list of delta ops to *ws* (mutates + returns) by DISPATCHING each to its
    registered handler in `WORLD_OPS`. `fact` ops are written back into the lorebook
    *scope* (provenance source='auto') so they become retrievable canon. `log` ops append
    to episodic memory. Unknown ops are ignored. `validate_move` (geography.py) gates
    `move` ops — implausible off-screen teleports are dropped."""
    ws = normalize(ws)
    ctx = _WorldCtx(root=root, scope=scope, validate_move=validate_move)
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
    if not d["name"]:
        return
    if ctx.validate_move is not None and not ctx.validate_move(ws, d["name"], d["value"]):
        # Implausible off-screen teleport → drop the move, note it in the episodic log so
        # the narrator (which reads the log) knows the world didn't actually change.
        ws["log"].append(f"({d['name']} could not have reached {d['value']} yet)")
        ws["log"][:] = ws["log"][-_LOG_CAP:]
        return
    e = _entity(ws, d["name"])
    e["location"] = d["value"]
    e["loc_step"] = int(ws.get("step") or 0)   # when they were last placed (rate-limit basis)


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


# ── Continuity ledger ops (the harness plan's Phase 3) ───────────────────────────
# `detail` = a small concrete particular (an object, a gesture, a scar, a phrase) worth keeping
# true; `promise` = a setup awaiting payoff; `payoff` = the fulfilment. The compiler surfaces
# ripe entries as the CONTINUITY lane — permission to recur, never an instruction to force.

def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'\-]{2,}", (t or "").lower())}


@world_op("detail")
def _op_detail(ws, d, ctx):
    t = " ".join((d["value"] or "").split())
    if not t:
        return
    tw = _words(t)
    for ex in ws["details"]:
        ew = _words(ex.get("text", ""))
        if tw and ew and (tw <= ew or ew <= tw):
            return                                     # near-dupe of an existing particular
    ws["details"].append({"text": t, "step": int(ws.get("step") or 0), "name": d["name"]})
    ws["details"][:] = ws["details"][-_DETAILS_CAP:]


@world_op("promise")
def _op_promise(ws, d, ctx):
    t = " ".join((d["value"] or "").split())
    if not t:
        return
    tw = _words(t)
    for ex in ws["promises"]:
        ew = _words(ex.get("setup", ""))
        if tw and ew and len(tw & ew) >= max(2, min(len(tw), len(ew)) - 1):
            return                                     # already tracked
    ws["promises"].append({"setup": t, "step": int(ws.get("step") or 0), "status": "open"})
    ws["promises"][:] = ws["promises"][-_PROMISES_CAP:]


@world_op("payoff")
def _op_payoff(ws, d, ctx):
    tw = _words(d["value"])
    if not tw:
        return
    best, score = None, 0
    for p in ws["promises"]:
        if p.get("status") != "open":
            continue
        s = len(tw & _words(p.get("setup", "")))
        if s > score:
            best, score = p, s
    if best is not None and score >= 2:
        best["status"] = "paid"
        ws["log"].append(f"(paid off: {best['setup'][:70]})")


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
        from ...server.services import lorebook_store as LS
        eid = "auto-" + re.sub(r"[^\w\-]+", "-", title.lower()).strip("-")[:40] or "auto-fact"
        if not keywords:  # derive crude triggers from the title so it's retrievable
            keywords = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']{2,}", title)][:6]
        LS.upsert_entry(root, scope, LoreEntry(
            id=eid, title=title[:70], keywords=keywords, content=content,
            priority=1, source="auto"))
    except Exception:  # noqa: BLE001 — write-back is best-effort, never break the turn
        pass


# ── Render for the prompt ────────────────────────────────────────────────────────

def render_state(ws: dict, focus: set[str] | None = None) -> str:
    """The WORLD STATE block injected into the narrator/scribe prompt. When `focus` is given
    (a set of lowercased character names), only those entities are listed — so a large cast's
    state doesn't flood every turn; pass the scene's people. Flags/inventory/log stay global."""
    ws = normalize(ws)
    if not (ws["entities"] or ws["flags"] or ws["inventory"] or ws["log"] or ws["location"]):
        return ""
    lines = ["WORLD STATE — the current, evolving truth of this playthrough. Honor it; "
             "do not contradict it. Report any changes via state_deltas."]
    if ws["location"]:
        lines.append(f"Location: {ws['location']}")
    if ws["clock"]:
        lines.append(f"Time: {ws['clock']}")
    _ents = [(nm, e) for nm, e in ws["entities"].items()
             if focus is None or nm.lower() in focus]
    if _ents:
        lines.append("Characters:")
        for nm, e in _ents:
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


# --- character perception timeline ---

"""Per-character PERCEPTION — who saw which indexed step.

A story advances in indexed STEPS (one per play turn). A character only knows what they
WITNESSED: their record starts at the step they JOINED (their first present step), so a late
arrival sees only from then on — at the moment they join, just the current (last) step, never
the backlog. This is the substrate for info-isolated cast + scoping each character's memory
(record_scene) and context to what they actually saw.

Stored on the world-state level: `step` (a monotonically increasing counter) and `presence`
({character_key: [step indices the character was present for]}).
"""


def record_step(world: dict, present: list[str]) -> int:
    """Advance to the next step and record every PRESENT character as having seen it. A
    character appearing for the first time JOINS at this step (their list starts here → no
    backlog), so they only ever see from their arrival on. Returns the new step's index."""
    if not isinstance(world, dict):
        return 0
    if not isinstance(world.get("presence"), dict):
        world["presence"] = {}
    step = int(world.get("step") or 0)
    present = [str(k) for k in (present or []) if k]
    for k in present:
        world["presence"].setdefault(k, []).append(step)
    # The inverse link too: scene_cast[step] = who was in this passage. So a passage links to its
    # characters directly (consolidation reads it; the UI can make passages clickable to cards).
    if not isinstance(world.get("scene_cast"), list):
        world["scene_cast"] = []
    world["scene_cast"].append(present)
    world["step"] = step + 1
    return step


def record_raw_turn(world: dict, *, step: int, present: list[str], location: str,
                    text: str, player_input: str = "") -> dict:
    """Append immutable source evidence for one play turn.

    This is deliberately mechanical: it records the already-produced text and
    who witnessed it. Interpretation belongs to the periodic consolidation pass,
    which writes evidence-citing residuals without replacing this source.
    """
    turns = world.setdefault("turns", [])
    row = {"step": int(step), "present": [str(p) for p in (present or []) if p],
           "location": str(location or ""), "input": str(player_input or ""), "text": str(text or "")}
    # A retry may re-enter apply after persistence failure; step is the stable id.
    for old in turns:
        if int(old.get("step", -1)) == row["step"]:
            return old
    turns.append(row)
    return row


def present_at(world: dict, step: int) -> list[str]:
    """Who was in the scene at this passage/step — the inverse of seen_steps (passage → characters)."""
    sc = (world or {}).get("scene_cast") or []
    return list(sc[step]) if 0 <= step < len(sc) else []


def seen_steps(world: dict, char_key: str) -> list[int]:
    """The step indices this character witnessed (in order; their join step is seen_steps[0])."""
    return list((world.get("presence") or {}).get(str(char_key), []))


def joined_at(world: dict, char_key: str) -> int | None:
    """The step the character first appeared (None if they've never been present)."""
    s = seen_steps(world, char_key)
    return s[0] if s else None


def saw_step(world: dict, char_key: str, step: int) -> bool:
    return step in seen_steps(world, char_key)


def visible(world: dict, char_key: str, steps: list) -> list:
    """Filter an indexed `steps` list (each item is whatever the caller stores per step, indexed
    by position) down to ONLY the ones this character saw — i.e. their view of the transcript. A
    just-joined character gets only their join step (the last one), never the prior backlog."""
    seen = set(seen_steps(world, char_key))
    return [s for i, s in enumerate(steps or []) if i in seen]


def demo() -> None:
    """Self-check: join semantics (no backlog) + accumulation. Run: python -m loom.stories.perception"""
    w: dict = {}
    assert record_step(w, ["a", "b"]) == 0          # step 0: a, b present
    assert record_step(w, ["a"]) == 1               # step 1: only a
    assert record_step(w, ["a", "c"]) == 2          # step 2: a joins-with c (c is new)
    assert w["step"] == 3
    assert seen_steps(w, "a") == [0, 1, 2]          # a saw everything
    assert seen_steps(w, "b") == [0]                # b only the opening
    assert seen_steps(w, "c") == [2]                # c JOINED at 2 → no backlog
    assert joined_at(w, "c") == 2
    assert visible(w, "c", ["m0", "m1", "m2"]) == ["m2"]   # c sees only the last message
    assert not saw_step(w, "c", 0)
    assert present_at(w, 0) == ["a", "b"]            # passage→characters (inverse link)
    assert present_at(w, 2) == ["a", "c"]
    assert present_at(w, 9) == []                    # out of range → empty
    print("perception demo ok")


if __name__ == "__main__":
    demo()
