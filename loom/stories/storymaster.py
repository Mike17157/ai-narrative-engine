"""The StoryMaster — the coordinator that owns the world model and the network of functions that
react to play.

The narrator writes prose; the scribe reports what's in it; the StoryMaster turns that report into
EVENTS, runs the handlers those events trigger (handlers may emit further events — the network),
mutates the ONE world model, and (later) spawns slow work — fleshing characters, rendering sprites
and scenes — as background jobs. It also owns the decision of what the narrator sees next turn
(the context compile). One owner instead of world-mutation scattered across the turn.

Slice 1 handlers (here): the CHARACTER NETWORK, organized by location —
  • `person`    a named person appeared in the narration → upsert a thin record AT a location
                (mention makes them real; they persist, pinned to a place — static for now).
  • `encounter` a person is physically ON STAGE and not yet fleshed → flesh them into a full card
                via the transposition engine (similarity-seeded), one at a time (gradual).
  • `fleshed`   (emitted by encounter) → the seam where a sprite-render job will hang later.

Registering a new capability is just `@StoryMaster.on("event")` — images and plot beats become
handlers on this same bus, not more inline code.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any, Callable


class StoryMaster:
    # event type -> [handler(self, data)]. Class-level so handlers register at import.
    HANDLERS: dict[str, list[Callable]] = defaultdict(list)

    @classmethod
    def on(cls, etype: str):
        def deco(fn: Callable) -> Callable:
            cls.HANDLERS[etype].append(fn)
            return fn
        return deco

    def __init__(self, ctx, st, world: dict, *, provider=None, location: str = "", jobs=None):
        self.ctx = ctx                    # AppContext (character lookups, root, providers)
        self.st = st                      # the Story
        self.world = world                # THE world model (mutated in place)
        self.provider = provider          # writer, for fleshing
        self.location = location          # the current scene location (name) — for placing people
        self.jobs = jobs                  # optional: spawn a background job (name, fn) — images later
        self._q: deque = deque()
        self.world.setdefault("people", {})   # {name: {at, note, born, card?}}

    # ── event bus ────────────────────────────────────────────────────────────────
    def emit(self, etype: str, **data) -> None:
        self._q.append((etype, data))

    def _drain(self) -> None:
        steps = 0
        while self._q and steps < 200:    # cascade guard (handlers emit handlers)
            etype, data = self._q.popleft()
            for h in StoryMaster.HANDLERS.get(etype, []):
                try:
                    h(self, data)
                except Exception:  # noqa: BLE001 — a handler must never drop the turn
                    pass
            steps += 1

    # ── the world model — helpers handlers use ─────────────────────────────────────
    def _cast_names(self) -> set[str]:
        out = set()
        for m in self.st.cast:
            c = self.ctx.base_settings.characters.get(m.character)
            out.add(((c.name if c else m.character) or m.character).lower())
        return out

    def known(self) -> set[str]:
        return self._cast_names() | {n.lower() for n in (self.world.get("people") or {})}

    # ── scene authority — the StoryMaster decides who is present; the narrator OBEYS ────────────
    def _names_at(self, location: str) -> list[str]:
        """Everyone the world model places AT `location` — recorded people plus cast entities
        whose tracked location is here. These are who CAN be brought on at this place."""
        loc = (location or "").strip().lower()
        out = [n for n, r in (self.world.get("people") or {}).items()
               if (r.get("at") or "").strip().lower() == loc]
        for nm, e in (self.world.get("entities") or {}).items():
            if (e.get("location") or "").strip().lower() == loc:
                out.append(nm)
        return out

    def plan_scene(self, *, present: list[str], action: str) -> list[str]:
        """THE authoritative roster for this turn. Sticky co-location carries over; the player's
        action may bring in AT MOST ONE person who is actually recorded at this location (so the
        narrator can't summon the unreachable, and can't drop who's here). The narrator is handed
        this verbatim and may not deviate."""
        roster = [n for n in (present or []) if n]
        low = {n.lower() for n in roster}
        act = (action or "").lower()
        for nm in self._names_at(self.location):
            if nm.lower() in low:
                continue
            toks = [t for t in nm.lower().split() if len(t) > 2]
            if toks and any(t in act for t in toks):
                roster.append(nm)                 # one controlled entrance: sought AND reachable
                break
        return roster

    # ── scene lifecycle — the StoryMaster fires PER SCENE, not per turn ───────────
    # A scene = one location until the story moves. Cheap reflexes (roster, ingest, derived
    # plot) run every turn; the EXPENSIVE coordination — an actual director reasoning pass —
    # fires only at a boundary, where its cost amortizes over the whole scene.

    def scene_boundary(self, loc_id: str) -> bool:
        """True when a new scene starts: nothing planned yet, or the location changed.
        # ponytail: boundary = location change; add time-skip/cast-turnover detection when
        # long single-location scenes need re-direction."""
        plan = self.world.get("scene_plan") or {}
        return (plan.get("space") or "") != (loc_id or "")

    def open_scene(self, *, loc_id: str, recent: str = "") -> dict:
        """Fire the per-scene event through the bus and return the fresh plan."""
        self.emit("scene", loc_id=loc_id, recent=recent)
        self._drain()
        return self.world.get("scene_plan") or {}

    # ── the turn hook ──────────────────────────────────────────────────────────────
    def ingest(self, *, people: list[dict], present: list[str], narration: str) -> list[str]:
        """Process one turn's scribe report into events, then run the network. `people` = every
        named person the scribe saw (name, at, note); `present` = who is physically on stage.
        Returns the names FLESHED this turn (for the response)."""
        self._fleshed_now: list[str] = []
        self._narration = narration
        for p in (people or []):
            nm = (p.get("name") or "").strip()
            if nm:
                self.emit("person", name=nm, at=(p.get("at") or "").strip(), note=(p.get("note") or "").strip())
        known = self.known()
        for nm in (present or []):
            if nm and nm.lower() not in known:
                self.emit("encounter", name=nm)
        self.emit("beat", step=int(self.world.get("step") or 0))
        self._drain()
        return self._fleshed_now


# ── Slice-1 handlers: the character network, organized by location ──────────────────

@StoryMaster.on("person")
def _h_person(sm: StoryMaster, d: dict) -> None:
    """Mention → a thin record, pinned to a location (static). Persists; never overwrites a
    fleshed card. Absent people keep their place; someone seen in-scene lands at the current loc."""
    nm = d["name"]
    if nm.lower() in sm._cast_names():
        return                                          # already a first-class cast member
    rec = sm.world["people"].get(nm) or {}
    rec["note"] = d.get("note") or rec.get("note", "")
    at = d.get("at") or rec.get("at") or (sm.location if nm.lower() in
                                          (sm._narration or "").lower() else "")
    rec["at"] = at
    rec.setdefault("born", False)
    sm.world["people"][nm] = rec


@StoryMaster.on("encounter")
def _h_encounter(sm: StoryMaster, d: dict) -> None:
    """On stage and not fleshed → flesh into a full card (similarity-seeded transposition), one
    per turn. Emits `fleshed` (the future sprite-render seam)."""
    if sm._fleshed_now:                                 # gradual: one new face fully realized per turn
        return
    nm = d["name"]
    rec = sm.world["people"].get(nm) or {}
    if rec.get("born"):
        return
    if sm.provider is None:
        sm.world["people"].setdefault(nm, rec).update({"at": sm.location, "born": False})
        return
    from .worldgen import birth_character
    story_ctx = f"{sm.st.premise or sm.st.name}. Just now in the story: {(sm._narration or '')[:600]}"
    card = birth_character(sm.provider, name=nm, role=(rec.get("note") or "someone met in this scene"),
                           story=story_ctx, prominence="supporting", root=sm.ctx.root)
    if card.get("card"):
        rec.update({"at": sm.location, "note": rec.get("note", ""), "born": True, "card": card["card"]})
        sm.world["people"][nm] = rec
        sm._fleshed_now.append(nm)
        sm.emit("fleshed", name=nm)


@StoryMaster.on("fleshed")
def _h_fleshed(sm: StoryMaster, d: dict) -> None:
    """Seam for the sprite-render job (Slice 2, needs ComfyUI). For now just note it in the log so
    the world records that a face was born."""
    sm.world.setdefault("log", []).append(f"(a new person entered the story: {d['name']})")


# ── The per-SCENE director: plan the scene ONCE at its boundary ─────────────────────
# One real reasoning call per scene (too slow per turn, free amortized over a scene): what
# is this scene FOR, what tension does it hold, what naturally ends it. Every turn of the
# scene then steers by the plan at zero added latency.

_SCENE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["goal", "pressure", "exit"],
    "properties": {
        "goal": {"type": "string", "description": "what this scene is FOR — the one concrete "
                 "thing it should accomplish for the story"},
        "pressure": {"type": "string", "description": "the tension or complication kept alive "
                     "under the surface of the scene"},
        "exit": {"type": "string", "description": "what would naturally end the scene"},
    },
}


