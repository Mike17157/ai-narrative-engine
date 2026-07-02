"""The story pipeline as pydantic-graph state machines.

Two graphs model the whole flow:
  • turn_graph:  consult → extract        (one workshop conversation turn)
  • draft_graph: expand → revise          (faithfully turn the dev-graph into a draft)

Steps are thin: they call the existing `provider` (OpenRouter, structured output via
`emits=`) and report progress through `StoryDeps` callbacks, so the graph layer stays
decoupled from FastAPI/SSE and the lorebook/AppContext machinery. Endpoints build the
prompts + wire the callbacks, then drive the graph.

The HTTP/SSE boundary is the human-in-the-loop checkpoint: each turn is one `turn_graph`
run; drafting is one `draft_graph` run; the client holds the state (conversation + graph)
between runs and approves the draft before it is committed.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext, reduce_list_append, reduce_null

# ── Structured-output contracts (the reliable channel; no in-prose markers) ───────

WORKSHOP_GRAPH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["logline", "wound", "lie", "truth", "nodes"],
    "properties": {
        "logline": {"type": "string"}, "wound": {"type": "string"},
        "lie": {"type": "string"}, "truth": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "title", "inflection", "start", "end", "what_happened", "next"],
                "properties": {
                    "id": {"type": "string"}, "title": {"type": "string"},
                    "inflection": {"type": "string"}, "start": {"type": "string"},
                    "end": {"type": "string"}, "what_happened": {"type": "string"},
                    "next": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

WORKSHOP_GRAPH_SYS = (
    "You convert a story-development consultation into a DEVELOPMENT GRAPH — the character's "
    "arc of change. Each node is a developmental beat: `inflection` (the internal shift — the "
    "point of the beat), `start`/`end` (internal state going in / coming out), `what_happened` "
    "(the external event that forces the shift — the lever), and `next[]` (how the change "
    "proceeds: two or more next ids = the arc DIVERGES / a decision; two or more nodes naming "
    "the same next id = routes CONVERGE to the same internal state). Build on the current "
    "working graph when one is given — keep node ids stable where a beat persists, add/revise "
    "as the conversation develops. Give 3-7 nodes once a shape has emerged; an empty nodes list "
    "while it is still too early. Capture the consultant's current best thinking. JSON only."
)

# One beat is fleshed per call (the draft graph forks over nodes — parallel, branch-aware).
EXPAND_NODE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "scene_prompt", "location", "characters"],
    "properties": {
        "title": {"type": "string"}, "summary": {"type": "string"},
        "scene_prompt": {"type": "string"}, "location": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "string"}},
    },
}

EXPAND_NODE_SYS = (
    "You are a story architect fleshing ONE developmental beat into a chapter. The beat has an "
    "INFLECTION (the internal shift — the point), the internal state going in and coming out, and "
    "an EVENT that serves as the lever. Dramatize the event so it forces the inflection, carrying "
    "the character from 'going in' to 'coming out'. Honour the spine (wound / lie / truth) and the "
    "beat's place in the full arc.\n\n"
    "Output JSON for this one chapter: `summary` = the concrete events (2-4 sentences) engineered "
    "to force the inflection; `scene_prompt` = a visual background prompt (place + atmosphere, NO "
    "characters); `location` = short place name; `characters` = names present. JSON only."
)

REVISE_SCHEMA = WORKSHOP_GRAPH_SCHEMA  # revise returns the same dev-graph shape, polished

REVISE_SYS = (
    "You are a developmental editor doing ONE consistency pass over a drafted story graph. "
    "Keep every node id and the branch structure (next[]) EXACTLY as given. Tighten each beat so "
    "the inflections form a coherent escalating arc of change, the wound/lie/truth stay honoured, "
    "and adjacent beats connect cleanly. Do not add, remove, merge, or reorder nodes. JSON only."
)


# ── Graph state & deps ────────────────────────────────────────────────────────

def _noop_delta(_: str) -> None: ...
def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False


@dataclass
class StoryDeps:
    """I/O the steps need, injected by the endpoint — keeps the graph framework-agnostic."""
    provider: Any
    on_delta: Callable[[str], None] = _noop_delta       # prose token sink → SSE
    on_event: Callable[[dict], None] = _noop_event      # structured events → SSE
    cancel: Callable[[], bool] = _never_cancel
    do_revise: bool = False                             # gate the optional revise pass


@dataclass
class StoryState:
    """Everything that flows across the pipeline (and persists between turns, client-side)."""
    card: str = ""
    system: str = ""              # consult system prompt (built by the endpoint, incl. lore)
    prompt: str = ""              # conversation transcript
    working_graph: dict | None = None   # current dev graph (model- or user-edited)
    reply: str = ""               # consultant's latest prose (filled by consult)
    graph: dict | None = None     # updated dev graph (filled by extract)
    draft: dict | None = None     # enriched graph: one chapter per node (filled by expand/revise)


async def _gen(deps: StoryDeps, *, system: str, prompt: str, emits: dict | None = None):
    """Call the (sync) provider off the event loop. Stream prose; never stream raw JSON."""
    return await asyncio.to_thread(lambda: deps.provider.generate_text(
        system=system, prompt=prompt, emits=emits,
        on_delta=(deps.on_delta if emits is None else None),
        cancel=deps.cancel))


def _spine_bits(g: dict) -> str:
    bits = [f"  {k.upper()}: {g[k]}" for k in ("logline", "wound", "lie", "truth") if g.get(k)]
    return "STORY SPINE:\n" + "\n".join(bits) if bits else ""


def spine_prose(g: dict) -> str:
    """The dev-graph rendered as labeled PROSE, not raw JSON — models read information context
    far better as an outline (ids kept inline so edits/ops can still reference them)."""
    if not isinstance(g, dict) or not g:
        return "(none yet)"
    out = [_spine_bits(g)] if _spine_bits(g) else []
    nodes = [n for n in (g.get("nodes") or []) if isinstance(n, dict)]
    if nodes:
        out.append("BEATS (in order):")
        for n in nodes:
            nxt = ", ".join(str(x) for x in (n.get("next") or []))
            out.append(f"- [{n.get('id', '?')}] {n.get('title', '')}\n"
                       f"    inflection: {n.get('inflection', '')}\n"
                       f"    going in: {n.get('start', '')} → coming out: {n.get('end', '')}\n"
                       f"    event: {n.get('what_happened', '')}"
                       + (f"\n    next: {nxt}" if nxt else ""))
    extra = {k: v for k, v in g.items()
             if k not in ("logline", "wound", "lie", "truth", "nodes") and v}
    for k, v in extra.items():
        out.append(f"{k.upper()}: {v}")
    return "\n".join(out) or "(none yet)"


# ── Steps ─────────────────────────────────────────────────────────────────────

async def consult(ctx: StepContext[StoryState, StoryDeps, None]) -> str:
    """Stream the consultant's prose reply."""
    s = ctx.state
    res = await _gen(ctx.deps, system=s.system, prompt=s.prompt)
    s.reply = (getattr(res, "text", "") or "").strip()
    return s.reply


