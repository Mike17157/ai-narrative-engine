"""Genesis as a pydantic-graph pipeline — the full 'grow a story' run as focused, resumable,
streamed operations.

Same substrate as loom/stories/graph_pipeline.py (GraphBuilder + a typed State/Deps + thin steps
that call the existing providers with `emits=`), applied to world/character/scene generation:

    start ─┬─ premise ──┐
           └─ substrate ┴─ (join) → particulars → cast → weave → scene → assemble → end
                    (premise ‖ substrate run in parallel — the two slow calls overlap)

Each node is ONE focused generation (its own prompt + schema), reusing the tested functions in
worldgen.py / genesis.py verbatim — the graph replaces the ad-hoc wiring, not the generation
logic. Steps stream progress via injected `GenesisDeps` callbacks (framework-agnostic → SSE), and
the graph is resumable via pydantic_graph persistence (see run_genesis). This is the template the
other generation pipelines (worldgen systems / scaffold / wardrobe / research) get migrated onto.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, fields as _dc_fields
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext, reduce_null

from .worldgen import (build_premise, build_substrate, gen_particulars, loop_scene,
                       accrete_systems, scenario_from_systems)
from .genesis import design_by_role, weave_relationships


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False


@dataclass
class GenesisDeps:
    """I/O the steps need, injected by the endpoint — keeps the graph framework-agnostic."""
    provider: Any
    root: Any = None                                # project root — exemplar-deck retrieval
    critic_provider: Any = None                     # scene refine critic (defaults to provider)
    on_event: Callable[[dict], None] = _noop_event  # structured progress → SSE
    cancel: Callable[[], bool] = _never_cancel
    # RESUME: called with the full state dict after each node completes, so an interrupted run can
    # be reloaded and re-run — completed nodes short-circuit (see the `if s.<field>:` guards), so a
    # resumed run replays instantly through what's done and only re-runs what's missing.
    on_checkpoint: Callable[[dict], None] = _noop_event


@dataclass
class GenesisState:
    """Everything that flows across the pipeline (and persists for resume)."""
    seed: str = ""
    world: str = ""            # optional world-frame string for cast grounding (else derived)
    grounding: str = ""        # psyche notes, precomputed by the endpoint (ctx-dependent)
    rounds: int = 2            # scene refine rounds
    premise: dict = field(default_factory=dict)
    substrate: dict = field(default_factory=dict)
    particulars: list = field(default_factory=list)
    cast: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    geography: dict = field(default_factory=dict)   # raw {areas, travel} — applied at commit
    scene: dict = field(default_factory=dict)


async def _thread(fn, *a, **kw):
    """Run a sync provider-call function off the event loop."""
    return await asyncio.to_thread(lambda: fn(*a, **kw))


def _emit(ctx, node: str, status: str, **extra) -> None:
    ctx.deps.on_event({"type": "node", "node": node, "status": status, **extra})


def _checkpoint(ctx) -> None:
    """Persist the accumulated state so an interrupted run can resume from here."""
    ctx.deps.on_checkpoint(asdict(ctx.state))


def _world_str(s: GenesisState) -> str:
    """A short world frame for cast grounding — the endpoint's `world`, else derived from what's made."""
    if (s.world or "").strip():
        return s.world.strip()
    sub, prem = s.substrate or {}, s.premise or {}
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    bits = [b for b in (prem.get("logline", ""), place, sub.get("preoccupation", "")) if b]
    return " — ".join(bits)


def _scene_brief(s: GenesisState) -> str:
    """Compose the scene brief from premise + substrate ache/place + a few lived fragments + cast."""
    p, sub = s.premise or {}, s.substrate or {}
    parts: list[str] = []
    if p.get("logline"):
        parts.append(f"PREMISE: {p['logline']}")
    for k in ("protagonist", "want", "obstacle", "stakes", "spark"):
        if p.get(k):
            parts.append(f"{k.upper()}: {p[k]}")
    if sub.get("preoccupation"):
        parts.append(f"THE ACHE (implicit — never named): {sub['preoccupation']}")
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    if place:
        parts.append(f"PLACE: {place}")
    frags = [f.get("text", "") for f in (s.particulars or [])[:3] if isinstance(f, dict) and f.get("text")]
    if frags:
        parts.append("WORLD TEXTURE (draw on, do NOT explain):\n" + "\n".join(f"- {t}" for t in frags))
    names = [(h.get("name") or h.get("role") or "") for h in (s.cast or [])[:4] if isinstance(h, dict)]
    names = [n for n in names if n]
    if names:
        parts.append("CAST who may appear: " + ", ".join(names))
    return "\n".join(parts)


# ── Steps (each ONE focused generation, reusing the tested functions) ──────────────

async def step_premise(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.premise:                       # resume: already generated → skip the model call
        _emit(ctx, "premise", "cached"); return s.premise
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "premise", "start")
    s.premise = await _thread(build_premise, ctx.deps.provider, s.seed) or {}
    _emit(ctx, "premise", "done", data=s.premise)
    _checkpoint(ctx)
    return s.premise


async def step_substrate(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.substrate:
        _emit(ctx, "substrate", "cached"); return s.substrate
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "substrate", "start")
    s.substrate = await _thread(build_substrate, ctx.deps.provider, s.seed) or {}
    _emit(ctx, "substrate", "done", data=s.substrate)
    _checkpoint(ctx)
    return s.substrate


async def step_particulars(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.particulars:
        _emit(ctx, "particulars", "cached", count=len(s.particulars)); return s.particulars
    if ctx.deps.cancel():
        return []
    _emit(ctx, "particulars", "start")
    s.particulars = await _thread(gen_particulars, ctx.deps.provider, s.seed, 6, s.substrate or None) or []
    _emit(ctx, "particulars", "done", count=len(s.particulars), data=s.particulars)
    _checkpoint(ctx)
    return s.particulars


async def step_cast(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.cast:
        _emit(ctx, "cast", "cached", count=len(s.cast)); return s.cast
    if ctx.deps.cancel():
        return []
    _emit(ctx, "cast", "start")
    s.cast = await _thread(design_by_role, ctx.deps.provider, s.seed, s.grounding, _world_str(s)) or []
    _emit(ctx, "cast", "done", count=len(s.cast), data=s.cast)
    _checkpoint(ctx)
    return s.cast


async def step_weave(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.relationships:
        _emit(ctx, "weave", "cached", count=len(s.relationships)); return s.relationships
    if ctx.deps.cancel() or len(s.cast) < 2:
        return s.relationships
    _emit(ctx, "weave", "start")
    s.relationships = await _thread(weave_relationships, ctx.deps.provider, s.cast) or []
    _emit(ctx, "weave", "done", count=len(s.relationships), data=s.relationships)
    _checkpoint(ctx)
    return s.relationships


async def step_geography(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.geography:
        _emit(ctx, "geography", "cached"); return s.geography
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "geography", "start")
    from .geography import gen_geography
    sub = s.substrate or {}
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    # Harnesses are NAMELESS until commit — use the short function tag ('protagonist'…) as the
    # handle, never the whole role paragraph (it pollutes the map prompt as a fake name).
    cast_lines = [{"name": h.get("name") or h.get("function") or "?",
                   "about": (h.get("role") or h.get("want") or "")[:160]}
                  for h in (s.cast or []) if isinstance(h, dict)]
    s.geography = await _thread(gen_geography, ctx.deps.provider,
                                premise=(s.premise or {}).get("logline", ""),
                                world_note=place, cast=cast_lines, root=ctx.deps.root) or {}
    _emit(ctx, "geography", "done",
          areas=len((s.geography or {}).get("areas") or []), data=s.geography)
    _checkpoint(ctx)
    return s.geography


async def step_scene(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.scene:
        _emit(ctx, "scene", "cached"); return s.scene
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "scene", "start")
    critic = ctx.deps.critic_provider or ctx.deps.provider
    out = await _thread(loop_scene, ctx.deps.provider, _scene_brief(s), s.rounds, critic) or {}
    s.scene = out.get("scene") or {}
    _emit(ctx, "scene", "done", data=s.scene, rounds_used=out.get("rounds_used"), trace=out.get("trace"))
    _checkpoint(ctx)
    return s.scene


async def step_assemble(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    # The graph's return value is delivered to the client as the job's `result` event — no
    # terminal event here (a `done` type would collide with the job wrapper's own `done`).
    s = ctx.state
    return {"premise": s.premise, "substrate": s.substrate, "particulars": s.particulars,
            "cast": s.cast, "relationships": s.relationships, "geography": s.geography,
            "scene": s.scene}


# ── Graph construction ─────────────────────────────────────────────────────────────

def _build_genesis_graph():
    gb = GraphBuilder(state_type=GenesisState, deps_type=GenesisDeps, output_type=dict)
    pr, sub = gb.step(step_premise), gb.step(step_substrate)
    part, cast = gb.step(step_particulars), gb.step(step_cast)
    weave, geo = gb.step(step_weave), gb.step(step_geography)
    scene, asm = gb.step(step_scene), gb.step(step_assemble)
    join = gb.join(reduce_null)
    join2 = gb.join(reduce_null)
    gb.add(gb.edge_from(gb.start_node).to(pr, sub))   # fork: the two slow calls overlap
    gb.add_edge(pr, join)
    gb.add_edge(sub, join)
    gb.add_edge(join, part)                            # then linear (each reads prior state)
    gb.add_edge(part, cast)
    gb.add(gb.edge_from(cast).to(weave, geo))          # fork: web + geography both hang off cast
    gb.add_edge(weave, join2)
    gb.add_edge(geo, join2)
    gb.add_edge(join2, scene)
    gb.add_edge(scene, asm)
    gb.add_edge(asm, gb.end_node)
    return gb.build()


GENESIS_GRAPH = _build_genesis_graph()


def genesis_state_from(d: dict) -> GenesisState:
    """Rebuild GenesisState from a persisted checkpoint dict (ignores unknown/legacy keys)."""
    names = {f.name for f in _dc_fields(GenesisState)}
    return GenesisState(**{k: v for k, v in (d or {}).items() if k in names})


# ── Run checkpoint store — a plain JSON file per run_id (NOT story_sessions, which forces its own
# schema and would drop the state). This IS the resume substrate: the accumulated graph state. ──

def _runs_dir(root):
    import pathlib
    d = pathlib.Path(root) / "configs" / "genesis_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run(root, run_id: str, state_dict: dict) -> None:
    import json
    import re
    safe = re.sub(r"[^\w\-]+", "_", str(run_id or "")).strip("_")
    if safe:
        (_runs_dir(root) / f"{safe}.json").write_text(
            json.dumps(state_dict, ensure_ascii=False), encoding="utf-8")


def load_run(root, run_id: str) -> dict | None:
    import json
    import re
    safe = re.sub(r"[^\w\-]+", "_", str(run_id or "")).strip("_")
    p = _runs_dir(root) / f"{safe}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


async def run_genesis(state: GenesisState, deps: GenesisDeps) -> dict:
    """Grow a full story: premise → substrate → particulars → cast → weave → scene. Progress
    streams through `deps.on_event`; state is checkpointed after each node via `deps.on_checkpoint`.
    RESUME: pass a `state` rebuilt from the last checkpoint (genesis_state_from) — completed nodes
    short-circuit (their `if s.<field>:` guards) and only the missing ones re-run."""
    return await GENESIS_GRAPH.run(state=state, deps=deps, inputs=None)


# ── Bottom-up WORLD SYSTEMS graph — accrete interlocking systems → let a scenario emerge ───────

@dataclass
class SystemsState:
    seed: str = ""
    n: int = 4
    systems: list = field(default_factory=list)
    scenario: dict = field(default_factory=dict)


async def step_systems(ctx: StepContext[SystemsState, GenesisDeps, None]) -> list:
    if ctx.deps.cancel():
        return []
    _emit(ctx, "systems", "start")
    s = ctx.state
    s.systems = await _thread(accrete_systems, ctx.deps.provider, s.seed, s.n) or []
    _emit(ctx, "systems", "done", count=len(s.systems), data=s.systems)
    return s.systems


async def step_scenario(ctx: StepContext[SystemsState, GenesisDeps, None]) -> dict:
    if ctx.deps.cancel() or not ctx.state.systems:
        return {}
    _emit(ctx, "scenario", "start")
    s = ctx.state
    s.scenario = await _thread(scenario_from_systems, ctx.deps.provider, s.systems, s.seed) or {}
    _emit(ctx, "scenario", "done", data=s.scenario)
    return s.scenario


async def step_systems_assemble(ctx: StepContext[SystemsState, GenesisDeps, None]) -> dict:
    return {"systems": ctx.state.systems, "scenario": ctx.state.scenario}


def _build_systems_graph():
    gb = GraphBuilder(state_type=SystemsState, deps_type=GenesisDeps, output_type=dict)
    sysn, scen, asm = gb.step(step_systems), gb.step(step_scenario), gb.step(step_systems_assemble)
    gb.add_edge(gb.start_node, sysn)
    gb.add_edge(sysn, scen)
    gb.add_edge(scen, asm)
    gb.add_edge(asm, gb.end_node)
    return gb.build()


SYSTEMS_GRAPH = _build_systems_graph()


async def run_systems(state: SystemsState, deps: GenesisDeps) -> dict:
    """Bottom-up world gen: accrete interlocking systems, then let a scenario emerge. Streams
    a `node` event per stage through `deps.on_event`."""
    return await SYSTEMS_GRAPH.run(state=state, deps=deps, inputs=None)