@StoryMaster.on("scene")
def _h_scene(sm: StoryMaster, d: dict) -> None:
    """Scene boundary → close the old scene into the log, direct the new one. The plan is
    written BEFORE the LLM call so a failed call still marks the boundary (no re-fire loop);
    an empty plan degrades gracefully — the derived plot direction still steers."""
    w = sm.world
    old = w.get("scene_plan") or {}
    if old.get("goal"):
        w.setdefault("log", []).append(f"(scene closes at {old.get('loc') or '?'}: {old['goal']})")
    plan = {"space": d.get("loc_id") or "", "loc": sm.location,
            "opened": int(w.get("step") or 0), "goal": "", "pressure": "", "exit": ""}
    w["scene_plan"] = plan
    if sm.provider is None:
        return
    from .guards import generate_guarded
    cast_lines = []
    for m in sm.st.cast:
        c = sm.ctx.base_settings.characters.get(m.character)
        nm = (c.name if c else m.character) or m.character
        desc = ((c.system or "").splitlines()[0] if c else "")[:100]
        cast_lines.append(f"- {nm}" + (f": {desc}" if desc else ""))
    cast_block = ("CAST (the people who EXIST in this story — plan the scene with THEM; do NOT "
                  "invent new named characters or beings):\n" + "\n".join(cast_lines)) \
        if cast_lines else ""
    bits = [b for b in (plot_direction(w, sm.st), cast_block,
                        people_by_location(w, sm.location)) if b]
    system = ("You direct ONE scene of an interactive novel. Decide what the scene is FOR: the "
              "one concrete thing it should accomplish for the story, the tension to keep alive "
              "under its surface, and what would naturally end it. Concrete and causal, never "
              "atmospheric. One line each."
              + ("\n\n" + "\n\n".join(bits) if bits else ""))
    prompt = (f"A new scene opens at: {sm.location or plan['space']}.\n"
              + (f"JUST BEFORE IT:\n{d['recent']}\n" if d.get("recent") else "")
              + "\nPlan the scene.")
    g = generate_guarded(sm.provider, system=system, prompt=prompt, root=sm.ctx.root,
                         emits=_SCENE_SCHEMA)
    data = g.get("data") or {}
    for k in ("goal", "pressure", "exit"):
        plan[k] = (data.get(k) or "").strip()


