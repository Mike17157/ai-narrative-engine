"""Legacy compatibility shim for the story chat agent's modes.

The agent definitions (persona + tools + triggers + craft section + injects) now live entirely in
`configs/story_agent.json` under `agents`. This module exists ONLY to translate the old internal
mode keys (used in persisted `active_behavior` signatures) to the new readable keys, so a session
saved before the rename still resolves its active agent after an upgrade.

Old key              → New key
  _smith_tools      → cast
  _location_fns     → locations
  _scene_fns        → scenes
  _wardrobe_fns     → wardrobe
  _story_tools      → story

This map and module can be deleted once no persisted sessions reference the old keys.
"""
from __future__ import annotations

LEGACY_KEY_MAP: dict[str, str] = {
    "_smith_tools": "cast",
    "_location_fns": "locations",
    "_scene_fns": "scenes",
    "_wardrobe_fns": "wardrobe",
    "_story_tools": "story",
}


def translate_key(key: str) -> str:
    """Map a (possibly old) agent key to its current name. Old keys translate; new/unknown keys
    pass through unchanged so the lookup either way is safe."""
    return LEGACY_KEY_MAP.get(key, key)
