"""The play turn as a pydantic-graph pipeline — the SHIPPED shape of the narrative harness.

    compile → prose → scribe → apply

Same substrate as genesis_graph.py (typed State/Deps, thin async steps over the sync
providers). The four nodes are the harness plan's two-pass turn made explicit:

  • compile — build_turn_context (pure): role-tiered cast, scene+POV, continuity lane,
    lore/state/relationships, bounded history → {system, prompt, scribe_system, lanes}.
  • prose   — the WRITER, free text, craft register, refusal-guarded. No schema: prose
    born inside JSON is flattened prose (degrader D1).
  • scribe  — a cheap model reads the fresh narration and emits the structured scene
    report + state_deltas. Failure degrades to narration-with-no-state-mutation.
  • apply   — deterministic: name→key mapping, emotion resolution, geography-gated
    delta application, perception step, scene persistence, consolidation, response.

The bench (bench_play.py) exercises THIS path — test what ships. Sprite/scene/emotion
orchestration nodes slot in between scribe and apply when that work lands. The critic is
a BENCH-ONLY instrument (user decision): no scoring node exists in the live turn.
"""
from __future__ import annotations

import asyncio
import copy as _copy
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False


@dataclass
class PlayDeps:
    """Everything the nodes need, injected by the endpoint."""
    ctx: Any                    # AppContext
    st: Any                     # the Story
    key: str                    # story key
    sid: str                    # play session id
    sess: dict                  # loaded session (persisted back with the new world state)
    provider: Any               # the WRITER
    scribe_provider: Any        # the cheap structured reporter (defaults to writer)
    fallback: Any = None        # refusal-guard fallback model
    story_scope: str = ""
    thread_scope: str = ""
    on_event: Callable[[dict], None] = _noop_event
    cancel: Callable[[], bool] = _never_cancel


@dataclass
class PlayState:
    """What flows across the turn."""
    body: dict = field(default_factory=dict)
    world_state: dict = field(default_factory=dict)
    tc: dict = field(default_factory=dict)          # compiled context (compile)
    narration: str = ""                             # prose pass output
    prose_guard: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)      # scribe pass output
    scribe_guard: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)      # the HTTP response body (apply)
    error: str = ""                                 # terminal error (endpoint → 500)


async def _thread(fn, *a, **kw):
    return await asyncio.to_thread(lambda: fn(*a, **kw))


# ── Nodes ────────────────────────────────────────────────────────────────────────────

