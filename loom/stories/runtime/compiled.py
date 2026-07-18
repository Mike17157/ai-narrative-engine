"""Executable state for an activated authored scenario.

The interview card stays readable authoring material.  ``scenario_compiler`` turns it
into a validated contract and this module is the deliberately small deterministic
runtime for that contract.  It owns the parts a narrator must never improvise:

* which authored scene can open at the current time and place;
* the opening roster and clock;
* a presence-indexed observation ledger; and
* a loop reset that restores a real baseline instead of asking a model to rewind.

Private director data lives in the State document's ``runtime`` level.  The mutable
``world`` level only contains the public play state given to the normal narrator.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from . import scenario as _scenario
from . import state as _state


DAY_SLOTS = ("morning", "evening", "night")


class ScenarioTransitionError(ValueError):
    """A player request attempted a scene the activated contract does not allow."""


def fingerprint(contract: dict[str, Any]) -> str:
    """Stable identity for the exact contract that seeded a playthrough."""
    payload = json.dumps(contract or {}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _opening(contract: dict[str, Any]) -> dict[str, Any]:
    return _as_dict((contract or {}).get("opening"))


def _opening_state(contract: dict[str, Any]) -> dict[str, Any]:
    opening = _opening(contract)
    raw = _as_dict(opening.get("state"))
    slot = raw.get("time") or opening.get("slot") or "morning"
    slot = slot if slot in DAY_SLOTS else "morning"
    location = raw.get("location") or opening.get("location") or ""
    present = raw.get("present") or opening.get("present") or []
    if not isinstance(present, list):
        present = []
    state = _scenario.empty_scenario_state()
    state.update({
        "time": slot,
        "location": str(location),
        "present": [str(k) for k in present if k],
        "flags": deepcopy(raw.get("flags") if isinstance(raw.get("flags"), dict) else {}),
        "active_scene": opening.get("scene_id") or raw.get("active_scene") or None,
    })
    return _scenario.normalize(state)


def _scene_location(scene: dict[str, Any]) -> str:
    """Compiler-normalized scenes use ``location``; accept legacy ``locations`` too."""
    if scene.get("location"):
        return str(scene["location"])
    locations = scene.get("locations") or []
    return str(locations[0]) if isinstance(locations, list) and locations else ""


def scene_catalog(contract: dict[str, Any]) -> list[dict[str, Any]]:
    scenes = (contract or {}).get("scene_catalog") or []
    return [deepcopy(scene) for scene in scenes if isinstance(scene, dict) and scene.get("id")]


def eligible_scenes(contract: dict[str, Any], scenario_state: dict[str, Any], *,
                    allow_travel: bool = False) -> list[dict[str, Any]]:
    """Return authored scenes whose explicit time/place/flag gates are true.

    Day offers deliberately set ``allow_travel``: selecting an authored offer is
    an explicit player transition to its declared location, while an ordinary
    ongoing turn remains locked to the current place.
    """
    state = _scenario.normalize(scenario_state)
    out: list[dict[str, Any]] = []
    for scene in scene_catalog(contract):
        slots = scene.get("slots") or []
        locations = scene.get("locations") or ([scene["location"]] if scene.get("location") else [])
        required = _as_dict(scene.get("requires"))
        flags = _as_dict(required.get("flags"))
        presence = _as_dict(scene.get("presence"))
        here = {str(person) for person in state["present"] if person}
        if slots and state["time"] not in slots:
            continue
        if locations and state["location"] not in locations and not allow_travel:
            continue
        if any(state["flags"].get(name) != value for name, value in flags.items()):
            continue
        required_all = {str(person) for person in (presence.get("all") or []) if person}
        required_any = {str(person) for person in (presence.get("any") or []) if person}
        required_absent = {str(person) for person in (presence.get("absent") or []) if person}
        if required_all and not required_all <= here:
            continue
        if required_any and not (required_any & here):
            continue
        if required_absent & here:
            continue
        out.append(scene)
    return out


def _scene_by_id(contract: dict[str, Any], scene_id: str) -> dict[str, Any] | None:
    return next((scene for scene in scene_catalog(contract) if scene["id"] == scene_id), None)


_WORD_RE = re.compile(r"[a-z0-9]+")


def _scene_alias_tokens(scene: dict[str, Any]) -> set[str]:
    """Words a player would naturally type to name this scene's place or title."""
    tokens: set[str] = set()
    for value in (scene.get("id"), _scene_location(scene), scene.get("title") or scene.get("name")):
        for token in _WORD_RE.findall(str(value or "").lower()):
            if len(token) >= 4:
                tokens.add(token)
    return tokens


