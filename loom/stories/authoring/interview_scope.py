"""Typed targets and isolated transcripts for editorial story conversations.

The original interview was one long, story-wide conversation.  That is useful
for a blank-card interview, but it is actively harmful once the author can
click a particular scene, person, relationship, or rule: a discussion about a
scene must not silently become context for a character arc.  This module keeps
the target vocabulary small, validates it against the saved card, and gives
each target a stable persistence key.

Nothing in here grants write authority.  The normal focused interview PATCH
boundary remains responsible for that.  A target is a context and history
boundary first, which lets clients safely open tiny editors for individual
pieces of a card.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any, Mapping

from ..visibility import model_view


class EditorialTargetError(ValueError):
    """A client requested a target that is malformed or absent from the card."""


SECTIONS = frozenset({"overview", "world", "premise", "first_day", "time_system", "cast", "arcs"})

# Item types are deliberately tied to a card section.  A random free-form id
# must never manufacture a transcript namespace or trick the model into
# believing it is editing an unrelated record.
ITEM_SECTION: dict[str, str] = {
    "location": "world",
    "world_rule": "world",
    # Notes and readiness checks are contextual sub-documents rather than
    # canonical Story fields, so they may be attached to more than one
    # section.  ``*`` is checked explicitly during normalization.
    "author_note": "*",
    "scene": "first_day",
    "entity_period": "time_system",
    "character": "cast",
    "relationship": "cast",
    "arc": "arcs",
    "theme": "arcs",
    "knowledge": "cast",
    "readiness": "*",
    "opening_state": "premise",
}

_KIND_ALIASES = {
    "entity-period": "entity_period",
    "entity period": "entity_period",
    "period": "entity_period",
    "character-card": "character",
    "character card": "character",
    "relation": "relationship",
    "bond": "relationship",
    "event": "scene",
    "day_one_scene": "scene",
    "world-rule": "world_rule",
    "world rule": "world_rule",
    "note": "author_note",
    "author-note": "author_note",
    "author note": "author_note",
    "readiness_issue": "readiness",
    "readiness-issue": "readiness",
    "opening": "opening_state",
    "opening-state": "opening_state",
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_NOTE_ID = re.compile(r"^(?:note-)?(?P<index>\d+)$")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _kind(value: Any) -> str:
    kind = _text(value).lower().replace("/", "_")
    kind = _KIND_ALIASES.get(kind, kind)
    if kind not in ITEM_SECTION:
        allowed = ", ".join(sorted(ITEM_SECTION))
        raise EditorialTargetError(f"unknown editorial item kind '{kind}'; use one of: {allowed}")
    return kind


def _relationship_key(item: Mapping[str, Any]) -> str:
    """Return the stable fallback used when an old relationship has no id."""
    source = _text(item.get("source"))
    target = _text(item.get("target"))
    return f"{source}--{target}" if source and target else ""


def _candidate_id(value: Any, *, fallback: str) -> str:
    """Produce a safe stable id for legacy UI records with no native id."""
    text = _text(value)
    if _ID.fullmatch(text):
        return text
    # A readable slug alone can collide (two messages both called "missing").
    # The short digest keeps a target/history key stable without persisting
    # arbitrary UI labels or punctuation as a database mapping key.
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:72] or fallback
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _matches(item_id: str, *candidates: Any) -> bool:
    """Match the caller's current/legacy id or the canonical persisted one."""
    target = _text(item_id)
    return any(target == _text(candidate) or target == _candidate_id(candidate, fallback="item")
               for candidate in candidates if _text(candidate))


