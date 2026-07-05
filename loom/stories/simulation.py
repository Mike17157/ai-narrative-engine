"""Emergent-narrative story engine: info-isolated character agents.

Instead of planning a story top-down, we DESIGN a cast (Sonnet, one pass) — each with
goals + a private secret — and then SIMULATE their interaction. Each character is
roleplayed by its own agent (GLM-5.1) that knows ONLY its own dossier and the public
transcript of scenes it was present for; it never sees another character's goals,
secret, or inner reasoning. A director (DeepSeek — the planner) sets up each scene,
runs a burst of turns, then closes the scene and progresses each character's features.

Roles (resolved by the endpoint, passed in as providers):
  • design_prov   — Sonnet: character design (one structured pass)
  • director_prov — DeepSeek: scene setup + scene close / feature progression
  • actor_prov    — GLM-5.1: in-character roleplay turns (free text)

State (plain dicts, persisted client-side / in a session):
  character = {name, persona, appearance, goals[], secret, knowledge[], state}
  scene     = {place, situation, present[], objective, transcript[{speaker,text}], summary}
  sim_state = {premise, characters[], scenes[]}
"""
from __future__ import annotations

from typing import Any, Callable


# ── Schemas (structured output for design / director) ───────────────────────────

DESIGN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["characters"],
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "persona", "appearance", "goals", "secret"],
                "properties": {
                    "name": {"type": "string"},
                    "persona": {"type": "string"},
                    "appearance": {"type": "string"},
                    "goals": {"type": "array", "items": {"type": "string"}},
                    "secret": {"type": "string"},
                },
            },
        },
    },
}

SCENE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["place", "situation", "present", "objective"],
    "properties": {
        "place": {"type": "string"},
        "situation": {"type": "string"},
        "present": {"type": "array", "items": {"type": "string"}},
        "objective": {"type": "string"},
    },
}

CLOSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "updates"],
    "properties": {
        "summary": {"type": "string"},
        "updates": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "learned", "emotion", "goal_progress"],
                "properties": {
                    "name": {"type": "string"},
                    "learned": {"type": "string"},
                    "emotion": {"type": "string"},
                    "goal_progress": {"type": "string"},
                },
            },
        },
    },
}


# ── State-doc bridge (the simulation OWNS the `sim` level) ───────────────────────
# In the unified model the per-thread record is a State doc (loom/stories/state_doc.py)
# with named levels; the simulation's cast + scenes are its `sim` level. The endpoint
# still accepts/returns `sim_state` (client contract unchanged) but, given a `sid`, also
# reads/persists it here so the State doc is the one home for thread state.

SIM_LEVEL = "sim"


def sim_of(state: dict | None) -> dict:
    """Read the simulation state from a State doc's `sim` level (normalized shape)."""
    from .state_doc import get_level
    s = get_level(state or {}, SIM_LEVEL) or {}
    return {"premise": s.get("premise", ""),
            "characters": s.get("characters") or [],
            "scenes": s.get("scenes") or []}


def with_sim(state: dict | None, sim_state: dict) -> dict:
    """Return the State doc with its `sim` level set to `sim_state`."""
    from .state_doc import set_level
    return set_level(state or {}, SIM_LEVEL, sim_state or {})


def _data(res) -> dict:
    return getattr(res, "data", None) or {}


def _text(res) -> str:
    return (getattr(res, "text", "") or "").strip()


# ── 1) Cast design — Sonnet, one pass ───────────────────────────────────────────

DESIGN_SYS = (
    "You are a character designer. In ONE pass, design vivid, distinct characters who will "
    "be ROLEPLAYED by separate AI agents in an emergent story simulation — so make them play "
    "well off each other. For each: a `name`; a `persona` written as a SECOND-PERSON briefing "
    "the actor will embody (voice, temperament, history, how they speak and move); an "
    "`appearance` for an image; 2-3 concrete `goals` that will drive them into relationship or "
    "collision with the others; and a `secret` — a private agenda or knowledge the others do "
    "NOT know. Engineer the goals and secrets so they intersect and generate drama. JSON only."
)


