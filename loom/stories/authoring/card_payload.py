"""Safe, display-ready Story-card payloads for authoring clients.

The persisted story document intentionally stores character *keys*.  That is
right for runtime joins, but it is a poor authoring surface: an interviewer
cannot tell that ``shuri_7`` is Shuri, and the card reads like implementation
data.  This adapter adds a small ephemeral display index without changing the
stored Story schema.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ..visibility import model_view


# Generic card agents are allowed to understand the public situation, but not
# the author/director machine that sits behind a playable scene.  The focused
# Director and the explicit arcs editor have their own protected endpoints for
# that material.
_MODEL_EVENT_FIELDS = ("id", "when", "location", "participants", "visible", "hook", "theme", "tone", "roles")
_MODEL_PLAN_FIELDS = ("objective", "opening_time", "opening_location", "opening_present")
_MODEL_ENTITY_FIELDS = ("description",)
_MODEL_LOOP_FIELDS = ("start", "reset", "memory", "returner", "end_condition")


def _entity_character(card: dict[str, Any]) -> dict[str, Any] | None:
    """Collapse the story entity into one character-shaped authoring record.

    Runtime storage remains backward compatible: identity, activity windows,
    and scene gates still live in the structures that enforce them.  Authoring
    clients should not have to reconstruct that distribution, though, so this
    projection gives them one stable object to select and discuss.
    """
    world = card.get("world") if isinstance(card.get("world"), dict) else {}
    entity = world.get("entity") if isinstance(world.get("entity"), dict) else None
    if not entity or not any(value not in (None, "", [], {}) for value in entity.values()):
        return None

    time_system = card.get("time_system") if isinstance(card.get("time_system"), dict) else {}
    periods = [deepcopy(item) for item in (time_system.get("entity_periods") or []) if isinstance(item, dict)]
    fields = card.get("fields") if isinstance(card.get("fields"), dict) else {}
    first_day = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), dict) else {}
    scene_ids: list[str] = []
    for event in first_day.get("events") or []:
        if not isinstance(event, dict):
            continue
        knowledge = event.get("knowledge") if isinstance(event.get("knowledge"), dict) else {}
        if event.get("entity_action") is True or event.get("entity_period") or event.get("entity_periods") or knowledge.get("entity"):
            event_id = str(event.get("id") or "").strip()
            if event_id and event_id not in scene_ids:
                scene_ids.append(event_id)

    name = str(entity.get("name") or entity.get("title") or entity.get("label") or "Entity").strip()
    return {
        "character": "__story_entity__",
        "name": name,
        "role": str(entity.get("role") or entity.get("type") or "Story entity"),
        "core": str(entity.get("description") or ""),
        "personality": str(entity.get("tactic") or ""),
        "background": str(entity.get("limitations") or ""),
        "connection": str(entity.get("objective") or ""),
        "knowledge": deepcopy(entity.get("knowledge")),
        "schedule": periods,
        "scene_ids": scene_ids,
        "private": True,
    }


def public_story_card(ctx: Any, key: str, card: dict[str, Any]) -> dict[str, Any]:
    """Return a UI-safe card with character display details.

    The executable runtime contract and the private arc design can contain
    hidden director actions, knowledge gates, or a character's unacknowledged
    truth.  They are always removed here, matching the public story endpoint.
    ``arc_outline`` is a deliberately structural, narrator-safe indication
    that an arc exists: only its public theme and cast owner.
    ``author_arc_outline`` is a separate compact projection for this authoring
    card: it contains a storyline's title and public dramatic question, but no
    private character pressure. ``model_story_card`` removes it before any
    generic model receives the card.
    ``cast_details`` is derived data only; the persisted roster remains the
    compact list of stable character keys.
    """
    result = deepcopy(card or {})
    fields = dict(result.get("fields") or {})
    contract = fields.pop("runtime_scenario", None)
    # The interview transcript is author-only working context.  It can quote
    # a private arc truth verbatim, so it must not ride along with the card
    # projection used by generic organizer/review/develop model calls.
    fields.pop("interview_history", None)
    # Focused editorial popups persist one transcript per card item.  Those
    # transcripts are just as author-only as the legacy single interview
    # history; exposing them through the normal card would reintroduce the
    # cross-target context leak they were created to prevent.
    fields.pop("interview_histories", None)
    # The primary Architect keeps a small mission/history/pending-question
    # record so its conversation survives a page reload.  It is operational
    # authoring state, never story canon or model context; a generic co-author
    # must not mistake its own old question for an established fact.
    fields.pop("architect_state", None)
    # `arc_design` is an author/director document.  It intentionally contains
    # the parts a narration model must not know (wounds, blind spots, private
    # pressure, revelations).  Never let a stale persisted outline bypass the
    # same boundary; derive a safe one afresh instead.
    arc_design = fields.pop("arc_design", None)
    fields.pop("arc_outline", None)
    fields.pop("author_arc_outline", None)
    if isinstance(contract, dict):
        fields["runtime_ready"] = bool(contract.get("ready"))
    if arc_design is not None:
        try:
            from .arc_design import arc_public_outline, author_arc_outline

            outline = arc_public_outline(arc_design)
            author_outline = author_arc_outline(arc_design)
        except Exception:  # noqa: BLE001 - a malformed private draft must not leak or break the card
            outline = {}
            author_outline = {}
        if outline:
            fields["arc_outline"] = outline
        if author_outline:
            fields["author_arc_outline"] = author_outline
    result["fields"] = fields

    character_cores = fields.get("character_cores") if isinstance(fields.get("character_cores"), dict) else {}
    # `character_wounds` is private author material (a concrete ordinary backstory), the
    # same privacy tier as arc_design's truth/blind_spot — surfaced here for the author UI
    # only; `model_story_card` pops it before any generic model call.
    character_wounds = fields.get("character_wounds") if isinstance(fields.get("character_wounds"), dict) else {}
    details: list[dict[str, str]] = []
    # Secret-tier facets (character_scaffold.FACET_TIERS) are never sent to any
    # model or client in full — the Atlas only gets to know one exists, the
    # same "sealed, count only" boundary as the Director/time_system kernel.
    try:
        from ...server.services import lorebook_store as _LS
        from ..pipeline.character_scaffold import entry_tier as _entry_tier
    except Exception:  # noqa: BLE001 - the card must still render if the lorebook store is unavailable
        _LS = None
        _entry_tier = None
    secret_facet_count = 0
    for member in result.get("cast") or []:
        character = member if isinstance(member, str) else (member.get("character") if isinstance(member, dict) else "")
        if not character:
            continue
        character = str(character)
        record = ctx.base_settings.characters.get(character)
        char_fields = (getattr(record, "fields", None) or {}) if record is not None else {}
        details.append({
            "character": character,
            "name": (getattr(record, "name", "") or character),
            "role": str(char_fields.get("role") or ""),
            "appearance": str(char_fields.get("appearance") or ""),
            "personality": str(char_fields.get("personality") or ""),
            "background": str(char_fields.get("background") or ""),
            "connection": str(char_fields.get("connection") or ""),
            # The compact core is story-owned because it can describe the
            # player too; expose the matching public entry here only as
            # convenient display data for card clients.
            "core": str(character_cores.get(character) or ""),
            "wound": str(character_wounds.get(character) or ""),
        })
        if _LS is not None:
            try:
                scope = re.sub(r"[^\w\-]+", "_", character)
                secret_facet_count += sum(1 for e in _LS.load_lorebook(ctx.root, scope) if _entry_tier(e) == "secret")
            except Exception:  # noqa: BLE001 - one bad scope must not blank the whole count
                pass
    result["cast_details"] = details
    # The entity behaves like a character in the authoring experience, but is
    # not inserted into ``cast``: doing that would expose a Director-private
    # actor to narration and ordinary character context.
    result["entity_character"] = _entity_character(result)
    result["protected_facet_count"] = secret_facet_count
    result["key"] = key
    return result


def model_story_card(ctx: Any, key: str, card: dict[str, Any]) -> dict[str, Any]:
    """Return the Story-card projection permitted to generic text models.

    The normal UI card is author-visible and can still carry a scene's
    director-side planning data.  Batch development, review, and organization
    are *not* Director operations: handing them hidden scene text, knowledge
    gates, triggers, evidence, or entity windows lets an otherwise useful
    co-author spoil the mystery in public prose.  This projection retains the
    visible scene premise and stable identities while removing those private
    mechanics before a model is called.

    ``model_view`` is applied last so author-authored ``[[hidden]]`` wrappers
    are redacted as a separate, fail-closed boundary too.
    """
    result = public_story_card(ctx, key, card)
    # This convenience record intentionally reunites Director-private fields
    # for the author UI. Generic model calls continue to use the independently
    # redacted world/time/scene projections below.
    result.pop("entity_character", None)
    fields = dict(result.get("fields") or {})
    # This browser-only authoring projection contains a storyline's title and
    # dramatic question. Generic development/review models use only the
    # topology-only ``arc_outline`` and must never get a broader arc view.
    fields.pop("author_arc_outline", None)
    # These notes are deliberately author-only even when their text does not
    # use an inline wrapper.  A generic card agent may never receive them.
    fields.pop("author_notes", None)
    # A character's concrete backstory is private author material (the same tier as
    # arc_design's truth/blind_spot) — never a generic model's context.
    fields.pop("character_wounds", None)
    # Compact cores are intentionally public model context, but malformed or
    # wrapper-marked legacy values never get a chance to masquerade as a safe
    # character surface in a generic co-author call.
    try:
        from .dramatic_kernel import DramaticKernelError, normalize_character_cores, normalize_scene_kernel

        if "character_cores" in fields:
            fields["character_cores"] = normalize_character_cores(fields["character_cores"])
    except (DramaticKernelError, TypeError, ValueError):
        fields.pop("character_cores", None)

    plan = fields.get("first_day_plan")
    if isinstance(plan, dict):
        safe_plan = {
            name: deepcopy(plan[name])
            for name in _MODEL_PLAN_FIELDS
            if name in plan
        }
        events: list[dict[str, Any]] = []
        for event in plan.get("events") or []:
            if not isinstance(event, dict):
                continue
            try:
                safe_event_source = normalize_scene_kernel(event)
            except (DramaticKernelError, TypeError, ValueError):
                # Keep the ordinary public scene surface usable while failing
                # closed on only its malformed optional kernel fields.
                safe_event_source = dict(event)
                for name in ("theme", "tone", "roles"):
                    safe_event_source.pop(name, None)
            public_event = {
                name: deepcopy(safe_event_source[name])
                for name in _MODEL_EVENT_FIELDS
                if name in safe_event_source
            }
            if public_event:
                events.append(public_event)
        if events or "events" in plan:
            safe_plan["events"] = events
        fields["first_day_plan"] = safe_plan
    result["fields"] = fields

    # World entity tactics and the executable reset policy are Director
    # knowledge too.  A generic co-author needs, at most, the public shape of
    # a threat and a compact statement that a loop exists; it never needs an
    # entity's knowledge, limitations, objective, or the exact memory/reset
    # mechanics to make an additive card proposal.
    world = result.get("world")
    if isinstance(world, dict):
        safe_world = deepcopy(world)
        entity = safe_world.get("entity")
        if isinstance(entity, dict):
            safe_entity = {
                name: deepcopy(entity[name])
                for name in _MODEL_ENTITY_FIELDS
                if name in entity
            }
            if safe_entity:
                safe_world["entity"] = safe_entity
            else:
                safe_world.pop("entity", None)
        loop = safe_world.get("loop")
        if isinstance(loop, dict):
            safe_loop = {
                name: deepcopy(loop[name])
                for name in _MODEL_LOOP_FIELDS
                if name in loop
            }
            if safe_loop:
                safe_world["loop"] = safe_loop
            else:
                safe_world.pop("loop", None)
        result["world"] = safe_world

    # Entity activity is a Director constraint.  The generic co-author can
    # see the story's time vocabulary, but not which windows enable which
    # action.  It can safely propose an initial schedule when one is absent;
    # the application layer will never overwrite an existing schedule.
    time_system = result.get("time_system")
    if isinstance(time_system, dict):
        result["time_system"] = ({"slots": deepcopy(time_system["slots"])}
                                 if "slots" in time_system else {})

    return model_view(result)