async def extract(ctx: StepContext[StoryState, StoryDeps, None]) -> dict:
    """Re-extract the development graph from the conversation (reliable structured call).

    Runs in PARALLEL with `consult`, so it reads the conversation (through the user's
    latest message) but not the reply being generated — that nuance lands next turn.
    Halves turn latency vs. running it after the prose.
    """
    s = ctx.state
    cur = spine_prose(s.working_graph if isinstance(s.working_graph, dict) else {})
    prompt = (
        f"CHARACTER CARD:\n{s.card}\n\n"
        f"CONVERSATION SO FAR:\n{s.prompt}\n\n"
        f"CURRENT WORKING GRAPH (build on it; keep ids stable):\n{cur}\n\n"
        f"Output the updated development graph as JSON."
    )
    res = await _gen(ctx.deps, system=WORKSHOP_GRAPH_SYS, prompt=prompt, emits=WORKSHOP_GRAPH_SCHEMA)
    data = getattr(res, "data", None)
    if data:
        s.graph = data
        ctx.deps.on_event({"type": "spine", "spine": data})
    return s.graph or {}


async def gate(ctx: StepContext[StoryState, StoryDeps, None]) -> dict:
    """Fan-in point: surface the extracted graph for the readiness decision."""
    return ctx.state.graph or {}