def design_cast(provider, premise: str, n: int = 3) -> list[dict]:
    if provider is None:
        return []
    res = provider.generate_text(
        system=DESIGN_SYS,
        prompt=f"PREMISE:\n{premise}\n\nDesign exactly {n} characters whose goals intersect.",
        emits=DESIGN_SCHEMA,
    )
    out = []
    for c in _data(res).get("characters", [])[:n]:
        out.append({
            "name": c.get("name", "").strip() or f"Character {len(out)+1}",
            "persona": c.get("persona", ""),
            "appearance": c.get("appearance", ""),
            "goals": [g for g in (c.get("goals") or []) if g],
            "secret": c.get("secret", ""),
            "knowledge": [],
            "state": "",
        })
    return out


# ── 2) Scene setup — Director (DeepSeek), omniscient ────────────────────────────

DIRECTOR_SETUP_SYS = (
    "You are the DIRECTOR of an emergent story simulation. You know every character's goals "
    "and secrets (the actors do not know each other's). Set up the NEXT scene to maximize "
    "dramatic potential: choose a `place`, a `situation` that pressures the characters' goals "
    "into collision or intimacy, the subset `present` (2 or more character names), and the "
    "scene's dramatic `objective`. Build on the story so far; escalate. JSON only."
)


def setup_scene(director_prov, premise: str, characters: list[dict],
                prior_summaries: list[str], steer: str = "") -> dict:
    names = [c["name"] for c in characters]
    dossier = "\n".join(
        f"- {c['name']}: goals={c.get('goals')}; secret={c.get('secret','')}; "
        f"current state={c.get('state','') or 'baseline'}"
        for c in characters
    )
    parts = [f"PREMISE:\n{premise}", f"CAST (you alone know all of this):\n{dossier}"]
    if prior_summaries:
        parts.append("STORY SO FAR:\n" + "\n".join(f"- {s}" for s in prior_summaries))
    if steer:
        parts.append(f"THE WRITER WANTS THIS NEXT: {steer}")
    parts.append("Set up the next scene.")
    res = director_prov.generate_text(system=DIRECTOR_SETUP_SYS, prompt="\n\n".join(parts),
                                      emits=SCENE_SCHEMA)
    d = _data(res)
    present = [p for p in (d.get("present") or []) if p in names]
    if len(present) < 2:
        present = names[:2] if len(names) >= 2 else names
    return {
        "place": d.get("place", ""), "situation": d.get("situation", ""),
        "present": present, "objective": d.get("objective", ""),
        "transcript": [], "summary": "",
    }


# ── 3) Actor turn — GLM-5.1, INFORMATION-ISOLATED ───────────────────────────────

def _actor_system(character: dict) -> str:
    learned = character.get("knowledge") or []
    return (
        f"You ARE {character['name']}. Stay strictly in character. Never break character, "
        f"narrate other people's private thoughts, or reveal information {character['name']} "
        f"could not plausibly know.\n\n"
        f"WHO YOU ARE:\n{character.get('persona','')}\n\n"
        f"YOUR GOALS: {', '.join(character.get('goals') or []) or '(unstated)'}\n"
        f"YOUR SECRET (no one else knows this): {character.get('secret','') or '(none)'}\n"
        + (f"WHAT YOU'VE LEARNED SO FAR: {'; '.join(learned)}\n" if learned else "")
        + f"YOUR CURRENT STATE: {character.get('state','') or 'composed'}\n\n"
        "Respond ONLY as this character's next beat in the scene: what they say and do, "
        "concise (1-3 sentences), pursuing your goals. Mix dialogue and action. Do not write "
        "for anyone else."
    )


def actor_turn(actor_prov, character: dict, scene: dict,
               on_delta: Callable[[str], None] | None = None) -> str:
    transcript = "\n".join(f"{t['speaker']}: {t['text']}" for t in scene["transcript"]) \
        or "(the scene opens)"
    prompt = (
        f"SCENE: {scene['place']} — {scene['situation']}\n"
        f"Present: {', '.join(scene['present'])}\n\n"
        f"WHAT HAS HAPPENED (only what you witnessed):\n{transcript}\n\n"
        f"What does {character['name']} say and do next?"
    )
    res = actor_prov.generate_text(system=_actor_system(character), prompt=prompt,
                                   on_delta=on_delta)
    return _text(res)