async def step_compile(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """Assemble both passes' contexts — pure, deterministic, no LLM call."""
    from .play_context import build_turn_context
    s, d = ctx.state, ctx.deps
    s.tc = await _thread(build_turn_context, d.ctx, d.st, d.key, s.body, s.world_state,
                         story_scope=d.story_scope, thread_scope=d.thread_scope)
    d.on_event({"type": "node", "node": "compile", "lanes": s.tc.get("lanes")})
    return s.tc


async def step_prose(ctx: StepContext[PlayState, PlayDeps, None]) -> str:
    """The WRITER: free-text narration under the craft register (no schema)."""
    from .guards import generate_guarded
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel():
        return ""
    g = await _thread(generate_guarded, d.provider, system=s.tc["system"], prompt=s.tc["prompt"],
                      root=d.ctx.root, emits=None, fallback=d.fallback)
    s.narration = (g.get("text") or "").strip()
    s.prose_guard = {"tripped": g.get("tripped"), "used_fallback": g.get("used_fallback")}
    if not s.narration:
        s.error = g.get("error") or "the narrator returned nothing"
    d.on_event({"type": "node", "node": "prose", "chars": len(s.narration)})
    return s.narration


async def step_scribe(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """The SCRIBE: read the fresh narration, emit the structured scene report + deltas."""
    from .guards import generate_guarded
    from ..server.services.prompts import PLAY_SCHEMA
    from . import state_engine as _SE
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel():
        return {}
    schema = _copy.deepcopy(PLAY_SCHEMA)
    del schema["properties"]["reply"]              # the scribe reads prose, never writes it
    schema["properties"]["state_deltas"] = {"type": "array", "items": _SE.STATE_DELTA_ITEM}
    schema["required"] = [r for r in schema["required"] if r != "reply"] \
        + ["state_deltas", "player_status"]
    hist = s.body.get("history") or []
    last_user = next((m.get("text", "") for m in reversed(hist) if m.get("role") == "user"), "")
    prompt = (f"PLAYER'S LATEST ACTION: {last_user}\n\n"
              f"NARRATION (the turn that just happened):\n{s.narration}\n\n"
              f"Report the scene state.")
    g = await _thread(generate_guarded, d.scribe_provider, system=s.tc["scribe_system"],
                      prompt=prompt, root=d.ctx.root, emits=schema,
                      fallback=d.provider if d.scribe_provider is not d.provider else d.fallback)
    s.report = g["data"] or {}
    s.scribe_guard = {"tripped": g.get("tripped"), "used_fallback": g.get("used_fallback")}
    d.on_event({"type": "node", "node": "scribe", "ok": bool(s.report)})
    return s.report


async def step_apply(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """Deterministic close: resolve names/emotions/location, apply deltas through the
    geography gate, record perception, persist the scene, consolidate on sleep/death,
    and shape the HTTP response. No LLM calls."""
    s, d = ctx.state, ctx.deps
    if s.error:
        return {}
    s.result = await _thread(_apply_turn, d, s)
    d.on_event({"type": "node", "node": "apply",
                "present": s.result.get("present"), "pov": s.result.get("pov")})
    return s.result


# Noun-phrase scan for the detail backstop. Token scanner, not a regex span — regex matches
# are non-overlapping, so "my pocket and turn my grandmother's…" would swallow the second "my".
# ponytail: heuristic scanner; an NER pass if players' phrasing outgrows it.
_TRIM = {"over", "in", "on", "at", "with", "into", "back", "out", "again", "around", "up", "down"}
_STOP = {"way", "day", "eyes", "hand", "hands", "mind", "heart", "breath", "feet", "life",
         "head", "voice", "turn", "side", "words", "thoughts", "pocket", "fingers", "own"}
_FUNC = {"and", "or", "but", "then", "the", "a", "an", "to", "my", "your", "for", "as", "is"}


def _player_particulars(text: str) -> list[str]:
    """Possessive noun-phrases from the player's message: 'my <phrase>' up to a function word /
    punctuation, trailing prepositions+adverbs trimmed; kept only if it names a real particular
    (a nested possessive, or ≥3 words) and isn't a body/abstract word."""
    import re as _re
    toks = _re.findall(r"[a-z\-']+|[^a-z\s]", (text or "").lower())
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] != "my":
            i += 1
            continue
        j, ph = i + 1, []
        while (j < len(toks) and len(ph) < 5 and _re.fullmatch(r"[a-z\-']+", toks[j])
               and toks[j] not in _FUNC and toks[j] not in _TRIM):
            ph.append(toks[j]); j += 1
        while ph and (ph[-1] in _TRIM or ph[-1].endswith("ly")):
            ph.pop()
        joined = " ".join(ph)
        if (ph and (("'s" in joined) or len(ph) >= 3) and ph[-1] not in _STOP
                and ph[0] not in ("day", "morning", "evening", "night", "week", "time")):
            out.append(joined)
        i = j
    return out


def _apply_turn(d: PlayDeps, s: PlayState) -> dict:
    from ..server.services.story_sessions import save_session
    from ..server.services.emotions import EMOTION_KEYS
    from . import state_engine as _SE

    appctx, st, key = d.ctx, d.st, d.key
    world_state, data, tc = s.world_state, s.report, s.tc
    cur, prior_pov = tc["cur"], tc["prior_pov"]

    if not data:
        # Scribe failure must not drop the turn: return the narration with the prior scene
        # carried over and NO state mutation (a wrong empty `present` would wipe the scene).
        return {
            "reply": s.narration, "location": cur,
            "present": [k for k in ((world_state.get("scene") or {}).get("members") or [])],
            "pov": prior_pov, "step": world_state.get("step"),
            "emotions": {}, "movement": False, "player_status": "active",
            "consolidation": None, "state": _SE.summary(world_state),
            "lanes": tc.get("lanes"),
            "guard": {**s.prose_guard, "scribe_failed": True},
        }

    data["reply"] = s.narration

    # Deterministic detail BACKSTOP: cheap scribes skip `detail` ops stochastically (measured —
    # imperative prompt wording alone still no-ops some runs), and the story MUST remember an
    # object the player themselves introduced. Possessive noun-phrases in the player's own words
    # ("my grandmother's cracked brass compass") are synthesized into detail deltas; the op's
    # near-dupe guard makes re-capture idempotent.
    hist = s.body.get("history") or []
    last_user = next((m.get("text", "") for m in reversed(hist) if m.get("role") == "user"), "")
    for ph in _player_particulars(last_user):
        data.setdefault("state_deltas", []).append(
            {"op": "detail", "name": "", "key": "", "value": f"the player carries {ph}",
             "title": "", "keywords": []})

    # map present/emotion names -> character keys for the UI's sprite lookup
    name_to_key = {(appctx.base_settings.characters[m.character].name
                    if m.character in appctx.base_settings.characters else m.character).lower():
                   m.character for m in st.cast}
    present_keys = [name_to_key.get((n or "").lower()) for n in data.get("present", [])]
    emotions = {}
    for e in data.get("emotions", []):
        ck = name_to_key.get((e.get("character") or "").lower())
        if not ck:
            continue
        ekey = (e.get("emotion") or "neutral").strip().lower()
        emotions[ck] = ekey if ekey in EMOTION_KEYS else "neutral"
    loc = data.get("location") if any(l.id == data.get("location") for l in st.locations) else cur

    # Evolve + persist the world state. `fact` deltas write back into the thread's lorebook
    # scope; `move` deltas pass the geography gate (on-screen = canon, off-screen rate-limited).
    try:
        from .geography import make_move_validator
        from . import perception as _PC
        on_names = {str(n) for n in (data.get("present") or []) if n} | {
            (appctx.base_settings.characters[k].name if k in appctx.base_settings.characters else k)
            for k in ((world_state.get("scene") or {}).get("members") or [])}
        world_state = _SE.apply_deltas(world_state, data.get("state_deltas") or [],
                                       root=appctx.root, scope=d.thread_scope,
                                       validate_move=make_move_validator(st, on_names))
        world_state["location"] = loc or world_state.get("location") or ""
        # Index this turn as a STEP + record who witnessed it (perception: join = no backlog).
        _PC.record_step(world_state, [k for k in present_keys if k])
        world_state.setdefault("transcript", []).append(data.get("reply", ""))
        # Carry the SCENE forward: space + co-located members + the sticky POV.
        pov_out = (data.get("pov") or "").strip()
        pov_key = name_to_key.get(pov_out.lower(), pov_out) or prior_pov
        world_state["scene"] = {"space": loc, "members": [k for k in present_keys if k], "pov": pov_key}
        save_session(appctx.root, d.sid, {**d.sess, "state": _SE.with_world(d.sess.get("state"), world_state)})
    except Exception:  # noqa: BLE001 — a state-write failure must not drop the turn
        pass

    # Lifecycle: ONLY sleep/death consolidates memory (dream/rest pass; death also rewinds).
    consolidation = None
    status = (data.get("player_status") or "active").strip().lower()
    if status in ("sleeping", "dead") and key:
        from . import stage_tools as _ST
        consolidation = _ST.consolidate_on_rest(appctx, key, world_state, status)
        save_session(appctx.root, d.sid, {**d.sess, "state": _SE.with_world(d.sess.get("state"), world_state)})

    return {
        "reply": data.get("reply", ""), "location": loc,
        "present": [k for k in present_keys if k],
        "pov": (world_state.get("scene") or {}).get("pov", ""),
        "step": world_state.get("step"),
        "emotions": emotions,
        "movement": bool(data.get("movement")),
        "player_status": status,
        "consolidation": consolidation,
        "state": _SE.summary(world_state),
        "lanes": tc.get("lanes"),
        "guard": {"tripped": s.prose_guard.get("tripped") or s.scribe_guard.get("tripped"),
                  "used_fallback": bool(s.prose_guard.get("used_fallback")
                                        or s.scribe_guard.get("used_fallback"))},
    }


# ── Graph ───────────────────────────────────────────────────────────────────────────

def _build_play_graph():
    gb = GraphBuilder(state_type=PlayState, deps_type=PlayDeps, output_type=dict)
    comp, prose = gb.step(step_compile), gb.step(step_prose)
    scribe, apply_ = gb.step(step_scribe), gb.step(step_apply)
    gb.add_edge(gb.start_node, comp)
    gb.add_edge(comp, prose)
    gb.add_edge(prose, scribe)
    gb.add_edge(scribe, apply_)
    gb.add_edge(apply_, gb.end_node)
    return gb.build()


PLAY_GRAPH = _build_play_graph()


async def run_play_turn(state: PlayState, deps: PlayDeps) -> dict:
    """One play turn through the graph. On success `state.result` is the HTTP response
    body; on failure `state.error` says why (endpoint → 500)."""
    return await PLAY_GRAPH.run(state=state, deps=deps, inputs=None)
