"""Small, deterministic public scene pressure for compiled play.

An activated scenario deliberately does not call the legacy ``StoryMaster`` scene
planner.  That protects the authored director plan, but it should not leave the
consequence pass without a concrete scene to play.  This module projects the
current *public* scene into one bounded live beat.  It is intentionally not a
planner: it makes no model call, changes no state, and cannot read private
event, character, or runtime fields.

The returned mapping is safe to return to a client and safe to put in the
Director-only consequence prompt.  It must never be used as narrator context;
the narrator continues to see only its existing public scene brief.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from ..visibility import strip_model_hidden


_INVESTIGATION = re.compile(
    r"\b(?:ask|follow|inspect|investigate|listen|look|notice|read|search|trace|"
    r"watch|why|where|who)\b",
    re.IGNORECASE,
)
_PUBLIC_CORE_FIELDS = ("role", "persona", "personality", "connection", "public_core", "surface")


def _text(value: Any, *, limit: int = 320) -> str:
    """Normalize one explicitly public string, fail-closed for hidden wrappers."""
    if not isinstance(value, str):
        return ""
    visible = strip_model_hidden(value) or ""
    return re.sub(r"\s+", " ", visible).strip()[:limit]


def _strings(value: Any, *, limit: int = 80) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item, limit=limit)
        if text and text not in out:
            out.append(text)
    return out


def _role(value: Any) -> str:
    """Accept a compact role string or the public ``{role: ...}`` form."""
    if isinstance(value, Mapping):
        return _text(value.get("role") or value.get("label"), limit=160)
    return _text(value, limit=160)


def public_character_core(value: Any) -> dict[str, str]:
    """Copy only a character's explicitly safe surface fields.

    Callers may hold a full character card with secrets, wounds, private
    knowledge, and system text.  This projection deliberately ignores all of
    it.  A ``character_cores`` input is expected to already be author-public,
    but retaining this whitelist makes the helper safe when called directly.
    """
    if isinstance(value, str):
        text = _text(value, limit=240)
        return {"public_core": text} if text else {}
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key in _PUBLIC_CORE_FIELDS:
        text = _text(value.get(key), limit=240)
        if text:
            out[key] = text
    return out


def _public_scene(scene: Any) -> dict[str, Any]:
    """Return exactly the runtime fields allowed to shape a live beat."""
    source = scene if isinstance(scene, Mapping) else {}
    roles_raw = source.get("roles")
    if not isinstance(roles_raw, Mapping):
        # ``participant_roles`` is accepted for early/legacy callers.  The
        # authoring contract writes ``roles``.
        roles_raw = source.get("participant_roles")
    roles = {
        str(key): _role(value)
        for key, value in (roles_raw.items() if isinstance(roles_raw, Mapping) else [])
        if _text(str(key), limit=80) and _role(value)
    }
    return {
        "id": _text(source.get("id"), limit=160),
        "title": _text(source.get("title") or source.get("name"), limit=240),
        "location": _text(source.get("location"), limit=160),
        "theme": _text(source.get("theme"), limit=240),
        "tone": _text(source.get("tone"), limit=180),
        "visible": _text(source.get("visible") or source.get("public_surface"), limit=360),
        "hook": _text(source.get("hook"), limit=360),
        "participants": _strings(source.get("participants") or source.get("present")),
        "roles": roles,
    }


def _recent(value: Any) -> dict[str, Any]:
    """Keep recent state structural; do not turn prior prose into a new prompt lane."""
    source = value if isinstance(value, Mapping) else {}

    def _count(key: str) -> int:
        raw = source.get(key, 0)
        try:
            return max(0, min(int(raw), 9999))
        except (TypeError, ValueError):
            return 0

    return {
        "turn_count": _count("turn_count"),
        "scene_turn_count": _count("scene_turn_count"),
        "time": _text(source.get("time"), limit=80),
        "location": _text(source.get("location"), limit=160),
    }


def select_live_beat(
    scene: Mapping[str, Any] | None,
    *,
    participant_roles: Mapping[str, Any] | None = None,
    character_cores: Mapping[str, Any] | None = None,
    recent_state: Mapping[str, Any] | None = None,
    player_id: str = "player",
) -> dict[str, Any]:
    """Select one concrete public scene beat, or ``{}`` when none is warranted.

    The selector is deterministic by construction: it chooses the first
    authored non-player participant, preserves the authored public hook, and
    uses only structural recent-state counters to distinguish opening from
    continuation.  It never reads event evidence, private plans, transcript
    prose, character system prompts, or hidden character fields.
    """
    public = _public_scene(scene)
    # A scene id/location alone is a runtime gate, not something the Director
    # can honestly turn into drama.  Do not manufacture pressure from it.
    if not (public["visible"] or public["hook"] or public["theme"]):
        return {}

    supplied_roles = participant_roles if isinstance(participant_roles, Mapping) else {}
    supplied_cores = character_cores if isinstance(character_cores, Mapping) else {}
    player_key = _text(player_id, limit=80) or "player"
    participants: list[dict[str, str]] = []
    for key in public["participants"]:
        role = _role(supplied_roles.get(key)) or public["roles"].get(key, "")
        core = public_character_core(supplied_cores.get(key))
        # A short behavior/relationship note gives the Director a concrete
        # human handle without treating a private wound as playable knowledge.
        surface = next((core[name] for name in ("public_core", "persona", "personality", "connection")
                        if core.get(name)), "")
        item = {"key": key}
        if role:
            item["role"] = role
        if surface:
            item["surface"] = surface
        participants.append(item)

    non_player_participants = [item for item in participants if item["key"] != player_key]
    # A named role is an authored signal that this person has a particular
    # scene function. Prefer it over a generic participant, while preserving
    # authored list order within each group.
    focus = next((item for item in non_player_participants if item.get("role")), None) \
        or next(iter(non_player_participants), None)
    recent = _recent(recent_state)
    phase = "opening" if not recent["scene_turn_count"] else "continuing"
    if public["hook"] and _INVESTIGATION.search(public["hook"]):
        kind = "investigation"
    elif public["hook"]:
        kind = "choice"
    elif focus:
        kind = "encounter"
    else:
        kind = "arrival"

    if public["hook"]:
        objective = f"Keep the player in contact with this open question: {public['hook']}"
        pressure = public["visible"] or public["hook"]
        player_opening = public["hook"]
    elif public["visible"]:
        objective = f"Make the visible situation actionable: {public['visible']}"
        pressure = public["visible"]
        player_opening = "Let the player choose how to respond to the visible situation."
    else:
        objective = f"Let one concrete interaction carry the scene's theme: {public['theme']}"
        pressure = public["theme"]
        player_opening = "Give the player a concrete way to enter the moment."

    next_move = (
        "Establish one playable detail, then leave the response with the player."
        if phase == "opening"
        else "Answer the player's actual move first; if it touches this pressure, make one visible change, relationship beat, or grounded NPC move."
    )
    beat: dict[str, Any] = {
        "version": 1,
        "scene_id": public["id"],
        "title": public["title"] or public["id"],
        "kind": kind,
        "phase": phase,
        "location": public["location"] or recent["location"],
        "objective": objective,
        "pressure": pressure,
        "player_opening": player_opening,
        "next_move": next_move,
        "participants": participants,
        "constraints": ["public-scene-only", "player-action-first", "no-forced-reveal"],
    }
    if public["theme"]:
        beat["theme"] = public["theme"]
    if public["tone"]:
        beat["tone"] = public["tone"]
    if focus:
        beat["focus"] = focus
    return beat


def director_live_beat_block(beat: Mapping[str, Any] | None) -> str:
    """Render a selected public beat as a compact Director-only instruction."""
    if not isinstance(beat, Mapping) or not _text(beat.get("objective"), limit=500):
        return ""
    title = _text(beat.get("title") or beat.get("scene_id"), limit=240)
    pressure = _text(beat.get("pressure"), limit=420)
    objective = _text(beat.get("objective"), limit=460)
    opening = _text(beat.get("player_opening"), limit=420)
    next_move = _text(beat.get("next_move"), limit=420)
    focus = beat.get("focus") if isinstance(beat.get("focus"), Mapping) else {}
    focus_key = _text(focus.get("key"), limit=120)
    focus_role = _text(focus.get("role"), limit=180)
    focus_surface = _text(focus.get("surface"), limit=240)
    lines = [
        "LIVE SCENE BEAT — a public anchor for this turn, not a script or a payoff:",
        "- The player's actual action remains the cause of what happens. Use this only where it naturally connects.",
        f"- Scene: {title or 'current scene'}.",
        f"- Objective: {objective}",
    ]
    if pressure:
        lines.append(f"- Immediate pressure: {pressure}")
    tone = _text(beat.get("tone"), limit=60)
    if tone:
        lines.append(f"- Register this plays in: {tone}")
    if focus_key:
        focus_line = focus_key + (f" ({focus_role})" if focus_role else "")
        if focus_surface:
            focus_line += f" — public surface: {focus_surface}"
        lines.append(f"- Center, if the player engages them: {focus_line}")
    if opening:
        lines.append(f"- Keep this opening available: {opening}")
    if next_move:
        lines.append(f"- This turn: {next_move}")
    lines.append("- Do not force a revelation, solve the scene for the player, or invent private causes.")
    return "\n".join(lines)


__all__ = ["director_live_beat_block", "public_character_core", "select_live_beat"]