def _readiness_records(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive current readiness records without persisting UI-only metadata."""
    try:
        from ..runtime.scenario_compiler import compile_authored_scenario

        compiled = compile_authored_scenario(dict(card))
    except Exception:  # a malformed partial card still has its section editor
        return []
    records = []
    for index, issue in enumerate(compiled.get("issues") or []):
        if not isinstance(issue, Mapping):
            continue
        item = dict(issue)
        raw_id = _text(item.get("id")) or _text(item.get("code")) or _text(item.get("path")) or _text(item.get("message"))
        item["id"] = _candidate_id(raw_id or f"readiness-{index}", fallback=f"readiness-{index}")
        records.append(item)
    return records


def _find_item(card: Mapping[str, Any], *, kind: str, item_id: str) -> tuple[str, dict[str, Any]]:
    """Find an existing item and return its canonical id plus safe item view.

    Several shipped cards predate stable ids on scenes, periods, relationships,
    and arcs.  Their UI fallbacks (``scene-0``, ``period-0``,
    ``source--target``) are accepted and normalized here instead of forcing a
    migration before the author can click them.
    """
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    if kind == "location":
        for index, raw in enumerate(card.get("locations") or []):
            value = dict(raw) if isinstance(raw, Mapping) else {"name": str(raw or "")}
            stable = _text(value.get("id")) or f"location-{index}"
            if _matches(item_id, stable, value.get("name"), f"location-{index}"):
                value.setdefault("id", stable)
                return _candidate_id(stable, fallback=f"location-{index}"), deepcopy(value)
    elif kind == "scene":
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        # Match the same derived identifiers the scenario compiler emits into
        # ``scene_catalog``.  DirectorTimeline renders those compiler ids, so
        # an id-less authored event must still round-trip from its timeline
        # card back to this source event.
        try:
            from ..runtime.scenario_compiler import _slug as compiler_slug
        except Exception:  # pragma: no cover - compiler lives in this package
            compiler_slug = lambda value, *, fallback: fallback  # type: ignore[assignment]
        used_scene_ids: set[str] = set()
        for index, raw in enumerate(plan.get("events") or []):
            value = dict(raw) if isinstance(raw, Mapping) else {"visible": str(raw or "")}
            authored_id = _text(value.get("id")) or _text(value.get("scene_id"))
            base = authored_id or compiler_slug(
                _text(value.get("title")) or _text(value.get("event")) or _text(value.get("visible")),
                fallback=f"day-1-event-{index + 1}",
            )
            stable = base
            suffix = 2
            while stable in used_scene_ids:
                stable = f"{base}-{suffix}"
                suffix += 1
            used_scene_ids.add(stable)
            if _matches(item_id, stable, authored_id, value.get("event"), value.get("title"), value.get("visible"), f"scene-{index}"):
                canonical = _candidate_id(stable, fallback=f"scene-{index}")
                value.setdefault("id", canonical)
                return canonical, deepcopy(value)
    elif kind == "entity_period":
        schedule = card.get("time_system") if isinstance(card.get("time_system"), Mapping) else {}
        if _matches(item_id, "entity-periods"):
            return "entity-periods", {"id": "entity-periods", "entity_periods": deepcopy(schedule.get("entity_periods") or [])}
        used_period_ids: set[str] = set()
        for index, raw in enumerate(schedule.get("entity_periods") or []):
            value = dict(raw) if isinstance(raw, Mapping) else {"state": str(raw or "")}
            # Match ``_entity_periods`` in the compiler.  Its generated ids
            # are one-based and it disambiguates duplicate authored ids, while
            # older card widgets used zero-based ``period-N`` aliases.
            base = _text(value.get("id")) or f"entity-period-{index + 1}"
            stable = base
            if stable in used_period_ids:
                stable = f"{base}-{index + 1}"
            used_period_ids.add(stable)
            if _matches(item_id, stable, value.get("id"), value.get("name"), f"period-{index}",
                        f"entity-period-{index}", f"entity-period-{index + 1}"):
                canonical = _candidate_id(stable, fallback=f"period-{index}")
                value.setdefault("id", canonical)
                return canonical, deepcopy(value)
    elif kind == "character":
        details = card.get("cast_details") if isinstance(card.get("cast_details"), list) else []
        details_by_key = {
            _text(detail.get("character") or detail.get("key")): detail
            for detail in details if isinstance(detail, Mapping)
        }
        for index, raw in enumerate(card.get("cast") or []):
            value = dict(raw) if isinstance(raw, Mapping) else {"character": _text(raw)}
            stable = _text(value.get("character")) or _text(value.get("key")) or f"character-{index}"
            if _matches(item_id, stable, value.get("name"), f"character-{index}"):
                value.setdefault("character", stable)
                detail = details_by_key.get(stable)
                if isinstance(detail, Mapping):
                    value = {**dict(detail), **value}
                return _candidate_id(stable, fallback=f"character-{index}"), deepcopy(value)
    elif kind == "relationship":
        for index, raw in enumerate(card.get("relationships") or []):
            if not isinstance(raw, Mapping):
                continue
            value = dict(raw)
            pair = _relationship_key(value)
            stable = _text(value.get("id")) or pair or f"relationship-{index}"
            if _matches(item_id, stable, pair, f"relationship-{index}"):
                canonical = _candidate_id(stable, fallback=f"relationship-{index}")
                value.setdefault("id", canonical)
                return canonical, deepcopy(value)
    elif kind == "arc":
        candidates: list[Any] = []
        design = fields.get("arc_design") if isinstance(fields.get("arc_design"), Mapping) else {}
        outline = fields.get("arc_outline") if isinstance(fields.get("arc_outline"), Mapping) else {}
        candidates.extend(design.get("arcs") or [])
        candidates.extend(outline.get("arcs") or [])
        for index, raw in enumerate(candidates):
            if not isinstance(raw, Mapping):
                continue
            value = dict(raw)
            stable = _text(value.get("id")) or _text(value.get("owner")) or _text(value.get("character")) or f"arc-{index}"
            if _matches(item_id, stable, value.get("owner"), value.get("character"), f"arc-{index}", f"character-arc-{index}"):
                canonical = _candidate_id(stable, fallback=f"arc-{index}")
                value.setdefault("id", canonical)
                return canonical, deepcopy(value)
    elif kind == "theme":
        design = fields.get("arc_design") if isinstance(fields.get("arc_design"), Mapping) else {}
        outline = fields.get("arc_outline") if isinstance(fields.get("arc_outline"), Mapping) else {}
        themes = list(design.get("themes") or outline.get("themes") or [])
        # A theme root is also the valid "add a theme" target on a partial card.
        if _matches(item_id, "theme"):
            return "theme", {"id": "theme", "themes": deepcopy(themes)}
        if _text(item_id).lower().startswith("theme-tag-") and themes:
            canonical = _candidate_id(item_id, fallback="theme-tag")
            return canonical, {"id": canonical, "themes": deepcopy(themes)}
        for index, raw in enumerate(themes):
            value = dict(raw) if isinstance(raw, Mapping) else {"label": str(raw or "")}
            stable = _text(value.get("id")) or _text(value.get("label")) or f"theme-{index}"
            if _matches(item_id, stable, value.get("label"), f"theme-{index}"):
                canonical = _candidate_id(stable, fallback=f"theme-{index}")
                value.setdefault("id", canonical)
                return canonical, deepcopy(value)
    elif kind == "world_rule":
        world = card.get("world") if isinstance(card.get("world"), Mapping) else {}
        if item_id in world:
            return item_id, {"id": item_id, "value": deepcopy(world[item_id])}
        if item_id == "player_abilities" and isinstance(fields.get("player_abilities"), list):
            return item_id, {"id": item_id, "value": deepcopy(fields["player_abilities"])}
    elif kind == "author_note":
        # Author notes are intentionally never inserted into a model slice.
        # They are indexed only, so the author can keep a focused UI thread
        # without the private note turning into narrator/interviewer context.
        notes = fields.get("author_notes") if isinstance(fields.get("author_notes"), list) else []
        match = _NOTE_ID.fullmatch(item_id)
        if match is not None:
            index = int(match.group("index"))
            if 0 <= index < len(notes):
                return f"note-{index}", {"id": f"note-{index}", "visibility": "author-only"}
        for index, note in enumerate(notes):
            if _text(note) == _text(item_id) or _matches(item_id, f"author-note-{index}", f"note-{index}"):
                return f"note-{index}", {"id": f"note-{index}", "visibility": "author-only"}
        questions = fields.get("open_questions") if isinstance(fields.get("open_questions"), list) else []
        for index, question in enumerate(questions):
            if _text(question) == _text(item_id) or _matches(item_id, f"open-question-{index}"):
                return f"open-question-{index}", {"id": f"open-question-{index}", "visibility": "author-visible"}
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        notes = plan.get("notes") if isinstance(plan.get("notes"), list) else []
        if notes and _matches(item_id, "first-day-note", f"first-day-note-{len(notes) - 1}"):
            return "first-day-note", {"id": "first-day-note", "visibility": "author-visible"}
    elif kind == "opening_state":
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        if _matches(item_id, "opening-state", "premise", "opening") and (card.get("premise") or plan):
            return "opening-state", {"id": "opening-state", "premise": card.get("premise") or "", "opening": {
                key: deepcopy(plan.get(key)) for key in ("opening_time", "opening_location", "opening_present") if key in plan
            }}
    elif kind == "knowledge":
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        has_knowledge = any(isinstance(event, Mapping) and event.get("knowledge") for event in (plan.get("events") or []))
        # Director preview knowledge entries have historically lacked stable
        # source ids.  Permit a namespaced target only when the card actually
        # contains a knowledge gate; the model receives no invented fact.
        if _matches(item_id, "knowledge"):
            return "knowledge", {"id": "knowledge", "visibility": "knowledge-gated", "configured": has_knowledge}
        if has_knowledge and _text(item_id):
            canonical = _candidate_id(item_id, fallback="knowledge")
            return canonical, {"id": canonical, "visibility": "knowledge-gated"}
    elif kind == "readiness":
        for issue in _readiness_records(card):
            raw_candidates = (issue.get("id"), issue.get("code"), issue.get("path"), issue.get("message"))
            if _matches(item_id, *raw_candidates):
                return str(issue["id"]), deepcopy(issue)
    raise EditorialTargetError(f"{kind} '{item_id}' does not exist on this story card")


def normalize_editorial_target(
    value: Any,
    *,
    card: Mapping[str, Any],
    fallback_section: str = "world",
) -> dict[str, Any]:
    """Validate a section/item target and return its canonical wire shape.

    Supported request forms are deliberately forgiving at the edge::

        {"section": "cast"}
        {"section": "cast", "item": {"kind": "character", "id": "shuri"}}
        {"section": "cast", "item_kind": "character", "item_id": "shuri"}

    The returned representation is always ``{section, key, item?}``, where
    ``key`` is safe to use as a mapping key in ``fields.interview_histories``.
    Item targets must already exist.  Creating something new remains a
    section-level operation, which avoids a phantom record/history namespace.
    """
    fallback = _text(fallback_section).lower() or "world"
    if fallback not in SECTIONS:
        fallback = "world"
    if value is None:
        source: dict[str, Any] = {"section": fallback}
    elif isinstance(value, str):
        source = {"section": value}
    elif isinstance(value, Mapping):
        source = dict(value)
    else:
        raise EditorialTargetError("editorial target must be an object or section name")

    section = _text(source.get("section") or source.get("focus") or fallback).lower()
    if section not in SECTIONS:
        raise EditorialTargetError(f"unknown editorial section '{section}'")

    raw_item = source.get("item")
    if raw_item is None and (source.get("item_kind") is not None or source.get("item_id") is not None):
        raw_item = {"kind": source.get("item_kind"), "id": source.get("item_id")}
    if raw_item in (None, "", "section"):
        return {"section": section, "key": section}
    if not isinstance(raw_item, Mapping):
        raise EditorialTargetError("editorial target.item must be an object with kind and id")

    item_source = dict(raw_item)
    raw_kind = item_source.get("kind") or item_source.get("type")
    if _text(raw_kind).lower() == "section":
        return {"section": section, "key": section}
    kind = _kind(raw_kind)
    expected_section = ITEM_SECTION[kind]
    if expected_section != "*" and expected_section != section:
        raise EditorialTargetError(f"{kind} belongs to {expected_section}, not {section}")
    raw_id = _text(item_source.get("id") or item_source.get("key"))
    if not raw_id:
        raise EditorialTargetError("item id must not be empty")
    if len(raw_id) > 256 or "\x00" in raw_id:
        raise EditorialTargetError("item id is invalid")
    # Old cards frequently use a scene title, character display name, or
    # compiler path as the UI id.  Resolve every incoming spelling against a
    # real card item first, then return a safe canonical key; never use the
    # caller string itself as persistence authority.
    item_id = raw_id
    canonical_id, _ = _find_item(card, kind=kind, item_id=item_id)
    item = {"kind": kind, "id": canonical_id}
    # A field target is a further context/history partition inside one real
    # item, e.g. scene ``arrival`` + field ``public_surface``.  Fields may be
    # introduced by an edit, so only validate their transport-safe spelling;
    # do not require that the field already has a value.
    field = _text(item_source.get("field"))
    if field:
        if len(field) > 128:
            raise EditorialTargetError("item field is too long")
        # Normal card properties use the readable spelling (``public_surface``).
        # Legacy UI detail ids can be a display name (an ability called
        # "Time Rewind"), so canonicalize just those instead of rejecting a
        # valid click target or putting arbitrary punctuation in the key.
        field = field if _FIELD.fullmatch(field) else _candidate_id(field, fallback="field")
        item["field"] = field
    key = f"{section}:{kind}:{canonical_id}"
    if field:
        key = f"{key}:field:{field}"
    return {"section": section, "item": item, "key": key}


def scoped_history(fields: Mapping[str, Any], target: Mapping[str, Any]) -> list[dict[str, str]]:
    """Read one target's persisted transcript (never fall through to another)."""
    from .interview import normalize_interview_messages

    histories = fields.get("interview_histories") if isinstance(fields, Mapping) else None
    if not isinstance(histories, Mapping):
        return []
    return normalize_interview_messages(histories.get(target.get("key")) or [])


def persist_scoped_history(
    fields: Mapping[str, Any], target: Mapping[str, Any], history: list[dict[str, Any]],
    *, limit: int = 24,
) -> dict[str, Any]:
    """Return fields with exactly this target's normalized history replaced."""
    from .interview import normalize_interview_messages

    updated = dict(fields or {})
    previous = updated.get("interview_histories")
    histories = dict(previous) if isinstance(previous, Mapping) else {}
    histories[str(target["key"])] = normalize_interview_messages(history)[-limit:]
    updated["interview_histories"] = histories
    return updated


def target_item(card: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any] | None:
    """Get the narrow model-safe view for a normalized target.

    A scene popup is an ordinary editorial conversation, not a Director
    console.  It may edit the public offer, but it must not receive a hidden
    action, evidence, knowledge gate, or entity schedule just because those
    fields happen to sit beside the visible scene in one stored object.  The
    interview route preserves those fields server-side when it merges a
    focused scene patch back into canonical state.
    """
    item = target.get("item") if isinstance(target, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    kind = str(item["kind"])
    _canonical, value = _find_item(card, kind=kind, item_id=str(item["id"]))
    if kind == "scene":
        public_fields = ("id", "title", "event", "when", "location", "participants", "visible", "public_surface", "hook")
        value = {name: deepcopy(value[name]) for name in public_fields if name in value}
    return model_view(value)


def target_snapshot(
    card: Mapping[str, Any], target: Mapping[str, Any], *, section_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the model-safe card context for one editor window.

    A section editor receives its existing section snapshot.  An item editor
    receives the selected item and only the small pieces necessary to connect
    it to the card; it never receives the rest of the section by accident.
    """
    if not target.get("item"):
        return model_view(deepcopy(dict(section_snapshot)))

    item_target = target["item"]
    kind = item_target["kind"]
    full_item = target_item(card, target)
    item = full_item
    selected_field = _text(item_target.get("field"))
    if selected_field and isinstance(full_item, Mapping):
        # ``world_rule`` is represented as ``{id, value}`` so a field-level
        # world edit should traverse the rule's value, not that wrapper.
        value: Any = full_item.get("value") if kind == "world_rule" else full_item
        for part in selected_field.split("."):
            if isinstance(value, Mapping):
                if part in value:
                    value = value.get(part)
                else:
                    value = next((entry for key, entry in value.items()
                                  if part == _candidate_id(key, fallback="field")), None)
            elif isinstance(value, list):
                # Player abilities and a few older card collections are lists
                # of records.  A clicked ability id/name is a legitimate
                # field selector even though it is not a numeric array index.
                if part.isdigit() and int(part) < len(value):
                    value = value[int(part)]
                else:
                    value = next((entry for entry in value if isinstance(entry, Mapping)
                                  and part in {_text(entry.get("id")), _text(entry.get("key")),
                                               _text(entry.get("name")), _text(entry.get("label")),
                                               _candidate_id(entry.get("id"), fallback="item"),
                                               _candidate_id(entry.get("key"), fallback="item"),
                                               _candidate_id(entry.get("name"), fallback="item"),
                                               _candidate_id(entry.get("label"), fallback="item")}), None)
            else:
                value = None
        # Keeping just the named field prevents an edit to a scene's public
        # surface from inheriting private scene prose as casual context.
        item = {"id": item.get("id") or item_target.get("id"), "field": selected_field, "value": deepcopy(value)}
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    result: dict[str, Any] = {"target": deepcopy(dict(target)), "item": item}
    if kind == "location":
        result["opening"] = {"start": card.get("start") or "", "premise": card.get("premise") or ""}
    elif kind == "world_rule":
        result["world_context"] = {"setting": (card.get("world") or {}).get("setting") or "", "premise": card.get("premise") or ""}
    elif kind == "author_note":
        result["instruction"] = "This is an author-only note. Its content is deliberately unavailable to models. Ask the author what they want to change."
    elif kind == "scene":
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        participants = set((full_item or {}).get("participants") or [])
        result["day_context"] = {"objective": plan.get("objective") or "", "opening_time": plan.get("opening_time") or "", "opening_location": plan.get("opening_location") or ""}
        # A public scene editor may need the story's coarse time vocabulary,
        # but not a director schedule's capabilities or constraints.  Those
        # fields can reveal how the threat works; handing them to the ordinary
        # interviewer would defeat the public-scene redaction above.
        result["time_slots"] = [
            str(slot) for slot in ((card.get("time_system") or {}).get("slots") or [])
            if isinstance(slot, str) and slot.strip()
        ]
        result["participants"] = [deepcopy(member) for member in (section_snapshot.get("cast") or []) if isinstance(member, Mapping) and member.get("character") in participants]
    elif kind == "entity_period":
        period_id = (full_item or {}).get("id")
        plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
        result["affected_scenes"] = [
            {key: value for key, value in event.items() if key in {"id", "when", "location", "entity_period", "entity_action", "trigger", "visible"}}
            for event in (plan.get("events") or [])
            if isinstance(event, Mapping) and event.get("entity_period") == period_id
        ]
    elif kind == "character":
        char_key = (full_item or {}).get("character")
        # The persisted Story roster is intentionally compact (usually just a
        # character key).  The interview route supplies display/card facts in
        # its section snapshot, so preserve that useful target-local context
        # without widening the conversation to every cast member.
        result["character_card"] = next(
            (deepcopy(member) for member in (section_snapshot.get("cast") or [])
             if isinstance(member, Mapping) and member.get("character") == char_key),
            None,
        )
        result["relationships"] = [deepcopy(rel) for rel in (card.get("relationships") or []) if isinstance(rel, Mapping) and char_key in {rel.get("source"), rel.get("target")}]
        result["opening"] = {"premise": card.get("premise") or ""}
    elif kind == "relationship":
        endpoints = {(full_item or {}).get("source"), (full_item or {}).get("target")}
        result["people"] = [deepcopy(member) for member in (section_snapshot.get("cast") or []) if isinstance(member, Mapping) and member.get("character") in endpoints]
        result["opening"] = {"premise": card.get("premise") or ""}
    elif kind == "arc":
        owner = (full_item or {}).get("owner")
        cast = section_snapshot.get("cast") or []
        result["owner"] = next((deepcopy(member) for member in cast if isinstance(member, Mapping) and member.get("character") == owner), None)
        result["themes"] = [deepcopy(theme) for theme in ((fields.get("arc_design") or {}).get("themes") or []) if isinstance(theme, Mapping) and theme.get("id") == (full_item or {}).get("theme_id")]
    return model_view(result)


def target_label(card: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    """Readable, non-secret label for UI/API responses and model instructions."""
    item = target.get("item") if isinstance(target, Mapping) else None
    if not isinstance(item, Mapping):
        return str(target.get("section") or "story section").replace("_", " ").title()
    kind, item_id = str(item.get("kind")), str(item.get("id"))
    if kind == "author_note":
        return f"Private author note {item_id.removeprefix('note-')}"
    try:
        found = target_item(card, target) or {}
    except EditorialTargetError:
        found = {}
    display = _text(found.get("name")) or _text(found.get("title")) or _text(found.get("id")) or item_id
    label = f"{kind.replace('_', ' ').title()}: {display}"
    field = _text(item.get("field"))
    return f"{label} · {field}" if field else label


def editorial_suggestions(card: Mapping[str, Any], target: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return a tiny deterministic menu for a focused editorial popup.

    Suggestions deliberately remain data, not model prose, so an unavailable
    model never leaves an empty editor.  A client can send ``prompt`` verbatim
    as the next author message.
    """
    item = target.get("item") if isinstance(target, Mapping) else None
    kind = item.get("kind") if isinstance(item, Mapping) else "section"
    label = target_label(card, target)
    templates: dict[str, list[tuple[str, str]]] = {
        "scene": [("Clarify the visible beat", "What does the player actually see and choose in this scene?"), ("Add a trigger", "What specifically causes this scene to change or fire?"), ("Leave evidence", "What trace should this scene leave for a later scene?")],
        "character": [("Sharpen the want", "What does this person want right now, in this story?"), ("Add resistance", "What makes this person resist the easy or obvious response?"), ("Ground the connection", "What concrete shared history changes how they treat the protagonist?")],
        "relationship": [("Name the tension", "What does each side want from this relationship that the other cannot easily give?"), ("Show it on the surface", "What small behavior makes this bond visible before anyone explains it?")],
        "arc": [("Find the blind spot", "What can this character not yet acknowledge about the theme?"), ("Add pressure", "What scene could pressure that blind spot without resolving it too early?"), ("Protect the resistance", "How does their protective strategy make an easy breakthrough impossible?")],
        "theme": [("State the human question", "What difficult human question should this theme keep asking?"), ("Give it a pressure", "What recurring pressure lets the theme appear in scenes rather than a label?"), ("Attach a person", "Which character is most changed by this theme, and why?")],
        "entity_period": [("Set a hard constraint", "What can the entity do in this window, and what can it not do?"), ("Connect a scene", "Which scene is changed by this activity window and how?"), ("Make the cost legible", "What evidence or consequence reveals this window to the player?")],
        "location": [("Make it playable", "What can happen here that cannot happen anywhere else?"), ("Add a sensory anchor", "What detail makes this place immediately recognizable on the page?"), ("Connect it", "Which character or scene gives this place emotional weight?")],
        "world_rule": [("State the rule", "What precisely is true here, and what exception or limit keeps it from solving everything?"), ("Show the consequence", "How does this rule visibly affect an ordinary scene?"), ("Create evidence", "What could let the player discover this rule rather than be told it?")],
        "author_note": [("Refine privately", "Describe what you want to change about this private note without exposing it to the model."), ("Connect the consequence", "What public consequence should this private fact eventually create?")],
        "opening_state": [("Sharpen the first image", "What is the first concrete image or action the player should encounter?"), ("Name the disruption", "What is already slightly wrong before the plot makes itself known?"), ("Ground the player", "Where are they, who is present, and what do they want in this moment?")],
        "knowledge": [("Assign an observer", "Who can know this fact directly, and who cannot?"), ("Choose the evidence", "What public evidence could make this knowledge discoverable?"), ("Set a boundary", "What must remain unknowable until a later scene or trigger?")],
        "readiness": [("Resolve the decision", "What concrete canon decision would satisfy this readiness issue?"), ("Place the evidence", "Which card section should establish the missing evidence or constraint?"), ("Keep it playable", "What is the smallest specific change that lets play begin without guessing?")],
        "section": [("Find the missing decision", f"What is the single most important unresolved decision in {label}?"), ("Improve the story pressure", f"What could make {label} create a more specific problem for the protagonist?"), ("Check continuity", f"What in {label} needs to line up with the rest of the card?")],
    }
    choices = templates.get(str(kind), templates["section"])
    return [
        {"id": f"{target['key']}:suggestion:{index + 1}", "kind": "prompt", "title": title, "prompt": prompt}
        for index, (title, prompt) in enumerate(choices[:3])
    ]


__all__ = [
    "EditorialTargetError",
    "ITEM_SECTION",
    "SECTIONS",
    "editorial_suggestions",
    "normalize_editorial_target",
    "persist_scoped_history",
    "scoped_history",
    "target_label",
    "target_snapshot",
]
