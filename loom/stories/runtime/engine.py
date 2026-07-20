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
    delta application, runtime_state step, scene persistence, consolidation, response.

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

from ..workflows import WorkflowTrace


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False


def _emit(deps, node: str, **data) -> None:
    """Keep legacy UI events while adding the shared workflow trace envelope."""
    deps.on_event({"type": "node", "node": node, **data})
    deps.trace.emit("node", node=node, status="completed", **data)


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
    # Activated stories carry a model-free compiled contract plus a private
    # runtime level.  Keeping it out of ``world_state`` prevents hidden plans
    # from being accidentally rendered into narrator context.
    scenario: dict = field(default_factory=dict)
    runtime_state: dict = field(default_factory=dict)
    on_event: Callable[[dict], None] = _noop_event
    cancel: Callable[[], bool] = _never_cancel
    trace: WorkflowTrace = field(default_factory=lambda: WorkflowTrace("play"))


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


def _compiled_live_beat(deps: PlayDeps, scene: dict) -> dict:
    """Project the current compiled scene into one public Director beat.

    The executable runtime contains private event status and author-only plan
    material.  This adapter reads only the scenario's structural clock/turn
    counters plus public card fields, then hands those narrow values to the
    pure selector.  In particular, it never reads a character ``system`` or
    the compiled ``director_plan``.
    """
    from .live_beat import select_live_beat

    if not isinstance(scene, dict):
        return {}
    fields = getattr(deps.st, "fields", None)
    fields = fields if isinstance(fields, dict) else {}
    cores = fields.get("character_cores")
    cores = cores if isinstance(cores, dict) else {}

    roles = scene.get("roles")
    if not isinstance(roles, dict):
        roles = scene.get("participant_roles")
    roles = dict(roles) if isinstance(roles, dict) else {}
    # Legacy cards may have the public role beside the reusable character
    # card rather than on the authored scene.  It is a narrow, safe fallback;
    # never use the character's free-form system prompt as a substitute.
    characters = getattr(getattr(deps.ctx, "base_settings", None), "characters", {})
    if isinstance(characters, dict):
        for key in scene.get("participants") or []:
            if key in roles:
                continue
            char = characters.get(key)
            char_fields = getattr(char, "fields", None)
            if isinstance(char_fields, dict) and isinstance(char_fields.get("role"), str):
                roles[key] = char_fields["role"]

    scenario_state = deps.runtime_state.get("scenario_state") \
        if isinstance(deps.runtime_state, dict) else {}
    scenario_state = scenario_state if isinstance(scenario_state, dict) else {}
    turns = scenario_state.get("turns")
    turns = turns if isinstance(turns, list) else []
    scene_id = str(scene.get("id") or "")
    scene_turn_count = sum(
        1 for turn in turns
        if isinstance(turn, dict) and str(turn.get("scene") or "") == scene_id
    )
    return select_live_beat(
        scene,
        participant_roles=roles,
        character_cores=cores,
        recent_state={
            "turn_count": len(turns),
            "scene_turn_count": scene_turn_count,
            "time": scenario_state.get("time"),
            "location": scenario_state.get("location"),
        },
        player_id=str(fields.get("player_id") or "player"),
    )


def _freeplay_live_beat(deps: PlayDeps, world_state: dict) -> dict:
    """Project the free-play StoryMaster's ``scene_plan`` into one public Director beat.

    Mirrors ``_compiled_live_beat``'s narrow adapter shape, but the source is the
    StoryMaster's own per-scene plan (director.py's ``_h_scene``) instead of an
    authored scenario. Only the plan's public ``theme``/``tone``/``roles`` and who
    is currently on stage ever reach the selector — never its private
    ``goal``/``pressure``/``exit`` agenda, which stays ``scene_block``'s job.
    """
    from .live_beat import select_live_beat

    plan = world_state.get("scene_plan") if isinstance(world_state, dict) else None
    if not isinstance(plan, dict):
        return {}
    fields = getattr(deps.st, "fields", None)
    fields = fields if isinstance(fields, dict) else {}
    cores = fields.get("character_cores")
    cores = cores if isinstance(cores, dict) else {}
    members = (world_state.get("scene") or {}).get("members")
    members = [str(m) for m in members if m] if isinstance(members, list) else []
    step = int(world_state.get("step") or 0)
    scene = {
        "id": str(plan.get("space") or ""),
        "location": str(plan.get("loc") or ""),
        "theme": plan.get("theme") or "",
        "tone": plan.get("tone") or "",
        "roles": plan.get("roles") if isinstance(plan.get("roles"), dict) else {},
        "participants": members,
    }
    return select_live_beat(
        scene,
        participant_roles=scene["roles"],
        character_cores=cores,
        recent_state={
            "turn_count": step,
            "scene_turn_count": max(0, step - int(plan.get("opened") or 0)),
            "time": "",
            "location": scene["location"],
        },
        player_id=str(fields.get("player_id") or "player"),
    )


