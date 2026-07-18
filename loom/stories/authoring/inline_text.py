"""Shared, deliberately narrow mutations for direct Story-card prose edits.

The Story workspace lets an author revise a small set of explicit text leaves
without handing a browser (or a local IPC client) a generic document-patch
capability.  Keeping the path allowlist and mutation here gives the HTTP
compatibility route and the desktop Story Host one source of truth.
"""

from __future__ import annotations

from typing import Any


class InlineTextEditError(ValueError):
    """A validation failure that is safe to report at an authoring boundary."""

    def __init__(self, message: str, *, code: str = "invalid_edit") -> None:
        super().__init__(message)
        self.code = code


def parse_inline_text_edit(path: Any, value: Any) -> tuple[list[str], str]:
    """Validate one author-editable leaf and normalize its text value.

    This mirrors the legacy HTTP route exactly: every persisted inline Story
    edit is trimmed, ids must name an already-existing record, and derived
    containers are never writable through this capability.
    """
    if not isinstance(path, list) or not path or not all(isinstance(part, str) and part for part in path):
        raise InlineTextEditError("a valid text path is required")
    if not isinstance(value, str):
        raise InlineTextEditError("inline edits must be text")

    normalized = value.strip()
    simple = {("name",), ("premise",), ("tone",), ("art_style",)}
    event_fields = {"visible", "hook", "theme", "tone", "trigger", "evidence", "hidden"}
    location_fields = {"name", "description", "background_prompt"}
    entity_fields = {"name", "role", "description", "tactic", "limitations", "objective", "knowledge"}
    world_fields = {"genre", "setting", "biome", "culture", "atmosphere", "history", "customs", "technology", "background"}
    period_fields = {"state", "constraint"}
    character_core = len(path) == 3 and path[:2] == ["fields", "character_cores"]
    objective = path == ["fields", "first_day_plan", "objective"]
    event = len(path) == 5 and path[:3] == ["fields", "first_day_plan", "events"] and path[4] in event_fields
    location = len(path) == 3 and path[0] == "locations" and path[2] in location_fields
    entity = len(path) == 3 and path[:2] == ["world", "entity"] and path[2] in entity_fields
    world = len(path) == 2 and path[0] == "world" and path[1] in world_fields
    period = len(path) == 4 and path[:2] == ["time_system", "entity_periods"] and path[3] in period_fields
    if tuple(path) not in simple and not any((character_core, objective, event, location, entity, world, period)):
        raise InlineTextEditError("that card text is derived or not inline-editable")
    return list(path), normalized


def apply_inline_text_edit(raw: dict[str, Any], path: list[str], value: str) -> str:
    """Mutate one previously validated Story prose leaf and return its root.

    ``raw`` deliberately remains a raw persisted aggregate.  The caller owns
    transactional validation/persistence, which avoids normalizing unknown
    legacy fields away as a side effect of an otherwise surgical text edit.
    """
    root = path[0]
    simple = {("name",), ("premise",), ("tone",), ("art_style",)}
    character_core = len(path) == 3 and path[:2] == ["fields", "character_cores"]
    objective = path == ["fields", "first_day_plan", "objective"]
    event = len(path) == 5 and path[:3] == ["fields", "first_day_plan", "events"]
    location = len(path) == 3 and path[0] == "locations"
    entity = len(path) == 3 and path[:2] == ["world", "entity"]
    world = len(path) == 2 and path[0] == "world"
    period = len(path) == 4 and path[:2] == ["time_system", "entity_periods"]

    if tuple(path) in simple:
        raw[root] = value
    elif objective:
        raw.setdefault("fields", {}).setdefault("first_day_plan", {})["objective"] = value
    elif character_core:
        raw.setdefault("fields", {}).setdefault("character_cores", {})[path[2]] = value
    elif event:
        events = raw.setdefault("fields", {}).setdefault("first_day_plan", {}).setdefault("events", [])
        target = next((item for item in events if isinstance(item, dict) and str(item.get("id")) == path[3]), None)
        if target is None:
            raise InlineTextEditError("no such scene", code="not_found")
        target[path[4]] = value
    elif location:
        target = next((item for item in raw.get("locations") or []
                       if isinstance(item, dict) and str(item.get("id")) == path[1]), None)
        if target is None:
            raise InlineTextEditError("no such location", code="not_found")
        target[path[2]] = value
    elif entity:
        raw.setdefault("world", {}).setdefault("entity", {})[path[2]] = value
    elif world:
        raw.setdefault("world", {})[path[1]] = value
    elif period:
        target = next((item for item in (raw.setdefault("time_system", {}).get("entity_periods") or [])
                       if isinstance(item, dict) and str(item.get("id")) == path[2]), None)
        if target is None:
            raise InlineTextEditError("no such activity window", code="not_found")
        target[path[3]] = value
    else:  # pragma: no cover - parse_inline_text_edit is the capability gate.
        raise InlineTextEditError("that card text is derived or not inline-editable")

    # Any narrative prose edit invalidates an active compiled snapshot.  A
    # title is presentation-only and intentionally retains the active status.
    if root != "name":
        story_fields = raw.setdefault("fields", {})
        if story_fields.get("status") == "active":
            story_fields["status"] = "interviewing"
            story_fields.pop("runtime_scenario", None)
    return root
