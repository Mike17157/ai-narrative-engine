"""The play turn as a pydantic-graph pipeline — the SHIPPED shape of the narrative harness.

    compile → scene → consequence → prose → scribe → apply

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
    consequence_provider: Any = None   # the reasoning "what happens" step (defaults to writer)
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
    beat: str = ""                                  # what-happens beat (consequence)
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


async def step_scene(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """PER-SCENE StoryMaster firing (the coordinator's cadence): on a scene boundary — the
    opening, or the location changed — the director plans the scene ONCE (goal / pressure /
    exit → world.scene_plan) and the fresh plan is injected into THIS turn's consequence
    context. Mid-scene turns skip entirely (no LLM call, no latency; compile already
    surfaced the standing plan)."""
    from .storymaster import StoryMaster, scene_block
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel():
        return {}
    cur = s.tc.get("cur") or ""
    if not StoryMaster(d.ctx, d.st, s.world_state).scene_boundary(cur):
        d.on_event({"type": "node", "node": "scene", "boundary": False})
        return s.world_state.get("scene_plan") or {}
    loc_name = next((l.name for l in d.st.locations if l.id == cur), cur)
    sm = StoryMaster(d.ctx, d.st, s.world_state,
                     provider=d.consequence_provider or d.provider, location=loc_name)
    hist = s.body.get("history") or []
    recent = "\n".join(str(m.get("text", "")) for m in hist[-2:])[-800:]
    plan = await _thread(sm.open_scene, loc_id=cur, recent=recent)
    blk = scene_block(plan)
    if blk and s.tc.get("consequence_system"):
        s.tc["consequence_system"] += "\n\n" + blk
    d.on_event({"type": "node", "node": "scene", "boundary": True,
                "goal": plan.get("goal", "")})
    return plan


async def step_consequence(ctx: StepContext[PlayState, PlayDeps, None]) -> str:
    """The LOGIC step (reasoning model): work out what the player's action ACTUALLY causes —
    cause→effect, how each present character reacts, what changes, the new cost/choice. The
    prose pass renders this; without it the narrator produces atmosphere, not events."""
    from .guards import generate_guarded
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel() or not s.tc.get("consequence_system"):
        return ""
    hist = s.body.get("history") or []
    last_user = next((m.get("text", "") for m in reversed(hist) if m.get("role") == "user"), "")
    recent = "\n".join((("Player" if m.get("role") == "user" else "Narrator")
                        + f": {m.get('text', '')}") for m in hist[-4:])
    prompt = (f"RECENT:\n{recent}\n\nTHE PLAYER JUST DID / SAID:\n{last_user}\n\n"
              f"Work out what actually happens next, step by step.")
    prov = d.consequence_provider or d.provider
    g = await _thread(generate_guarded, prov, system=s.tc["consequence_system"], prompt=prompt,
                      root=d.ctx.root, emits=None, fallback=d.fallback)
    s.beat = (g.get("text") or "").strip()
    d.on_event({"type": "node", "node": "consequence", "chars": len(s.beat)})
    return s.beat


async def step_prose(ctx: StepContext[PlayState, PlayDeps, None]) -> str:
    """The WRITER: free-text narration under the minimal register (no schema). When a
    consequence beat exists, it renders THAT — the beat is the scene's truth (scaffold),
    not a script: flowing narration, change no facts, add no new events."""
    from .guards import generate_guarded
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel():
        return ""
    prompt = s.tc["prompt"]
    if s.beat:
        prompt = (s.tc["prompt"] + "\n\nWHAT HAPPENS THIS TURN (the truth of the scene — render it "
                  "as flowing narration; change no facts and add no new events; compress, reorder, "
                  f"or leave things implied for flow):\n{s.beat}")
    g = await _thread(generate_guarded, d.provider, system=s.tc["system"], prompt=prompt,
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
    # Every NAMED person the narration touched — present or merely mentioned — with where they are
    # and a one-line. The StoryMaster turns these into the character network (records by location).
    schema["properties"]["people"] = {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "at", "note"],
        "properties": {"name": {"type": "string"},
                       "at": {"type": "string", "description": "where they are right now — a "
                              "location name, 'here' if in the scene, or '' if unknown/away"},
                       "note": {"type": "string", "description": "one plain line: who they are / "
                                "their tie to the viewpoint"}}}}
    schema["properties"]["arc_milestone"] = {
        "type": "boolean", "description": "true ONLY if the narration just accomplished the "
        "current arc stage's milestone (see the system brief); false otherwise or if no arc"}
    schema["required"] = [r for r in schema["required"] if r != "reply"] \
        + ["state_deltas", "player_status", "people", "arc_milestone"]
    # VN/webnovel LINES: code cuts the narration into verbatim segments; the scribe (already
    # reading this turn) only LABELS them — speaker per quote, narrator/thought per narration.
    from .lines import segment_prose, lines_prompt
    _segs = segment_prose(s.narration)
    _pname = ((s.body.get("player") or {}).get("name") or "Player").strip() or "Player"
    _cnames = [getattr(d.ctx.base_settings.characters.get(m.character), "name", m.character)
               or m.character for m in d.st.cast]
    if _segs:
        schema["properties"]["lines"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["i", "speaker", "emotion"],
            "properties": {"i": {"type": "integer", "description": "the segment's number"},
                           "speaker": {"type": "string", "description": "exact cast/player name "
                                       "for a quoted line; 'narrator' or 'thought' for narration"},
                           "emotion": {"type": "string", "description": "for a quoted line, ONE "
                                       "emotion key from the speaker's listed range (the sprite "
                                       "shown on this line); '' for narration/thought"}}}}
        schema["required"] = schema["required"] + ["lines"]
    hist = s.body.get("history") or []
    last_user = next((m.get("text", "") for m in reversed(hist) if m.get("role") == "user"), "")
    prompt = (f"PLAYER'S LATEST ACTION: {last_user}\n\n"
              f"NARRATION (the turn that just happened):\n{s.narration}\n\n"
              f"Report the scene state."
              + lines_prompt(_segs, _cnames, _pname))
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


def _vn_lines(d, s, data: dict) -> list[dict]:
    """The narration re-cut as VN lines (segments are code-verbatim; the scribe's `lines`
    labels attribute them). Pure; [] when there's nothing to split."""
    from .lines import segment_prose, assemble_lines
    pname = ((s.body.get("player") or {}).get("name") or "Player").strip() or "Player"
    cnames = {getattr(d.ctx.base_settings.characters.get(m.character), "name", m.character)
              or m.character for m in d.st.cast}
    return assemble_lines(segment_prose(s.narration), data.get("lines"), cnames, pname)


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
    # AUTHORITATIVE present = the StoryMaster's roster (decided at compile, obeyed by the narrator),
    # NOT whatever the scribe read off the prose. The scribe can't add or drop who's in the scene.
    roster = s.tc.get("roster") or data.get("present") or []
    present_keys = [name_to_key.get((n or "").lower()) for n in roster]
    loc = data.get("location") if any(l.id == data.get("location") for l in st.locations) else cur

    # THE STORYMASTER owns the character network: `people` report → records pinned to locations
    # (mention → thin record; static), and fleshes anyone in the authoritative roster who isn't a
    # known character (a controlled entrance → full card, one per turn). Slice 1 of the coordinator.
    from .storymaster import StoryMaster, state_card as _sm_card
    loc_name = next((l.name for l in st.locations if l.id == loc), loc)
    _sm = StoryMaster(appctx, st, world_state, provider=d.provider, location=loc_name)
    born_now = _sm.ingest(people=data.get("people") or [], present=roster, narration=s.narration)

    # ARC advancement: the scribe watched this turn's narration against the current stage's
    # milestone; on a hit, the stage advances deterministically (no LLM).
    if data.get("arc_milestone"):
        from .storymaster import advance_arc
        advance_arc(world_state)

    emotions = {}
    for e in data.get("emotions", []):
        ck = name_to_key.get((e.get("character") or "").lower())
        if not ck:
            continue
        ekey = (e.get("emotion") or "neutral").strip().lower()
        emotions[ck] = ekey if ekey in EMOTION_KEYS else "neutral"

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
        # The MANUSCRIPT: the same prose, grouped by scene for the reading/editing pane.
        from .storymaster import record_page
        _beat_line = next((str(dd.get("value", "")) for dd in (data.get("state_deltas") or [])
                           if dd.get("op") == "log"), "")
        record_page(world_state, loc=loc_name, text=data.get("reply", ""),
                    beat=_beat_line, step=int(world_state.get("step") or 0))
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
        if status == "sleeping":                       # sleep is the only door out of night:
            from .storymaster import next_day          # the day turns over to the next morning
            next_day(world_state)
        save_session(appctx.root, d.sid, {**d.sess, "state": _SE.with_world(d.sess.get("state"), world_state)})

    return {
        "reply": data.get("reply", ""), "location": loc,
        # VN/webnovel LINES: the same narration as speaker-attributed lines (dialogue /
        # narration / the player's inner-voice thoughts). Flat `reply` stays the source of truth.
        "lines": _vn_lines(d, s, data),
        "beat": s.beat,                 # the consequence reasoning (what-happens), for the UI
        "scene_plan": world_state.get("scene_plan") or {},   # the per-scene director's agenda
        "card": _sm_card(world_state, st),   # the STATE CARD: derived view, free every turn
        "born": born_now,               # characters created on the fly this turn (gradual)
        "present": [k for k in present_keys if k],
        "pov": (world_state.get("scene") or {}).get("pov", ""),
        "step": world_state.get("step"),
        "emotions": emotions,
        "movement": bool(data.get("movement")),
        "player_status": status,
        "day": world_state.get("day") or None,   # the slot rhythm (None before the day model)
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
    comp, conseq = gb.step(step_compile), gb.step(step_consequence)
    scene = gb.step(step_scene)
    prose, scribe, apply_ = gb.step(step_prose), gb.step(step_scribe), gb.step(step_apply)
    gb.add_edge(gb.start_node, comp)
    gb.add_edge(comp, scene)          # per-SCENE StoryMaster firing (boundary-only director)
    gb.add_edge(scene, conseq)        # LOGIC before prose: work out what happens, then write it
    gb.add_edge(conseq, prose)
    gb.add_edge(prose, scribe)
    gb.add_edge(scribe, apply_)
    gb.add_edge(apply_, gb.end_node)
    return gb.build()


PLAY_GRAPH = _build_play_graph()


async def run_play_turn(state: PlayState, deps: PlayDeps) -> dict:
    """One play turn through the graph. On success `state.result` is the HTTP response
    body; on failure `state.error` says why (endpoint → 500)."""
    return await PLAY_GRAPH.run(state=state, deps=deps, inputs=None)