# ── Nodes ────────────────────────────────────────────────────────────────────────────

async def step_compile(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """Assemble both passes' contexts — pure, deterministic, no LLM call."""
    from .context import build_turn_context
    s, d = ctx.state, ctx.deps
    scenario_scene = {}
    if d.scenario:
        from .compiled import ScenarioTransitionError, prepare_turn
        try:
            scenario_scene = prepare_turn(d.scenario, s.world_state, d.runtime_state, s.body)
        except ScenarioTransitionError as exc:
            s.error = f"scenario transition: {exc}"
            return {}
    from . import state as _SE0
    s.tc = await _thread(build_turn_context, d.ctx, d.st, d.key, s.body, s.world_state,
                         story_scope=d.story_scope, thread_scope=d.thread_scope,
                         scenario=d.scenario, runtime_state=d.runtime_state,
                         scenario_scene=scenario_scene,
                         # The scene-keyed loader's persisted cache (State doc `ctx` level) —
                         # read-only here; the updated copy returns in tc and is saved by apply.
                         scene_cache=_SE0.get_level(d.sess.get("state"), "ctx", {}))
    # Compiled stories intentionally skip the free-form StoryMaster scene
    # planner.  Give their consequence pass one deterministic *public* scene
    # anchor instead: it establishes a playable pressure but cannot reveal a
    # private director event or overrule the player's action.
    # Free play reads the StoryMaster's own scene_plan here — this is the
    # STANDING plan for every mid-scene turn (zero extra cost). On a scene
    # boundary this turn, step_scene overwrites it below with the fresh plan,
    # the same two-tier pattern context.py already uses for scene_block.
    live_beat = _compiled_live_beat(d, scenario_scene) if d.scenario else _freeplay_live_beat(d, s.world_state)
    if live_beat:
        from .live_beat import director_live_beat_block
        live_block = director_live_beat_block(live_beat)
        if live_block and s.tc.get("consequence_system"):
            s.tc["consequence_system"] += "\n\n" + live_block
            s.tc["director_live_beat"] = live_beat
            lanes = s.tc.get("lanes")
            if isinstance(lanes, dict):
                lanes["live_beat"] = len(live_block)
                lanes["consequence"] = len(s.tc["consequence_system"])
    _emit(d, "compile", lanes=s.tc.get("lanes"),
          live_beat=(live_beat.get("kind") if isinstance(live_beat, dict) else ""))
    return s.tc


async def step_scene(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """PER-SCENE StoryMaster firing (the coordinator's cadence): on a scene boundary — the
    opening, or the location changed — the director plans the scene ONCE (goal / pressure /
    exit → world.scene_plan) and the fresh plan is injected into THIS turn's consequence
    context. Mid-scene turns skip entirely (no LLM call, no latency; compile already
    surfaced the standing plan)."""
    from .director import StoryMaster, scene_block
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel():
        return {}
    # The activated contract already chose the scene and its eligibility.
    # A free-form StoryMaster call here could see private authored material and
    # invent a conflicting agenda, so it remains a legacy-only affordance.
    if d.scenario:
        _emit(d, "scene", boundary=False, compiled=True)
        return {}
    cur = s.tc.get("cur") or ""
    if not StoryMaster(d.ctx, d.st, s.world_state).scene_boundary(cur):
        _emit(d, "scene", boundary=False)
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
    # The fresh plan's theme/tone/roles supersede whatever compile saw before
    # this boundary fired — same reason scene_block is re-injected above.
    fresh_beat = _freeplay_live_beat(d, s.world_state)
    if fresh_beat:
        from .live_beat import director_live_beat_block
        fresh_block = director_live_beat_block(fresh_beat)
        if fresh_block and s.tc.get("consequence_system"):
            s.tc["consequence_system"] += "\n\n" + fresh_block
            s.tc["director_live_beat"] = fresh_beat
            lanes = s.tc.get("lanes")
            if isinstance(lanes, dict):
                lanes["live_beat"] = len(fresh_block)
                lanes["consequence"] = len(s.tc["consequence_system"])
    _emit(d, "scene", boundary=True, goal=plan.get("goal", ""))
    return plan


async def step_consequence(ctx: StepContext[PlayState, PlayDeps, None]) -> str:
    """The LOGIC step (reasoning model): work out what the player's action ACTUALLY causes —
    cause→effect, how each present character reacts, what changes, the new cost/choice. The
    prose pass renders this; without it the narrator produces atmosphere, not events."""
    from .narration import generate_guarded
    s, d = ctx.state, ctx.deps
    if s.error or d.cancel() or not s.tc.get("consequence_system"):
        return ""
    hist = s.body.get("history") or []
    last_user = next((m.get("text", "") for m in reversed(hist) if m.get("role") == "user"), "")
    if d.scenario:
        recent = s.tc.get("shared_recent") or "(no shared prior scene yet)"
    else:
        recent = "\n".join((("Player" if m.get("role") == "user" else "Narrator")
                            + f": {m.get('text', '')}") for m in hist[-4:])
    prompt = (f"RECENT:\n{recent}\n\nTHE PLAYER JUST DID / SAID:\n{last_user}\n\n"
              f"Work out what actually happens next, step by step.")
    prov = d.consequence_provider or d.provider
    g = await _thread(generate_guarded, prov, system=s.tc["consequence_system"], prompt=prompt,
                      root=d.ctx.root, emits=None, fallback=d.fallback)
    s.beat = (g.get("text") or "").strip()
    # A pressure beat is not a revelation permit.  Reasoning models are
    # particularly prone to treating a pointed player question as a satisfying
    # answer, then handing that payoff to the prose pass as "what happens".
    # Repair that boundary here before it can become a narrative instruction.
    from .arc_guard import breaks_resistance, fallback_beat, retry_instruction
    _locks = s.tc.get("arc_resistance_locks") or []
    _arc_repaired = False
    if _locks and s.beat and breaks_resistance(s.beat, _locks):
        _repair = await _thread(
            generate_guarded, prov,
            system=s.tc["consequence_system"],
            prompt=prompt + retry_instruction("director beat", s.beat),
            root=d.ctx.root, emits=None, fallback=d.fallback,
        )
        _candidate = (_repair.get("text") or "").strip()
        if _candidate and not breaks_resistance(_candidate, _locks):
            s.beat = _candidate
        else:
            s.beat = fallback_beat(_locks)
        _arc_repaired = True
    # A player assertion is not an ability grant.  Keep this guard after the
    # arc repair so an otherwise safe pressure beat cannot smuggle in a fake
    # spell on its way to the prose writer.
    from .player_guard import (breaks_player_reality, fallback_beat as player_fallback_beat,
                               retry_instruction as player_retry_instruction)
    _player_guard = s.tc.get("player_action_guard") or {}
    _player_repaired = False
    if _player_guard.get("blocked") and s.beat and breaks_player_reality(s.beat, _player_guard):
        _repair = await _thread(
            generate_guarded, prov,
            system=s.tc["consequence_system"],
            prompt=prompt + player_retry_instruction("director beat", s.beat),
            root=d.ctx.root, emits=None, fallback=d.fallback,
        )
        _candidate = (_repair.get("text") or "").strip()
        if _candidate and not breaks_player_reality(_candidate, _player_guard):
            s.beat = _candidate
        else:
            s.beat = player_fallback_beat(_player_guard)
        _player_repaired = True
    s.tc["player_action_repaired"] = _player_repaired
    _emit(d, "consequence", chars=len(s.beat), arc_repaired=_arc_repaired,
          player_repaired=_player_repaired)
    return s.beat


async def step_prose(ctx: StepContext[PlayState, PlayDeps, None]) -> str:
    """The WRITER: free-text narration under the minimal register (no schema). When a
    consequence beat exists, it renders THAT — the beat is the scene's truth (scaffold),
    not a script: flowing narration, change no facts, add no new events."""
    from .narration import generate_guarded
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
    # Keep the writer behind the same hard boundary as the director.  The
    # first repair normally preserves the model's voice; the deterministic
    # fallback exists only so a repeatedly noncompliant model cannot make a
    # hidden recognition canon just by writing it beautifully.
    from .arc_guard import breaks_resistance, fallback_narration, retry_instruction
    _locks = s.tc.get("arc_resistance_locks") or []
    _arc_repaired = False
    if _locks and s.narration and breaks_resistance(s.narration, _locks):
        _repair = await _thread(
            generate_guarded, d.provider,
            system=s.tc["system"],
            prompt=prompt + retry_instruction("narration", s.narration),
            root=d.ctx.root, emits=None, fallback=d.fallback,
        )
        _candidate = (_repair.get("text") or "").strip()
        if _candidate and not breaks_resistance(_candidate, _locks):
            s.narration = _candidate
        else:
            s.narration = fallback_narration(_locks)
        _arc_repaired = True
    from .player_guard import (breaks_player_reality, fallback_narration as player_fallback_narration,
                               retry_instruction as player_retry_instruction)
    _player_guard = s.tc.get("player_action_guard") or {}
    _player_repaired = False
    if _player_guard.get("blocked") and s.narration and breaks_player_reality(s.narration, _player_guard):
        _repair = await _thread(
            generate_guarded, d.provider,
            system=s.tc["system"],
            prompt=prompt + player_retry_instruction("narration", s.narration),
            root=d.ctx.root, emits=None, fallback=d.fallback,
        )
        _candidate = (_repair.get("text") or "").strip()
        if _candidate and not breaks_player_reality(_candidate, _player_guard):
            s.narration = _candidate
        else:
            s.narration = player_fallback_narration(_player_guard)
        _player_repaired = True
    s.prose_guard = {"tripped": g.get("tripped"), "used_fallback": g.get("used_fallback"),
                     "arc_repaired": _arc_repaired, "player_repaired": _player_repaired}
    if not s.narration:
        s.error = g.get("error") or "the narrator returned nothing"
    _emit(d, "prose", chars=len(s.narration))
    return s.narration


async def step_scribe(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """The SCRIBE: read the fresh narration, emit the structured scene report + deltas."""
    from .narration import generate_guarded
    from ...server.services.prompts import PLAY_SCHEMA
    from . import state as _SE
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
    from .narration import segment_prose, lines_prompt
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
    _emit(d, "scribe", ok=bool(s.report))
    return s.report


async def step_apply(ctx: StepContext[PlayState, PlayDeps, None]) -> dict:
    """Deterministic close: resolve names/emotions/location, apply deltas through the
    geography gate, record runtime_state, persist the scene, consolidate on sleep/death,
    and shape the HTTP response. No LLM calls."""
    s, d = ctx.state, ctx.deps
    if s.error:
        return {}
    s.result = await _thread(_apply_turn, d, s)
    _emit(d, "apply", present=s.result.get("present"), pov=s.result.get("pov"))
    return s.result


def _vn_lines(d, s, data: dict) -> list[dict]:
    """The narration re-cut as VN lines (segments are code-verbatim; the scribe's `lines`
    labels attribute them). Pure; [] when there's nothing to split."""
    from .narration import segment_prose, assemble_lines
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
    from ...server.services.story_sessions import save_session
    from ...server.services.emotions import EMOTION_KEYS
    from . import state as _SE

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
            "live_beat": tc.get("director_live_beat") or {},
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
    from .player_guard import sanitize_state_deltas
    _player_action_guard = tc.get("player_action_guard") or {}
    # Apply the state boundary before any write path.  A scribe may still report
    # that someone looked worried, but it cannot turn "I cast a spell" into an
    # inventory item, a fact, a flag, travel, or a future permission.
    data["state_deltas"] = sanitize_state_deltas(data.get("state_deltas"), _player_action_guard)
    if _player_action_guard.get("blocked"):
        data["player_status"] = "active"
        data["movement"] = False
        data["location"] = cur
        data["arc_milestone"] = False
    else:
        for ph in _player_particulars(last_user):
            data.setdefault("state_deltas", []).append(
                {"op": "detail", "name": "", "key": "", "value": f"the player carries {ph}",
                 "title": "", "keywords": []})
    _safe_last_user = _player_action_guard.get("safe_action") or last_user

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
    from .director import StoryMaster, state_card as _sm_card
    loc_name = next((l.name for l in st.locations if l.id == loc), loc)
    _sm = StoryMaster(appctx, st, world_state, provider=d.provider, location=loc_name)
    born_now = _sm.ingest(people=data.get("people") or [], present=roster, narration=s.narration)

    # ARC advancement: the scribe watched this turn's narration against the current stage's
    # milestone; on a hit, the stage advances deterministically (no LLM).
    if data.get("arc_milestone"):
        from .director import advance_arc
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
        from ..world.creation import make_move_validator
        from . import state as _PC
        on_names = {str(n) for n in (data.get("present") or []) if n} | {
            (appctx.base_settings.characters[k].name if k in appctx.base_settings.characters else k)
            for k in ((world_state.get("scene") or {}).get("members") or [])}
        _beats: list = []
        world_state = _SE.apply_deltas(world_state, data.get("state_deltas") or [],
                                       root=appctx.root, scope=d.thread_scope,
                                       validate_move=make_move_validator(st, on_names),
                                       beat_sink=_beats)
        world_state["location"] = loc or world_state.get("location") or ""
        # Index this turn as a STEP + record who witnessed it (runtime_state: join = no backlog).
        _turn_step = _PC.record_step(world_state, [k for k in present_keys if k])
        # The permanent chronicle: every beat appended this turn lands in the
        # session_beats table — the in-doc log is a capped window and would otherwise
        # silently forget the oldest entries.
        if _beats and getattr(d, "sid", ""):
            from ...server.services.story_sessions import append_beats
            append_beats(appctx.root, d.sid, _beats, step=_turn_step, story_key=d.key or "")
        world_state.setdefault("transcript", []).append(data.get("reply", ""))
        _PC.record_raw_turn(world_state, step=_turn_step, present=[k for k in present_keys if k],
                            location=loc, text=data.get("reply", ""), player_input=_safe_last_user)
        # The MANUSCRIPT: the same prose, grouped by scene for the reading/editing pane.
        from .director import record_page
        _beat_line = next((str(dd.get("value", "")) for dd in (data.get("state_deltas") or [])
                           if dd.get("op") == "log"), "")
        record_page(world_state, loc=loc_name, text=data.get("reply", ""),
                    beat=_beat_line, step=int(world_state.get("step") or 0))
        # Carry the SCENE forward: space + co-located members + the sticky POV.
        pov_out = (data.get("pov") or "").strip()
        pov_key = name_to_key.get(pov_out.lower(), pov_out) or prior_pov
        world_state["scene"] = {"space": loc, "members": [k for k in present_keys if k], "pov": pov_key}
        # Periodic consolidation: self-gates to fire ~every 50 turns, deepening character cards + the
        # running story summary from the recent transcript. Additive; never raises.
        if key:
            from ..authoring import stages as _ST2
            _ST2.consolidate_cast(appctx, key, world_state, sid=d.sid)
        if d.scenario:
            from .compiled import persist_runtime, record_observation
            record_observation(d.runtime_state, text=data.get("reply", ""),
                               player_input=_safe_last_user, present=[k for k in present_keys if k])
            saved_state = persist_runtime(d.sess.get("state"), world_state, d.runtime_state)
        else:
            saved_state = _SE.with_world(d.sess.get("state"), world_state)
        # The scene-keyed loader's cache travels with the save file (State doc `ctx` level).
        _scn = (s.tc or {}).get("scene_cache")
        if isinstance(_scn, dict):
            saved_state = _SE.set_level(saved_state, "ctx", _scn)
        save_session(appctx.root, d.sid, {**d.sess, "state": saved_state}, story_key=d.key or "")
    except Exception:  # noqa: BLE001 — a state-write failure must not drop the turn
        pass

    # Lifecycle: ONLY sleep/death consolidates memory (dream/rest pass; death also rewinds).
    consolidation = None
    loop_reset = None
    status = (data.get("player_status") or "active").strip().lower()
    if status in ("sleeping", "dead") and key:
        from ..authoring import stages as _ST
        if status == "dead" and d.scenario:
            # A loop is an executable state transition, not a narrator suggestion.
            # Restore the baseline and delete loop-local fact retrieval so a fresh
            # loop cannot receive last loop's knowledge through a side channel.
            from .compiled import persist_runtime, reset_loop
            reset = reset_loop(d.scenario, world_state, d.runtime_state)
            if reset is not None:
                world_state, loop_reset = reset
                try:
                    from ...server.services import lorebook_store as _LS
                    _LS.delete_book(appctx.root, d.thread_scope)
                except Exception:  # noqa: BLE001 — a stale fact book must not break reset
                    pass
                save_session(appctx.root, d.sid, {
                    **d.sess,
                    # Loop reset also clears the scene-context cache: a fresh loop cannot
                    # inherit last loop's loaded scene through this side channel either.
                    "state": _SE.set_level(
                        persist_runtime(d.sess.get("state"), world_state, d.runtime_state),
                        "ctx", {}),
                })
            else:
                consolidation = _ST.consolidate_on_rest(appctx, key, world_state, status)
                save_session(appctx.root, d.sid, {**d.sess, "state": _SE.with_world(d.sess.get("state"), world_state)})
        else:
            consolidation = _ST.consolidate_on_rest(appctx, key, world_state, status)
        if status == "sleeping":                       # sleep is the only door out of night:
            from .director import next_day          # the day turns over to the next morning
            next_day(world_state)
        if status == "sleeping":
            if d.scenario:
                from .compiled import persist_runtime
                # Keep the private scenario clock in lockstep with the public day
                # progression before the next deterministic offer is requested.
                # Sleeping closes the prior slot's authored scene; otherwise an
                # old night could silently carry into the next morning.
                _ss = d.runtime_state.setdefault("scenario_state", {})
                _ss["time"] = world_state["day"]["slot"]
                _ss["active_scene"] = None
                saved_state = persist_runtime(d.sess.get("state"), world_state, d.runtime_state)
            else:
                saved_state = _SE.with_world(d.sess.get("state"), world_state)
            # A new day opens fresh scenes — the scene-context cache turns over with it.
            saved_state = _SE.set_level(saved_state, "ctx", {})
            save_session(appctx.root, d.sid, {**d.sess, "state": saved_state}, story_key=d.key or "")

    return {
        "reply": data.get("reply", ""), "location": world_state.get("location") or loc,
        # VN/webnovel LINES: the same narration as speaker-attributed lines (dialogue /
        # narration / the player's inner-voice thoughts). Flat `reply` stays the source of truth.
        "lines": _vn_lines(d, s, data),
        "beat": s.beat,                 # the consequence reasoning (what-happens), for the UI
        "live_beat": tc.get("director_live_beat") or {},  # public deterministic scene anchor
        "scene_plan": world_state.get("scene_plan") or {},   # the per-scene director's agenda
        "card": _sm_card(world_state, st),   # the STATE CARD: derived view, free every turn
        "born": born_now,               # characters created on the fly this turn (gradual)
        "present": ((world_state.get("scene") or {}).get("members") if loop_reset
                    else [k for k in present_keys if k]),
        "pov": (world_state.get("scene") or {}).get("pov", ""),
        "step": world_state.get("step"),
        "emotions": emotions,
        "movement": bool(data.get("movement")),
        "player_status": status,
        "day": world_state.get("day") or None,   # the slot rhythm (None before the day model)
        "consolidation": consolidation,
        "loop_reset": loop_reset,
        "reset_history": bool(loop_reset),
        "state": _SE.summary(world_state),
        "lanes": tc.get("lanes"),
        "guard": {"tripped": s.prose_guard.get("tripped") or s.scribe_guard.get("tripped"),
                  "used_fallback": bool(s.prose_guard.get("used_fallback")
                                        or s.scribe_guard.get("used_fallback")),
                  "player_claim_blocked": bool(_player_action_guard.get("blocked")),
                  "player_repaired": bool(s.prose_guard.get("player_repaired")
                                           or tc.get("player_action_repaired"))},
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