def _alias_score(tokens: set[str], text: str, words: list[str]) -> int:
    """Distinct alias tokens found in the player's text.

    A token scores when it appears at a word boundary (``club`` matches
    "clubroom" and "garden" matches "gardens" via the free right edge) or when
    a typed word is a close prefix of it (``roof`` → "rooftop").  The loose
    right edge plus the ≤3-letter prefix window keeps "home" from ever
    matching "homeroom".
    """
    score = 0
    for token in tokens:
        if re.search(r"\b" + re.escape(token), text):
            score += 1
        elif any(token.startswith(word) and len(token) - len(word) <= 3 for word in words):
            score += 1
    return score


def _free_text_scene_follow(contract: dict[str, Any], runtime: dict[str, Any],
                            body: dict[str, Any]) -> str | None:
    """Let ordinary free-text movement open an authored scene.

    Compiled play used to transition only through explicit scene offers, so a
    player typing "I walk up to the rooftop garden" stayed frozen in the
    opening scene (and its roster) forever.  Here the latest player message is
    matched against scene id/location/title tokens.  A transition happens only
    on a strictly unique best match, the clock may move forward (never
    backward) to reach the scene's declared slot, and the scene must pass the
    same eligibility gates an explicit offer would.  Any ambiguity leaves the
    current scene, roster, and clock untouched.
    """
    history = body.get("history") or []
    text = next((str(message.get("text") or "") for message in reversed(history)
                 if isinstance(message, dict) and message.get("role") == "user"), "")
    if not text.strip():
        text = str(body.get("text") or "")
    text = text.lower()
    if not text.strip():
        return None
    words = [word for word in _WORD_RE.findall(text) if len(word) >= 4]

    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    active = str(scenario_state.get("active_scene") or "")
    # The current scene scores too: "I think about the ferry and the harbor"
    # said while standing on the ferry is a tie, and a tie must stay put.
    best: dict[str, Any] | None = None
    best_score = 0
    tied = False
    for scene in scene_catalog(contract):
        score = _alias_score(_scene_alias_tokens(scene), text, words)
        if score > best_score:
            best, best_score, tied = scene, score, False
        elif score == best_score and score > 0:
            tied = True
    if best is None or tied or best["id"] == active:
        return None

    # The clock may advance to meet the scene's slot, but never rewinds: a
    # scene whose day has already passed simply stays out of reach.
    slots = [slot for slot in (best.get("slots") or []) if slot in DAY_SLOTS]
    tentative = deepcopy(scenario_state)
    if slots and tentative["time"] not in slots:
        later = sorted((slot for slot in slots
                        if DAY_SLOTS.index(slot) > DAY_SLOTS.index(tentative["time"])),
                       key=DAY_SLOTS.index)
        if not later:
            return None
        tentative["time"] = later[0]
    eligible = {scene["id"] for scene in eligible_scenes(contract, tentative,
                                                         allow_travel=True)}
    if best["id"] not in eligible:
        return None
    runtime["scenario_state"] = tentative
    try:
        _open_scene(contract, runtime, best["id"], allow_travel=True)
    except ScenarioTransitionError:
        runtime["scenario_state"] = scenario_state
        return None
    return best["id"]


