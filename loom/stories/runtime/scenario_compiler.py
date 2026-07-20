"""Compile an authored Story card into a deterministic play-scenario contract.

This module is deliberately *not* the live runtime.  It has no model, database,
or application dependencies: it turns the parts of an authored card that are
already explicit into a small, inspectable package that the runtime can consume.

The important boundary is intentional.  Natural-language card prose is useful to
the narrator, but it is not safe to execute as rules.  For example, ``loop.reset``
may explain a loop beautifully without saying which runtime keys are restored.  In
that case this compiler preserves the prose in the source card, emits a precise
readiness issue, and refuses to pretend the loop is executable.

The output uses the scene shape consumed by :mod:`scenario` (``id``, ``slots``,
``locations``, ``location``, ``participants``, ``requires``), while carrying
additional gates and director-only data alongside it.  Nothing in here mutates
the supplied card.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any


COMPILER_VERSION = 1
DEFAULT_DAY_SLOTS = ("morning", "evening", "night")

# These are the keys in ``runtime.scenario.empty_scenario_state``.  A loop policy
# must name preservation against this finite set rather than prose such as
# "everything resets"; that makes a reset implementable and auditable.
RUNTIME_STATE_KEYS = (
    "time",
    "location",
    "present",
    "public_facts",
    "flags",
    "active_scene",
    "turns",
    "scene_memories",
    "character_memories",
)

_SLOT_ALIASES = {
    "dawn": "morning",
    "day": "morning",
    "daytime": "morning",
    "midday": "morning",
    "noon": "morning",
    "afternoon": "morning",
    "sunset": "evening",
    "dusk": "evening",
    "late evening": "evening",
    "late-night": "night",
    "late night": "night",
    "midnight": "night",
}

# Authoring prose naturally uses these roles for the playable person.  The
# runtime contract, however, needs one stable key.  Keep this deliberately
# small: a name that is not a known role remains an author-visible problem
# rather than being guessed into a cast member.
_PLAYER_PARTICIPANT_ALIASES = frozenset({"player", "you", "protagonist", "returner"})


def _entity_alias_token(value: str) -> str:
    """Return a comparison form for a director-only entity stand-in."""
    return re.sub(r"[\s_]+", "-", value.strip().lower()).strip("-")


def _is_hidden_entity_alias(value: str, event: Mapping[str, Any] | None) -> bool:
    """Whether ``value`` is a private entity stand-in, not a public cast key.

    An alias such as ``entity-shuri`` represents the antagonist wearing a
    person's shape.  Treating it as Shuri's public presence would both invent
    a cast member in the scene and make an author resolve a fake character.
    This exemption is intentionally limited to events that already carry
    director-private material (or an explicit entity action); an ordinary,
    public name still gets the usual unknown-participant readiness error.
    """
    if not isinstance(event, Mapping):
        return False
    is_private = _has_content(event.get("hidden")) or event.get("entity_action") is True
    if not is_private:
        return False
    token = _entity_alias_token(value)
    return token in {"entity", "the-entity", "mimic", "copy"} or token.startswith(
        ("entity-", "mimic-", "copy-")
    )


def _canonical_participants(value: Any, *, player_id: str,
                            event: Mapping[str, Any] | None = None) -> list[str]:
    """Compile prose player roles into the player key and hide entity stand-ins.

    This is a projection-only repair: callers never mutate the authored card.
    Unknown public names deliberately survive so validation can ask the author
    for the smallest real cast decision instead of silently deleting it.
    """
    out: list[str] = []
    for person in _strings(value):
        canonical = player_id if person.casefold() in _PLAYER_PARTICIPANT_ALIASES else person
        if _is_hidden_entity_alias(canonical, event):
            continue
        if canonical not in out:
            out.append(canonical)
    return out


def compile_authored_scenario(story: dict | Any) -> dict[str, Any]:
    """Compile a Story model or raw card into a deterministic scenario package.

    Returns a JSON-safe mapping with these stable top-level fields:

    ``ready``
        True only when there are no error-level readiness issues.
    ``opening``
        The opening ``state`` can seed ``runtime.scenario`` directly, with the
        scene id and location source made explicit.
    ``scene_catalog``
        First-day scene possibilities using the existing scenario runtime shape.
    ``time_slots``
        The normalized authored day slots.  This is retained alongside the
        compiled plan so author tooling can render a time lane without
        duplicating the compiler's slot aliases/defaults.
    ``director_plan``
        Private event / entity constraints.  Do not pass this wholesale to a
        narrator or character prompt.
    ``loop_policy``
        An executable reset contract only when the authored card supplied typed
        loop policy fields; legacy prose intentionally remains non-executable.
    ``issues``
        Machine-readable readiness findings: ``code``, ``severity``, ``path``,
        ``message``, and ``fix``.
    """
    card = _story_data(story)
    issues: list[dict[str, str]] = []
    # Prose-units lint: every narrator-facing text field must read as COMPLETE units,
    # not fragments (warning-level — readiness is structural, but the author sees every cut).
    from ...prose import lint_story_texts
    for _li in lint_story_texts(card):
        _issue(issues, str(_li["code"]), str(_li["severity"]), str(_li["path"]),
               str(_li["message"]), str(_li["fix"]))
    fields = _mapping(card.get("fields"))
    world = _mapping(card.get("world"))
    plan = _mapping(fields.get("first_day_plan"))
    slots = _day_slots(card.get("time_system"), issues)
    locations = _location_index(card.get("locations"))
    cast = _cast_index(card.get("cast"))
    player_id = _text(fields.get("player_id")) or "player"

    raw_events = plan.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        _issue(
            issues,
            "missing_first_day_events",
            "error",
            "fields.first_day_plan.events",
            "Live play needs at least one authored opening-day scene possibility.",
            "Add a first-day plan with one or more events, each with a time and visible situation.",
        )
        raw_events = []

    event_specs = _event_specs(raw_events, slots, issues, player_id=player_id)
    opening = _compile_opening(
        card=card,
        fields=fields,
        world=world,
        plan=plan,
        event_specs=event_specs,
        slots=slots,
        locations=locations,
        cast=cast,
        player_id=player_id,
        issues=issues,
    )
    catalog = _compile_scene_catalog(
        event_specs=event_specs,
        opening=opening,
        locations=locations,
        cast=cast,
        player_id=player_id,
        issues=issues,
    )
    entity_periods = _entity_periods(card.get("time_system"), slots, issues)
    director_plan = _compile_director_plan(
        plan=plan,
        event_specs=event_specs,
        catalog=catalog,
        entity_periods=entity_periods,
        entity_declared=_entity_declared(world),
        issues=issues,
    )
    loop_policy = _compile_loop_policy(
        loop=_mapping(world.get("loop")),
        fields=fields,
        opening=opening,
        slots=slots,
        locations=locations,
        player_id=player_id,
        issues=issues,
    )
    # Player capabilities are a separate, explicit contract.  Entity schedule
    # capabilities remain director-only and must never implicitly authorize the
    # player just because a story contains magic.
    from .player_guard import compile_player_capabilities
    player_capabilities = compile_player_capabilities(fields)

    # A card may name a cast roster without asserting that a particular person is
    # present in the opening.  That is a useful (non-blocking) authoring prompt,
    # not a reason to invent a character entrance.  An explicit ``[player]``
    # value—either on the opening or on the opening scene—records the author's
    # deliberate solo opening and must not keep resurfacing as an unresolved
    # prompt.
    opening_non_player = [p for p in opening["state"]["present"] if p != player_id]
    first_event = _mapping(event_specs[0].get("raw")) if event_specs else {}
    opening_presence_declared = (
        "opening_present" in fields
        or "opening_present" in plan
        or "participants" in first_event
        or "present" in first_event
    )
    if cast and not opening_non_player and not opening_presence_declared:
        _issue(
            issues,
            "opening_cast_unspecified",
            "warning",
            "fields.first_day_plan.opening_present",
            "The opening is playable with the player alone, but no cast member is explicitly present.",
            "Name opening_present or event participants if someone begins on stage.",
        )

    return {
        "version": COMPILER_VERSION,
        "ready": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
        "time_slots": list(slots),
        "opening": opening,
        "scene_catalog": catalog,
        "director_plan": director_plan,
        "loop_policy": loop_policy,
        "player_capabilities": player_capabilities,
    }


def readiness_issues(story: dict | Any) -> list[dict[str, str]]:
    """Return only the deterministic play-readiness findings for an authored card."""
    return deepcopy(compile_authored_scenario(story)["issues"])


def _story_data(story: dict | Any) -> dict[str, Any]:
    """Accept raw dictionaries and Pydantic Story objects without mutating either."""
    if isinstance(story, Mapping):
        return deepcopy(dict(story))
    dump = getattr(story, "model_dump", None)
    if callable(dump):
        return deepcopy(dump(mode="json"))
    data = getattr(story, "__dict__", None)
    if isinstance(data, dict):
        return deepcopy(data)
    raise TypeError("story must be a mapping or a model with model_dump()")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _public_roles(value: Any) -> dict[str, str]:
    """Keep the authored public participant-role map compact and JSON-safe.

    Roles are scene surface, not a replacement for character-private material:
    the compiled catalog carries only a key-to-short-string projection for the
    live Director beat and public card views.
    """
    source = _mapping(value)
    return {
        _text(key): _text(role)
        for key, role in source.items()
        if _text(key) and _text(role)
    }


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or fallback


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [value for value in values if value and not (value in seen or seen.add(value))]


def _issue(issues: list[dict[str, str]], code: str, severity: str, path: str,
           message: str, fix: str) -> None:
    issues.append({"code": code, "severity": severity, "path": path,
                   "message": message, "fix": fix})


def _day_slots(time_system: Any, issues: list[dict[str, str]]) -> list[str]:
    system = _mapping(time_system)
    configured = _strings(system.get("slots"))
    slots = _unique(configured) or list(DEFAULT_DAY_SLOTS)
    if configured and len(slots) != len(configured):
        _issue(
            issues,
            "duplicate_day_slot",
            "warning",
            "time_system.slots",
            "Duplicate day-slot names were collapsed by the compiler.",
            "Keep each time_system slot name unique.",
        )
    return slots


def _normalise_slot(value: Any, slots: list[str]) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    by_lower = {slot.lower(): slot for slot in slots}
    if text in by_lower:
        return by_lower[text]
    alias = _SLOT_ALIASES.get(text)
    return by_lower.get(alias or "", "")


def _event_slots(event: dict[str, Any], slots: list[str], index: int,
                 issues: list[dict[str, str]]) -> list[str]:
    raw = event.get("slots", event.get("when", event.get("time")))
    values = _strings(raw)
    # A compact authoring form such as "morning, evening" should remain useful,
    # while prose such as "sometime after lunch" stays visibly unresolved.
    if isinstance(raw, str) and "," in raw:
        values = [piece.strip() for piece in raw.split(",") if piece.strip()]
    normalized = [_normalise_slot(value, slots) for value in values]
    unknown = [value for value, slot in zip(values, normalized) if not slot]
    if unknown:
        _issue(
            issues,
            "scene_time_unknown",
            "error",
            f"fields.first_day_plan.events[{index}].when",
            f"The event uses a time the runtime cannot gate: {', '.join(unknown)}.",
            f"Use one of: {', '.join(slots)} (midday/afternoon map to morning; dusk maps to evening).",
        )
    result = _unique([slot for slot in normalized if slot])
    if not result:
        _issue(
            issues,
            "scene_time_missing",
            "error",
            f"fields.first_day_plan.events[{index}].when",
            "Every playable scene needs an explicit time-slot gate.",
            f"Set when or slots to one of: {', '.join(slots)}.",
        )
    return result


def _event_specs(raw_events: list[Any], slots: list[str], issues: list[dict[str, str]],
                 *, player_id: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            _issue(
                issues,
                "invalid_first_day_event",
                "error",
                f"fields.first_day_plan.events[{index}]",
                "A first-day event must be an object, not free text.",
                "Store the event as an object with visible, when, and optional hidden/trigger/evidence fields.",
            )
            continue
        event = deepcopy(dict(raw))
        authored_id = _text(event.get("id"))
        base_id = authored_id or _slug(
            _text(event.get("title")) or _text(event.get("event")) or _text(event.get("visible")),
            fallback=f"day-1-event-{index + 1}",
        )
        event_id = base_id
        suffix = 2
        while event_id in used_ids:
            event_id = f"{base_id}-{suffix}"
            suffix += 1
        if not authored_id:
            _issue(
                issues,
                "scene_id_derived",
                "warning",
                f"fields.first_day_plan.events[{index}].id",
                f"The compiler derived scene id '{event_id}'.",
                "Give the event a stable id so external tools can refer to it directly.",
            )
        elif event_id != authored_id:
            _issue(
                issues,
                "duplicate_scene_id",
                "error",
                f"fields.first_day_plan.events[{index}].id",
                f"Scene id '{authored_id}' is duplicated; this copy was compiled as '{event_id}'.",
                "Give every first-day event a unique id.",
            )
        used_ids.add(event_id)
        specs.append({
            "index": index,
            "id": event_id,
            "raw": event,
            "slots": _event_slots(event, slots, index, issues),
            "location_value": event.get("location", event.get("place")),
            "participants": _canonical_participants(
                event.get("participants", event.get("present")),
                player_id=player_id,
                event=event,
            ),
        })
    return specs


def _location_index(raw_locations: Any) -> dict[str, Any]:
    aliases: dict[str, str] = {}
    labels: dict[str, str] = {}
    for raw in raw_locations if isinstance(raw_locations, list) else []:
        if not isinstance(raw, Mapping):
            continue
        location = dict(raw)
        ident = _text(location.get("id"))
        label = _text(location.get("name")) or ident
        if not ident:
            continue
        labels[ident] = label
        aliases[ident.lower()] = ident
        if label:
            aliases[label.lower()] = ident
    return {"aliases": aliases, "labels": labels}


def _resolve_location(value: Any, locations: dict[str, Any]) -> dict[str, str]:
    if isinstance(value, Mapping):
        value = value.get("id") or value.get("name")
    text = _text(value)
    if not text:
        return {"id": "", "label": "", "source": "missing"}
    ident = locations["aliases"].get(text.lower())
    if ident:
        return {"id": ident, "label": locations["labels"].get(ident, ident), "source": "declared"}
    return {"id": _slug(text, fallback="opening"), "label": text, "source": "derived"}


def _compile_opening(*, card: dict[str, Any], fields: dict[str, Any], world: dict[str, Any],
                     plan: dict[str, Any], event_specs: list[dict[str, Any]],
                     slots: list[str],
                     locations: dict[str, Any], cast: set[str], player_id: str,
                     issues: list[dict[str, str]]) -> dict[str, Any]:
    loop = _mapping(world.get("loop"))
    first = event_specs[0] if event_specs else {}
    candidates = (
        (card.get("start"), "start"),
        (fields.get("opening_location"), "fields.opening_location"),
        (plan.get("opening_location"), "fields.first_day_plan.opening_location"),
        (world.get("opening_location"), "world.opening_location"),
        (first.get("location_value"), "fields.first_day_plan.events[0].location"),
        (loop.get("start"), "world.loop.start"),
    )
    location, source_path = ({"id": "", "label": "", "source": "missing"}, "")
    for value, path in candidates:
        location = _resolve_location(value, locations)
        if location["id"]:
            source_path = path
            break
    if not location["id"]:
        location = {"id": "opening", "label": "Opening", "source": "fallback"}
        _issue(
            issues,
            "opening_location_missing",
            "error",
            "start",
            "The card does not identify an opening location.",
            "Set story.start, fields.opening_location, or the first event's location.",
        )
    elif location["source"] == "derived":
        _issue(
            issues,
            "opening_location_undeclared",
            "error",
            source_path,
            f"Opening location '{location['label']}' is prose-derived rather than a declared map location.",
            "Add a location with a stable id and set story.start before activating play.",
        )

    raw_time = (fields.get("opening_time") or plan.get("opening_time")
                or world.get("opening_time"))
    opening_time = _normalise_slot(raw_time, slots)
    if not opening_time and first.get("slots"):
        opening_time = first["slots"][0]
    if not opening_time:
        opening_time = slots[0]
        _issue(
            issues,
            "opening_time_missing",
            "error",
            "fields.first_day_plan.opening_time",
            "The card does not identify when the opening begins.",
            "Set opening_time or give the first event a valid when/slots value.",
        )

    raw_present = (fields.get("opening_present") or plan.get("opening_present")
                   or first.get("participants") or [])
    present = _unique([player_id, *_canonical_participants(raw_present, player_id=player_id)])
    unknown_present = [person for person in present if person != player_id and person not in cast]
    if unknown_present:
        _issue(
            issues,
            "opening_participant_unknown",
            "error",
            "fields.first_day_plan.opening_present",
            "The opening names people who are not cast keys: " + ", ".join(unknown_present) + ".",
            "Add those character keys to cast, or use their existing card keys in opening_present.",
        )
    scene_id = first.get("id") or "opening"
    return {
        "scene_id": scene_id,
        "location_label": location["label"],
        "location_source": location["source"],
        "source": source_path or "compiler fallback",
        "state": {
            "time": opening_time,
            "location": location["id"],
            "present": present,
            "public_facts": [],
            "flags": {},
            "active_scene": None,
            "turns": [],
            "scene_memories": [],
            "character_memories": {},
        },
    }


def _presence_gate(event: dict[str, Any]) -> dict[str, list[str]]:
    requires = _mapping(event.get("requires"))
    presence = _mapping(event.get("presence"))
    return {
        "all": _unique(_strings(presence.get("all") or presence.get("present")
                                 or requires.get("present_all") or requires.get("present"))),
        "any": _unique(_strings(presence.get("any") or requires.get("present_any"))),
        "absent": _unique(_strings(presence.get("absent") or requires.get("absent"))),
    }


def _flag_gate(event: dict[str, Any]) -> dict[str, Any]:
    required = _mapping(event.get("requires"))
    flags = required.get("flags")
    if not isinstance(flags, Mapping):
        return {}
    return {str(key): deepcopy(value) for key, value in flags.items() if str(key).strip()}


def _compile_scene_catalog(*, event_specs: list[dict[str, Any]], opening: dict[str, Any],
                           locations: dict[str, Any], cast: set[str], player_id: str,
                           issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for spec in event_specs:
        event, index = spec["raw"], spec["index"]
        resolved = _resolve_location(spec.get("location_value"), locations)
        if not resolved["id"]:
            resolved = {
                "id": opening["state"]["location"],
                "label": opening["location_label"],
                "source": "inherited",
            }
            _issue(
                issues,
                "scene_location_inherited",
                "warning",
                f"fields.first_day_plan.events[{index}].location",
                f"Scene '{spec['id']}' inherits the opening location.",
                "Set a location when this event happens somewhere else.",
            )
        elif resolved["source"] == "derived":
            _issue(
                issues,
                "scene_location_undeclared",
                "error",
                f"fields.first_day_plan.events[{index}].location",
                f"Scene '{spec['id']}' uses prose-derived location '{resolved['label']}'.",
                "Add a declared location id before activating play.",
            )

        slots = spec["slots"] or [opening["state"]["time"]]
        participants = spec["participants"]
        if index == 0 and not participants:
            participants = list(opening["state"]["present"])
        unknown_participants = [person for person in participants if person != player_id and person not in cast]
        if unknown_participants:
            _issue(
                issues,
                "scene_participant_unknown",
                "error",
                f"fields.first_day_plan.events[{index}].participants",
                f"Scene '{spec['id']}' names people who are not cast keys: " + ", ".join(unknown_participants) + ".",
                "Add those character keys to cast, or use existing character-card keys.",
            )
        title = (_text(event.get("title")) or _text(event.get("event"))
                 or _text(event.get("visible")) or spec["id"])
        visible = _text(event.get("visible")) or _text(event.get("event"))
        hook = _text(event.get("hook"))
        theme = _text(event.get("theme"))
        tone = _text(event.get("tone"))
        roles = _public_roles(event.get("roles"))
        if not visible:
            _issue(
                issues,
                "scene_visible_situation_missing",
                "warning",
                f"fields.first_day_plan.events[{index}].visible",
                f"Scene '{spec['id']}' has no visible situation for the player to enter.",
                "Add a concise visible field describing what the player can see or do.",
            )
        catalog.append({
            "id": spec["id"],
            "title": title,
            "slots": slots,
            "location": resolved["id"],
            "locations": [resolved["id"]],
            "participants": participants,
            # `scenario.eligible_scenes` already respects this `flags` object.
            "requires": {"flags": _flag_gate(event)},
            # Presence is intentionally explicit even though the current selector
            # only enforces flags; wiring it later cannot silently lose author intent.
            "presence": _presence_gate(event),
            "visible": visible,
            # A public scene offer may name a more direct player choice than
            # its atmospheric visible situation.  It remains safe narrator
            # context, unlike trigger/evidence/knowledge director fields.
            "hook": hook,
            # These fields are public scene texture.  They let the
            # deterministic live-beat selector keep a scene thematically
            # pointed without consulting the private director plan.
            "theme": theme,
            "tone": tone,
            "roles": roles,
            "evidence": deepcopy(event.get("evidence")),
            "source_event_id": _text(event.get("id")) or spec["id"],
        })
    return catalog


def _entity_periods(time_system: Any, slots: list[str], issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    system = _mapping(time_system)
    raw_periods = system.get("entity_periods")
    if raw_periods is None:
        return []
    if not isinstance(raw_periods, list):
        _issue(
            issues,
            "entity_schedule_invalid",
            "error",
            "time_system.entity_periods",
            "Entity periods must be a list of objects.",
            "Use entity_periods: [{id, slots, state, capabilities, constraint}].",
        )
        return []
    periods: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_periods):
        if not isinstance(raw, Mapping):
            _issue(
                issues,
                "entity_period_invalid",
                "error",
                f"time_system.entity_periods[{index}]",
                "An entity period must be an object.",
                "Store a period with id, slots, state, capabilities, and constraint.",
            )
            continue
        period = dict(raw)
        ident = _text(period.get("id")) or f"entity-period-{index + 1}"
        if ident in seen:
            _issue(
                issues,
                "duplicate_entity_period_id",
                "error",
                f"time_system.entity_periods[{index}].id",
                f"Entity period id '{ident}' is duplicated.",
                "Give every entity period a unique id.",
            )
            ident = f"{ident}-{index + 1}"
        seen.add(ident)
        period_slots = []
        for raw_slot in _strings(period.get("slots")):
            slot = _normalise_slot(raw_slot, slots)
            if slot:
                period_slots.append(slot)
            else:
                _issue(
                    issues,
                    "entity_period_time_unknown",
                    "error",
                    f"time_system.entity_periods[{index}].slots",
                    f"Entity period '{ident}' uses unknown slot '{raw_slot}'.",
                    f"Use one of: {', '.join(slots)}.",
                )
        period_slots = _unique(period_slots)
        if not period_slots:
            _issue(
                issues,
                "entity_period_time_missing",
                "error",
                f"time_system.entity_periods[{index}].slots",
                f"Entity period '{ident}' has no usable time slot.",
                f"Set slots to one of: {', '.join(slots)}.",
            )
        periods.append({
            "id": ident,
            "slots": period_slots,
            "state": _text(period.get("state")),
            "capabilities": _strings(period.get("capabilities")),
            "constraint": _text(period.get("constraint")),
        })
    return periods


def _entity_declared(world: dict[str, Any]) -> bool:
    entity = world.get("entity")
    return bool(_text(entity) if isinstance(entity, str) else _mapping(entity))


def _event_period_ids(event: dict[str, Any]) -> list[str]:
    return _unique(_strings(event.get("entity_periods") or event.get("entity_period")))


_ENTITY_ACTION_RE = re.compile(
    r"\b(entity|mimic|mimics|mimicked|creature|monster|killer|copy(?:ing|ies|ied)?|"
    r"attack(?:s|ed|ing)?|kill(?:s|ed|ing)?|hunt(?:s|ed|ing)?|stalk(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)


def _is_entity_action(event: dict[str, Any], *, entity_declared: bool) -> bool:
    """Separate private character facts from entity-gated hidden actions."""
    explicit = event.get("entity_action")
    if isinstance(explicit, bool):
        return explicit
    if _event_period_ids(event):
        return True
    if not entity_declared:
        return False
    # Legacy cards had only `hidden`. Keep clear attacks gated, but do not make
    # a character changing the subject fail an unrelated entity time window.
    return bool(_ENTITY_ACTION_RE.search(_text(event.get("hidden"))))


def _compile_director_plan(*, plan: dict[str, Any], event_specs: list[dict[str, Any]],
                           catalog: list[dict[str, Any]], entity_periods: list[dict[str, Any]],
                           entity_declared: bool, issues: list[dict[str, str]]) -> dict[str, Any]:
    scenes_by_id = {scene["id"]: scene for scene in catalog}
    periods_by_id = {period["id"]: period for period in entity_periods}
    private_events: list[dict[str, Any]] = []
    has_entity_action = False
    schedule_missing_reported = False
    for spec in event_specs:
        event = spec["raw"]
        hidden = deepcopy(event.get("hidden"))
        entity_action = _is_entity_action(event, entity_declared=entity_declared)
        # A scene can carry no private material at all.  A private note and a
        # current entity action are deliberately independent: the former is
        # director context, the latter is a time-gated causal action.
        if not _has_content(hidden) and not entity_action:
            continue
        allowed: list[dict[str, Any]] = []
        if entity_action:
            has_entity_action = True
            explicit_ids = _event_period_ids(event)
            if explicit_ids:
                allowed = [periods_by_id[ident] for ident in explicit_ids if ident in periods_by_id]
                missing = [ident for ident in explicit_ids if ident not in periods_by_id]
                if missing:
                    _issue(
                        issues,
                        "hidden_event_unknown_entity_period",
                        "error",
                        f"fields.first_day_plan.events[{spec['index']}].entity_period",
                        f"Hidden entity action '{spec['id']}' names unknown entity period(s): {', '.join(missing)}.",
                        "Reference an id from time_system.entity_periods.",
                    )
            else:
                allowed = [period for period in entity_periods if set(period["slots"]) & set(spec["slots"])]
            if entity_declared:
                if not entity_periods and not schedule_missing_reported:
                    schedule_missing_reported = True
                    _issue(
                        issues,
                        "entity_schedule_missing",
                        "error",
                        "time_system.entity_periods",
                        "The card has a hidden entity action but no deterministic activity schedule.",
                        "Define the entity's active slots, capabilities, and constraints.",
                    )
                elif entity_periods and not allowed:
                    _issue(
                        issues,
                        "hidden_event_outside_entity_window",
                        "error",
                        f"fields.first_day_plan.events[{spec['index']}].hidden",
                        f"Hidden entity action '{spec['id']}' has no compatible entity activity window.",
                        "Move the event, change its entity_period, or add a compatible time-system period.",
                    )
        scene = scenes_by_id.get(spec["id"], {})
        private_events.append({
            "id": spec["id"],
            "scene_id": spec["id"],
            "slots": list(scene.get("slots") or spec["slots"]),
            "location": scene.get("location", ""),
            "hidden": hidden,
            "trigger": deepcopy(event.get("trigger")),
            "evidence": deepcopy(event.get("evidence")),
            "knowledge": deepcopy(event.get("knowledge")),
            "entity_action": entity_action,
            "entity_period_ids": [period["id"] for period in allowed],
        })
    if entity_declared and not has_entity_action and not entity_periods:
        _issue(
            issues,
            "entity_schedule_unspecified",
            "error",
            "time_system.entity_periods",
            "The card names an entity but does not yet constrain its active periods.",
            "Define active slots, capabilities, and constraints before activating play.",
        )
    return {
        "mode": "possibility_plan",
        "objective": _text(plan.get("objective")),
        "events": private_events,
        "entity_periods": deepcopy(entity_periods),
        "private": True,
    }


def _has_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, Mapping)):
        return bool(value)
    return value is not None


def _cast_index(raw_cast: Any) -> set[str]:
    out: set[str] = set()
    for raw in raw_cast if isinstance(raw_cast, list) else []:
        if not isinstance(raw, Mapping):
            continue
        for candidate in (raw.get("character"), raw.get("name")):
            text = _text(candidate)
            if text:
                out.add(text)
    return out


def _compile_loop_policy(*, loop: dict[str, Any], fields: dict[str, Any], opening: dict[str, Any],
                         slots: list[str],
                         locations: dict[str, Any], player_id: str,
                         issues: list[dict[str, str]]) -> dict[str, Any]:
    field_policy = _mapping(fields.get("loop_policy"))
    nested_policy = _mapping(loop.get("policy"))
    enabled = bool(loop or field_policy)
    if not enabled:
        return {
            "enabled": False,
            "executable": True,
            "trigger": "",
            "restart": None,
            "reset": {"clear_runtime": [], "preserve_runtime": [],
                      "memory": {"preserve_for": [], "clear_for_others": False}},
            "end_condition": "",
            "victims_return_after_end": None,
        }

    # `world.loop.policy` is the documented canonical location.  Fields is also
    # accepted for migration tools, while top-level typed keys accommodate an
    # early card without forcing a destructive reorganization.
    policy = {**field_policy, **nested_policy}
    trigger = _text(policy.get("trigger") or loop.get("trigger") or policy.get("reset_on") or loop.get("reset_on"))
    if not trigger:
        _issue(
            issues,
            "loop_trigger_missing",
            "error",
            "world.loop.policy.trigger",
            "A loop story must state the deterministic reset trigger.",
            "Set world.loop.policy.trigger (for example, 'death').",
        )

    restart_value = policy.get("restart", loop.get("restart"))
    if restart_value is None and policy.get("restart_at_opening") is True:
        restart_value = "opening"
    restart, restart_ok = _restart_state(restart_value, opening, slots, locations, issues)

    preserve = _mapping(policy.get("preserve"))
    runtime_marker = ("runtime" in preserve or "runtime" in loop
                      or "preserve_runtime" in policy or "preserve_runtime" in loop)
    raw_runtime = preserve.get("runtime", policy.get("preserve_runtime",
                              loop.get("preserve_runtime", loop.get("runtime"))))
    preserve_runtime = _unique(_strings(raw_runtime))
    invalid_runtime = [key for key in preserve_runtime if key not in RUNTIME_STATE_KEYS]
    if invalid_runtime:
        _issue(
            issues,
            "loop_runtime_key_unknown",
            "error",
            "world.loop.policy.preserve.runtime",
            f"The loop policy names unknown runtime key(s): {', '.join(invalid_runtime)}.",
            f"Use only: {', '.join(RUNTIME_STATE_KEYS)}.",
        )
    preserve_runtime = [key for key in preserve_runtime if key in RUNTIME_STATE_KEYS]
    if not runtime_marker:
        _issue(
            issues,
            "loop_runtime_policy_missing",
            "error",
            "world.loop.policy.preserve.runtime",
            "The loop policy must explicitly state which runtime facts survive a reset (use [] for none).",
            "Set world.loop.policy.preserve.runtime to an explicit list.",
        )

    memory_marker = ("memories" in preserve or "memories" in loop
                     or "preserve_memories" in policy or "preserve_memories" in loop)
    raw_memories = preserve.get("memories", policy.get("preserve_memories",
                               loop.get("preserve_memories", loop.get("memories"))))
    preserve_memories = _unique(_strings(raw_memories))
    if not memory_marker:
        _issue(
            issues,
            "loop_memory_policy_missing",
            "error",
            "world.loop.policy.preserve.memories",
            "The loop policy must explicitly name who retains memories.",
            f"Set world.loop.policy.preserve.memories (for example, ['{player_id}']).",
        )
    clear_marker = ("clear_memories_for_others" in preserve or "clear_memories_for_others" in loop
                    or "clear_memories_for_others" in policy
                    )
    clear_others = preserve.get(
        "clear_memories_for_others",
        policy.get("clear_memories_for_others", loop.get("clear_memories_for_others")),
    )
    if not isinstance(clear_others, bool):
        if clear_marker:
            _issue(
                issues,
                "loop_memory_clear_policy_invalid",
                "error",
                "world.loop.policy.preserve.clear_memories_for_others",
                "clear_memories_for_others must be true or false.",
                "Set it explicitly so non-preserved character memory is deterministic.",
            )
        else:
            _issue(
                issues,
                "loop_memory_clear_policy_missing",
                "error",
                "world.loop.policy.preserve.clear_memories_for_others",
                "The loop policy must state whether everyone else loses memories.",
                "Set clear_memories_for_others to true or false.",
            )
        clear_others = False

    end_condition = _text(policy.get("end_condition") or loop.get("end_condition"))
    if not end_condition:
        _issue(
            issues,
            "loop_end_condition_open",
            "warning",
            "world.loop.end_condition",
            "The loop can reset deterministically, but its exit condition remains open.",
            "Record the condition that ends or transforms the loop when it is established.",
        )
    victims = policy.get("victims_return_after_end", loop.get("victims_return_after_end"))
    if victims is not None and not isinstance(victims, bool):
        _issue(
            issues,
            "loop_victim_outcome_invalid",
            "error",
            "world.loop.victims_return_after_end",
            "victims_return_after_end must be true or false when specified.",
            "Use a boolean rather than prose for this consequence.",
        )
        victims = None
    elif victims is None:
        _issue(
            issues,
            "loop_victim_outcome_open",
            "warning",
            "world.loop.victims_return_after_end",
            "The post-loop outcome for people killed during a loop is not explicit.",
            "Set victims_return_after_end when that consequence is known.",
        )

    executable = (restart_ok and bool(trigger) and runtime_marker and memory_marker and clear_marker
                  and not invalid_runtime)
    return {
        "enabled": True,
        "executable": executable,
        "trigger": trigger,
        "restart": restart,
        "reset": {
            "clear_runtime": [key for key in RUNTIME_STATE_KEYS if key not in preserve_runtime],
            "preserve_runtime": preserve_runtime,
            "memory": {"preserve_for": preserve_memories, "clear_for_others": clear_others},
        },
        "end_condition": end_condition,
        "victims_return_after_end": victims,
    }


def _restart_state(value: Any, opening: dict[str, Any], slots: list[str], locations: dict[str, Any],
                   issues: list[dict[str, str]]) -> tuple[dict[str, Any] | None, bool]:
    if isinstance(value, str):
        restart = value.strip()
        if restart.lower() == "opening":
            return deepcopy(opening["state"]), True
        # Authoring models naturally say `restart: ferry` when the loop resumes
        # at the opening ferry.  It is deterministic precisely when that label
        # resolves to the already-complete opening state; accept this shorthand
        # without accepting unrelated prose as a custom reset.
        resolved = _resolve_location(restart, locations)
        if resolved["id"] and resolved["id"] == opening["state"]["location"]:
            return deepcopy(opening["state"]), True
    if not isinstance(value, Mapping):
        _issue(
            issues,
            "loop_restart_missing",
            "error",
            "world.loop.policy.restart",
            "A loop policy must say exactly where and when the reset resumes.",
            "Set restart to 'opening' or an object with time, location, and present.",
        )
        return None, False
    raw = dict(value)
    # A custom restart must be complete: filling omitted keys from the opening
    # would hide exactly the ambiguity the compiler is meant to expose.
    missing = [key for key in ("time", "location", "present") if key not in raw]
    if missing:
        _issue(
            issues,
            "loop_restart_incomplete",
            "error",
            "world.loop.policy.restart",
            f"Custom loop restart omits: {', '.join(missing)}.",
            "Use restart: 'opening' or provide time, location, and present explicitly.",
        )
        return None, False
    time = _normalise_slot(raw.get("time"), slots)
    location = _resolve_location(raw.get("location"), locations)
    present = _unique(_strings(raw.get("present")))
    if not time or not location["id"] or not present:
        _issue(
            issues,
            "loop_restart_invalid",
            "error",
            "world.loop.policy.restart",
            f"Custom loop restart needs a valid time ({', '.join(slots)}), location, and present values.",
            "Use restart: 'opening' if the loop always restarts at the opening snapshot.",
        )
        return None, False
    return {
        "time": time,
        "location": location["id"],
        "present": present,
        "public_facts": [],
        "flags": {},
        "active_scene": None,
        "turns": [],
        "scene_memories": [],
        "character_memories": {},
    }, True