def scene_block(plan: dict) -> str:
    """The director's per-scene agenda as a consequence-context block ('' if unplanned)."""
    if not (isinstance(plan, dict) and plan.get("goal")):
        return ""
    out = ["THIS SCENE (planned when it opened — steer toward it, never announce it):",
           f"- what the scene is for: {plan['goal']}"]
    if plan.get("pressure"):
        out.append(f"- keep alive: {plan['pressure']}")
    if plan.get("exit"):
        out.append(f"- it ends when: {plan['exit']}")
    return "\n".join(out)


# ── Plot progression: the arc north-star the per-turn consequence step steers toward ───────────
# Cheap/derived for now (no LLM call → no added latency): phase from how far in we are, the thread
# to press from the oldest open promise. Upgrade path: a periodic LLM "director" call that
# re-reasons the pressure — same handler, richer body. This is the other half of the StoryMaster
# (the DM function) that previously only ran on sleep (dreams/consolidation).

_PHASES = [(4, "setup"), (12, "rising action"), (22, "the turn"), (10 ** 9, "toward resolution")]


@StoryMaster.on("beat")
def _h_plot(sm: StoryMaster, d: dict) -> None:
    w = sm.world
    step = int(d.get("step") or w.get("step") or 0)
    threads = [p.get("setup", "") for p in (w.get("promises") or [])
               if p.get("status") == "open" and p.get("setup")]
    phase = next(name for lim, name in _PHASES if step < lim)
    w["plot"] = {"question": (getattr(sm.st, "premise", "") or sm.st.name),
                 "phase": phase, "focus": threads[0] if threads else "",
                 "threads": threads[:3], "beat": step}