def is_ready(g: dict) -> bool:
    """A draftable shape has emerged: a logline and ≥3 beats that carry an inflection."""
    nodes = (g or {}).get("nodes") or []
    return bool((g or {}).get("logline")) and sum(1 for n in nodes if n.get("inflection")) >= 3


async def mark_ready(ctx: StepContext[StoryState, StoryDeps, dict]) -> dict:
    ctx.deps.on_event({"type": "ready", "ready": True})
    return ctx.inputs or ctx.state.graph or {}


async def mark_shaping(ctx: StepContext[StoryState, StoryDeps, dict]) -> dict:
    ctx.deps.on_event({"type": "ready", "ready": False})
    return ctx.inputs or ctx.state.graph or {}


# ── Draft graph (fork): flesh each beat in parallel, then assemble ──────────────

async def plan_expand(ctx: StepContext[StoryState, StoryDeps, None]) -> list[dict]:
    """Fan-out source: the beats to flesh (one parallel `expand_one` per beat)."""
    g = ctx.state.working_graph or {}
    return [n for n in (g.get("nodes") or []) if isinstance(n, dict) and n.get("id")]


async def expand_one(ctx: StepContext[StoryState, StoryDeps, dict]) -> dict:
    """Flesh ONE developmental beat into a chapter (runs in parallel across beats)."""
    n = ctx.inputs
    s = ctx.state
    g = s.working_graph or {}
    titles = " → ".join((m.get("title") or m.get("id") or "?") for m in (g.get("nodes") or []))
    prompt = (
        f"CHARACTER CARD:\n{s.card}\n\n{_spine_bits(g)}\n\n"
        f"FULL ARC (for context, in order): {titles}\n\n"
        f"FLESH THIS BEAT into one chapter:\n"
        f"  title: {n.get('title', '')}\n"
        f"  inflection (internal shift): {n.get('inflection') or n.get('emotional_core', '')}\n"
        f"  going in: {n.get('start', '')}\n"
        f"  event (the lever): {n.get('what_happened', '')}\n"
        f"  coming out: {n.get('end', '')}\n\n"
        f"Output one chapter as JSON."
    )
    res = await _gen(ctx.deps, system=EXPAND_NODE_SYS, prompt=prompt, emits=EXPAND_NODE_SCHEMA)
    e = getattr(res, "data", None) or {}
    ctx.deps.on_event({"type": "draft_progress", "id": n.get("id")})
    return {
        **n,
        "title": e.get("title") or n.get("title", ""),
        "summary": e.get("summary", ""),
        "scene_prompt": e.get("scene_prompt", ""),
        "location": e.get("location", ""),
        "characters": e.get("characters") or [],
    }


async def assemble(ctx: StepContext[StoryState, StoryDeps, list]) -> dict:
    """Fan-in: stitch the parallel chapters back into the graph (original order + branches)."""
    s = ctx.state
    g = s.working_graph or {}
    by_id = {e.get("id"): e for e in (ctx.inputs or []) if isinstance(e, dict)}
    ordered = []
    for n in (g.get("nodes") or []):
        ordered.append(by_id.get(n.get("id")) or n)
    s.draft = {**g, "nodes": ordered}
    ctx.deps.on_event({"type": "graph", "graph": s.draft})
    return s.draft