def arm_director_events(contract: dict[str, Any], runtime: dict[str, Any]) -> list[str]:
    """Arm private authored events whose time/scene/entity gates now hold.

    Arming is deliberately not narration and not an LLM decision.  A future
    trigger evaluator can resolve the event once a structured player condition
    fires; until then the only public material is the selected scene's authored
    visible situation.  The private state stores ids and status only, never
    copies hidden action text into the public world level.
    """
    plan = _as_dict((contract or {}).get("director_plan"))
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    periods = {str(item.get("id")): item for item in (plan.get("entity_periods") or [])
               if isinstance(item, dict) and item.get("id")}
    status = runtime.setdefault("event_status", {})
    armed: list[str] = []
    for event in plan.get("events") or []:
        if not isinstance(event, dict) or not event.get("id"):
            continue
        ident = str(event["id"])
        if event.get("scene_id") and event.get("scene_id") != scenario_state.get("active_scene"):
            continue
        slots = event.get("slots") or []
        if slots and scenario_state["time"] not in slots:
            continue
        if event.get("location") and event.get("location") != scenario_state["location"]:
            continue
        period_ids = [str(pid) for pid in (event.get("entity_period_ids") or []) if pid]
        if period_ids and not any(scenario_state["time"] in (periods.get(pid, {}).get("slots") or [])
                                  for pid in period_ids):
            continue
        status[ident] = {"state": "armed", "scene_id": scenario_state.get("active_scene"),
                         "time": scenario_state["time"]}
        armed.append(ident)
    return armed


def _open_scene(contract: dict[str, Any], runtime: dict[str, Any], scene_id: str, *,
                allow_travel: bool = False) -> dict[str, Any]:
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    scene = next((item for item in eligible_scenes(contract, scenario_state, allow_travel=allow_travel)
                  if item.get("id") == scene_id), None)
    if scene is None:
        raise ScenarioTransitionError(f"scene '{scene_id}' is not eligible at this time")
    scenario_state["active_scene"] = scene["id"]
    scenario_state["location"] = _scene_location(scene) or scenario_state["location"]
    participants = scene.get("participants") or scene.get("present") or []
    if isinstance(participants, list):
        scenario_state["present"] = [str(k) for k in participants if k]
    runtime["scenario_state"] = scenario_state
    # A freshly opened scene has not been narrated yet: the next prose pass gets
    # the establishing-beat length budget instead of the mid-scene one.
    runtime["scene_just_opened"] = True
    return scene


def _baseline_world(contract: dict[str, Any], scenario_state: dict[str, Any]) -> dict[str, Any]:
    world = _state.empty_state()
    world["location"] = scenario_state["location"]
    world["day"] = {"n": 1, "slot": scenario_state["time"]}
    world["clock"] = f"day 1, {scenario_state['time']}"
    world["scene"] = {"space": scenario_state["location"],
                      "members": list(scenario_state["present"]), "pov": ""}
    # This marker is intentionally public and contains no hidden plan.  It makes it
    # clear in state viewers that a controlled scenario, not free-form fallback, is live.
    world["scenario"] = {"active_scene": scenario_state.get("active_scene"),
                         "time": scenario_state["time"]}
    return _state.normalize(world)