# ── The ARC: a PLANNED staged progression the story lives through ──────────────────
# One LLM call plans it from a one-line request (the request IS the template — "a village
# romance: he draws up his courage…"); the per-scene director + per-turn consequence step
# then steer toward the CURRENT stage via plot_direction. Advancement is free: the scribe
# (already reading every turn) reports the stage's concrete milestone; code advances.

_ARC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "question", "conditions", "stages"],
    "properties": {
        "name": {"type": "string"},
        "question": {"type": "string", "description": "the arc's dramatic question in YOUR OWN "
                     "words — one line, never a copy of the request"},
        "conditions": {"type": "array", "items": {"type": "string"},
                       "description": "setting-stage ids this arc runs under (from the list given); "
                       "these activate while the arc is live. Empty if none."},
        "stages": {"type": "array", "minItems": 3, "maxItems": 7, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "purpose", "milestone", "events"],
            "properties": {
                "title": {"type": "string"},
                "purpose": {"type": "string", "description": "what this stage does to the "
                            "characters — one line, earned and ordinary-human"},
                "milestone": {"type": "string", "description": "the ONE concrete, observable "
                              "moment that completes this stage (an action, not a feeling)"},
                "events": {"type": "array", "items": {"type": "string"},
                           "description": "2-4 small planned events / touching moments to "
                           "weave into scenes — particular, quiet, no melodrama"}}}}},
}


def generate_arc(provider, ctx, st, world: dict, request: str) -> dict:
    """Plan an ARC from a one-line request and install it on the world model (stage 0).
    Returns the arc ({} on failure). The request is the reusable template."""
    from .guards import generate_guarded
    cast = []
    for m in st.cast:
        c = ctx.base_settings.characters.get(m.character)
        nm = (c.name if c else m.character) or m.character
        cast.append(f"- {nm}: {((c.system or '').splitlines()[0] if c else '')[:120]}")
    stage_ids = [c.id for c in (getattr(st, "conditions", None) or []) if c.id]
    stage_menu = "\n".join(f"- {c.id}: {c.name} — {(c.effect or c.description or '')[:100]}"
                           for c in (getattr(st, "conditions", None) or []) if c.id)
    system = (
        "You plan ONE story arc for an interactive novel — a staged emotional progression the "
        "story will live through, scene by scene. Stages are earned and ordinary-human, never "
        "melodrama. Each stage has: a purpose (what it does to the characters), ONE concrete "
        "observable milestone that completes it (an action someone takes, not a feeling), and "
        "a few small planned events — particular, quiet moments a scene can weave in naturally. "
        "Use ONLY the people who exist. Fit the story's world and its pressures.\n"
        "SETTING: if the story has SETTING STAGES, pick the one(s) whose world this arc lives in "
        "and put their ids in `conditions` — the arc runs under those conditions, changing how the "
        "cast behaves. Use only ids from the list, or leave empty if the arc is under none.")
    prompt = (f"STORY: {st.premise}\nTONE: {st.tone}\nCAST:\n" + "\n".join(cast)
              + (f"\n\nSETTING STAGES (assign the arc's `conditions` from these ids):\n{stage_menu}"
                 if stage_menu else "")
              + f"\n\nARC REQUEST: {request}\n\nPlan the arc.")
    g = generate_guarded(provider, system=system, prompt=prompt, root=ctx.root,
                         emits=_ARC_SCHEMA)
    data = g.get("data") or {}
    if not data.get("stages"):
        return {}
    data["stage"] = 0
    world["arc"] = data
    return data


