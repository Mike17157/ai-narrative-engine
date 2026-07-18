"""Small public dramatic kernels for Story-card authoring.

These fields give an authoring model enough shape to make a scene about
people rather than a loose lore fact, without leaking a character's private
arc diagnosis into the narrator.  They remain optional so legacy cards and
hand-authored scenes continue to work unchanged.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any


_PLAYER_ALIASES = frozenset({"player", "you", "protagonist", "returner"})


class DramaticKernelError(ValueError):
    """A compact public character/scene kernel has an invalid shape."""


def _text(value: Any, *, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise DramaticKernelError(f"{label} must be text")
    text = " ".join(value.split())
    if "[[" in text or "]]" in text:
        raise DramaticKernelError(f"{label} must be public text")
    return text[:limit]


def _person_key(value: Any, *, label: str) -> str:
    key = _text(value, label=label, limit=160)
    if not key:
        raise DramaticKernelError(f"{label} needs a character key")
    return "player" if key.casefold() in _PLAYER_ALIASES else key


def normalize_character_cores(
    value: Any,
    *,
    known_characters: Collection[str] | None = None,
    limit: int = 360,
) -> dict[str, str]:
    """Return a compact, public map of character key to playable core.

    Blank values intentionally mean "no core proposed" for strict JSON-schema
    responses.  A caller that knows the story roster can opt into rejecting a
    core attached to an unknown key. ``limit`` defaults to the one-line public
    `character_cores` surface; a caller validating a longer private field
    (e.g. `character_wounds`, a full ordinary backstory paragraph) should pass
    a larger value rather than truncating it down to a one-liner's length.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DramaticKernelError("character_cores must be an object keyed by character")
    allowed = {str(item).strip() for item in known_characters or () if str(item).strip()}
    allowed.add("player")
    result: dict[str, str] = {}
    for raw_key, raw_core in value.items():
        key = _person_key(raw_key, label="character_cores key")
        core = _text(raw_core, label=f"character core for '{key}'", limit=limit)
        if not core:
            continue
        if known_characters is not None and key not in allowed:
            raise DramaticKernelError(f"character_cores names unknown character '{key}'")
        result[key] = core
    return result


def character_core_map(value: Any) -> dict[str, Any]:
    """Accept the strict-schema pair list as well as the persisted map."""
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, list):
        return {}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise DramaticKernelError("character_cores entries must be objects")
        key = item.get("character")
        if key is None:
            key = item.get("key")
        if not isinstance(key, str):
            raise DramaticKernelError("character_cores entry needs a character key")
        result[key] = item.get("core", "")
    return result


def normalize_scene_kernel(
    event: Mapping[str, Any],
    *,
    known_characters: Collection[str] | None = None,
) -> dict[str, Any]:
    """Canonicalize optional public ``theme``, ``tone``, and ``roles`` fields.

    ``roles`` is a mapping from an on-page participant's stable character key
    to their immediate function in *this* scene.  It is deliberately not a
    psychological diagnosis or a hidden agenda.  It may cover only the named
    participants while a scene is being drafted, but it may never name someone
    who is not on the scene.
    """
    if not isinstance(event, Mapping):
        raise DramaticKernelError("scene kernel needs an event object")
    out = dict(event)
    for field, limit in (("theme", 180), ("tone", 180)):
        if field not in out:
            continue
        text = _text(out[field], label=f"scene {field}", limit=limit)
        if text:
            out[field] = text
        else:
            out.pop(field, None)

    if "roles" not in out:
        return out
    raw_roles = out.get("roles")
    if raw_roles in (None, []):
        out.pop("roles", None)
        return out
    if isinstance(raw_roles, list):
        pairs: dict[str, Any] = {}
        for item in raw_roles:
            if not isinstance(item, Mapping):
                raise DramaticKernelError("scene role entries must be objects")
            key = item.get("character")
            if key is None:
                key = item.get("participant")
            if not isinstance(key, str):
                raise DramaticKernelError("scene role entry needs a character key")
            pairs[key] = item.get("role", "")
        raw_roles = pairs
    if not isinstance(raw_roles, Mapping):
        raise DramaticKernelError("scene roles must be an object keyed by character")
    participants = {
        _person_key(value, label="scene participant")
        for value in (out.get("participants") or [])
        if isinstance(value, str) and value.strip()
    }
    allowed = {str(item).strip() for item in known_characters or () if str(item).strip()}
    allowed.add("player")
    roles: dict[str, str] = {}
    for raw_key, raw_role in raw_roles.items():
        key = _person_key(raw_key, label="scene role key")
        if participants and key not in participants:
            raise DramaticKernelError(f"scene role '{key}' is not an on-page participant")
        if known_characters is not None and key not in allowed:
            raise DramaticKernelError(f"scene role names unknown character '{key}'")
        role = _text(raw_role, label=f"scene role for '{key}'", limit=280)
        if role:
            roles[key] = role
    if roles:
        out["roles"] = roles
    else:
        out.pop("roles", None)
    return out