# ── 4) Scene close + feature progression — Director ─────────────────────────────

DIRECTOR_CLOSE_SYS = (
    "You are the DIRECTOR. The scene has played out. Give a `summary` of what dramatically "
    "happened (2-3 sentences). Then for EACH present character, report `learned` (new "
    "information they now plausibly know after this scene — '' if none), `emotion` (their "
    "emotional state leaving the scene), and `goal_progress` (how their goals advanced or "
    "were thwarted). JSON only."
)


def close_scene(director_prov, scene: dict) -> dict:
    transcript = "\n".join(f"{t['speaker']}: {t['text']}" for t in scene["transcript"])
    prompt = (
        f"SCENE: {scene['place']} — {scene['situation']}\n"
        f"Objective: {scene['objective']}\nPresent: {', '.join(scene['present'])}\n\n"
        f"TRANSCRIPT:\n{transcript}\n\nClose the scene and progress the characters."
    )
    return _data(director_prov.generate_text(system=DIRECTOR_CLOSE_SYS, prompt=prompt,
                                             emits=CLOSE_SCHEMA))


# ── Sim-state ops (the per-character mutations applied at scene close) ────────────
# The director's CLOSE returns structured `updates`; applying them is the only mutation
# of sim state. Registered as named ops (same pattern as world ops / graph scripts) so
# every model-driven state change in the project lives in a registry, not inline. The
# orchestration in run_scene_burst stays as code — it's fixed control flow, not a
# model-callable function vocabulary.

SIM_OPS: dict[str, Callable] = {}


def sim_op(name: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        SIM_OPS[name] = fn
        return fn
    return deco


@sim_op("learn")
def _sim_learn(character: dict, value: str) -> None:
    if value:
        character.setdefault("knowledge", []).append(value)


@sim_op("set_emotion")
def _sim_set_emotion(character: dict, value: str) -> None:
    if value:
        character["state"] = value


# ── 5) Orchestration — one autonomous scene burst ───────────────────────────────

def run_scene_burst(*, director_prov, actor_prov, sim_state: dict, max_turns: int = 8,
                    steer: str = "", on_event: Callable[[dict], None] | None = None) -> dict:
    """Run ONE scene to completion (autonomous burst), streaming events, and return the
    updated sim_state. The caller persists it and may run another burst, or stop."""
    emit = on_event or (lambda e: None)
    characters = sim_state.get("characters") or []
    by_name = {c["name"]: c for c in characters}
    prior = [s.get("summary", "") for s in sim_state.get("scenes", []) if s.get("summary")]

    scene = setup_scene(director_prov, sim_state.get("premise", ""), characters, prior, steer)
    emit({"type": "scene", "place": scene["place"], "situation": scene["situation"],
          "present": scene["present"], "objective": scene["objective"]})

    present = [by_name[n] for n in scene["present"] if n in by_name] or characters[:2]
    if not present:
        emit({"type": "error", "error": "no characters present for the scene"})
        return sim_state

    # Round-robin turns among the present cast (skip immediate self-repeat).
    for i in range(max_turns):
        speaker = present[i % len(present)]
        text = actor_turn(actor_prov, speaker, scene)
        if not text:
            continue
        scene["transcript"].append({"speaker": speaker["name"], "text": text})
        emit({"type": "turn", "speaker": speaker["name"], "text": text})

    close = close_scene(director_prov, scene)
    scene["summary"] = close.get("summary", "")
    for u in close.get("updates", []):
        c = by_name.get(u.get("name"))
        if not c:
            continue
        SIM_OPS["learn"](c, u.get("learned"))
        SIM_OPS["set_emotion"](c, u.get("emotion"))
    sim_state.setdefault("scenes", []).append(scene)
    emit({"type": "scene_close", "summary": scene["summary"], "updates": close.get("updates", [])})
    return sim_state