def design_arc(provider, ctx, st, messages: list[dict], draft: dict | None) -> dict:
    """COLLABORATIVE arc design — the user and the model shape the arc together over turns.
    Each call takes the conversation + the current draft and returns {reply, arc}: the reply
    speaks to the writer, the arc is the FULL updated draft (the model maintains it every
    turn, applying what was agreed). Nothing installs until the user keeps it — the draft
    rides the story's pending store so the work queue tracks it. See generate_arc (one-shot)."""
    from .guards import generate_guarded
    cast = []
    for m in st.cast:
        c = ctx.base_settings.characters.get(m.character)
        nm = (c.name if c else m.character) or m.character
        cast.append(f"- {nm}: {((c.system or '').splitlines()[0] if c else '')[:120]}")
    stage_menu = "\n".join(f"- {c.id}: {c.name} — {(c.effect or c.description or '')[:100]}"
                           for c in (getattr(st, "conditions", None) or []) if c.id)
    schema = {"type": "object", "additionalProperties": False, "required": ["reply", "arc"],
              "properties": {
                  "reply": {"type": "string", "description": "your spoken turn to the writer — "
                            "conversational, one idea or question at a time, never a lecture"},
                  "arc": _ARC_SCHEMA}}
    system = (
        "You are co-designing ONE story arc WITH a writer — a staged emotional progression the "
        "story will live through. You propose, they steer; build on what they say, never restart "
        "unless asked. Stages are earned and ordinary-human, never melodrama: each has a purpose "
        "(what it does to the characters), ONE concrete observable milestone that completes it "
        "(an action someone takes, not a feeling), and a few small planned events. Use ONLY the "
        "people who exist.\n"
        "EVERY turn return BOTH: `reply` (talk to the writer — react, then offer the next choice "
        "or question) and `arc` (the FULL current draft with everything agreed so far applied — "
        "it is the living document, keep unchanged parts intact).\n"
        + ("SETTING STAGES: pick the stage(s) whose world this arc lives in and keep their ids in "
           "the draft's `conditions` (only ids from the list; empty if none).\n" if stage_menu else ""))
    convo = "\n".join(f"{'Writer' if m.get('role') == 'user' else 'You'}: {m.get('text', '')}"
                      for m in (messages or []) if m.get("text"))
    prompt = (f"STORY: {st.premise}\nTONE: {st.tone}\nCAST:\n" + "\n".join(cast)
              + (f"\n\nSETTING STAGES:\n{stage_menu}" if stage_menu else "")
              + ("\n\nCURRENT DRAFT (update this, don't restart):\n"
                 + json.dumps(draft, ensure_ascii=False) if draft else "")
              + f"\n\nCONVERSATION:\n{convo}\n\nRespond and return the updated draft.")
    g = generate_guarded(provider, system=system, prompt=prompt, root=ctx.root, emits=schema)
    data = g.get("data") or {}
    return {"reply": (data.get("reply") or "").strip(), "arc": data.get("arc") or None,
            "error": g.get("error")}


