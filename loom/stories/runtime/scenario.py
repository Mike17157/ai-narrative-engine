"""Deterministic scene selection and presence-gated memory.

This is the new runtime foundation: authored scene possibilities + mutable state.
It never asks a language model to decide who knows a fact or whether a scene is
available. Models only narrate the scene brief produced here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def empty_scenario_state() -> dict[str, Any]:
    return {"time": "morning", "location": "", "present": [], "public_facts": [],
            "flags": {}, "active_scene": None, "turns": [], "scene_memories": [],
            "character_memories": {}}


def normalize(state: dict | None) -> dict[str, Any]:
    if state is None:
        state = empty_scenario_state()
    out = state
    for key, value in empty_scenario_state().items():
        out.setdefault(key, deepcopy(value))
    for key in ("present", "public_facts", "turns", "scene_memories"):
        if not isinstance(out[key], list): out[key] = []
    for key in ("flags", "character_memories"):
        if not isinstance(out[key], dict): out[key] = {}
    return out


def scene_catalog(story: dict | Any) -> list[dict]:
    """Read the authored scene possibilities from the Story card's flexible field."""
    fields = story.get("fields", {}) if isinstance(story, dict) else getattr(story, "fields", {})
    return [s for s in (fields.get("scene_catalog") or []) if isinstance(s, dict) and s.get("id")]


def eligible_scenes(story: dict | Any, state: dict) -> list[dict]:
    """Return authored scenes whose deterministic gates are currently true."""
    state = normalize(state)
    eligible = []
    for scene in scene_catalog(story):
        slots = scene.get("slots") or []
        locations = scene.get("locations") or []
        required = scene.get("requires") or {}
        flags = required.get("flags") or {}
        if slots and state["time"] not in slots: continue
        if locations and state["location"] not in locations: continue
        if any(state["flags"].get(k) != v for k, v in flags.items()): continue
        eligible.append(deepcopy(scene))
    return eligible


def start_scene(story: dict | Any, state: dict, scene_id: str) -> dict:
    state = normalize(state)
    scene = next((s for s in eligible_scenes(story, state) if s["id"] == scene_id), None)
    if scene is None: raise ValueError(f"scene '{scene_id}' is not eligible")
    state["active_scene"] = scene_id
    state["location"] = scene.get("location") or state["location"]
    state["present"] = list(scene.get("participants") or state["present"])
    return state


def record_turn(state: dict, text: str, *, speaker: str = "player", addressed: str = "") -> dict:
    state = normalize(state)
    state["turns"].append({"text": text, "speaker": speaker, "addressed": addressed,
                           "present": list(state["present"]), "scene": state["active_scene"]})
    return state


def close_scene(state: dict, *, summary: str, public_facts: list[str] | None = None,
                private: dict[str, list[str]] | None = None) -> dict:
    """Consolidate one scene once, then project it only to witnesses."""
    state = normalize(state); public_facts, private = public_facts or [], private or {}
    record = {"scene": state["active_scene"], "summary": summary, "present": list(state["present"]),
              "public": list(public_facts), "private": deepcopy(private)}
    state["scene_memories"].append(record)
    state["public_facts"].extend(f for f in public_facts if f not in state["public_facts"])
    for character in state["present"]:
        memory = {"scene": state["active_scene"], "summary": summary,
                  "public": list(public_facts), "private": list(private.get(character) or [])}
        state["character_memories"].setdefault(character, []).append(memory)
    state["active_scene"] = None
    return state


def character_context(state: dict, character: str, *, recent_turns: int = 5) -> dict:
    """The exact information a present character may receive in generation."""
    state = normalize(state)
    heard = [t for t in state["turns"] if character in t.get("present", [])][-recent_turns:]
    return {"public_facts": list(state["public_facts"]), "recent_turns": heard,
            "memories": list(state["character_memories"].get(character) or [])}


def scene_brief(story: dict | Any, state: dict) -> dict:
    state = normalize(state)
    scene = next((s for s in scene_catalog(story) if s["id"] == state["active_scene"]), {})
    return {"scene": deepcopy(scene), "time": state["time"], "location": state["location"],
            "present": list(state["present"]), "public_facts": list(state["public_facts"])}