def _fresh_runtime(contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario_state = _opening_state(contract)
    # An opening scene's location and participants are canonical.  If the compiler
    # did not already project them into opening.state, reconcile them once here.
    opening_id = scenario_state.get("active_scene")
    scene = _scene_by_id(contract, str(opening_id)) if opening_id else None
    if scene:
        scenario_state["location"] = _scene_location(scene) or scenario_state["location"]
        people = scene.get("participants") or scene.get("present") or scenario_state["present"]
        scenario_state["present"] = [str(k) for k in people if k] if isinstance(people, list) else []
    baseline = _baseline_world(contract, scenario_state)
    runtime = {
        "version": int((contract or {}).get("version") or 1),
        "fingerprint": fingerprint(contract),
        "scenario_state": deepcopy(scenario_state),
        "baseline_scenario_state": deepcopy(scenario_state),
        "baseline_world": deepcopy(baseline),
        "loop": {"iteration": 1, "returner_memory": []},
        "event_status": {},
        # The opening scene has never been narrated: turn one establishes it.
        "scene_just_opened": True,
    }
    return baseline, runtime


def ensure_runtime(state_doc: dict | None, contract: dict[str, Any]) -> tuple[dict, dict, dict, bool]:
    """Install/read the private runtime level and return ``(doc, world, runtime, changed)``.

    A contract change starts a fresh session baseline intentionally; the activate endpoint
    is the explicit author boundary where that is expected.  Old sessions remain valid as
    long as their contract fingerprint still matches.
    """
    doc = _state.document_normalize(state_doc)
    current = _state.get_level(doc, "runtime", {})
    fresh = not isinstance(current, dict) or current.get("fingerprint") != fingerprint(contract)
    if fresh:
        world, runtime = _fresh_runtime(contract)
        _state.set_level(doc, "runtime", runtime)
        doc = _state.with_world(doc, world)
        return doc, world, runtime, True
    runtime = current
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    runtime["scenario_state"] = scenario_state
    world = _state.world_of(doc)
    return doc, world, runtime, False


def persist_runtime(state_doc: dict | None, world: dict, runtime: dict) -> dict:
    """Persist both State levels together so a reset cannot leave a split brain."""
    doc = _state.document_normalize(state_doc)
    _state.set_level(doc, "runtime", runtime)
    return _state.with_world(doc, world)


def prepare_turn(contract: dict[str, Any], world: dict, runtime: dict, body: dict) -> dict[str, Any]:
    """Synchronize the current scene/time before any model receives a prompt.

    Scene offers from the deterministic day endpoint carry an ``id``.  A forged seed
    cannot turn into an arbitrary model-selected scene: it has to be one of the
    currently eligible authored candidates.
    """
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    day = world.get("day") if isinstance(world.get("day"), dict) else {}
    slot = day.get("slot") if day.get("slot") in DAY_SLOTS else scenario_state["time"]
    scenario_state["time"] = slot
    if isinstance(body.get("scene_seed"), dict):
        seed = body["scene_seed"]
        selected = str(seed.get("id") or seed.get("scene_id") or "")
        if not selected:
            raise ScenarioTransitionError("choose one of the authored scene offers")
        _open_scene(contract, runtime, selected, allow_travel=True)
        scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    elif body.get("choice"):
        # The regular location navigator remains usable, but only when it maps
        # to a single eligible authored scene.  A location id alone is not a
        # license for a model to invent what happens there.
        choice = str(body.get("choice") or "")
        candidates = [scene for scene in eligible_scenes(contract, scenario_state, allow_travel=True)
                      if scene.get("id") == choice or _scene_location(scene) == choice]
        if len(candidates) != 1:
            raise ScenarioTransitionError("that move is not an available authored scene right now")
        _open_scene(contract, runtime, candidates[0]["id"], allow_travel=True)
        scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    elif scenario_state.get("active_scene"):
        # Free-text play: the player's own words can move them between authored
        # scenes.  The matcher is deliberately conservative — it fires only on
        # a strictly unique location/title match and otherwise leaves the
        # standing scene, roster, and clock exactly as they were.
        _free_text_scene_follow(contract, runtime, body)
        scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    elif not scenario_state.get("active_scene"):
        opening_id = str(_opening(contract).get("scene_id") or "")
        if opening_id:
            _open_scene(contract, runtime, opening_id)
            scenario_state = _scenario.normalize(runtime.get("scenario_state"))

    scene = _scene_by_id(contract, str(scenario_state.get("active_scene") or "")) or {}
    world["location"] = scenario_state["location"]
    world["day"] = {"n": int((day or {}).get("n") or 1), "slot": scenario_state["time"]}
    world["clock"] = f"day {world['day']['n']}, {scenario_state['time']}"
    world["scenario"] = {"active_scene": scenario_state.get("active_scene"),
                         "time": scenario_state["time"]}
    world["scene"] = {"space": scenario_state["location"],
                      "members": list(scenario_state["present"]),
                      "pov": (world.get("scene") or {}).get("pov", "")}
    runtime["scenario_state"] = scenario_state
    arm_director_events(contract, runtime)
    return scene


def scene_options(contract: dict[str, Any], world: dict, runtime: dict) -> list[dict[str, Any]]:
    """Map deterministic candidates into the existing Player offer shape."""
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    day = world.get("day") if isinstance(world.get("day"), dict) else {}
    if day.get("slot") in DAY_SLOTS:
        scenario_state["time"] = day["slot"]
    runtime["scenario_state"] = scenario_state
    out = []
    for scene in eligible_scenes(contract, scenario_state, allow_travel=True):
        out.append({
            "id": scene["id"],
            "title": scene.get("title") or scene.get("name") or scene["id"],
            "location": _scene_location(scene),
            "who": list(scene.get("participants") or scene.get("present") or []),
            "hook": scene.get("hook") or scene.get("visible") or scene.get("summary") or "",
        })
    return out


def record_observation(runtime: dict, *, text: str, player_input: str, present: list[str]) -> None:
    """Append a witnessed turn to the per-character ledger without inventing facts."""
    scenario_state = _scenario.normalize(runtime.get("scenario_state"))
    # The compiled roster is authoritative.  The UI-facing list contains only
    # sprite-backed cast keys and therefore omits a virtual ``player`` key; do
    # not accidentally erase that player witness (or another authored member)
    # just because the scribe did not render a sprite for them.
    scenario_state["present"] = list(dict.fromkeys(
        [str(k) for k in scenario_state["present"] if k]
        + [str(k) for k in present if k]
    ))
    _scenario.record_turn(scenario_state, text, speaker="narrator", addressed="")
    # A concise player action is a separate record: later per-character actor calls can
    # distinguish what was said from how it was narrated without replaying global history.
    if (player_input or "").strip():
        _scenario.record_turn(scenario_state, player_input, speaker="player", addressed="")
    runtime["scenario_state"] = scenario_state
    # The active scene has now been narrated at least once; later turns in the
    # same scene stay on the tighter mid-scene budget until a scene opens again.
    runtime["scene_just_opened"] = False


def player_memory(runtime: dict) -> list[str]:
    loop = _as_dict(runtime.get("loop"))
    memories = loop.get("returner_memory") or []
    return [str(item) for item in memories if str(item).strip()][-8:]


def reset_loop(contract: dict[str, Any], world: dict, runtime: dict) -> tuple[dict, dict] | None:
    """Restore the immutable baseline after death, preserving only Returner memory.

    The ``loop_policy`` is compiled from explicit author decisions.  No policy means
    ordinary death/rest behavior; prose like "there is a loop" is never enough to
    silently reset a playthrough.
    """
    policy = _as_dict((contract or {}).get("loop_policy"))
    if (not policy.get("enabled") or not policy.get("executable", True)
            or str(policy.get("trigger") or "").strip().lower() not in {"death", "dead"}):
        return None
    loop = _as_dict(runtime.get("loop"))
    iteration = int(loop.get("iteration") or 1)
    preserve_for = _as_dict(policy.get("reset")).get("memory", {}).get("preserve_for", [])
    preserve_for = [str(k) for k in preserve_for] if isinstance(preserve_for, list) else []
    memory = [str(item) for item in (loop.get("returner_memory") or []) if str(item).strip()]
    last_log = [str(item) for item in (world.get("log") or []) if str(item).strip()][-3:]
    if preserve_for and last_log:
        memory.append(f"Loop {iteration} ended: " + " | ".join(last_log))
    memory = memory[-8:] if preserve_for else []
    restored = _state.normalize(deepcopy(runtime.get("baseline_world") or {}))
    previous_state = _scenario.normalize(runtime.get("scenario_state"))
    reset_state = _scenario.normalize(deepcopy(runtime.get("baseline_scenario_state") or {}))
    preserve_runtime = _as_dict(policy.get("reset")).get("preserve_runtime", [])
    if isinstance(preserve_runtime, list):
        for key in preserve_runtime:
            if key in previous_state:
                reset_state[key] = deepcopy(previous_state[key])
    runtime["scenario_state"] = reset_state
    runtime["event_status"] = {}
    # The restored opening has not been narrated in this iteration yet.
    runtime["scene_just_opened"] = True
    runtime["loop"] = {"iteration": iteration + 1, "returner_memory": memory}
    restored["scenario"] = {"active_scene": runtime["scenario_state"].get("active_scene"),
                            "time": runtime["scenario_state"].get("time", "morning")}
    return restored, {"iteration": iteration + 1, "memory_count": len(memory),
                      "opening": _opening(contract)}