# ── The DAY: a fixed three-slot rhythm (morning → evening → night) the playthrough moves
# through. A slot holds ONE scene; the user advances slots deliberately, and NIGHT ends only
# by sleeping (which fires consolidation/dreams and turns the day over). Scenes are offered,
# not imposed: suggest_slot_scenes proposes options grounded in the arc + whereabouts.

DAY_SLOTS = ("morning", "evening", "night")


def day_of(world: dict) -> dict:
    """The current day marker {n, slot}, seeding day 1 morning on first touch."""
    d = world.get("day")
    if not (isinstance(d, dict) and d.get("slot") in DAY_SLOTS):
        d = {"n": 1, "slot": "morning"}
        world["day"] = d
    return d


def advance_slot(world: dict) -> dict:
    """Move to the next slot within the day (morning→evening→night). Night does NOT roll
    over here — sleeping is the only door out of night (see next_day). Returns {n, slot}."""
    d = day_of(world)
    i = DAY_SLOTS.index(d["slot"])
    if i + 1 < len(DAY_SLOTS):
        d["slot"] = DAY_SLOTS[i + 1]
    return d


def next_day(world: dict) -> dict:
    """Sleep turned the day over: next day, morning slot. Returns {n, slot}."""
    d = day_of(world)
    world["day"] = {"n": int(d.get("n") or 1) + 1, "slot": "morning"}
    world.setdefault("log", []).append(f"(day {world['day']['n']} begins)")
    return world["day"]


def suggest_slot_scenes(provider, ctx, st, world: dict) -> dict:
    """Offer 3 scene options for the CURRENT slot — suggestion-based, the user picks (or
    ignores them and free-plays). Grounded in the arc's current stage, who is where, the
    active setting stages, and the time of day. Returns {day, slot, options} — each option
    {title, location, who, hook} with `location` a real location id."""
    from .guards import generate_guarded
    d = day_of(world)
    loc_ids = [l.id for l in st.locations] or ["nowhere"]
    locs = "\n".join(f"- {l.id} | {l.name}: {(l.description or '')[:80]}" for l in st.locations)
    cast = ", ".join((ctx.base_settings.characters.get(m.character).name
                      if ctx.base_settings.characters.get(m.character) else m.character)
                     for m in st.cast)
    conds = {k[5:] for k, v in (world.get("flags") or {}).items()
             if isinstance(k, str) and k.startswith("cond:") and v}
    stage_fx = "; ".join((c.effect or c.name) for c in (getattr(st, "conditions", None) or [])
                         if c.id in conds)
    schema = {"type": "object", "additionalProperties": False, "required": ["options"],
              "properties": {"options": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                  "type": "object", "additionalProperties": False,
                  "required": ["title", "location", "who", "hook"],
                  "properties": {
                      "title": {"type": "string", "description": "the scene, plainly named"},
                      "location": {"type": "string", "enum": loc_ids},
                      "who": {"type": "array", "items": {"type": "string"},
                              "description": "cast names present (besides the player), 0-3"},
                      "hook": {"type": "string", "description": "1-2 sentences: the concrete "
                               "situation the player walks into — an opening, never an outcome"}}}}}}
    system = (
        "You offer the player THREE scene options for one time-slot of the day — small, concrete, "
        "playable situations (a person to find, a task underway, a moment about to happen). "
        "Variety over drama: one option should serve the arc's current stage, one should be "
        "relationship/daily-life, one a wildcard. Hooks are openings the player walks into — "
        "never outcomes, never spoilers. Fit the time of day and the world's current stage(s).")
    prompt = (f"STORY: {st.premise}\nTONE: {st.tone}\nCAST: {cast}\n"
              f"TIME: day {d['n']}, {d['slot']}\n"
              + (f"WORLD STAGE NOW: {stage_fx}\n" if stage_fx else "")
              + f"LOCATIONS:\n{locs}\n"
              + (f"\n{plot_direction(world, st)}\n" if plot_direction(world, st) else "")
              + (f"\n{people_by_location(world, '')}\n" if people_by_location(world, '') else "")
              + f"\nOffer three {d['slot']} scenes.")
    g = generate_guarded(provider, system=system, prompt=prompt, root=ctx.root, emits=schema)
    return {"day": d["n"], "slot": d["slot"],
            "options": (g.get("data") or {}).get("options") or [], "error": g.get("error")}