async def revise(ctx: StepContext[StoryState, StoryDeps, dict]) -> dict:
    """Optional single consistency pass over the drafted graph (gated by deps.do_revise)."""
    s = ctx.state
    draft = s.draft or ctx.inputs or {}
    if not ctx.deps.do_revise or not (draft.get("nodes")):
        return draft
    prompt = (
        f"CHARACTER CARD:\n{s.card}\n\n"
        f"DRAFTED STORY GRAPH (polish for consistency; keep ids + next exactly):\n"
        f"{spine_prose(draft)}\n\nOutput the polished development graph as JSON."
    )
    res = await _gen(ctx.deps, system=REVISE_SYS, prompt=prompt, emits=REVISE_SCHEMA)
    data = getattr(res, "data", None)
    if data and data.get("nodes"):
        # keep the enriched chapter fields from the draft, take revised text where present
        by_id = {n.get("id"): n for n in draft["nodes"]}
        for rn in data["nodes"]:
            base = by_id.get(rn.get("id"))
            if base:
                base.update({k: rn[k] for k in ("title", "inflection", "start", "end", "what_happened") if rn.get(k)})
        s.draft = {**draft, **{k: data[k] for k in ("logline", "wound", "lie", "truth") if data.get(k)}}
    ctx.deps.on_event({"type": "graph", "graph": s.draft})
    return s.draft


# ── Graph construction ──────────────────────────────────────────────────────────

def _build_turn_graph():
    # start → (fork) consult ‖ extract → (join) → gate → decision(ready?) → mark_* → end
    # consult and extract run in PARALLEL (independent of each other) — halves turn latency.
    gb = GraphBuilder(state_type=StoryState, deps_type=StoryDeps, output_type=dict)
    c, e, g = gb.step(consult), gb.step(extract), gb.step(gate)
    mr, ms = gb.step(mark_ready), gb.step(mark_shaping)
    join = gb.join(reduce_null)
    decide = (gb.decision(note="draftable shape?")
              .branch(gb.match(dict, matches=is_ready).to(mr))
              .branch(gb.match(dict, matches=lambda gr: not is_ready(gr)).to(ms)))
    gb.add(gb.edge_from(gb.start_node).to(c, e))   # fork
    gb.add_edge(c, join)
    gb.add_edge(e, join)
    gb.add_edge(join, g)
    gb.add_edge(g, decide)
    gb.add_edge(mr, gb.end_node)
    gb.add_edge(ms, gb.end_node)
    return gb.build()


def _build_draft_graph():
    # start → plan → (fork) expand_one × N → (join) → assemble → revise → end
    gb = GraphBuilder(state_type=StoryState, deps_type=StoryDeps, output_type=dict)
    plan, one, asm, rev = gb.step(plan_expand), gb.step(expand_one), gb.step(assemble), gb.step(revise)
    join = gb.join(reduce_list_append, initial_factory=list)
    gb.add_edge(gb.start_node, plan)
    gb.add_mapping_edge(plan, one)   # fan-out: one parallel expand per beat
    gb.add_edge(one, join)           # fan-in
    gb.add_edge(join, asm)
    gb.add_edge(asm, rev)
    gb.add_edge(rev, gb.end_node)
    return gb.build()


TURN_GRAPH = _build_turn_graph()
DRAFT_GRAPH = _build_draft_graph()


async def run_turn(state: StoryState, deps: StoryDeps) -> dict:
    """One workshop turn: stream prose, then extract the updated development graph."""
    return await TURN_GRAPH.run(state=state, deps=deps, inputs=None)


async def run_draft(state: StoryState, deps: StoryDeps) -> dict:
    """Faithfully expand the working graph into a chapter draft (+ optional revise)."""
    return await DRAFT_GRAPH.run(state=state, deps=deps, inputs=None)