def arc_milestone(world: dict) -> str:
    """The current stage's completion condition ('' when no active arc) — handed to the
    scribe so every turn's narration is checked against it for free."""
    arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
    stages = arc.get("stages") or []
    i = int(arc.get("stage") or 0)
    return stages[i]["milestone"] if i < len(stages) else ""


def sync_arc_conditions(world: dict) -> set:
    """Setting stages are ARC-SCOPED and RATCHETED — crossing into an arc ACTIVATES its stages, and
    activation is PERMANENT: once a stage lights up, its `cond:<id>` flag stays on for the rest of
    the playthrough. So a character's facets accumulate — arc 1 unlocks one side of them, arc 2 adds
    another, and neither reverts. (A stage's content still only SURFACES when the beat is relevant —
    salience-gated in play — so latched-but-irrelevant stages stay quiet; they're eligible, not
    forced.) Deterministic, no LLM; run each turn before context assembly. Returns the FULL set of
    stages activated so far (the ratchet)."""
    arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
    active = set(arc.get("conditions") or [])
    stages = arc.get("stages") or []
    i = int(arc.get("stage") or 0)
    if 0 <= i < len(stages) and isinstance(stages[i], dict):
        active |= set(stages[i].get("conditions") or [])   # a stage inside an arc may add one
    flags = world.setdefault("flags", {})
    for cid in active:
        flags[f"cond:{cid}"] = True                          # LATCH on — never dropped
    # The ratchet: report EVERY stage ever activated, not just this arc's.
    return {k[5:] for k, v in flags.items() if isinstance(k, str) and k.startswith("cond:") and v}


def advance_arc(world: dict) -> str:
    """The scribe observed the milestone → advance one stage (deterministic, no LLM).
    Returns the new stage title ('complete' at the end, '' if no active arc)."""
    arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
    stages = arc.get("stages") or []
    i = int(arc.get("stage") or 0)
    if not stages or i >= len(stages):
        return ""
    arc["stage"] = i + 1
    nxt = stages[i + 1]["title"] if i + 1 < len(stages) else "complete"
    world.setdefault("log", []).append(f"(arc: '{stages[i]['title']}' completes → {nxt})")
    return nxt


def plot_direction(world: dict, st) -> str:
    """The story's north-star for the consequence step and the per-scene director. An ACTIVE
    ARC (the planned progression) takes precedence; else the derived plot. Advance TOWARD it,
    never force it."""
    arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
    stages = arc.get("stages") or []
    i = int(arc.get("stage") or 0)
    if stages and i < len(stages):
        sg = stages[i]
        out = ["STORY ARC (the planned progression. Build TOWARD the current stage's milestone "
               "— but the milestone MOMENT belongs to the PLAYER: set it up, invite it, make "
               "space for it; NEVER perform it for them or run ahead of them. A stage should "
               "take several scenes — weave in at most ONE planned moment per scene, never "
               "announce the plan):",
               f"- the arc: {arc.get('name', '')}: {arc.get('question', '')}",
               f"- current stage ({i + 1}/{len(stages)}): {sg['title']} — {sg['purpose']}",
               f"- this stage completes when: {sg['milestone']}"]
        if sg.get("events"):
            out.append("- planned moments to weave in: " + "; ".join(sg["events"]))
        if i + 1 < len(stages):
            out.append(f"- after that: {stages[i + 1]['title']}")
        return "\n".join(out)
    plot = world.get("plot") if isinstance(world.get("plot"), dict) else {}
    q = plot.get("question") or getattr(st, "premise", "") or ""
    if not q and not plot.get("focus"):
        return ""
    out = ["STORY (the arc — advance the story TOWARD this; escalate, complicate, or pay off a "
           "thread when the moment allows, but never force it):"]
    if q:
        out.append(f"- what the story is about: {q}")
    out.append(f"- phase: {plot.get('phase') or 'setup'}")
    if plot.get("focus"):
        out.append(f"- press toward: {plot['focus']}")
    if plot.get("threads"):
        out.append("- open threads: " + "; ".join(plot["threads"]))
    return "\n".join(out)


def record_page(world: dict, *, loc: str, text: str, beat: str, step: int) -> None:
    """The MANUSCRIPT — the story's prose grouped by SCENE, for the reading/editing pane.
    A new scene entry opens when the location changes; each turn appends one page (its
    narration + the scribe's one-line beat). `ti` ties the page to its transcript entry so
    a manual edit updates both. Pure bookkeeping, no LLM."""
    if not (text or "").strip():
        return
    ms = world.setdefault("manuscript", [])
    if not ms or (ms[-1].get("loc") or "") != (loc or ""):
        ms.append({"loc": loc or "?", "opened": step, "pages": []})
    ms[-1]["pages"].append({"step": step, "text": text, "beat": (beat or "").strip(),
                            "ti": len(world.get("transcript") or []) - 1})


def state_card(world: dict, st) -> str:
    """THE STATE CARD — the story's current state as ONE readable card: the scene and what
    it's for, the arc, who's where, what's established and promised, the hard world state.
    A pure VIEW over the delta-maintained world model, so updating it is FREE: the scribe's
    deltas + the bus handlers keep the model current each turn; no LLM ever rewrites a card."""
    from . import state_engine as _SE
    parts = [f"STATE CARD — {getattr(st, 'name', '') or '?'} · step {int(world.get('step') or 0)}"]
    plan = world.get("scene_plan") if isinstance(world.get("scene_plan"), dict) else {}
    where = plan.get("loc") or world.get("location") or ""
    if where:
        parts.append(f"SCENE — at {where}" + (f" (opened step {plan.get('opened')})"
                                              if plan.get("goal") else ""))
    for block in (scene_block(plan), plot_direction(world, st),
                  people_by_location(world, where)):
        if block:
            parts.append(block)
    dets = [d.get("text", "") for d in (world.get("details") or []) if d.get("text")][-5:]
    if dets:
        parts.append("ESTABLISHED (recent):\n" + "\n".join(f"- {t}" for t in dets))
    proms = [p.get("setup", "") for p in (world.get("promises") or [])
             if p.get("status") == "open" and p.get("setup")][:5]
    if proms:
        parts.append("OPEN PROMISES:\n" + "\n".join(f"- {t}" for t in proms))
    hard = _SE.render_state(world)
    if hard:
        parts.append(hard)
    return "\n\n".join(parts)


def people_by_location(world: dict, current: str) -> str:
    """The narrator's PEOPLE feed, organized by where they are: who's at the current place up
    front, everyone else indexed under their location. Bounds a growing cast to the current scene
    plus a location index. '' if nobody's been recorded yet."""
    people = world.get("people") or {}
    if not people:
        return ""
    here, elsewhere = [], defaultdict(list)
    cur = (current or "").strip().lower()
    for nm, r in people.items():
        at = (r.get("at") or "").strip()
        line = nm + (f" — {r['note']}" if r.get("note") else "")
        if at and at.lower() == cur:
            here.append(line)
        else:
            elsewhere[at or "whereabouts unknown"].append(nm)
    out = []
    if here:
        out.append("HERE with you:\n" + "\n".join(f"- {l}" for l in here))
    if elsewhere:
        out.append("ELSEWHERE (go to them to bring them on):\n"
                   + "\n".join(f"- at {loc}: {', '.join(ns)}" for loc, ns in elsewhere.items()))
    return "PEOPLE (met in this story):\n" + "\n".join(out) if out else ""
