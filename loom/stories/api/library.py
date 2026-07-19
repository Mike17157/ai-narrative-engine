"""Story library HTTP endpoints.

Public paths are retained for backwards compatibility.
"""

from __future__ import annotations

from __future__ import annotations

import base64
from copy import deepcopy
import json
import re

import yaml
from fastapi.responses import FileResponse, JSONResponse

from ...config.schema import ModelDef
from ...providers.base import ModelRequestTimeout
from ...server.services import config_files
from ...server.services.config_files import STORY_BUILDER_DEFAULT
from ...server.services.images import _clean_reference_png, _randomize_seeds, _render
from ...server.services.jobs_util import _start_stream_job
from ...server.services.prompts import FEATURES_SCHEMA, PLAY_SCHEMA, _assemble_base_prompt
from ..pipeline import apply_manifest as _apply_manifest, plan_and_apply as _plan_and_apply
# The story pipeline runs on pydantic-graph state machines (see graph_pipeline.py).
from ..authoring.pipeline_graph import StoryState, StoryDeps, run_turn, run_draft
from ..authoring.card_payload import model_story_card, public_story_card
from ..authoring.inline_text import InlineTextEditError, apply_inline_text_edit, parse_inline_text_edit


_READINESS_SECTION = {
    "start": "premise",
    "locations": "world",
    "world": "world",
    "time_system": "time_system",
    "fields.first_day_plan": "first_day",
    "fields.opening": "first_day",
    "fields.loop_policy": "world",
    "fields.player_abilities": "world",
    "fields.arc_design": "arcs",
    "cast": "cast",
}


def _section_for_readiness_path(path: str) -> str:
    for prefix, section in _READINESS_SECTION.items():
        if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
            return section
    return "world"


def _play_readiness(card: dict) -> dict:
    """Small UI/API report around the pure compiler; never expose director secrets."""
    from ..runtime.scenario_compiler import compile_authored_scenario

    compiled = compile_authored_scenario(card)
    issues = [
        {**issue, "section": _section_for_readiness_path(str(issue.get("path") or ""))}
        for issue in (compiled.get("issues") or [])
        if isinstance(issue, dict)
    ]
    return {
        "ready": bool(compiled.get("ready")),
        "status": (card.get("fields") or {}).get("status", "interviewing"),
        "blockers": [issue for issue in issues if issue.get("severity") == "error"],
        "warnings": [issue for issue in issues if issue.get("severity") != "error"],
        "opening": compiled.get("opening") if compiled.get("ready") else None,
        # Kept server-side by activate/play.  The UI only needs the safe report.
        "_contract": compiled,
    }


def _public_architect_model_card(ctx, key: str, card: dict) -> dict:
    """Build the strict browser/model snapshot for one public Architect pass.

    ``model_story_card`` is intentionally a broad projection for legacy model
    features.  It can retain unrelated top-level story documents, though, and
    those are not inputs to the thin browser capability.  Start with its
    wrapper redaction and derived cast display names, then copy only the
    public fields this bounded worker actually needs.  Keeping this projection
    local prevents a future generic-card addition from silently becoming a
    browser or public-worker capability.
    """
    source = model_story_card(ctx, key, card)
    result: dict = {}

    for field in ("key", "name", "type", "premise", "tone", "start"):
        value = source.get(field)
        if isinstance(value, str):
            result[field] = deepcopy(value)

    raw_world = source.get("world")
    if isinstance(raw_world, dict):
        world = {
            field: deepcopy(raw_world[field])
            for field in ("genre", "setting", "atmosphere", "history", "customs", "technology")
            if isinstance(raw_world.get(field), str)
        }
        if world:
            result["world"] = world

    locations: list[dict] = []
    for raw_location in source.get("locations") or []:
        if not isinstance(raw_location, dict) or not isinstance(raw_location.get("id"), str):
            continue
        location = {
            field: deepcopy(raw_location[field])
            for field in ("id", "name", "description")
            if isinstance(raw_location.get(field), str)
        }
        locations.append(location)
    if locations:
        result["locations"] = locations

    cast: list[dict] = []
    for raw_member in source.get("cast") or []:
        if isinstance(raw_member, str):
            member = {"character": raw_member}
        elif isinstance(raw_member, dict) and isinstance(raw_member.get("character"), str):
            member = {"character": deepcopy(raw_member["character"])}
            if isinstance(raw_member.get("primary"), bool):
                member["primary"] = raw_member["primary"]
            for field in ("home", "outfit"):
                if isinstance(raw_member.get(field), str):
                    member[field] = deepcopy(raw_member[field])
        else:
            continue
        cast.append(member)
    if cast:
        result["cast"] = cast

    # `model_story_card` derives this useful name index, but its generic shape
    # can include an author-only character wound.  Copy the public character
    # surface explicitly rather than forwarding the record wholesale.
    cast_details: list[dict] = []
    for raw_detail in source.get("cast_details") or []:
        if not isinstance(raw_detail, dict) or not isinstance(raw_detail.get("character"), str):
            continue
        detail = {
            field: deepcopy(raw_detail[field])
            for field in ("character", "name", "role", "appearance", "personality", "connection", "core")
            if isinstance(raw_detail.get(field), str)
        }
        cast_details.append(detail)
    if cast_details:
        result["cast_details"] = cast_details

    raw_time_system = source.get("time_system")
    if isinstance(raw_time_system, dict):
        slots = [deepcopy(slot) for slot in (raw_time_system.get("slots") or []) if isinstance(slot, str)]
        if slots:
            result["time_system"] = {"slots": slots}

    raw_fields = source.get("fields")
    fields: dict = {}
    if isinstance(raw_fields, dict):
        raw_plan = raw_fields.get("first_day_plan")
        if isinstance(raw_plan, dict):
            plan = {
                field: deepcopy(raw_plan[field])
                for field in ("objective", "opening_time", "opening_location")
                if isinstance(raw_plan.get(field), str)
            }
            opening_present = [
                deepcopy(person) for person in (raw_plan.get("opening_present") or []) if isinstance(person, str)
            ]
            if opening_present:
                plan["opening_present"] = opening_present
            events: list[dict] = []
            for raw_event in raw_plan.get("events") or []:
                if not isinstance(raw_event, dict):
                    continue
                event = {
                    field: deepcopy(raw_event[field])
                    for field in ("id", "when", "location", "visible", "hook", "theme", "tone")
                    if isinstance(raw_event.get(field), str)
                }
                participants = [
                    deepcopy(person) for person in (raw_event.get("participants") or []) if isinstance(person, str)
                ]
                if participants:
                    event["participants"] = participants
                raw_roles = raw_event.get("roles")
                if isinstance(raw_roles, dict):
                    roles = {
                        str(person): deepcopy(role)
                        for person, role in raw_roles.items()
                        if isinstance(person, str) and isinstance(role, str)
                    }
                    if roles:
                        event["roles"] = roles
                if event:
                    events.append(event)
            if events or plan:
                plan["events"] = events
                fields["first_day_plan"] = plan
        raw_cores = raw_fields.get("character_cores")
        if isinstance(raw_cores, dict):
            cores = {
                str(character): deepcopy(core)
                for character, core in raw_cores.items()
                if isinstance(character, str) and isinstance(core, str)
            }
            if cores:
                fields["character_cores"] = cores
    if fields:
        result["fields"] = fields
    return result


def _director_preview(card: dict) -> dict:
    """Project one freshly compiled scenario for the authoring-only director UI.

    This deliberately is *not* the Story-card projection.  A director needs
    hidden event material, knowledge gates, and executable reset detail in
    order to inspect the story machine, whereas the ordinary card and narrator
    paths must never receive a compiled director contract.  The compiler is
    pure, so serving a preview cannot activate a story or persist runtime data.
    """
    from ..runtime.scenario_compiler import compile_authored_scenario

    compiled = compile_authored_scenario(card)
    issues = [
        {**issue, "section": _section_for_readiness_path(str(issue.get("path") or ""))}
        for issue in (compiled.get("issues") or [])
        if isinstance(issue, dict)
    ]
    scene_catalog = deepcopy(compiled.get("scene_catalog") or [])
    _annotate_director_scene_schedules(card, scene_catalog, compiled.get("issues") or [])
    return {
        "author_only": True,
        "readiness": {
            "ready": bool(compiled.get("ready")),
            "status": (card.get("fields") or {}).get("status", "interviewing"),
            "blockers": [issue for issue in issues if issue.get("severity") == "error"],
            "warnings": [issue for issue in issues if issue.get("severity") != "error"],
        },
        "time_slots": list(compiled.get("time_slots") or []),
        # Location choices are public card facts, but including the declared
        # ids here lets the Director offer only mutations the compiler can
        # validate.  Do not infer new locations from scene prose.
        "locations": _director_available_locations(card.get("locations")),
        "opening": compiled.get("opening") or {},
        "scene_catalog": scene_catalog,
        "director_plan": compiled.get("director_plan") or {},
        "loop_policy": compiled.get("loop_policy") or {},
        # The Director is explicitly author-only, so it can inspect the full
        # protected arc document.  The public card receives only a redacted
        # outline via ``public_story_card``.
        "arc_design": _director_arc_design(card),
    }


def _director_arc_design(card: dict) -> dict:
    """Return the normalized private arc design plus Director-friendly indexes.

    A malformed draft is diagnostic data, not a reason to expose it through the
    public card or to make the rest of the Director preview fail.  This endpoint
    is the sole API boundary that deliberately includes a character's hidden
    contradiction and possible recognition for the story author.
    """
    raw = (card.get("fields") or {}).get("arc_design")
    empty = {
        "configured": False,
        "valid": True,
        "themes": [],
        "arcs": [],
        "character_threads": [],
        "scene_beats": [],
        "issues": [],
    }
    if raw is None:
        return empty
    try:
        from ..authoring.arc_design import (
            ArcDesignValidationError,
            arc_design_issues,
            normalize_arc_design,
        )

        design = normalize_arc_design(raw, card=card)
    except (ArcDesignValidationError, TypeError, ValueError) as exc:
        return {
            **empty,
            "configured": True,
            "valid": False,
            "issues": [{
                "code": "arc_design_invalid",
                "severity": "error",
                "path": "fields.arc_design",
                "message": str(exc) or "The private arc design is not valid yet.",
                "fix": "Repair the theme-and-arc document before relying on it for scene pressure.",
            }],
        }

    themes = [item for item in (design.get("themes") or []) if isinstance(item, dict)]
    arcs = [item for item in (design.get("arcs") or []) if isinstance(item, dict)]
    theme_by_id = {str(theme.get("id") or ""): theme for theme in themes if theme.get("id")}
    character_threads: list[dict] = []
    scene_beats: list[dict] = []
    for arc in arcs:
        theme = theme_by_id.get(str(arc.get("theme_id") or ""), {})
        shared = {
            "arc_id": str(arc.get("id") or ""),
            "arc_title": str(arc.get("title") or ""),
            "theme_id": str(arc.get("theme_id") or ""),
            "theme": str(theme.get("label") or ""),
        }
        for thread in arc.get("character_threads") or []:
            if isinstance(thread, dict):
                character_threads.append({**shared, **deepcopy(thread)})
        for point in arc.get("turning_points") or []:
            if isinstance(point, dict):
                scene_beats.append({**shared, **deepcopy(point)})
    issues = arc_design_issues(design, card=card)
    return {
        "configured": True,
        "valid": not any(issue.get("severity") == "error" for issue in issues if isinstance(issue, dict)),
        "themes": deepcopy(themes),
        "arcs": deepcopy(arcs),
        "character_threads": character_threads,
        "scene_beats": scene_beats,
        "issues": deepcopy(issues),
    }


def _authored_schedule_value(event: dict) -> object:
    """Read time fields with the compiler's same explicit-key precedence.

    In particular, a present-but-empty ``slots`` field remains an incomplete
    authored schedule; it must not silently fall through to ``when``.  That is
    how :func:`compile_authored_scenario` treats legacy cards too.
    """
    for field in ("slots", "when", "time"):
        if field in event:
            return event[field]
    return None


def _has_authored_schedule(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(isinstance(item, str) and item.strip() for item in value)
    # An object or scalar was explicitly supplied, but the compiler will flag
    # it as invalid instead of pretending the scene is simply unplaced.
    return value is not None


def _annotate_director_scene_schedules(card: dict, catalog: list[dict], issues: list[dict]) -> None:
    """Mark compiler-fallback slots so the director board can render honestly.

    The runtime intentionally gives a legacy unscheduled scene an opening-slot
    fallback so its shape stays executable enough to inspect.  That fallback
    is misleading in an authoring timeline.  Keep the compiled ``slots``
    unchanged, but annotate the preview with:

    - ``time_explicit`` — whether the author actually supplied a time value;
    - ``schedule_state`` — ``scheduled``, ``unplaced``, or ``invalid``.

    The metadata is preview-only and does not alter the compiler or the stored
    scenario contract.
    """
    plan = (card.get("fields") or {}).get("first_day_plan") or {}
    raw_events = plan.get("events") if isinstance(plan, dict) else []
    if not isinstance(raw_events, list):
        raw_events = []

    time_issue_indexes: set[int] = set()
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("code") not in {
            "scene_time_missing", "scene_time_unknown",
        }:
            continue
        match = re.fullmatch(r"fields\.first_day_plan\.events\[(\d+)\]\.when", str(issue.get("path") or ""))
        if match:
            time_issue_indexes.add(int(match.group(1)))

    # ``_event_specs`` emits exactly one catalog scene for every object event,
    # while ignoring non-object list entries.  Mirror that order rather than
    # joining by id, because legacy cards may have blank or duplicate ids.
    scene_index = 0
    for event_index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            continue
        if scene_index >= len(catalog):
            break
        scene = catalog[scene_index]
        scene_index += 1
        supplied = _has_authored_schedule(_authored_schedule_value(raw_event))
        scene["time_explicit"] = supplied
        if not supplied:
            scene["schedule_state"] = "unplaced"
        elif event_index in time_issue_indexes:
            scene["schedule_state"] = "invalid"
        else:
            scene["schedule_state"] = "scheduled"

    # Defensive defaults preserve a stable view even if a future compiler
    # changes catalog/event alignment.
    for scene in catalog[scene_index:]:
        scene.setdefault("time_explicit", False)
        scene.setdefault("schedule_state", "unplaced")


def _director_scene_event_index(events: list[object], catalog: list[dict], scene_id: str) -> int | None:
    """Resolve a Director scene id back to its authored event list index.

    The compiler intentionally gives even legacy events without an ``id`` a
    stable derived scene id, and disambiguates duplicate authored ids.  The
    Director UI renders those compiled ids, so a schedule edit must resolve
    through the catalog rather than assuming the persisted event id is unique.
    ``_event_specs`` emits one catalog scene for each object event in source
    order; mirroring that rule makes the mapping deterministic.
    """
    object_event_indexes = [index for index, event in enumerate(events) if isinstance(event, dict)]
    for event_index, scene in zip(object_event_indexes, catalog):
        if str(scene.get("id") or "") == scene_id:
            return event_index
    return None


def _director_target_slot(value: object, slots: list[str]) -> str | None:
    """Normalize one Director drag target to a configured runtime slot.

    ``None`` means intentionally unplace the scene.  Regular values use the
    compiler's own alias rules (for example, ``dusk`` maps to ``evening``), so
    a drag-and-drop edit cannot create a schedule the compiler reads
    differently from the authoring UI.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("slot must be a non-empty string or null")
    from ..runtime.scenario_compiler import _normalise_slot

    slot = _normalise_slot(value, slots)
    if not slot:
        raise ValueError(f"unknown time slot '{value}'; use one of: {', '.join(slots)}")
    return slot


def _director_available_locations(raw_locations: object) -> list[dict[str, str]]:
    """Return declared location choices in the same stable order as the card."""
    from ..runtime.scenario_compiler import _location_index

    locations = _location_index(raw_locations)
    return [
        {"id": ident, "label": str(label)}
        for ident, label in locations.get("labels", {}).items()
    ]


def _director_target_location(value: object, raw_locations: object) -> tuple[str, list[dict[str, str]]]:
    """Resolve a Director location move to a declared location id only.

    The scenario compiler can intentionally *derive* a location from prose so
    it can explain a malformed card.  A direct manipulation must not create
    more of that ambiguity: the Director can move a scene only to a location
    the story card already declares.  Returning the choices makes failures
    actionable without exposing any private director material.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("location must be a declared location id or name")
    from ..runtime.scenario_compiler import _location_index, _resolve_location

    locations = _location_index(raw_locations)
    available = _director_available_locations(raw_locations)
    if not available:
        raise ValueError("the story has no declared locations; add one to the story card before moving a scene")
    resolved = _resolve_location(value, locations)
    if not resolved.get("id") or resolved.get("source") != "declared":
        raise ValueError(
            f"unknown location '{value}'; use one of: "
            + ", ".join(entry["id"] for entry in available)
        )
    return str(resolved["id"]), available


def register(app, ctx):
    # This identity is deliberately closure-private: HTTP JSON can never
    # manufacture it.  Protected Director work is therefore callable only
    # from the Architect's server-side hand-off, never by adding an
    # internal-looking field to the public interview payload.
    _architect_interview_capability = object()

    def _public_architect_work_order(body: dict):
        """Derive the sole capability available to browser orchestration.

        A browser can choose a canonical public section and give the worker a
        brief, but it cannot select a role, turn off the visibility boundary,
        or forward a previously serialized work order as a credential.  This
        keeps the thin capability API useful without reopening the protected
        Architect/Director routes that remain server-owned.
        """
        from ..authoring.starter_set import DevelopError
        from ..authoring.story_control_graph import resolve_architect_work_order

        forbidden = {
            name for name in (
                "mode", "worker", "specialist", "protected", "public_only", "work_order",
                "model", "use_fallback",
            )
            if name in body
        }
        if forbidden:
            return JSONResponse({
                "error": "public Architect requests cannot select a worker, visibility mode, or model route",
            }, status_code=400)
        try:
            public_scopes = {"world", "premise", "cast", "first_day"}
            raw_scope = body.get("scope")
            if not isinstance(raw_scope, str) or raw_scope not in public_scopes:
                raise DevelopError(
                    "public Architect scopes may include only world, premise, cast, and first_day",
                )
            # This capability deliberately accepts only canonical section ids:
            # aliases and batches are legacy UX conveniences, not authority.
            scopes = {raw_scope}
            work_order = resolve_architect_work_order(
                scopes, mode="develop", public_only=True,
            )
        except (DevelopError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return scopes, work_order

    def _public_architect_stale_revision(revision: str):
        """Give the browser a deterministic refresh target after a card race."""
        return JSONResponse({
            "error": "the Story card changed; refresh the public Architect context and try again",
            "retryable": True,
            "revision": revision,
        }, status_code=409)

    def _public_architect_scoped_proposal(proposal: dict) -> dict:
        """Drop proposal fields that are not owned by a single card section.

        ``open_questions`` is intentionally global working state in the legacy
        batch developer, so ``apply_develop_proposal`` writes it regardless of
        selected scopes.  A browser-owned *single-section* pass must not gain
        that cross-section write capability.  Removing it for both model and
        browser-provided proposals makes the exact scope boundary explicit and
        keeps the returned proposal safe to replay at commit time.
        """
        scoped = deepcopy(proposal)
        scoped.pop("open_questions", None)
        return scoped

    @app.post("/api/stories/new")
    def create_empty_story(body: dict):
        """Mint the empty card used by the conversation-first authoring UI.

        This is a library lifecycle operation, not the retired genesis wizard:
        it creates no draft graph, generated cast, or model-authored canon.
        """
        body = body or {}
        name = str(body.get("name") or "Untitled story").strip() or "Untitled story"
        type_ = body.get("type") if body.get("type") in {"novel", "vn"} else "novel"
        key = ctx.create_story(name, {"fields": {"status": "interviewing"}}, character_keys=[], type_=type_)
        return {"ok": True, "key": key}

    @app.post("/api/stories/from-cast")
    def create_story_from_cast(body: dict):
        """Create an interviewing card that references existing characters.

        Character import still offers this lifecycle shortcut. It deliberately
        does not generate a seed, arcs, scenes, or any other model-authored
        material from the retired creation subsystem.
        """
        body = body or {}
        requested_keys = [key for key in (body.get("characters") or []) if isinstance(key, str)]
        character_keys = [
            key for key in dict.fromkeys(requested_keys)
            if key in ctx.base_settings.characters
        ]
        if not character_keys:
            return JSONResponse({"error": "no valid characters"}, status_code=400)
        fallback_name = ctx.base_settings.characters[character_keys[0]].name or "Story"
        name = str(body.get("name") or fallback_name).strip() or fallback_name
        premise = str(body.get("premise") or "").strip()
        key = ctx.create_story(
            name,
            {
                "premise": premise,
                "cast": [{"character": character_key} for character_key in character_keys],
                "fields": {"status": "interviewing"},
            },
            character_keys=character_keys,
        )
        return {"ok": True, "key": key}

    @app.get("/api/stories")
    def list_stories() -> list:
        out = []
        from ...server.services import story_store as _SS
        updated = _SS.story_updated_map(ctx.root)
        for k, st in ctx.base_settings.stories.items():
            mtime = updated.get(k, 0.0)
            out.append({"key": k, "name": st.name, "premise": st.premise, "tone": st.tone,
                        "themes": st.themes, "locations": len(st.locations), "start": st.start,
                        "cast": [m.character for m in st.cast], "_mtime": mtime})
        out.sort(key=lambda s: s["_mtime"], reverse=True)  # newest first
        for s in out:
            s.pop("_mtime", None)
        return out

    @app.get("/api/stories/{key}")
    def get_story(key: str):
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return public_story_card(ctx, key, st.model_dump())

    @app.get("/api/stories/{key}/control-graph")
    def story_control_graph(key: str):
        """Return the safe graph that drives Story control-room navigation."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..authoring.story_control_graph import build_story_control_graph
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        return {"key": key, "graph": build_story_control_graph(raw)}

    @app.get("/api/stories/{key}/interview-history")
    def story_interview_history(key: str, section: str = "", item_kind: str = "",
                                item_id: str = "", field: str = ""):
        """Return the author-only interview transcript outside the Story card.

        The transcript is needed to resume the author's conversation after a
        refresh, but it is not safe context for generic card operations: an
        author may have stated a character's private truth there before the
        arc document turns it into a gated Director fact.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..authoring.interview import normalize_interview_messages
        from ..authoring.interview_scope import (EditorialTargetError,
                                                 editorial_suggestions,
                                                 normalize_editorial_target,
                                                 scoped_history)

        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        fields = raw.get("fields") if isinstance(raw, dict) else {}
        scoped = any((section, item_kind, item_id, field))
        if not scoped:
            # Compatibility for the old single-pane interview.  New popup
            # clients always provide a target and never read another target's
            # history through this fallback.
            return {"history": normalize_interview_messages(
                (fields or {}).get("interview_history") or []
            )}
        target_body: dict[str, object] = {"section": section or "world"}
        if item_kind or item_id or field:
            target_body["item"] = {"kind": item_kind, "id": item_id, "field": field}
        try:
            target = normalize_editorial_target(target_body, card=raw, fallback_section=section or "world")
        except EditorialTargetError as exc:
            return JSONResponse({"error": f"invalid editorial target: {exc}"}, status_code=400)
        history = scoped_history(fields or {}, target)
        suggestions = editorial_suggestions(raw, target)
        return {
            "target": target,
            "history": history,
            "suggestions": suggestions,
            "conversation": {"target": target, "history": history, "suggestions": suggestions},
        }

    @app.put("/api/stories/{key}")
    def update_story(key: str, body: dict):
        """Edit a saved story in place (iterate). Updates only the fields sent; cast members are
        existing character keys, so no NPCs are re-created. Routed through update_story_fields so it
        lands in the story's DB (or legacy YAML) + validates."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        fields = {f: body[f] for f in (
            "name", "type", "premise", "tone", "themes", "art_style", "premise_parts", "conditions",
            "storyboard", "cast", "lorebook", "locations", "start", "background", "fields",
            "arcs", "chapters", "scenes", "features", "start_scene", "world", "time_system",
            "relationships", "connections", "default_personas", "recent_window")
            if f in (body or {})}
        # Structural edits made outside the interview change the executable
        # contract too.  Demote an active card before writing so the next play
        # request cannot use an old compiled snapshot.
        scenario_inputs = {"premise", "world", "time_system", "locations", "start", "cast",
                           "relationships", "connections", "conditions", "scenes", "features",
                           "start_scene", "fields"}
        if scenario_inputs & set(fields):
            try:
                raw = ctx._read_story_data(key)
                current_fields = dict(raw.get("fields") or {})
                if current_fields.get("status") == "active":
                    current_fields.update(fields.get("fields") or {})
                    current_fields["status"] = "interviewing"
                    current_fields.pop("runtime_scenario", None)
                    fields["fields"] = current_fields
            except FileNotFoundError:
                pass
        try:
            ctx.update_story_fields(key, fields)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.patch("/api/stories/{key}/inline-text")
    def update_story_inline_text(key: str, body: dict):
        """Update one explicitly authorable text leaf without replacing its container.

        The normal public card is a projection and deliberately omits private
        authoring state. Accepting a whole projected ``fields`` object here
        would erase that state, so inline edits are resolved against the raw
        persisted document and restricted to known prose leaves.
        """
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            path, value = parse_inline_text_edit(
                body.get("path") if isinstance(body, dict) else None,
                body.get("value") if isinstance(body, dict) else None,
            )
            raw = ctx._read_story_data(key)
            root = apply_inline_text_edit(raw, path, value)
            ctx.update_story_fields(key, {root: raw.get(root), **({"fields": raw.get("fields")} if root != "fields" and root != "name" else {})})
        except InlineTextEditError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404 if exc.code == "not_found" else 400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        latest = ctx._read_story_data(key)
        return {"ok": True, "story": public_story_card(ctx, key, latest)}

    @app.get("/api/stories/{key}/play-readiness")
    def story_play_readiness(key: str):
        """Report exactly what an interview card still needs before live play.

        This is intentionally model-free: it validates only explicit authored
        constraints, so the button never becomes a guess that prose happens to
        be enough to run.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        report = _play_readiness(raw)
        report.pop("_contract", None)
        return report

    @app.get("/api/stories/{key}/card/gaps")
    def story_card_gaps(key: str):
        """Return a deterministic, read-only next-action plan for this card.

        Unlike the prose card review, this endpoint makes no model call and
        never proposes canon.  It reports only structural connections that are
        missing or not yet executable (cast ↔ scenes ↔ bonds ↔ knowledge ↔
        arcs), making it safe for an agent loop to observe before asking the
        author one focused question.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        from ..authoring.gap_analysis import analyze_story_card

        return {"key": key, **analyze_story_card(raw)}

    @app.get("/api/stories/{key}/director-preview")
    def story_director_preview(key: str):
        """Return the private compiled plan for the story author alone.

        It is calculated from the current authored card on every request.  It
        is not saved into ``fields.runtime_scenario`` (activation owns that
        snapshot), and it is never mixed into the public Story-card response.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        return {"key": key, **_director_preview(raw)}

    @app.put("/api/stories/{key}/director/scenes/{scene_id}/schedule")
    def move_director_scene_to_slot(key: str, scene_id: str, body: dict):
        """Move one authored Day One possibility to a single time-slot lane.

        The board sends the compiled Director scene id and ``{"slot": "..."}``.
        This endpoint translates that id back to the source event, replacing
        any old ``slots``/``when``/``time`` shape with the canonical ``when``
        value.  It deliberately allows a move that leaves other readiness
        problems (for example, an incompatible entity window) visible: moving
        the card is an authoring operation, not permission to silently rewrite
        the rest of the story machine.  ``{"slot": null}`` is the explicit
        counterpart for dropping a scene back into the Unplaced tray.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if not isinstance(body, dict) or "slot" not in body:
            return JSONResponse({"error": "provide a target slot"}, status_code=400)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        raw = deepcopy(raw)
        fields = dict(raw.get("fields") or {})
        plan = fields.get("first_day_plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("events"), list):
            return JSONResponse({"error": "the story has no editable Day One events"}, status_code=400)
        events = list(plan["events"])

        # Resolve through the compiler so the endpoint accepts exactly the ids
        # the Director board rendered, including derived or de-duplicated ids.
        from ..runtime.scenario_compiler import compile_authored_scenario

        compiled = compile_authored_scenario(raw)
        catalog = [scene for scene in (compiled.get("scene_catalog") or []) if isinstance(scene, dict)]
        source_index = _director_scene_event_index(events, catalog, scene_id)
        if source_index is None:
            return JSONResponse({"error": f"no editable Day One scene named '{scene_id}'"}, status_code=404)
        try:
            target_slot = _director_target_slot(body.get("slot"), list(compiled.get("time_slots") or []))
        except ValueError as exc:
            return JSONResponse({
                "error": str(exc),
                "available_slots": list(compiled.get("time_slots") or []),
            }, status_code=400)

        event = dict(events[source_index])
        # A timeline lane means one explicit gate.  Remove every legacy shape
        # before writing the canonical field so precedence cannot preserve an
        # old schedule behind the value the author just dragged into place.
        for schedule_field in ("slots", "when", "time"):
            event.pop(schedule_field, None)
        if target_slot is not None:
            event["when"] = target_slot
        events[source_index] = event
        updated_plan = dict(plan)
        updated_plan["events"] = events
        fields["first_day_plan"] = updated_plan
        # A changed possibility timeline invalidates an active runtime
        # snapshot.  The author must explicitly reactivate after reviewing the
        # newly compiled gates; play can never start from stale timing.
        if fields.get("status") == "active":
            fields["status"] = "interviewing"
            fields.pop("runtime_scenario", None)
        raw["fields"] = fields
        try:
            ctx.update_story_fields(key, {"fields": fields})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save scene schedule: {exc}"}, status_code=400)

        preview = {"key": key, **_director_preview(raw)}
        return {
            "ok": True,
            "key": key,
            "scene_id": scene_id,
            "slot": target_slot,
            "preview": preview,
            "story": public_story_card(ctx, key, raw),
        }

    @app.put("/api/stories/{key}/director/scenes/{scene_id}/placement")
    def move_director_scene_placement(key: str, scene_id: str, body: dict):
        """Apply an explicit location and/or time placement to one Day One scene.

        Unlike the focused ``/schedule`` endpoint, this route is the auditable
        mutation boundary for a location move.  It accepts either
        ``{"location": "declared-location-id"}``, ``{"slot": "night"}``,
        or both fields together.  Omitted fields are deliberately left alone;
        the endpoint never infers a location from prose, moves participants,
        or changes entity gates as a side effect.  A location is always written
        as the canonical declared location id, and a supplied slot is always
        written as the canonical ``when`` field.
        """
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if not isinstance(body, dict) or not any(field in body for field in ("slot", "location")):
            return JSONResponse({"error": "provide a target slot and/or declared location"}, status_code=400)
        unknown = set(body) - {"slot", "location"}
        if unknown:
            return JSONResponse({"error": f"unsupported placement fields: {', '.join(sorted(unknown))}"}, status_code=400)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        raw = deepcopy(raw)
        fields = dict(raw.get("fields") or {})
        plan = fields.get("first_day_plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("events"), list):
            return JSONResponse({"error": "the story has no editable Day One events"}, status_code=400)
        events = list(plan["events"])

        # Resolve through the same compiler the Director preview uses.  This
        # handles derived and de-duplicated ids without conflating them with
        # potentially duplicate persisted event ids.
        from ..runtime.scenario_compiler import compile_authored_scenario

        compiled = compile_authored_scenario(raw)
        catalog = [scene for scene in (compiled.get("scene_catalog") or []) if isinstance(scene, dict)]
        source_index = _director_scene_event_index(events, catalog, scene_id)
        if source_index is None:
            return JSONResponse({"error": f"no editable Day One scene named '{scene_id}'"}, status_code=404)

        changes: dict[str, object] = {}
        available_slots = list(compiled.get("time_slots") or [])
        available_locations: list[dict[str, str]] = []
        try:
            if "slot" in body:
                changes["slot"] = _director_target_slot(body.get("slot"), available_slots)
            if "location" in body:
                target_location, available_locations = _director_target_location(body.get("location"), raw.get("locations"))
                changes["location"] = target_location
        except ValueError as exc:
            # Preserve the exact allowed choices in the failure so a UI can
            # recover without guessing or issuing a second hidden request.
            if not available_locations:
                available_locations = _director_available_locations(raw.get("locations"))
            return JSONResponse({
                "error": str(exc),
                "available_slots": available_slots,
                "available_locations": available_locations,
            }, status_code=400)

        event = dict(events[source_index])
        if "slot" in changes:
            # A time lane means one explicit gate.  Remove every legacy shape
            # before writing ``when`` so precedence cannot preserve a hidden
            # older schedule behind the author-visible placement.
            for schedule_field in ("slots", "when", "time"):
                event.pop(schedule_field, None)
            if changes["slot"] is not None:
                event["when"] = changes["slot"]
        if "location" in changes:
            # The compiler accepts legacy ``place`` but direct manipulation
            # writes only the canonical field so the event stays auditable.
            event.pop("location", None)
            event.pop("place", None)
            event["location"] = changes["location"]
        events[source_index] = event
        updated_plan = dict(plan)
        updated_plan["events"] = events
        fields["first_day_plan"] = updated_plan
        if fields.get("status") == "active":
            fields["status"] = "interviewing"
            fields.pop("runtime_scenario", None)
        raw["fields"] = fields
        try:
            ctx.update_story_fields(key, {"fields": fields})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save scene placement: {exc}"}, status_code=400)

        preview = {"key": key, **_director_preview(raw)}
        return {
            "ok": True,
            "key": key,
            "scene_id": scene_id,
            "changes": changes,
            "preview": preview,
            "story": public_story_card(ctx, key, raw),
        }

    @app.post("/api/stories/{key}/activate")
    def activate_story_play(key: str):
        """Commit a validated runtime contract and make an interview story playable."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        report = _play_readiness(raw)
        contract = report.pop("_contract")
        if not report["ready"]:
            return JSONResponse({"error": "story is not ready for live play", "readiness": report},
                                status_code=409)
        fields = dict(raw.get("fields") or {})
        fields["status"] = "active"
        fields["runtime_scenario"] = contract
        try:
            ctx.update_story_fields(key, {"fields": fields})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not activate story: {exc}"}, status_code=400)
        report["status"] = "active"
        return {"ok": True, "readiness": report}

    @app.post("/api/stories/{key}/card/organize")
    async def organize_story_card(key: str, body: dict):
        """Consolidate the real card without inventing or changing cast identity."""
        from ..authoring.interview import apply_patch
        from ..visibility import preserve_model_hidden

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        # Card authoring uses one named route.  A gateway model id must not be
        # sent through an unrelated active provider just because the user
        # changed their chat connection.
        provider, model_route = ctx.story_agent_provider(body)
        if provider is None:
            return JSONResponse({"error": model_route.get("error") or "no Story Agent model configured",
                                 "model_route": model_route}, status_code=400)
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["world", "premise", "time_system", "fields"],
                  "properties": {
                      "world": {"type": "object", "additionalProperties": False,
                                "required": ["genre", "setting", "atmosphere", "history", "customs", "technology", "background", "loop", "entity"],
                                "properties": {
                                    "genre": {"type": "string"}, "setting": {"type": "string"},
                                    "atmosphere": {"type": "string"}, "history": {"type": "string"},
                                    "customs": {"type": "string"}, "technology": {"type": "string"},
                                    "background": {"type": "string"},
                                    "loop": {"type": "object", "additionalProperties": False,
                                             "required": ["start", "reset", "memory", "returner"],
                                             "properties": {"start": {"type": "string"}, "reset": {"type": "string"},
                                                            "memory": {"type": "string"}, "returner": {"type": "string"}}},
                                    "entity": {"type": "object", "additionalProperties": False,
                                               "required": ["description", "knowledge", "limitations", "tactic", "objective"],
                                               "properties": {"description": {"type": "string"}, "knowledge": {"type": "string"},
                                                              "limitations": {"type": "string"}, "tactic": {"type": "string"},
                                                              "objective": {"type": "string"}}}}},
                      "premise": {"type": "string"},
                      "time_system": {"type": "object", "required": ["entity_periods"],
                                      "properties": {"slots": {"type": "array", "items": {"type": "string"}},
                                                     "entity_periods": {"type": "array", "items": {"type": "object"}}}},
                      "fields": {"type": "object", "additionalProperties": False, "required": ["first_day_plan", "open_questions"],
                                 "properties": {"first_day_plan": {"type": "object", "required": ["objective", "events"],
                                                                     "properties": {"objective": {"type": "string"}, "events": {"type": "array", "items": {"type": "object"}}}},
                                                "open_questions": {"type": "array", "items": {"type": "string"}}}}}}
        system = """You are a meticulous story editor. Reorganize the supplied Story card into concise, internally consistent canon. Preserve every established fact; do not add plot, resolve open questions, or change cast identities, character keys, or relationships.

The requested world object is a REPLACEMENT, not a patch. Organize its public material by role instead of leaving it as one undifferentiated lore dump: `setting` is the present-day place and social reality; `atmosphere` is the felt everyday texture; `history` is established island or community past; `customs` is recurring ordinary practice or tradition; `technology` fixes the material era; `background` is only any remaining current public context that belongs in none of those sections. Use concise readable prose, never keyword lists. Put a named playable site in locations, not in a world paragraph. Keep loop and entity rules in their own nested fields.

Day One is a flexible set of possible playable encounters, not a list of general facts. Preserve an established scene only as an observable moment with a place, time, people or a meaningful absence, and immediate pressure; do not invent a new encounter merely to disguise lore. Preserve any established public scene `theme`, `tone`, and per-participant `roles` as compact scene context rather than collapsing them into lore. Historical facts belong in `world.history` unless the card already establishes them as a clue someone can encounter. Return the requested structured object only."""
        # The organizer is a generic prose editor, not the author-only
        # Director.  Give it the same safe card projection that the Story UI
        # receives so a private recognition cannot be paraphrased into public
        # premise/world prose during a cosmetic reorganization.
        prompt = f"STORY CARD TO ORGANIZE:\n{json.dumps(model_story_card(ctx, key, raw), ensure_ascii=False)}"
        try:
            from ..authoring.card_graph import run_card_operation

            patch = await run_card_operation(
                operation="organize_card", provider=provider, system=system, prompt=prompt, schema=schema
            )
            if not patch:
                return JSONResponse({"error": "organizer returned no card changes"}, status_code=502)
            if isinstance(patch.get("fields"), dict):
                patch["fields"] = {k: v for k, v in patch["fields"].items()
                                   if k in {"first_day_plan", "open_questions"}}
            updated = apply_patch(raw, patch, "interview")
            # Organization is a normalization pass: unlike conversational
            # edits, its canonical world object REPLACES old extraction keys.
            # A shallow merge here would leave the unreadable raw key dump alive.
            updated["world"] = patch["world"]
            # The organizer only received a redacted card projection.  Keep
            # any source-only spans in place after it rewrites the public prose
            # so a cosmetic pass can never delete an author note it could not
            # see.
            updated = preserve_model_hidden(raw, updated)
            if (updated.get("fields") or {}).get("status") == "active":
                updated["fields"] = dict(updated["fields"])
                updated["fields"]["status"] = "interviewing"
                updated["fields"].pop("runtime_scenario", None)
            ctx.update_story_fields(key, {k: updated[k] for k in ("world", "premise", "time_system", "fields") if k in updated})
        except ValueError as exc:
            return JSONResponse({"error": f"invalid organizer response: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not organize card: {exc}"}, status_code=500)
        return {"ok": True, "changed": list(patch)}

    @app.post("/api/stories/{key}/card/review")
    async def review_story_card(key: str, body: dict):
        """Suggest the next highest-leverage Story-card improvements; never mutates."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        provider, model_route = ctx.story_agent_provider(body or {})
        if provider is None:
            return JSONResponse({"error": model_route.get("error") or "no Story Agent model configured",
                                 "model_route": model_route}, status_code=400)
        schema = {"type": "object", "additionalProperties": False, "required": ["suggestions"],
                  "properties": {"suggestions": {"type": "array", "minItems": 2, "maxItems": 4,
                      "items": {"type": "object", "additionalProperties": False,
                          "required": ["section", "suggestion", "reason"],
                           "properties": {"section": {"type": "string", "enum": ["world", "premise", "first_day", "time_system", "cast", "arcs"]},
                                          "suggestion": {"type": "string"}, "reason": {"type": "string"}}}}}}
        system = """You are a precise story editor. Review the supplied Story card and identify only its 2-4 highest-leverage missing decisions or weak links. Do not praise it, rewrite it, invent canon, or suggest generic craft exercises. Each suggestion must name one concrete next decision the author can make in one card section. When named people exist but the card has no thematic character pressure, prefer an arcs suggestion that asks about one specific person's sincere belief, protective pattern, limitation, or a scene that could test it—never a generic request for 'character development'."""
        try:
            from ..authoring.card_graph import run_card_operation

            out = await run_card_operation(
                operation="review_card", provider=provider, system=system,
                # Review suggestions are rendered into the ordinary Story
                # interview.  They can point to a missing arc, but must never
                # know or restate its private answer.
                prompt=f"STORY CARD:\n{json.dumps(model_story_card(ctx, key, raw), ensure_ascii=False)}", schema=schema,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"card review failed: {exc}"}, status_code=500)
        suggestions = [s for s in (out.get("suggestions") or []) if isinstance(s, dict)]
        return {"ok": True, "suggestions": suggestions}

    @app.post("/api/stories/{key}/card/develop")
    async def develop_story_card(key: str, body: dict):
        """Explicitly co-author a compact playable starting set in one pass.

        Unlike the interview's narrow incremental turn, this endpoint is an
        opt-in batch action.  The model proposes only missing material; card
        application is additive, full-aggregate validation happens before any
        write, and one relational-store save commits both new story-bound
        character records and the Story card together.
        """
        from ...server.services import story_store as story_store
        from ..authoring.card_graph import run_card_operation
        from ..authoring.completion_planner import (build_public_baseline_population_task,
                                                    sanitize_public_baseline_population)
        from ..authoring.gap_analysis import analyze_story_card
        from ..authoring.starter_set import (DevelopError, apply_develop_proposal,
                                             build_develop_prompt, develop_schema,
                                             normalize_scope, validate_candidate)
        from ..authoring.story_control_graph import (
            matches_architect_work_order,
            resolve_architect_work_order,
            work_order_prompt,
        )

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        brief = str(body.get("brief") or "").strip()
        # Completion is an autonomous convenience, not an alternate route into
        # director-only canon.  The flag reaches both the prompt and the
        # proposal-application boundary; the latter is the real guarantee.
        public_only = bool(body.get("public_only"))
        if len(brief) > 8000:
            return JSONResponse({"error": "brief is too long"}, status_code=400)
        try:
            scopes = normalize_scope(body.get("scope"))
        except DevelopError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            work_order = resolve_architect_work_order(
                scopes,
                mode="develop",
                public_only=public_only,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        supplied_work_order = body.get("work_order")
        if supplied_work_order is not None and not matches_architect_work_order(supplied_work_order, work_order):
            return JSONResponse({
                "error": "the supplied Architect work order is no longer valid; refresh the Architect plan",
            }, status_code=409)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        public_population_task = body.get("public_population_task")
        population_mode = public_population_task is not None
        if population_mode:
            # This internal-looking descriptor still crosses an HTTP boundary;
            # recompute it from the live card instead of trusting a client to
            # choose a convenient location, archetype, or observer.
            if not public_only or scopes != {"cast"} or not isinstance(public_population_task, dict):
                return JSONResponse({"error": "supporting-cast completion requires the bounded public cast task"}, status_code=400)
            expected_population_task = build_public_baseline_population_task(raw, analyze_story_card(raw))
            if expected_population_task != public_population_task:
                return JSONResponse({"error": "the supporting-cast task is no longer current; refresh the Architect plan"}, status_code=409)
        has_canon = bool(raw.get("premise") or raw.get("world") or raw.get("locations")
                         or raw.get("cast") or ((raw.get("fields") or {}).get("first_day_plan") or {}).get("events"))
        if not brief and not has_canon and (scopes & {"world", "premise"}):
            return JSONResponse({"error": "give the co-author a starting spark before building a blank card"},
                                status_code=400)
        # One bounded card-development pass shares the named Story Agent with
        # the interview/organizer/reviewer.  A deliberate body.model override
        # remains a hard override for experiments.
        provider, model_route = ctx.story_agent_provider(body)
        if provider is None:
            return JSONResponse({"error": model_route.get("error") or "no Story Agent model configured",
                                 "model_route": model_route}, status_code=400)
        try:
            proposal = await run_card_operation(
                operation=f"develop_{work_order.specialist}", provider=provider,
                system=work_order_prompt(work_order),
                # The batch co-author may create public people/scenes.  It
                # receives the public outline, not a private character's
                # hidden truth, so generated cast cannot conveniently know a
                # revelation that has not been earned in play.
                prompt=(
                    build_develop_prompt(
                        model_story_card(ctx, key, raw), brief=brief, scopes=scopes,
                        public_only=public_only,
                    )
                    + ("\n\nBOUNDED PUBLIC SUPPORTING-CAST TASK:\n"
                       "Create at most one ordinary named person for each listed role/location. "
                       "They are background support, never the protagonist, a mystery witness, "
                       "or a relationship node. Return only their name and a short public appearance.\n"
                       f"{json.dumps(public_population_task.get('archetypes') or [], ensure_ascii=False)}"
                       if population_mode else "")
                ), schema=develop_schema(),
            )
            loaded = story_store.load_story(ctx.root, key)
            if loaded is None:
                return JSONResponse({"error": "no such story"}, status_code=404)
            live_raw, embedded_characters = loaded
            if population_mode:
                # A long model call may race a direct authoring edit.  Recheck
                # the descriptor against that latest card; it is safer to ask
                # for a fresh plan than attach a person to a now-sensitive
                # location.
                live_task = build_public_baseline_population_task(live_raw, analyze_story_card(live_raw))
                if live_task != public_population_task:
                    raise DevelopError("supporting-cast task changed while the proposal was being prepared")
                existing_names = []
                for member in live_raw.get("cast") or []:
                    if not isinstance(member, dict):
                        continue
                    record = embedded_characters.get(member.get("character"))
                    name = getattr(record, "name", "") if record is not None else ""
                    if isinstance(record, dict):
                        name = record.get("name") or name
                    if isinstance(name, str) and name.strip():
                        existing_names.append(name)
                proposal = {
                    "message": str((proposal or {}).get("message") or ""),
                    **sanitize_public_baseline_population(
                        proposal, live_task, existing_names=existing_names,
                    ),
                }
            # Re-read just before construction so a long model call cannot apply
            # its proposal to an old card snapshot.  The additive merge preserves
            # edits made while the call was in flight.
            candidate = apply_develop_proposal(
                live_raw, proposal, story_key=key, embedded_characters=embedded_characters,
                known_characters=ctx.base_settings.characters, scopes=scopes,
                public_only=public_only,
            )
            # The batch developer receives a model-safe card projection.  It
            # must not erase an author-only wrapper while adding public setup.
            from ..visibility import preserve_model_hidden
            candidate.story = preserve_model_hidden(live_raw, candidate.story)
            validated = validate_candidate(candidate, ctx.base_settings.characters)
        except ModelRequestTimeout as exc:
            return JSONResponse({
                "error": str(exc),
                "retryable": True,
                "model_route": model_route,
            }, status_code=504)
        except DevelopError as exc:
            return JSONResponse({"error": f"invalid developer response: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not develop card: {exc}"}, status_code=500)

        # ``save_story`` atomically replaces the Story rows and its embedded
        # character map.  New people are never first written as global YAML
        # files, so a failed proposal cannot leave Shuri_2-style orphan cards.
        try:
            story_store.save_story(ctx.root, key, validated["story"], validated["characters"])
            ctx.reload_settings()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save developed card: {exc}"}, status_code=400)
        updated = ctx._read_story_data(key)
        readiness = _play_readiness(updated)
        readiness.pop("_contract", None)
        public = public_story_card(ctx, key, updated)
        return {
            "ok": True,
            "message": candidate.message,
            "reply": candidate.message,
            "patch": candidate.patch,
            "story": public,
            "card": public,  # small compatibility bridge for older authoring clients
            "created": {"characters": candidate.created},
            "updated_sections": candidate.updated_sections,
            "readiness": readiness,
            # Expose safe routing metadata so an authoring surface can state
            # whether the named DeepSeek route or its configured fallback
            # actually handled this pass.  It contains no credentials.
            "model_route": model_route,
            "work_order": work_order.as_dict(),
            "review": {
                "agent": work_order.reviewer,
                "status": "approved",
                "gates": list(work_order.reviewer_gates()),
            },
        }

    async def _run_story_interview(
        key: str,
        body: dict,
        *,
        _capability: object | None = None,
        _work_order: object | None = None,
    ):
        """Take one guided-authoring turn and persist its validated Story-card PATCH.

        The response is deliberately plain prose plus a fenced PATCH.  Parsing and
        validation happen here; only then do we use the normal validated story write.
        """
        from ..authoring.interview import (SECTIONS, apply_patch,
                                           normalize_interview_messages,
                                           restrict_to_existing_scene_knowledge,
                                           split_response)
        from ..authoring.interview_scope import (EditorialTargetError,
                                                 editorial_suggestions,
                                                 normalize_editorial_target,
                                                 persist_scoped_history,
                                                 scoped_history,
                                                 target_label,
                                                 target_snapshot)

        architect_handoff = _capability is _architect_interview_capability
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        legacy_focus = body.get("focus") if body.get("focus") in {"world", "premise", "first_day", "time_system", "cast", "arcs"} else "world"
        target_supplied = "target" in body
        bootstrap = bool(body.get("bootstrap"))
        # Inspection only: echoes the exact system/prompt sent and the model's raw,
        # unparsed completion alongside the normal response. Never persisted, never
        # sent to any model — a debugging aid for prompt/model iteration.
        debug = bool(body.get("debug"))
        messages = normalize_interview_messages(body.get("messages") or [])
        if not messages and not bootstrap:
            return JSONResponse({"error": "say something"}, status_code=400)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        fields = dict(raw.get("fields") or {})
        # A bounded Architect hand-off must not commit a proposal against an
        # obsolete card snapshot.  The public interview endpoint keeps its
        # existing collaborative behavior, while the coordinator gets an
        # explicit stale-result boundary before it writes.
        initial_card_revision = ""
        if architect_handoff:
            from ..authoring.story_architect import card_revision

            initial_card_revision = card_revision(raw)
        try:
            target = normalize_editorial_target(body.get("target"), card=raw, fallback_section=legacy_focus)
        except EditorialTargetError as exc:
            return JSONResponse({"error": f"invalid editorial target: {exc}"}, status_code=400)
        focus = target["section"]
        # The Architect may invoke one director-only knowledge pass.  This is
        # intentionally a narrow write capability, not a client-selected
        # shortcut around the normal Day One editor: the final patch is rebuilt
        # from the live event and can add facts only for observers the planner
        # already found on that event.
        raw_knowledge_only = body.get("knowledge_only")
        knowledge_only: dict[str, list[str]] | None = None
        if raw_knowledge_only is not None:
            if focus != "first_day" or not isinstance(raw_knowledge_only, dict):
                return JSONResponse({"error": "director knowledge assignment requires the first_day focus"}, status_code=400)
            event_ids = [str(value).strip() for value in (raw_knowledge_only.get("event_ids") or [])
                         if isinstance(value, str) and value.strip()]
            observer_ids = [str(value).strip() for value in (raw_knowledge_only.get("observer_ids") or [])
                            if isinstance(value, str) and value.strip()]
            if len(event_ids) != 1 or not observer_ids:
                return JSONResponse({"error": "director knowledge assignment requires one scene and existing observers"}, status_code=400)
            # The serialized work-order trace is intentionally not a bearer
            # credential.  Require the closure-private capability and the
            # exact server-derived object passed by the Architect in this
            # same request chain before crossing into Director knowledge.
            from ..authoring.story_control_graph import resolve_architect_work_order

            expected_order = resolve_architect_work_order(
                section="first_day", mode="director_knowledge",
            )
            if not architect_handoff or _work_order != expected_order:
                return JSONResponse({
                    "error": "director knowledge assignments are available only through an approved Architect hand-off",
                }, status_code=403)
            knowledge_only = {"event_ids": event_ids, "observer_ids": observer_ids}
        history = scoped_history(fields, target)
        # Existing stories had one undifferentiated transcript.  Keep it for
        # callers still using the legacy focus-only payload, but never import
        # it into a new target popup where it would contaminate that item's
        # discussion.
        if not target_supplied and not history and not isinstance(fields.get("interview_histories"), dict):
            history = normalize_interview_messages(fields.get("interview_history") or [])
        incoming = messages
        has_canon = bool(
            raw.get("premise") or raw.get("world") or raw.get("locations") or raw.get("start") or raw.get("cast")
            or (raw.get("time_system") or {}).get("entity_periods")
            or ((raw.get("fields") or {}).get("first_day_plan") or {}).get("events")
            or raw.get("themes") or (raw.get("fields") or {}).get("arc_design")
        )
        # A true blank story gets an immediate useful invitation without paying
        # a model round trip.  It is an assistant-only turn, so it cannot later
        # masquerade as authorial canon or contaminate interview history.
        if bootstrap and not history and not has_canon:
            reply = "Give me any starting spark—a scene, person, image, problem, or mood—and we’ll find the story in it."
            bootstrap_history = [{"role": "assistant", "text": reply}]
            fields = persist_scoped_history(fields, target, bootstrap_history)
            # Keep the one legacy read endpoint useful while old clients are
            # migrated.  It is a mirror only; targeted clients read the map.
            fields["interview_history"] = bootstrap_history
            try:
                ctx.update_story_fields(key, {"fields": fields})
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"could not start interview: {exc}"}, status_code=400)
            suggestions = editorial_suggestions(raw, target)
            conversation_payload = {"target": target, "history": bootstrap_history, "suggestions": suggestions}
            return {"ok": True, "reply": reply, "patch": {}, "target": target,
                    "history": bootstrap_history, "conversation": conversation_payload, "suggestions": suggestions,
                    "model_input": {"target": focus, "editorial_target": target, "prompt": "blank-story bootstrap"},
                    "open_questions": fields.get("open_questions") or [],
                    "status": fields.get("status", "interviewing"),
                    "story": public_story_card(ctx, key, ctx._read_story_data(key)),
                    "next_focus": "world", "next_target": target}
        # The browser sends its visible transcript while the story also persists
        # one.  They normally overlap completely; blindly concatenating them
        # doubled the model's context, made each call slower, and let repeated
        # facts look falsely important. Keep the one genuinely new suffix.
        overlap = 0
        limit = min(len(history), len(incoming))
        for size in range(limit, 0, -1):
            if history[-size:] == incoming[:size]:
                overlap = size
                break
        transcript = history + incoming[overlap:]
        transcript = transcript[-24:]
        conversation = "\n".join(f"{'Author' if m.get('role') == 'user' else 'Interviewer'}: {m.get('text', '')}"
                                 for m in transcript)
        system = """You are a sharp story interviewer, not a form-filler. Listen for the story promise in what the author has already said, commit the facts they establish, then ask ONE question whose answer would most change the story.

This is an existing interview as soon as any Story card fact or prior conversation is supplied. Never use the blank-story opener in that case, never ask the author to provide a new spark, and never discard an established fact because it is absent from the selected card slice. Follow the strongest live thread. In a mystery, pursue the rupture, the apparent explanation, what the protagonist stands to lose, what the suspicious person wants, or the clue that makes the ordinary world feel wrong. When the author gives a peaceful surface and a violent reversal, do not ask another generic setup question; ask what makes the reversal personal, strange, or impossible. Treat corrections as canon (for example, someone is at the dock rather than on the ferry). A question about a person should be motivated by the mystery or situation, not demanded as a profile form.

Ask plainly and specifically. Never present a menu, recap the process, use workshop labels, or ask for several unrelated facts at once. Preserve genuine uncertainty as open_questions; never invent canon. Characters only appear after the world and immediate situation provide context. Emit a cast item only for a named person the author has actually established; it must include name plus only facts needed now (role, appearance, personality, background, connection). ONLY the person this turn is actually about — never re-list the whole existing cast just because you can see it; SET cast with everyone already established wastes the whole response re-stating what's already true and drops the actual new content when it runs out of room. Never emit placeholder cast items or make the player an anonymous cast member. A cast item never carries `core` or `wound` — those are separate fields, below. Relationships may use those new character names as source and target. When a character has a useful public driving posture, store one short surface sentence in `fields.character_cores` keyed by `player` or that character's key/name. It must not be a hidden wound, secret, diagnosis, or promised arc result.

GROUNDING, WORLD-FIRST: whenever you write `world.history`, a location's own `history`, or `fields.character_wounds`, use one concrete paragraph — a real situation, a real pressure, a real want or block, in the register of an actual life: parents who ran a failing restaurant, pressured him to work unpaid day and night, while he yearned to do well in school for a better education. Nothing extraordinary, no genre element, and never a separate abstract "meaning" sentence tacked on — whatever thematic resonance exists stays implicit in the concrete telling. Concrete does not mean generic. Privately weigh a couple of different directions before committing to the one you write, and reject whichever is the flattest, most interchangeable version — the one that could be pasted into any other story unchanged. Reach instead for the specific detail that makes THIS world or THIS person hard to mistake for a stock backdrop: a particular trade, grudge, object, or habit, not "a struggling family" in the abstract. When the author asks you to invent a new character, decide who they are one step before you ground them: pick a striking role or type this story doesn't already have (a disgraced specialist, a fixer in over their head, a rival who's right, an outsider everyone still defers to) — then write their `fields.character_wounds` as the concrete life that produced exactly that person, never a placeholder "ordinary life" that happens to be attached to them. A character's `fields.character_wounds` (keyed by `player` or the character's key) should be a specific, plausible INSTANCE of whatever `world.history` already establishes, not invented independently of it — but this is guidance for HOW to write these two fields, never a reason to touch both in the same turn: write `world.history` only when the active focus is world/premise/overview, and `fields.character_wounds` only when the active focus is cast, exactly like every other section boundary in this prompt. `fields.character_wounds` is private author material, never a player-facing fact, and separate from the spoiler-free `fields.character_cores` surface line above. Genre, setting, and plot are applied afterward to amplify this material; they never originate it.

When the author is ready to shape STORYLINES, treat a storyline as an authored pressure, not a prediction that the model gets to fulfill. Exactly one storyline belongs to the protagonist (`owner: "player"`) and it comes first: generate its `dramatic_question`/`starting_belief`/`truth`/character-thread fields (`want`, `blind_spot`, `protective_strategy`, `limitation`) FROM that same character's own `fields.character_wounds` — their yearning for a better education becomes their `want`; what the family's pressure cost them becomes their `blind_spot`; never invent these independently of their own established backstory. A theme asks a human question; each character thread says what the person wants, the belief or protective strategy that keeps them from seeing the answer, their visible tell, and an optional recognition they might earn. A blind spot is not a secret the narrator may announce. Do not make a person conveniently self-aware, cooperative, healed, or confessional just because the player asks. Their limitations must keep producing believable resistance until a particular scene and choice earn a partial turn. Store the canonical theme and storyline document only in fields.arc_design: {"version":1,"themes":[{"id":"...","label":"...","question":"..."}],"arcs":[{"id":"...","title":"...","theme_id":"...","owner":"character-key","dramatic_question":"...","starting_belief":"...","truth":"author-only truth","stakes":"...","turning_points":[{"id":"...","kind":"pressure|reversal|revelation|choice|aftermath","scene_id":"optional-scene-id","when":"optional-slot","public_surface":"...","private_pressure":"author-only","changes":"..."}],"character_threads":[{"character":"character-key","want":"...","protective_strategy":"...","blind_spot":"...","unacknowledged_need":"author-only","limitation":"...","visible_tell":"...","pressure_points":[{"scene_id":"optional-scene-id","when":"optional-slot","public_pressure":"..."}],"recognition":"author-only possible recognition","possible_outcomes":["..."]}]}]}. The server derives its legacy theme index from this document; never write a root themes field. Use actual existing character keys. Do not create arc threads for unnamed people. Fields called truth, unacknowledged_need, private_pressure, and recognition are author/director-only, never player-facing facts.

When the opening situation and threat are established, help plan the first day as a flexible set of actual possible scenes. `events` is not a lore list: every new event must be a concrete encounter the player can enter at a real time and declared location, with on-page participants including `player`, an immediate visible situation or pressure, and a public `hook` naming the question, choice, or action that gives the player a way in. Give a real scene a terse public `theme`, `tone`, and `roles` map keyed by on-page character, where each role is that person's immediate function in this moment—not a hidden motivation or diagnosis. A bare fact such as “the island was once a sacrifice site” belongs in structured world/location material, not an event. History may appear in an event only as a present clue, rumor, person, object, or place the player can inspect, question, follow, or respond to. Each event should still distinguish what the protagonist sees, director-private context, the trigger that can change it, the evidence it leaves, and who can know it. Store this as fields.first_day_plan: {"objective":"...","opening_time":"morning","opening_location":"location-id","opening_present":["player","character-key"],"events":[{"id":"stable-scene-id","when":"morning","location":"location-id","participants":["player","character-key"],"visible":"A concrete on-stage situation and pressure.","hook":"What the player can ask, inspect, choose, or do next.","theme":"A short public tension","tone":"A short on-stage feeling","roles":{"player":"immediate player position","character-key":"immediate scene function"},"hidden":"director-only fact or action","entity_action":true,"entity_period":"period-id","trigger":"...","evidence":"...","knowledge":{"protagonist":"...","entity":"...","public":"..."}}]}. Set entity_action true only when the entity actively changes this scene; then entity_period must name a compatible window. Set it false for a private clue, old event, or character reaction that the director knows but the entity is not doing now. It is a loop baseline, not a script: player choices can alter triggered events while the entity follows its information-gated policy. Use actual location ids and character keys, not prose placeholders.

Give supernatural actors a time system rather than arbitrary availability. Store time_system as {"entity_periods":[{"id":"...","slots":["morning"|"evening"|"night"],"state":"dormant|observing|hunting|feeding","capabilities":["..."],"constraint":"..."}]}. Every hidden first-day action must name a compatible time period. Time windows are real constraints: an entity cannot use capabilities outside its active period.

Player abilities are separate from entity capabilities. A player can only perform a supernatural action if the author explicitly stores it in fields.player_abilities as [{"id":"stable-id","name":"...","aliases":["words the player can use"],"description":"what it visibly does","limits":"hard scope and cost"}]. Do not infer a player power from their persona, a character card, genre, or an entity's capabilities. Leave this field absent for an ordinary protagonist.

VISIBILITY IS PART OF AUTHORING. When the author explicitly establishes a fact that should guide the author/director but must never be handed to the prose narrator, a character voice, a card organizer, or an image model, classify it automatically with `SET fields.author_notes ["the fact"]`. Use this only for a concrete fact the author gave you—never hide a guess or ordinary canon. The server stores it as an author-only block and removes it from all later model prompts. Do not put wrapper tags in the CMD value yourself.

For a death-loop story, prose rules are not enough to run the loop. Once those decisions are known, store an explicit world.loop.policy: {"trigger":"death","restart":"opening","preserve":{"runtime":[],"memories":["player"],"clear_memories_for_others":true},"victims_return_after_end":false}. Preserve no runtime fields unless the author explicitly says they survive. Keep end_condition as world.loop.end_condition.

Every substantive author answer MUST update the PATCH with the facts it establishes. Record concrete setting, atmosphere, technology, situation, and mystery details in world; keep the immediate dramatic situation in premise. Do not wait for the author to explicitly say 'save this.' Use {} only for a greeting, a question about the process, or a turn that genuinely establishes no story fact.

Write brief, natural authorial prose followed by exactly one fenced CMD block. CMD is mandatory when a fact is established. Use one command per line: `MERGE world {"genre":"...","history":"..."}`, `SET locations [{"id":"...","name":"...","description":"...","history":"..."}]`, `SET start "location-id"`, `SET premise "..."`, `SET cast [{"name":"..."}]`, `SET relationships [...]`, `SET fields.character_cores {"player":"...","character-key":"..."}`, `SET fields.character_wounds {"player":"...","character-key":"..."}`, `SET fields.arc_design {...}`, `SET fields.first_day_plan {...}`, `SET fields.player_abilities [...]`, `SET fields.author_notes ["author/director-only fact"]`, `SET time_system {...}`, or `SET fields.open_questions ["..."]`. Values must be valid JSON. After the block, add exactly one private line `NEXT_FOCUS: world|premise|first_day|time_system|cast|arcs` naming the most useful next card section. Never echo fields.interview_history or any other fields key. Do not expose or discuss the commands or NEXT_FOCUS marker."""
        focus_instruction = {
            "overview": "The author is defining the STORY OVERVIEW—the durable public setting and immediate situation, not a one-line plot synopsis. Keep the biome, culture, ordinary social texture, material era, lived atmosphere, setting, premise, and opening position mutually consistent. When the author establishes enough material, write concrete world.biome, world.culture, world.customs, world.technology, world.setting, and world.atmosphere facts alongside a premise that explains why this opening matters. Theme belongs to individual storylines: never set or revise themes here. Preserve established facts; do not invent a solution, private motive, or future scene. Do not reduce the overview to a recap of one incident.",
            "world": "The author is defining the WORLD and its map. Put world facts in world; when a playable place is established, add it to locations with a stable id and set start when it is the opening. world.history and each location's own history should be one concrete paragraph — the kind of real, specific circumstance everything else in this world gets to be a plausible instance of; see the grounding guidance above. Favor the distinctive version of this world over the generic one: a specific trade, dispute, custom, or figure that couldn't belong to just any setting, not the first vague backdrop that comes to mind. An explicitly authored player supernatural ability belongs only in fields.player_abilities with id, name, aliases, description, and hard limits; do not infer one from a persona. Do not turn facts into first-day events yet.",
            "premise": "The author is defining the OPENING. Update premise with the immediate situation; do not create future day events.",
            "first_day": "The author is planning DAY ONE. Add only real possible scenes: each new event needs a time, declared location, on-page participants including player, a visible immediate situation/pressure, a player-facing hook, concise public theme/tone, and a roles map keyed by the on-page people. Do not turn island history, setting summaries, or general facts into events; put those in world/location material unless the scene contains a concrete clue or person the player can engage with. Put all scene information into fields.first_day_plan: objective, events, timing, public scene kernel, hidden entity action, triggers, evidence, and knowledge. Do NOT rewrite world or premise merely because an event mentions them.",
            "time_system": "The author is defining ENTITY ACTIVITY WINDOWS. Update only time_system.entity_periods, including allowed slots, capabilities, and constraints. Do not turn scheduling details into generic world lore.",
            "cast": "The author is defining PEOPLE. Update a named character/cast fact and any established bond attached to those people. When the author asks you to invent someone new, don't reach for the flattest person who could fill the slot — pick a specific, striking role or type this story is missing (see the grounding guidance above) and let their name, appearance, and role reflect that hook rather than a generic descriptor; a boring, interchangeable character is a bug, not safety. When useful, add a concise public fields.character_cores entry keyed by player or stable character key; never turn an unacknowledged private truth into a core. Once the person is real enough to have a life before this story, add fields.character_wounds for them too: one concrete backstory paragraph (a family situation, a pressure, a want or block) that is a specific, textured instance of this world's own condition — not the blandest situation that technically fits, a one-line trauma label, or the public core. `world` below is READ-ONLY reference for this turn — it is already established; do not emit a `world` command to restate, confirm, or align with it, no matter how relevant it feels. This turn may only touch cast/relationships/fields.character_cores/fields.character_wounds. Relationships belong to cast context, not a separate story section; do not turn either into world lore.",
            "arcs": "The author is defining STORYLINES: the theme and character pressure belong to the storyline document. Update only fields.arc_design; do not write a root themes field, because the server derives its compatibility index from the canonical document. Give every storyline a title, owner, theme_id, and dramatic_question. The protagonist's own storyline (owner: \"player\") comes first, before any supporting character's. Put the rich theme question and all character pressure in `SET fields.arc_design {...}`, generated FROM that character's own fields.character_wounds when it exists — find the specific thing a named character cannot yet acknowledge, why their protection makes sense, how it shows on the surface, and what could pressure it without forcing a breakthrough, all traceable back to their concrete backstory. These are possible turns, never a required plot script.",
        }[focus]
        # The selected card section is the working context. This prevents an
        # edit to Day One from being diluted by unrelated cast/world material.
        def cast_snapshot() -> list[dict]:
            """Expose names and usable facts beside stable keys to the interviewer.

            The persisted Story intentionally keeps only character ids.  A
            model planning Day One needs to know that `shuri_7` is Shuri before
            it can return valid participant references.
            """
            out = []
            story_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
            character_cores = story_fields.get("character_cores") if isinstance(story_fields.get("character_cores"), dict) else {}
            character_wounds = story_fields.get("character_wounds") if isinstance(story_fields.get("character_wounds"), dict) else {}
            for member in raw.get("cast") or []:
                if not isinstance(member, dict) or not member.get("character"):
                    continue
                char_key = member["character"]
                record = ctx.base_settings.characters.get(char_key)
                facts = (getattr(record, "fields", None) or {}) if record is not None else {}
                out.append({
                    "character": char_key,
                    "name": getattr(record, "name", "") or char_key,
                    "role": facts.get("role") or "",
                    "appearance": facts.get("appearance") or "",
                    "personality": facts.get("personality") or "",
                    "background": facts.get("background") or "",
                    "connection": facts.get("connection") or "",
                    "core": character_cores.get(char_key, ""),
                    "wound": character_wounds.get(char_key, ""),
                })
            # The protagonist ("player") is rarely a roster CastMember, but their own
            # wound/core is exactly the material the "arcs" focus needs first — surface
            # it explicitly so it isn't invisible just because they're not in `cast`.
            if "player" not in {item["character"] for item in out} and (
                character_wounds.get("player") or character_cores.get("player")
            ):
                out.append({
                    "character": "player", "name": "player",
                    "role": "", "appearance": "", "personality": "", "background": "", "connection": "",
                    "core": character_cores.get("player", ""),
                    "wound": character_wounds.get("player", ""),
                })
            return out

        cast_view = cast_snapshot()
        snapshots = {
            "overview": {"premise": raw.get("premise") or "", "world": raw.get("world") or {}, "locations": raw.get("locations") or [], "start": raw.get("start") or "", "first_day_plan": (raw.get("fields") or {}).get("first_day_plan") or {}},
            "world": {"world": raw.get("world") or {}, "locations": raw.get("locations") or [], "start": raw.get("start") or "", "time_system": raw.get("time_system") or {}, "premise": raw.get("premise") or "", "player_abilities": (raw.get("fields") or {}).get("player_abilities") or []},
            "premise": {"premise": raw.get("premise") or "", "world": raw.get("world") or {}},
            "first_day": {"premise": raw.get("premise") or "", "world": raw.get("world") or {}, "first_day_plan": (raw.get("fields") or {}).get("first_day_plan") or {}, "time_system": raw.get("time_system") or {}, "cast": cast_view},
            "time_system": {"world": raw.get("world") or {}, "time_system": raw.get("time_system") or {}, "first_day_plan": (raw.get("fields") or {}).get("first_day_plan") or {}},
            "cast": {"cast": cast_view, "character_cores": (raw.get("fields") or {}).get("character_cores") or {}, "relationships": raw.get("relationships") or [], "premise": raw.get("premise") or "", "world": raw.get("world") or {}},
            "arcs": {"arc_design": (raw.get("fields") or {}).get("arc_design") or {}, "cast": cast_view, "relationships": raw.get("relationships") or [], "premise": raw.get("premise") or "", "world": raw.get("world") or {}, "first_day_plan": (raw.get("fields") or {}).get("first_day_plan") or {}},
        }
        selected_snapshot = target_snapshot(raw, target, section_snapshot=snapshots[focus])
        selected_label = target_label(raw, target)
        mode = "BOOTSTRAP: ask one question from the existing card; do not claim a new author fact." if bootstrap else "AUTHOR TURN: commit the latest author facts, then ask one focused next question."
        knowledge_only_instruction = ""
        if knowledge_only:
            knowledge_only_instruction = (
                "\nDIRECTOR-ONLY KNOWLEDGE ASSIGNMENT:\n"
                "Work on the one existing protected scene named in the author turn. Preserve every scene field "
                "exactly. You may add a concise `knowledge` fact only for the existing observer keys listed there, "
                "and only when the scene itself already establishes why that observer directly knows it. Do not add "
                "a witness, public clue, entity rule, relationship, or new scene. Return the full unchanged Day One "
                "plan with only those blank knowledge entries filled.\n"
            )
        prompt = f"""{mode}
ACTIVE AUTHORING TARGET: {focus}
SPECIFIC EDIT TARGET: {json.dumps(target, ensure_ascii=False)}
TARGET LABEL: {selected_label}
{focus_instruction}
{knowledge_only_instruction}

This is an isolated editorial window. Stay on the selected item or field: do not use another item's transcript, and do not casually rewrite siblings in the same section. A linked fact may change only when the author explicitly establishes that link. Offer a concise, actionable improvement or ask one precise question about this target rather than restarting a general story interview.

HARD BOUNDARY: emit a command ONLY for the ACTIVE AUTHORING TARGET above ({focus}). Background material below (world, cast, premise, etc.) is shown so you can ground THIS turn's answer in it — reading it is not permission to also rewrite it. A command for any other section is rejected and fails the whole turn, so when in doubt, leave it out.

SELECTED CARD TARGET:
{json.dumps(selected_snapshot, ensure_ascii=False)}

CONVERSATION FOR THIS TARGET ONLY:
{conversation}

Respond to the latest author turn."""
        # This endpoint owns its full interviewer prompt, so resolve the
        # dedicated Story Agent directly instead of going through the generic
        # builder tuple.  Returning the safe route metadata lets focused
        # authoring surfaces say which configured model actually handled the
        # turn (including an active-model fallback) without exposing a key.
        # reasoning_effort/max_tokens for this role live in story_builder.json —
        # reasoning tokens compete with the visible reply+CMD block for the same
        # budget, and a cast/arcs/first_day turn commits several facts at once
        # (core + wound + cast fact, or a full arc_design, or a multi-event plan),
        # so the configured budget needs real headroom or these truncate mid-write.
        provider, model_route = ctx.story_agent_provider(body)
        if provider is None:
            return JSONResponse({"error": model_route.get("error") or "no interview model configured",
                                 "model_route": model_route}, status_code=400)
        latest_author = next((m["text"] for m in reversed(incoming)
                              if m.get("role") == "user"), "")
        turn = None
        try:
            from ..authoring.interview import patch_from_recovery, recovery_schema
            from ..authoring.interview_graph import run_interview_turn

            turn = await run_interview_turn(provider=provider, system=system, prompt=prompt)
            reply, patch = turn.reply, turn.patch
            next_focus = getattr(turn, "next_focus", "") or ""
            # The selected card section is an enforced mutation boundary.  A
            # small model may still mention adjacent facts in its CMD; preserve
            # the focused edit without letting it silently retarget the card.
            patch = {name: value for name, value in patch.items() if name in SECTIONS[focus]}
            if bootstrap:
                patch = {}  # a question may not manufacture canonical facts
            # Some providers echo the live `fields` object despite the prompt.
            # Ignore those unsupported keys rather than making a valid author turn
            # fail; they are never allowed through to the Story-card write.
            if isinstance(patch.get("fields"), dict):
                safe_fields = {k: v for k, v in patch["fields"].items()
                              if k in {"status", "open_questions", "first_day_plan", "arc_design", "player_abilities", "character_cores", "character_wounds", "author_notes"}}
                if safe_fields:
                    patch["fields"] = safe_fields
                else:
                    patch.pop("fields")
            # A prose response is never evidence that a mutation succeeded.
            # When the primary CMD protocol is omitted, run one small
            # structured extraction pass against the author's exact words.
            # It has no authority outside the focused card section.
            if not patch and latest_author and not knowledge_only and not architect_handoff:
                try:
                    from ..authoring.card_graph import run_card_operation

                    extracted = await run_card_operation(
                        operation="recover_interview_patch", provider=provider,
                        system=("Extract only Story-card facts explicitly stated in the AUTHOR TURN. "
                                "Do not infer, embellish, or ask a question. Use only the allowed PATH values. "
                                "For time_system, fields.first_day_plan, fields.player_abilities, cast, and relationships, VALUE must be "
                                "a valid JSON value encoded as a string. Return an empty changes array when the "
                                "author establishes no fact."),
                        prompt=(f"FOCUS: {focus}\nALLOWED PATHS: {json.dumps(list(recovery_schema(focus)['properties']['changes']['items']['properties']['path']['enum']))}\n"
                                f"CURRENT FOCUSED CARD:\n{json.dumps(snapshots[focus], ensure_ascii=False)}\n\n"
                                f"AUTHOR TURN:\n{latest_author}"),
                        schema=recovery_schema(focus),
                    )
                    patch = patch_from_recovery(focus, extracted)
                except ModelRequestTimeout:
                    raise
                except Exception:  # noqa: BLE001
                    patch = {}
            # A cast PATCH is a set of initial character-card facts, not an
            # anonymous roster. Mint cards only once the interview has supplied
            # enough context, then put their real keys on the Story card.
            # Resolve display names before minting anything.  The real card
            # persists stable keys, but the interview naturally speaks about
            # Shuri; a second fact about Shuri must reuse her existing record.
            name_keys = {}
            for cast_member in raw.get("cast") or []:
                if not isinstance(cast_member, dict):
                    continue
                existing_key = cast_member.get("character")
                existing = ctx.base_settings.characters.get(existing_key)
                existing_name = (getattr(existing, "name", "") or "").strip()
                if existing_key and existing_name:
                    name_keys.setdefault(existing_name.lower(), existing_key)
            for existing_key, existing in ctx.base_settings.characters.items():
                existing_name = (getattr(existing, "name", "") or "").strip()
                if existing_name:
                    name_keys.setdefault(existing_name.lower(), existing_key)
            if isinstance(patch.get("cast"), list):
                from ..authoring.dramatic_kernel import _PLAYER_ALIASES

                members = []
                # A model reliably keeps core/wound prose it just wrote close to the
                # cast item it names, instead of the separate fields.character_cores/
                # character_wounds commands the prompt asks for — measured live, not
                # a hypothetical. Extracted here, where char_key is actually resolved,
                # rather than after: cast items are reduced to {character, home}
                # below, so anything popped later never sees this data at all.
                inline_cores: dict[str, str] = {}
                inline_wounds: dict[str, str] = {}

                def _stash_inline(char_key: str, member: dict) -> None:
                    core, wound = member.get("core"), member.get("wound")
                    if isinstance(core, str) and core.strip():
                        inline_cores[char_key] = core.strip()
                    if isinstance(wound, str) and wound.strip():
                        inline_wounds[char_key] = wound.strip()

                for member in patch["cast"]:
                    if not isinstance(member, dict):
                        continue
                    proposed = str(member.get("character") or "").strip()
                    name = (member.get("name") or "").strip()
                    # The context snapshot shows the protagonist's own wound/core under a
                    # synthetic "player" cast entry so the model can see it (cast_snapshot,
                    # above) — measured live, a model echoes that entry back as a literal
                    # SET cast item. "player" is never a real roster member (system prompt
                    # says so explicitly); minting one would create a bogus character
                    # record. Salvage any inline core/wound under "player" and drop the item.
                    if proposed.lower() in _PLAYER_ALIASES or name.lower() in _PLAYER_ALIASES:
                        _stash_inline("player", member)
                        continue
                    char_key = proposed
                    if char_key and char_key in ctx.base_settings.characters:
                        _stash_inline(char_key, member)
                        members.append({"character": char_key, "home": member.get("home") or ""})
                        continue
                    if not name:
                        continue  # model placeholder: never let it derail the author's turn
                    char_key = name_keys.get(name.lower())
                    if not char_key:
                        facts = {k: member[k] for k in ("role", "appearance", "personality", "background", "connection")
                                 if isinstance(member.get(k), str) and member[k].strip()}
                        made = ctx.write_character({"name": name, "system": "\n".join(facts.values()),
                                                    "fields": facts, "playable": False}, None)
                        char_key = made["key"]
                        name_keys[name.lower()] = char_key
                    # The model's own proposed identity (`character`) is usually a clean
                    # slug it then reuses consistently in this same turn's character_cores/
                    # character_wounds commands — measured live (a model writes SET cast
                    # [{"character": "walt", "name": "Walt Hargrove", ...}] then SET
                    # fields.character_wounds {"walt": "..."}). The mint above only derives
                    # a key from `name`, which can differ ("walt_hargrove"), orphaning that
                    # later reference. Alias the model's own key to the real one so it still
                    # resolves below.
                    if proposed and proposed.lower() != char_key.lower():
                        name_keys.setdefault(proposed.lower(), char_key)
                    _stash_inline(char_key, member)
                    members.append({"character": char_key, "home": member.get("home") or ""})
                if members:
                    patch["cast"] = members
                else:
                    patch.pop("cast")
                if inline_cores or inline_wounds:
                    patch_fields = dict(patch.get("fields") or {})
                    if inline_cores:
                        merged = dict(patch_fields.get("character_cores") or {})
                        for char_key, core in inline_cores.items():
                            merged.setdefault(char_key, core)
                        patch_fields["character_cores"] = merged
                    if inline_wounds:
                        merged = dict(patch_fields.get("character_wounds") or {})
                        for char_key, wound in inline_wounds.items():
                            merged.setdefault(char_key, wound)
                        patch_fields["character_wounds"] = merged
                    patch["fields"] = patch_fields
            if isinstance(patch.get("relationships"), list):
                from ..authoring.dramatic_kernel import _PLAYER_ALIASES

                # The protagonist is never a `cast[]` member by design (see the minting
                # loop above), but "player" is exactly the identifier the system prompt
                # tells the model to use for them everywhere else (character_cores,
                # character_wounds, arc_design's owner) — a bond naming the protagonist
                # is normal, not a placeholder, and was previously dropped silently here.
                valid_cast = {"player"}
                valid_cast |= {m.get("character") for m in (raw.get("cast") or []) if m.get("character")}
                valid_cast |= {m.get("character") for m in (patch.get("cast") or []) if m.get("character")}
                relations = []
                for i, relation in enumerate(patch["relationships"]):
                    if not isinstance(relation, dict):
                        continue
                    for side in ("source", "target"):
                        value = relation.get(side)
                        if isinstance(value, str):
                            value = value.strip()
                            value = "player" if value.lower() in _PLAYER_ALIASES else name_keys.get(value.lower(), value)
                            relation[side] = value
                    if relation.get("source") not in valid_cast or relation.get("target") not in valid_cast:
                        continue  # a genuinely unnamed/unknown person — do not corrupt the card
                    relation.setdefault("id", f"interview-bond-{i + 1}")
                    relations.append(relation)
                if relations:
                    patch["relationships"] = relations
                else:
                    patch.pop("relationships")
            # Public character cores AND private wounds are stored by stable
            # key. Interview prose can naturally say "Shuri", so resolve that
            # display name at the same boundary used for cast relationships
            # and scenes — for both fields, not just cores. A wound written
            # under a display name that never gets resolved sits under a key
            # nothing downstream looks up (cast_snapshot reads it by the
            # resolved cast key), so it's silently unreachable.
            def _resolve_person_map(value_map: dict, label: str) -> dict:
                raw_cast = raw.get("cast") if isinstance(raw.get("cast"), list) else []
                patch_cast = patch.get("cast") if isinstance(patch.get("cast"), list) else []
                valid_keys = {
                    str(member.get("character") or "").strip()
                    for member in raw_cast + patch_cast
                    if isinstance(member, dict) and str(member.get("character") or "").strip()
                }
                # A model sometimes invents its own slug for THIS field, independent of the
                # cast item's own key/name — measured live (fields.character_cores keyed
                # "eunice-marrow" when the actual minted key was "eunice_marrow"; the cast
                # item never proposed a `character` identity, so there was nothing for the
                # display-name/proposed-key lookups above to alias). Punctuation is the only
                # thing that differs, so fall back to a hyphen/underscore/space-insensitive
                # match against the real cast keys before giving up.
                def _squash(s: str) -> str:
                    return re.sub(r"[\s_-]+", "", s.lower())
                squashed_valid = {_squash(k): k for k in valid_keys}
                resolved = {}
                for person, text in value_map.items():
                    if not isinstance(person, str):
                        continue
                    person_key = person.strip()
                    if person_key.lower() in {"player", "you", "protagonist", "returner"}:
                        person_key = "player"
                    else:
                        person_key = name_keys.get(person_key.lower(), person_key)
                        if person_key not in valid_keys:
                            person_key = squashed_valid.get(_squash(person_key), person_key)
                    if person_key != "player" and person_key not in valid_keys:
                        raise ValueError(f"character {label} needs a named story cast member")
                    resolved[person_key] = text
                return resolved

            core_patch = (patch.get("fields") or {}).get("character_cores")
            if isinstance(core_patch, dict):
                patch["fields"]["character_cores"] = _resolve_person_map(core_patch, "core")
            wound_patch = (patch.get("fields") or {}).get("character_wounds")
            if isinstance(wound_patch, dict):
                patch["fields"]["character_wounds"] = _resolve_person_map(wound_patch, "wound")
            # Day-one plans also arrive in natural language.  Resolve every
            # known display name to the stable cast key before compiler/runtime
            # validation, while retaining genuinely unknown names as visible
            # authoring errors rather than silently deleting an intended person.
            plan_patch = (patch.get("fields") or {}).get("first_day_plan")
            if isinstance(plan_patch, dict):
                item_target = target.get("item") if isinstance(target.get("item"), dict) else {}
                if item_target.get("kind") == "scene" and not knowledge_only:
                    # A clicked scene gets a deliberately narrow model
                    # snapshot, so it is legitimate for its response to carry
                    # only that one event.  Rebuild the complete plan from
                    # canonical state and merge only the selected scene; this
                    # prevents a focused popup from deleting siblings or
                    # director-only fields it was never allowed to see.
                    target_scene_id = str(item_target.get("id") or "").strip()
                    source_plan = deepcopy((raw.get("fields") or {}).get("first_day_plan") or {})
                    source_events = source_plan.get("events") if isinstance(source_plan.get("events"), list) else []
                    proposed_event = next(
                        (dict(event) for event in (plan_patch.get("events") or [])
                         if isinstance(event, dict) and str(event.get("id") or "").strip() == target_scene_id),
                        None,
                    )
                    source_index = next(
                        (index for index, event in enumerate(source_events)
                         if isinstance(event, dict) and str(event.get("id") or "").strip() == target_scene_id),
                        None,
                    )
                    if proposed_event is None or source_index is None:
                        # An item-scoped turn may not use an unrelated plan
                        # patch as an escape hatch.  Treat it as no plan edit;
                        # its prose/history can still ask the author for the
                        # missing clarification.
                        patch["fields"].pop("first_day_plan", None)
                        plan_patch = None
                    else:
                        merged_event = deepcopy(source_events[source_index])
                        for name, value in proposed_event.items():
                            if name == "id":
                                continue
                            if name == "knowledge" and isinstance(value, dict) and isinstance(merged_event.get(name), dict):
                                merged_event[name] = {**merged_event[name], **deepcopy(value)}
                            else:
                                merged_event[name] = deepcopy(value)
                        merged_event["id"] = target_scene_id
                        source_events[source_index] = merged_event
                        source_plan["events"] = source_events
                        patch["fields"]["first_day_plan"] = source_plan
                        plan_patch = source_plan
            if isinstance(plan_patch, dict):
                def resolve_person(value):
                    if not isinstance(value, str):
                        return value
                    key = value.strip()
                    if key.lower() in {"player", "you", "protagonist", "returner"}:
                        return "player"
                    return name_keys.get(key.lower(), key)

                def resolve_people(values):
                    if not isinstance(values, list):
                        return values
                    out = []
                    for value in values:
                        if isinstance(value, str):
                            out.append(resolve_person(value))
                        else:
                            out.append(value)
                    return out
                if "opening_present" in plan_patch:
                    plan_patch["opening_present"] = resolve_people(plan_patch.get("opening_present"))
                for event in plan_patch.get("events") or []:
                    if isinstance(event, dict) and "participants" in event:
                        event["participants"] = resolve_people(event.get("participants"))
                    if isinstance(event, dict) and isinstance(event.get("roles"), dict):
                        event["roles"] = {
                            resolve_person(person): role
                            for person, role in event["roles"].items()
                            if isinstance(person, str)
                        }
                    if isinstance(event, dict) and isinstance(event.get("roles"), list):
                        event["roles"] = [
                            {**role, "character": resolve_person(role.get("character"))}
                            if isinstance(role, dict) and isinstance(role.get("character"), str) else role
                            for role in event["roles"]
                        ]
                # A conversational Day One edit can still include an entire
                # legacy plan.  Preserve an unchanged legacy scene for a
                # private annotation edit, but require every new or changed
                # public scene surface to be a real player-enterable offer.
                # The helper inspects public fields only and never exposes a
                # hidden action, evidence, or knowledge boundary.
                if not knowledge_only and isinstance(plan_patch.get("events"), list):
                    from ..authoring.scene_contract import (
                        ensure_player_participant,
                        public_scene_surface,
                        validate_changed_scene_offers,
                    )

                    current_plan = (raw.get("fields") or {}).get("first_day_plan") or {}
                    current_events = current_plan.get("events") if isinstance(current_plan, dict) else []
                    current_by_id = {
                        str(item.get("id") or "").strip(): item
                        for item in current_events or []
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    }
                    location_ids = {
                        str(location.get("id") or "").strip()
                        for location in raw.get("locations") or []
                        if isinstance(location, dict) and str(location.get("id") or "").strip()
                    }
                    normalized_events = []
                    for raw_event in plan_patch["events"]:
                        if not isinstance(raw_event, dict):
                            normalized_events.append(raw_event)
                            continue
                        event = dict(raw_event)
                        previous = current_by_id.get(str(event.get("id") or "").strip())
                        changed_surface = previous is None or public_scene_surface(previous) != public_scene_surface(event)
                        if changed_surface:
                            event = ensure_player_participant(event)
                            location = str(event.get("location") or "").strip()
                            if location and location not in location_ids:
                                raise ValueError("first-day scene needs a declared location")
                        normalized_events.append(event)
                    plan_patch["events"] = normalized_events
                    validate_changed_scene_offers(current_events, normalized_events)
            if knowledge_only:
                patch = restrict_to_existing_scene_knowledge(
                    raw, patch,
                    event_ids=knowledge_only["event_ids"],
                    observer_ids=knowledge_only["observer_ids"],
                )
            # The author’s words are primary evidence. If a model asks a useful
            # follow-up but omits its PATCH, retain the answer on the real card
            # anyway; a later turn can distil it into richer world/premise prose.
            # Preserve raw author evidence only when the model produced no
            # usable card update at all.  Previously a valid focused patch
            # (fields.first_day_plan or time_system) was mistaken for an empty
            # response and overwritten by this fallback.
            if not patch and latest_author and not knowledge_only:
                if focus == "first_day":
                    plan = dict((raw.get("fields") or {}).get("first_day_plan") or {"objective": "", "events": []})
                    notes = list(plan.get("notes") or [])
                    if latest_author not in notes:
                        notes.append(latest_author)
                    plan["notes"] = notes[-24:]
                    plan.setdefault("events", [])
                    patch["fields"] = {"first_day_plan": plan}
                elif focus == "time_system":
                    schedule = dict(raw.get("time_system") or {"slots": ["morning", "evening", "night"], "entity_periods": []})
                    notes = list(schedule.get("notes") or [])
                    if latest_author not in notes:
                        notes.append(latest_author)
                    schedule["notes"] = notes[-24:]
                    schedule.setdefault("entity_periods", [])
                    patch["time_system"] = schedule
                elif focus == "arcs":
                    # Do not smuggle an unstructured emotional claim into the
                    # private arc contract.  The conversation history retains
                    # the author's exact words; the next extraction turn can
                    # turn them into a bounded character pressure safely.
                    patch = {}
                elif "world" in SECTIONS.get(focus, ()):
                    world = dict(raw.get("world") or {})
                    notes = list(world.get("interview_notes") or [])
                    if latest_author not in notes:
                        notes.append(latest_author)
                    patch["world"] = {"interview_notes": notes[-24:]}
                else:
                    # A `world` fallback note only makes sense for a focus whose
                    # own SECTIONS actually allows touching `world` (world,
                    # overview). For every other focus (cast, premise,
                    # relationships, ...) writing `patch["world"]` unconditionally
                    # used to fail the whole turn — that section can't touch
                    # world at all. Same safe default as arcs: rely on the
                    # conversation history instead of smuggling the raw claim
                    # into a field it doesn't belong in.
                    patch = {}
            updated = apply_patch(raw, patch, focus)
            # Retain private source spans if this focused model edit replaced a
            # whole public field.  The interview model is deliberately unable
            # to see those spans, so it cannot be their deletion authority.
            from ..visibility import preserve_model_hidden
            updated = preserve_model_hidden(raw, updated)
        except ModelRequestTimeout as exc:
            return JSONResponse({
                "error": str(exc),
                "retryable": True,
                "model_route": model_route,
            }, status_code=504)
        except ValueError as exc:
            # This is usually the model's own response failing validation (a
            # malformed CMD block, an out-of-shape field) — exactly the case
            # where seeing what it actually said matters most.
            error_body = {"error": f"invalid interview response: {exc}"}
            if debug and turn is not None:
                error_body["model_input"] = {"system": system, "prompt": prompt,
                                             "raw_response": turn.raw_response}
            return JSONResponse(error_body, status_code=502)
        except Exception as exc:  # noqa: BLE001
            error_body = {"error": f"interview failed: {exc}"}
            if debug and turn is not None:
                error_body["model_input"] = {"system": system, "prompt": prompt,
                                             "raw_response": turn.raw_response}
            return JSONResponse(error_body, status_code=500)
        # ``[[hidden]]…[[/hidden]]`` inside an author turn is intentionally
        # not handed to the interviewer.  Preserve it separately, wrapped, so
        # the author sees it on the card and every later model projection
        # redacts it again.  This direct write is the counterpart to the
        # provider-side redaction: private text must not disappear just because
        # the model never saw it.
        from ..visibility import extract_model_hidden, wrap_model_hidden
        _hidden_notes = extract_model_hidden(latest_author)
        _hidden_changed = False
        next_fields = dict(updated.get("fields") or {})
        if _hidden_notes:
            notes = [str(note) for note in (next_fields.get("author_notes") or [])
                     if isinstance(note, str) and note.strip()]
            for note in _hidden_notes:
                wrapped = wrap_model_hidden(note)
                if wrapped not in notes:
                    notes.append(wrapped)
                    _hidden_changed = True
            if _hidden_changed:
                next_fields["author_notes"] = notes[-24:]
                patch.setdefault("fields", {})["author_notes"] = notes[-24:]
        # Any new authoring fact invalidates the previous executable snapshot.
        # Reactivation is explicit so a player never starts against stale rules.
        if (patch or _hidden_changed) and next_fields.get("status") == "active":
            next_fields["status"] = "interviewing"
            next_fields.pop("runtime_scenario", None)
        persisted_history = (transcript + [{"role": "assistant", "text": reply}])[-24:]
        next_fields = persist_scoped_history(next_fields, target, persisted_history)
        # Compatibility mirror for the pre-popup interview surface.  It is
        # intentionally not read by a target-aware request, so opening one
        # item's editor can never inject its history into another's.
        next_fields["interview_history"] = persisted_history
        updated["fields"] = next_fields
        try:
            if architect_handoff:
                from ..authoring.story_architect import card_revision

                live_before_commit = ctx._read_story_data(key)
                if card_revision(live_before_commit) != initial_card_revision:
                    return JSONResponse({
                        "error": "the Story card changed while the Architect worker was running; refresh the plan and try again",
                        "retryable": True,
                    }, status_code=409)
            ctx.update_story_fields(key, {k: updated[k] for k in ("world", "locations", "start", "time_system", "premise", "themes", "cast", "relationships", "fields")
                                           if k in updated})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save interview patch: {exc}"}, status_code=400)
        # Return the per-turn working prompt so the interview UI can make its
        # context visible. The static interviewer policy remains server-side;
        # this is the live card slice and conversation actually supplied here.
        readiness = _play_readiness(updated)
        readiness.pop("_contract", None)
        if not next_focus:
            # Old/small models may omit the private NEXT_FOCUS marker.  Keep
            # navigation moving forward instead of stranding a cast question
            # on the world tab; an explicit marker always wins.
            next_focus = {
                "overview": "first_day",
                "world": "premise",
                "premise": "cast",
                "cast": "arcs",
                "arcs": "first_day",
                "first_day": "time_system",
                "time_system": "first_day",
            }.get(focus, focus)
        # A field/item popup should remain attached to the thing the author
        # clicked.  Section-level interview flow still receives the normal
        # forward-looking hint above.
        if target.get("item"):
            next_focus = focus
        public = public_story_card(ctx, key, updated)
        suggestions = editorial_suggestions(updated, target)
        conversation_payload = {"target": target, "history": persisted_history, "suggestions": suggestions}
        from ..visibility import strip_model_hidden
        model_input = {"target": focus, "editorial_target": target,
                       "prompt": strip_model_hidden(prompt) or ""}
        if debug:
            model_input["system"] = system
            model_input["raw_response"] = getattr(turn, "raw_response", "")
        return {"ok": True, "reply": reply, "patch": patch,
                "target": target, "history": persisted_history, "conversation": conversation_payload,
                "suggestions": suggestions,
                "model_route": model_route,
                "model_input": model_input,
                "open_questions": next_fields.get("open_questions") or [],
                "status": next_fields.get("status", "interviewing"),
                "readiness": readiness,
                "next_focus": next_focus,
                "next_target": target,
                "story": public,
                "card": public}

    @app.post("/api/stories/{key}/interview")
    async def story_interview(key: str, body: dict):
        """Public interview boundary; protected work requires the Architect."""
        return await _run_story_interview(key, body)

    @app.get("/api/stories/{key}/architect/public/context")
    def public_architect_context(key: str):
        """Return the browser-safe snapshot for a frontend-owned public pass.

        This is intentionally narrower than the ordinary author card endpoint:
        it is exactly the projection that a generic text model may see.  The
        frontend can therefore plan visible hand-offs without ever becoming a
        second route into author notes, arc truths, or Director mechanics.
        """
        from ..authoring.story_architect import card_revision
        from ..authoring.story_control_graph import build_story_control_graph

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        safe_card = _public_architect_model_card(ctx, key, raw)
        # The revision intentionally fingerprints the same redacted snapshot
        # supplied to the browser/model, not the raw card.  A private-only edit
        # therefore cannot be probed through this public capability token; the
        # commit path still preserves private spans from the live raw card.
        revision = card_revision(safe_card)
        return {
            "ok": True,
            "revision": revision,
            "card": safe_card,
            # Make the visibility tier unmistakable for a client while keeping
            # ``card`` convenient for ordinary snapshot consumers.
            "model_card": safe_card,
            "control_graph": build_story_control_graph(safe_card),
            "allowed_scopes": ["world", "premise", "cast", "first_day"],
        }

    @app.post("/api/stories/{key}/architect/public/model")
    async def public_architect_model(key: str, body: dict | None = None):
        """Run one server-prompted, public-only proposal pass without writing.

        The browser supplies only a canonical scope and an author brief.  It
        receives the structured proposal to review, alter, or discard; no
        canonical data changes until the separate revision-bound commit call.
        """
        from ..authoring.card_graph import run_card_operation
        from ..authoring.starter_set import DevelopError, _assert_public_only_proposal, build_develop_prompt, develop_schema
        from ..authoring.story_architect import card_revision
        from ..authoring.story_control_graph import work_order_prompt
        from ..visibility import strip_model_hidden

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "public Architect request must be an object"}, status_code=400)
        resolved = _public_architect_work_order(body)
        if isinstance(resolved, JSONResponse):
            return resolved
        scopes, work_order = resolved
        raw_brief = body.get("brief")
        if raw_brief is not None and not isinstance(raw_brief, str):
            return JSONResponse({"error": "brief must be text"}, status_code=400)
        brief = (strip_model_hidden(raw_brief or "") or "").strip()
        if len(brief) > 8000:
            return JSONResponse({"error": "brief is too long"}, status_code=400)
        supplied_revision = body.get("revision")
        if not isinstance(supplied_revision, str) or not supplied_revision:
            return JSONResponse({"error": "a public Architect model request requires a revision"}, status_code=400)
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        safe_card = _public_architect_model_card(ctx, key, raw)
        revision = card_revision(safe_card)
        if supplied_revision != revision:
            return _public_architect_stale_revision(revision)
        # Browser code does not choose a provider, override model policy, or
        # promote a failed pass to a configured fallback.  Those are server
        # routing decisions for this public capability.
        provider, model_route = ctx.story_agent_provider({})
        if provider is None:
            return JSONResponse({
                "error": model_route.get("error") or "no Story Agent model configured",
                "model_route": model_route,
            }, status_code=400)
        try:
            proposal = await run_card_operation(
                operation=f"public_architect_{work_order.specialist}",
                provider=provider,
                system=work_order_prompt(work_order),
                prompt=build_develop_prompt(
                    safe_card, brief=brief, scopes=scopes, public_only=True,
                ),
                schema=develop_schema(),
            )
            if not isinstance(proposal, dict):
                raise DevelopError("developer returned no structured proposal")
            # A proposal is shown to the browser before it reaches the commit
            # gate, so enforce its public visibility boundary here as well as
            # during commit.  Do not echo any rejected model content.
            proposal = _public_architect_scoped_proposal(proposal)
            _assert_public_only_proposal(proposal)
        except ModelRequestTimeout as exc:
            return JSONResponse({
                "error": str(exc), "retryable": True, "model_route": model_route,
            }, status_code=504)
        except DevelopError as exc:
            return JSONResponse({"error": f"invalid public Architect proposal: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"public Architect model call failed: {exc}"}, status_code=500)
        return {
            "ok": True,
            "revision": revision,
            "proposal": proposal,
            "work_order": work_order.as_dict(),
            "model_route": model_route,
        }

    @app.post("/api/stories/{key}/architect/public/commit")
    async def commit_public_architect_proposal(key: str, body: dict | None = None):
        """Validate and atomically commit one browser-reviewed public proposal."""
        from ...server.services import story_store as story_store
        from ..authoring.starter_set import DevelopError, apply_develop_proposal, validate_candidate
        from ..authoring.story_architect import card_revision
        from ..visibility import preserve_model_hidden

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "public Architect commit must be an object"}, status_code=400)
        resolved = _public_architect_work_order(body)
        if isinstance(resolved, JSONResponse):
            return resolved
        scopes, work_order = resolved
        expected_revision = body.get("revision")
        if not isinstance(expected_revision, str) or not expected_revision:
            return JSONResponse({"error": "a public Architect commit requires a revision"}, status_code=400)
        proposal = body.get("proposal")
        if not isinstance(proposal, dict):
            return JSONResponse({"error": "a public Architect commit requires a structured proposal"}, status_code=400)
        scoped_proposal = _public_architect_scoped_proposal(proposal)
        outcome_state: dict = {}

        def commit_latest(live_raw: dict, embedded_characters: dict):
            """Build the candidate only after the store holds its write lock.

            The browser's revision fingerprints the strict public projection,
            so a private-only write remains compatible.  Rebuilding from this
            locked aggregate carries that private material forward instead of
            saving the pre-model snapshot over it.
            """
            live_revision = card_revision(_public_architect_model_card(ctx, key, live_raw))
            if expected_revision != live_revision:
                outcome_state["stale_revision"] = live_revision
                return None
            try:
                candidate = apply_develop_proposal(
                    live_raw, scoped_proposal, story_key=key,
                    embedded_characters=embedded_characters,
                    known_characters=ctx.base_settings.characters, scopes=scopes,
                    public_only=True,
                )
                # The public worker never sees hidden spans, so its additive
                # candidate cannot be allowed to erase one while filling a
                # nearby public field.
                candidate.story = preserve_model_hidden(live_raw, candidate.story)
                validated = validate_candidate(candidate, ctx.base_settings.characters)
            except DevelopError as exc:
                outcome_state["error"] = f"invalid public Architect proposal: {exc}"
                return None
            except Exception as exc:  # noqa: BLE001
                outcome_state["error"] = f"could not validate public Architect proposal: {exc}"
                return None
            result = {"candidate": candidate, "story": validated["story"]}
            return validated["story"], validated["characters"], result

        try:
            outcome = story_store.update_story_atomically(ctx.root, key, commit_latest)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save public Architect proposal: {exc}"}, status_code=400)
        if not outcome.found:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if not outcome.committed:
            stale_revision = outcome_state.get("stale_revision")
            if isinstance(stale_revision, str):
                return _public_architect_stale_revision(stale_revision)
            return JSONResponse({
                "error": outcome_state.get("error") or "could not validate public Architect proposal",
            }, status_code=400)

        committed = outcome.result or {}
        candidate = committed["candidate"]
        updated = committed["story"]
        # Reload after commit so later ordinary authoring routes see the same
        # aggregate.  The response itself is derived from the committed value,
        # not a fresh unlocked read that could describe someone else's write.
        try:
            ctx.reload_settings()
        except Exception:  # noqa: BLE001 - persistence already succeeded
            pass
        safe_updated = _public_architect_model_card(ctx, key, updated)
        updated_revision = card_revision(safe_updated)
        readiness = _play_readiness(updated)
        readiness.pop("_contract", None)
        return {
            "ok": True,
            "revision": updated_revision,
            "message": candidate.message,
            "patch": candidate.patch,
            "story": safe_updated,
            "card": safe_updated,
            "created": {"characters": candidate.created},
            "updated_sections": candidate.updated_sections,
            "readiness": readiness,
            "work_order": work_order.as_dict(),
            "review": {
                "agent": work_order.reviewer,
                "status": "approved",
                "gates": list(work_order.reviewer_gates()),
            },
        }

    @app.post("/api/stories/{key}/architect/turn")
    async def story_architect_turn(key: str, body: dict | None = None):
        """Run one bounded turn of the primary Story Architect.

        This is intentionally a coordinator, not a hidden background loop:
        it observes deterministic gaps, makes *at most one* model call in
        response to an author message, commits only through existing validated
        boundaries, then returns one next question.  A blank request is the
        first-class bootstrap/reload operation and never calls a model.

        ``answer_to`` is optional for the primary chat surface: when omitted,
        a submitted message answers the persisted pending question.  It may be
        supplied by a client that needs explicit replay protection, but it is
        always checked against the saved question or a live deterministic gap;
        an arbitrary request cannot select a hidden editorial target.
        """
        from ..authoring.story_architect import (
            append_architect_action,
            append_architect_history,
            author_plan_presentation,
            architect_observation,
            block_gap_ids,
            card_revision,
            completion_request,
            current_pending_question,
            normalize_architect_state,
            plan_comment_target,
            question_for_answer,
        )
        from ..authoring.completion_planner import build_completion_plan, normalize_mechanical_setup
        from ..authoring.story_control_graph import build_story_control_graph, resolve_architect_work_order
        from ..visibility import extract_model_hidden, strip_model_hidden, wrap_model_hidden

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "Architect request must be an object"}, status_code=400)
        raw_message = str(body.get("message") or body.get("brief") or "").strip()
        if len(raw_message) > 8000:
            return JSONResponse({"error": "message is too long"}, status_code=400)
        # Hidden spans are never supplied to the generic development route.
        # The dedicated interview provider is independently redaction-wrapped,
        # but its persisted Architect history also receives this safe view.
        safe_message = (strip_model_hidden(raw_message) or "").strip()
        hidden_notes = extract_model_hidden(raw_message)
        # The dock normally edits the selected card element.  This is a
        # focused interview turn—not a request to route through whichever
        # global plan tile happened to be pending.  Keep plan comments and
        # explicit completion in the Architect coordinator below.
        if body.get("direct_edit") is True and safe_message:
            direct_target = body.get("target")
            if not isinstance(direct_target, dict):
                return JSONResponse({"error": "a direct Architect edit needs a card target"}, status_code=400)
            direct_body = {
                "target": direct_target,
                "section": direct_target.get("section"),
                "focus": direct_target.get("section"),
                # Preserve any author-only span locally.  The interview
                # provider still receives its redacted projection, while the
                # interview boundary records the original span as an author
                # note after the bounded pass.
                "messages": [{"role": "user", "text": raw_message}],
            }
            direct_result = await _run_story_interview(
                key, direct_body, _capability=_architect_interview_capability,
            )
            if isinstance(direct_result, JSONResponse):
                return direct_result
            return {
                **direct_result,
                "action": "interview",
                "bounded": {"model_calls": 1, "maximum_model_calls": 1},
            }
        requested_answer = str(body.get("answer_to") or "").strip()
        # The plan surface is primary now: an author may comment on any one
        # of its six visible sections without having to answer the old single
        # "next question".  This stays separate from ``answer_to`` so a stale
        # question id can never be used as an arbitrary cross-section target.
        requested_plan_section = str(body.get("plan_section") or body.get("comment_on") or "").strip()
        if requested_answer and requested_plan_section:
            return JSONResponse({"error": "send answer_to or plan_section, not both"}, status_code=400)
        # An explicit answer always wins over a convenience completion flag.
        # This lets an API client answer a visible plan question and request a
        # follow-through in the same submission without turning the answer
        # into an unauthorised shortcut around that plan.
        requested_completion = completion_request(body, safe_message) and not requested_answer and not requested_plan_section
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()

        def display_names(card: dict) -> dict[str, str]:
            public = public_story_card(ctx, key, card)
            return {
                str(item.get("character")): str(item.get("name"))
                for item in (public.get("cast_details") or [])
                if isinstance(item, dict) and item.get("character") and item.get("name")
            }

        def completion_plan(card: dict, report: dict, current_state: dict) -> dict:
            """Expose the deterministic plan the Architect actually follows.

            The plan is operational metadata, never a model prompt or a second
            autonomous writer.  It lets the primary surface show why a pass is
            safe to automate and which remaining item is deliberately waiting
            for the author.
            """
            return build_completion_plan(
                card,
                report,
                blocked_gap_ids=current_state.get("blocked_gap_ids") or [],
            )

        def author_plan(card: dict, report: dict, current_state: dict) -> dict:
            """Return the complete public-safe plan the Architect presents.

            The gap report remains server-side implementation input.  The
            presentation function projects only high-level card facts and plan
            shape, so adding it to every successful turn does not open another
            path for private scene mechanics or compiler diagnostics.
            """
            return author_plan_presentation(
                card,
                report,
                names=display_names(card),
                state=current_state,
            )

        def with_author_plan(payload: dict, card: dict, report: dict, current_state: dict) -> dict:
            result = dict(payload)
            result["author_plan"] = author_plan(card, report, current_state)
            # The card remains canonical; this is only the safe navigation and
            # specialist-routing projection the control room renders.
            result["control_graph"] = build_story_control_graph(card)
            return result

        def persist_state(card: dict, state: dict) -> dict:
            # State records the exact canonical card it observed.  Excluding
            # ``architect_state`` inside the fingerprint makes this stable
            # across its own bookkeeping writes while detecting any external
            # card edit before a stale question can drive a model pass.
            # Persist the finite completion plan beside actions/history, not
            # merely in the transient response.  This is a safe projection of
            # ids, scopes, reasons, and counts; it is never sent to a model.
            normalized_state = normalize_architect_state(state)
            plan_report, _ = architect_observation(card, names=display_names(card), state=normalized_state)
            normalized_state["plan"] = completion_plan(card, plan_report, normalized_state)
            state.clear()
            state.update(normalized_state)
            state["card_revision"] = card_revision(card)
            fields = dict(card.get("fields") or {})
            fields["architect_state"] = state
            ctx.update_story_fields(key, {"fields": fields})
            next_card = deepcopy(card)
            next_card["fields"] = fields
            return next_card

        # A completion request may carry enough already-authored evidence to
        # make a few runtime representations mechanical rather than creative:
        # for example, a prose "first scene" reset becomes the compiler's
        # literal `opening` restart token.  This pass never invents a scene,
        # location, relationship, or secret.  It runs before model routing and
        # therefore cannot become an invisible retry loop.
        mechanical_adjustments: list[dict] = []
        mechanical_changed = False
        if requested_completion:
            normalized, mechanical_adjustments, _mechanical_unresolved = normalize_mechanical_setup(raw)
            if normalized != raw:
                try:
                    ctx.update_story_fields(key, {
                        field: normalized[field]
                        for field in ("world", "locations", "start", "time_system", "premise", "themes", "cast", "relationships", "fields")
                        if field in normalized
                    })
                    raw = ctx._read_story_data(key)
                    mechanical_changed = True
                except Exception as exc:  # noqa: BLE001
                    return JSONResponse({"error": f"could not save deterministic completion setup: {exc}"}, status_code=400)

        names = display_names(raw)
        state = normalize_architect_state((raw.get("fields") or {}).get("architect_state"))
        if mechanical_changed:
            # A direct author edit normally invalidates a pending question.
            # These exact, evidence-preserving normalizations are part of the
            # same completion request instead, so clear obsolete no-op skips
            # and continue into the newly valid plan.
            state["blocked_gap_ids"] = []
            state["pending_question"] = None
            state.pop("worker_authorization", None)
        report, observed_question = architect_observation(raw, names=names, state=state)
        live_card_revision = card_revision(raw)
        revision_changed = bool(state.get("card_revision") and state.get("card_revision") != live_card_revision)
        # A worker authorization is a one-card capability, not a durable
        # permission.  It is invalid as soon as any other authoring surface or
        # normalization changes canon, even if that change is unrelated to the
        # protected scene it formerly described.
        authorization_invalidated = False
        worker_authorization = state.get("worker_authorization")
        if (isinstance(worker_authorization, dict)
                and str(worker_authorization.get("card_revision") or "") != live_card_revision):
            state.pop("worker_authorization", None)
            authorization_invalidated = True
        pending = state.get("pending_question")

        # Director drag/drop persists the placement first, then reports this
        # compact event to the Architect.  Reconciliation is deliberately
        # model-free: it can mirror a known time value into an arc reference,
        # but it must never decide what a moved hidden scene now *means*.
        move_event = body.get("event")
        if move_event is not None:
            if raw_message:
                return JSONResponse({"error": "send a scene-moved event or an author message, not both"}, status_code=400)
            if not isinstance(move_event, dict) or move_event.get("type") != "scene_moved":
                return JSONResponse({"error": "unsupported Architect event"}, status_code=400)
            moved_scene_id = str(move_event.get("scene_id") or "").strip()
            if not moved_scene_id:
                return JSONResponse({"error": "scene_moved requires scene_id"}, status_code=400)
            fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
            plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), dict) else {}
            events = list(plan.get("events") or []) if isinstance(plan.get("events"), list) else []
            from ..runtime.scenario_compiler import compile_authored_scenario
            from ..authoring.story_architect import scene_has_semantic_gate, sync_scene_time_mirrors

            compiled = compile_authored_scenario(raw)
            catalog = [scene for scene in (compiled.get("scene_catalog") or []) if isinstance(scene, dict)]
            source_index = _director_scene_event_index(events, catalog, moved_scene_id)
            if source_index is None or not isinstance(events[source_index], dict):
                return JSONResponse({"error": f"no editable Day One scene named '{moved_scene_id}'"}, status_code=404)
            source_event = dict(events[source_index])
            canonical_scene_id = str(source_event.get("id") or moved_scene_id).strip()
            current_when = str(source_event.get("when") or "").strip()
            before = move_event.get("before") if isinstance(move_event.get("before"), dict) else {}
            previous_when = str(before.get("slot") or before.get("when") or move_event.get("before_when") or "").strip()
            reconciled, mirror = sync_scene_time_mirrors(
                raw, scene_id=canonical_scene_id, previous_when=previous_when, current_when=current_when,
            )
            reconciled_names = display_names(reconciled)
            reconciled_report, default_question = architect_observation(reconciled, names=reconciled_names, state=state)
            # Moving a scene is an implementation action, not a second
            # conversation surface.  Preserve protected mechanics and any
            # intentionally divergent arc timing, then return the Architect's
            # current *plan-level* decision.  Older versions manufactured a
            # fresh technical question here, which leaked worker concerns
            # such as knowledge boundaries straight into the author chat.
            next_question = default_question
            if scene_has_semantic_gate(source_event):
                # A private move can change the *investigation plan*, but the
                # editor must not interrogate the author with an internal
                # knowledge/evidence/entity checklist.  Keep one reusable,
                # Architect-level question instead.
                next_question = {
                    "id": "authoring-plan-investigation",
                    "section": "first_day",
                    "mode": "protected_interview",
                    "text": ("Before I build the investigation, what should the player be able to notice, pursue, "
                             "and plausibly misunderstand before the private answer comes into view?"),
                    "target": {"section": "first_day", "plan_kind": "investigation"},
                    "scopes": [],
                }
                reply = "I kept the scene's protected mechanics unchanged."
            elif mirror["conflicts"]:
                reply = "I left the intentionally divergent arc timing untouched."
            else:
                reply = (f"I synced {mirror['updated']} derived arc timing reference{'s' if mirror['updated'] != 1 else ''}."
                         if mirror["updated"] else "I checked the move; it did not require a canon rewrite.")
            same_pending_question = (
                isinstance(pending, dict)
                and str(pending.get("id") or "") == str(next_question.get("id") or "")
                and str(pending.get("text") or "") == str(next_question.get("text") or "")
            )
            state["pending_question"] = next_question
            state = append_architect_action(state, {
                "kind": "reconcile", "section": next_question["section"],
                "updated_sections": ["arcs"] if mirror["updated"] else [],
                # This remains the plan question even though a concrete
                # implementation event caused the reconciliation.
                "question_id": next_question["id"],
            })
            if not same_pending_question:
                state = append_architect_history(state, role="architect", text=next_question["text"])
            try:
                persisted = persist_state(reconciled, state)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"could not save Architect reconciliation: {exc}"}, status_code=400)
            public = public_story_card(ctx, key, persisted)
            return with_author_plan({
                "ok": True,
                "phase": "awaiting_author",
                "action": "reconcile",
                "reply": reply,
                "question": next_question,
                "card": public,
                "story": public,
                "gaps": reconciled_report,
                "plan": completion_plan(reconciled, reconciled_report, state),
                "model_route": None,
                "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                              "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
            }, persisted, reconciled_report, state)

        # A card may have changed through a direct edit, focused popup, or
        # another open authoring surface since this question was chosen.  Do
        # not apply the old question's scope to the new card.  Replace it with
        # a fresh deterministic observation and let the author answer that.
        # A scene-moved event is exempt: it is the explicit reconciliation
        # notification for exactly such a placement write and is handled above.
        if revision_changed and not mechanical_changed:
            # A real card revision may make a previously blocked additive gap
            # actionable again.  Do not let old no-op memory shadow new canon.
            state["blocked_gap_ids"] = []
            report, observed_question = architect_observation(raw, names=names, state=state)
            state["pending_question"] = observed_question
            state = append_architect_action(state, {"kind": "assess", "section": observed_question["section"]})
            state = append_architect_history(state, role="architect", text=observed_question["text"])
            try:
                persisted = persist_state(raw, state)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"could not refresh Architect state: {exc}"}, status_code=400)
            public = public_story_card(ctx, key, persisted)
            return with_author_plan({
                "ok": True,
                "phase": "awaiting_author",
                "action": "assess",
                "reply": "The card changed outside this conversation, so I re-checked it before making another pass.",
                "question": observed_question,
                "card": public,
                "story": public,
                "gaps": report,
                "plan": completion_plan(raw, report, state),
                "model_route": None,
                "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                              "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
            }, persisted, report, state)

        # A repeated bootstrap/reload request must be cheap and idempotent.
        # Return a still-live pending question rather than waking a model or
        # replacing it with a nearby diagnostic that changed sort order.  A
        # compiler upgrade can resolve a saved diagnostic without changing the
        # card revision, though, so stale questions are rebased once here.
        if not raw_message and not requested_completion and not requested_plan_section:
            if isinstance(pending, dict):
                live_pending = current_pending_question(raw, report, state, names=names)
                if live_pending is not None and live_pending == pending:
                    return with_author_plan({
                        "ok": True,
                        "phase": "awaiting_author",
                        "action": "assess",
                        "reply": "",
                        "question": pending,
                        "card": public_story_card(ctx, key, raw),
                        "story": public_story_card(ctx, key, raw),
                        "gaps": report,
                        "plan": completion_plan(raw, report, state),
                        "model_route": None,
                        "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                        "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                                      "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
                    }, raw, report, state)
                if live_pending is not None:
                    state["pending_question"] = live_pending
                    try:
                        persisted = persist_state(raw, state)
                    except Exception as exc:  # noqa: BLE001
                        return JSONResponse({"error": f"could not refresh Architect question: {exc}"}, status_code=400)
                    public = public_story_card(ctx, key, persisted)
                    return with_author_plan({
                        "ok": True,
                        "phase": "awaiting_author",
                        "action": "assess",
                        "reply": "I re-checked the card and refreshed the next author decision.",
                        "question": live_pending,
                        "card": public,
                        "story": public,
                        "gaps": report,
                        "plan": completion_plan(persisted, report, state),
                        "model_route": None,
                        "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                        "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                                      "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
                    }, persisted, report, state)
            state["pending_question"] = observed_question
            state = append_architect_history(state, role="architect", text=observed_question["text"])
            state = append_architect_action(state, {"kind": "assess", "section": observed_question["section"]})
            try:
                persisted = persist_state(raw, state)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"could not save Architect state: {exc}"}, status_code=400)
            public = public_story_card(ctx, key, persisted)
            return with_author_plan({
                "ok": True,
                "phase": "awaiting_author",
                "action": "assess",
                "reply": "",
                "question": observed_question,
                "card": public,
                "story": public,
                "gaps": report,
                "plan": completion_plan(persisted, report, state),
                "model_route": None,
                "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                              "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
            }, persisted, report, state)

        # A high-level freeform message answers the current question by
        # default.  This keeps the chat primary without requiring the user to
        # choose a section.  ``answer_to`` is only necessary for a client that
        # wants to prove it is replying to a particular visible question.
        completion_active = False
        active_plan = completion_plan(raw, report, state)
        author_direction = safe_message

        def director_knowledge_answer(task: dict) -> dict:
            """Build the internal-only executor descriptor for one safe task."""
            return {
                # Keep the public action tied to the Architect plan rather
                # than exposing an implementation task id in its history.
                "id": "authoring-plan-investigation",
                "section": "first_day",
                "mode": "director_knowledge",
                "text": "Complete the established Director-only knowledge boundary.",
                "target": {
                    "section": "first_day",
                    "gap_ids": [str(task.get("gap_id") or "")],
                    "event_id": str(task.get("event_id") or ""),
                    "observer_ids": list(task.get("observer_ids") or []),
                },
                "scopes": [],
            }

        if requested_completion:
            safe_scopes = [str(scope) for scope in (active_plan.get("safe_scopes") or [])
                           if str(scope) in {"world", "premise", "cast", "first_day", "time_system"}]
            protected_task = next((task for task in (active_plan.get("protected_tasks") or [])
                                   if isinstance(task, dict) and task.get("kind") == "director_knowledge_boundary"), None)
            authorization = state.get("worker_authorization") if isinstance(state.get("worker_authorization"), dict) else {}
            protected_task_authorized = bool(
                protected_task
                and authorization.get("plan_id") == "authoring-plan-investigation"
                and authorization.get("card_revision") == live_card_revision
            )
            # A worker is deliberately not a Complete-button shortcut.  The
            # Architect must first receive an author-facing plan decision:
            # public scaffold work gets its opening direction, while a
            # Director task gets its investigation plan.  The sole exception
            # is a Director-only task explicitly authorised *after* that
            # protected plan committed against this exact card revision.
            if not protected_task_authorized:
                # The plan has reached an authored hinge (or the card is
                # already structurally ready).  Do not fabricate progress just
                # because the author clicked Complete: report the real stop
                # and ask exactly one question.
                state = normalize_architect_state(state)
                completion_mission = author_direction or "Complete the story from its established canon."
                # The primary UI's Complete button is deliberately repeatable.
                # Once this exact mission is already waiting at this exact
                # authorial hinge, a second click is a read, not a new turn.
                # Do not append the same question/history item again just
                # because the author checked whether the plan had advanced.
                if (
                    not mechanical_adjustments
                    and not authorization_invalidated
                    and isinstance(pending, dict)
                    and str(pending.get("id") or "") == str(observed_question.get("id") or "")
                    and str(state.get("mission") or "") == completion_mission
                ):
                    public = public_story_card(ctx, key, raw)
                    return with_author_plan({
                        "ok": True,
                        "phase": "awaiting_author",
                        "action": "assess",
                        "reply": "The plan is already paused at the next authorial decision.",
                        "question": pending,
                        "card": public,
                        "story": public,
                        "gaps": report,
                        "plan": active_plan,
                        "model_route": None,
                        "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                        "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                                      "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
                    }, raw, report, state)
                # A completion command explicitly supersedes an earlier
                # narrow answer as the Architect's operational mission.
                state["mission"] = completion_mission
                if author_direction:
                    state = append_architect_history(state, role="author", text=author_direction)
                if mechanical_adjustments:
                    state = append_architect_action(state, {
                        "kind": "normalize", "section": "world",
                        "updated_sections": sorted({str(item.get("section") or "world") for item in mechanical_adjustments}),
                        "outcome": "changed",
                    })
                state["pending_question"] = observed_question
                state = append_architect_action(state, {
                    "kind": "assess", "section": observed_question["section"],
                    "question_id": observed_question["id"], "outcome": "waiting",
                })
                state = append_architect_history(state, role="architect", text=observed_question["text"])
                try:
                    persisted = persist_state(raw, state)
                except Exception as exc:  # noqa: BLE001
                    return JSONResponse({"error": f"could not save Architect completion plan: {exc}"}, status_code=400)
                public = public_story_card(ctx, key, persisted)
                hinge = next(iter(active_plan.get("authorial_hinges") or []), {})
                hinge_label = str(hinge.get("title") or "the next authorial decision")
                if safe_scopes:
                    reply = ("I have a bounded public pass ready, but first I need the opening direction in "
                             "your own words.")
                elif protected_task:
                    reply = ("Before I assign the bounded Director pass, I need the investigation plan in your "
                             "own words.")
                else:
                    reply = ("I completed the deterministic setup available from the card. "
                             f"The next useful stop is {hinge_label.lower()}."
                             if mechanical_adjustments else
                             f"The remaining work needs your judgment: {hinge_label}.")
                return with_author_plan({
                    "ok": True,
                    "phase": "awaiting_author",
                    "action": "normalize" if mechanical_adjustments else "assess",
                    "reply": reply,
                    "question": observed_question,
                    "card": public,
                    "story": public,
                    "gaps": report,
                    "plan": completion_plan(persisted, report, state),
                    "model_route": None,
                    "bounded": {"model_calls": 0, "maximum_model_calls": 1},
                    "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                                  "actions": state.get("actions", []), "execution_ready": bool(state.get("worker_authorization"))},
                }, persisted, report, state)
            completion_active = True
            if protected_task:
                author_direction = author_direction or "Complete one established director-only knowledge boundary without changing any other canon."
                answered = director_knowledge_answer(protected_task)
            else:
                author_direction = author_direction or "Complete the public, non-secret structural baseline from established canon."
                answered = {
                    "id": "safe-completion-pass",
                    "section": safe_scopes[0],
                    "mode": "develop",
                    "text": "Complete the remaining public structural baseline in one bounded pass.",
                    "target": {"section": safe_scopes[0], "gap_ids": list(active_plan.get("safe_gap_ids") or [])},
                    "scopes": safe_scopes,
                }
            answer_id = answered["id"]
        else:
            if requested_plan_section:
                # A plan tile is a high-level, live-whitelisted context—not a
                # raw card path.  It can be selected even when another tile
                # is currently highlighted, which lets the author comment on
                # the plan as a whole rather than answering serial prompts.
                answered = plan_comment_target(
                    raw,
                    report,
                    requested_plan_section,
                    names=names,
                    state=state,
                )
                if answered is None:
                    return JSONResponse({"error": "that story-plan section is not available; refresh the Architect plan first"},
                                        status_code=409)
                answer_id = str(answered.get("id") or "")
            else:
                answer_id = requested_answer or (str(pending.get("id")) if isinstance(pending, dict) else "")
                answered = question_for_answer(raw, report, state, answer_id, names=names) if answer_id else observed_question
                if requested_answer and answered is None:
                    return JSONResponse({"error": "that Architect question is no longer current; ask for the latest turn first"},
                                        status_code=409)
                if answered is None:
                    answered = observed_question

            # A user-facing plan question is the handoff point between the
            # Architect and implementation workers.  Re-derive its bounded
            # task from the live plan instead of trusting the persisted
            # question's scopes.  This keeps the response from falling into a
            # broad co-author call (``public_only=False``) after the user has
            # explicitly established the direction for a public baseline.
            if answered.get("id") == "authoring-plan-public-baseline":
                plan_scopes = [str(scope) for scope in (active_plan.get("safe_scopes") or [])
                               if str(scope) in {"world", "premise", "cast", "first_day", "time_system"}]
                if plan_scopes:
                    completion_active = True
                    answered = {
                        "id": "authoring-plan-public-baseline",
                        "section": plan_scopes[0],
                        "mode": "develop",
                        "text": "Implement the established public baseline in one bounded pass.",
                        "target": {
                            "section": plan_scopes[0],
                            "plan_kind": "public_baseline",
                            "gap_ids": list(active_plan.get("safe_gap_ids") or []),
                        },
                        "scopes": plan_scopes,
                    }
            # An investigation-plan answer is deliberately *not* handed
            # straight to the Director knowledge worker.  It is the author's
            # high-level brief for that worker, so the protected interview
            # must first commit it to the card.  A later completion pass can
            # safely fill a remaining, bounded knowledge entry from the
            # established plan.  Otherwise the worker would receive only its
            # generic scene instruction and silently discard the author's
            # stated investigative intent.

        # The mission is intentionally author-visible operational context.  A
        # direct, unthreaded message resets it; an answer retains the broader
        # direction that led to the current question.
        if completion_active or not state.get("mission") or not answer_id or bool(body.get("new_mission")):
            state["mission"] = author_direction
        state = append_architect_history(state, role="author", text=author_direction)

        # The primary Architect owns this hand-off.  A client may choose only a
        # visible plan section or answer a live question; the planner derives
        # the scout, bounded worker, and reviewer gate from that trusted route.
        # This is intentionally metadata, not a second agent runtime—the
        # existing API boundaries still own visibility, validation, and commit.
        try:
            work_order_public_only = (
                completion_active and str(answered.get("mode") or "") == "develop"
            )
            work_order = resolve_architect_work_order(
                answered.get("scopes"),
                section=str(answered.get("section") or ""),
                mode=str(answered.get("mode") or "develop"),
                public_only=work_order_public_only,
            )
        except ValueError as exc:
            return JSONResponse({"error": f"could not resolve Architect work order: {exc}"}, status_code=409)

        model_route: dict | None = None
        action = "develop"
        reply = ""
        updated_sections: list[str] = []
        latest = raw
        model_calls = 0
        model_changed = False

        # A message made entirely of model-hidden spans is an author-side note,
        # not a prompt.  Persist it below and keep the current question alive;
        # calling a model with an intentionally empty view would add latency
        # without a permitted fact to reason about.
        if not safe_message and not completion_active:
            action = "assess"
            reply = "I saved that as an author-only note; it was not sent to a model."
        elif work_order.worker == "oracle":
            is_director_knowledge = work_order.mode == "director_knowledge"
            action = "knowledge" if is_director_knowledge else "interview"
            model_calls = 1
            target = answered.get("target") if isinstance(answered.get("target"), dict) else {}
            event_id = str(target.get("event_id") or "").strip()
            observer_ids = [str(item).strip() for item in (target.get("observer_ids") or [])
                            if isinstance(item, str) and item.strip()]
            knowledge_direction = (
                "Complete only the director-only knowledge boundary for the existing scene "
                f"'{event_id}'. Use the established private scene context; add concise facts only for these exact "
                f"existing observer keys: {', '.join(observer_ids)}. Preserve every other Day One field exactly."
            )
            interview_body: dict[str, object] = {
                "focus": answered["section"],
                "target": answered.get("target") or {"section": answered["section"]},
                "messages": [{"role": "user", "text": knowledge_direction if is_director_knowledge else raw_message}],
            }
            if is_director_knowledge:
                interview_body["target"] = {"section": "first_day"}
                interview_body["knowledge_only"] = {
                    "event_ids": [event_id],
                    "observer_ids": observer_ids,
                }
            if body.get("model"):
                interview_body["model"] = body["model"]
            # The primary Architect is only a coordinator.  Preserve an
            # explicit fallback retry as it crosses into the protected
            # interview boundary; never let that boundary silently choose a
            # second model itself.
            if body.get("use_fallback") is True:
                interview_body["use_fallback"] = True
            result = await _run_story_interview(
                key,
                interview_body,
                _capability=_architect_interview_capability,
                _work_order=work_order,
            )
            if isinstance(result, JSONResponse):
                try:
                    failure = json.loads(result.body.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    failure = {"error": "protected Architect turn failed"}
                return JSONResponse({**failure, "phase": "interview", "action": action,
                                     "bounded": {"model_calls": 1, "maximum_model_calls": 1}},
                                    status_code=result.status_code)
            model_route = result.get("model_route") if isinstance(result.get("model_route"), dict) else None
            updated_sections = [str(name) for name in (result.get("patch") or {}) if str(name)]
            model_changed = bool(result.get("patch"))
            try:
                latest = ctx._read_story_data(key)
            except FileNotFoundError:
                latest = raw
            reply = ("I assigned a director-only knowledge boundary without changing the scene."
                     if is_director_knowledge else "I’ve incorporated that into the protected authoring layer.")
        elif work_order.worker == "task":
            model_calls = 1
            scopes = [str(scope) for scope in (answered.get("scopes") or [])
                      if str(scope) in {"world", "premise", "cast", "first_day", "time_system"}]
            if not scopes:
                # This is defensive only: all generic Architect questions are
                # constructed with a safe scope, but a stale persisted state
                # must never trigger an unconstrained model write.
                return JSONResponse({"error": "the saved Architect question has no safe development scope"}, status_code=409)
            # The mission is deliberately passed as an explicit, redacted
            # author instruction rather than smuggled in through the Story
            # card projection.  That preserves continuity across Architect
            # turns without allowing its operational history to become canon
            # or generic ambient model context.
            mission = str(state.get("mission") or "").strip()
            developer_brief = author_direction
            if completion_active:
                developer_brief = (
                    "AUTONOMOUS COMPLETION PASS: fill only the requested public structural gaps. "
                    "Do not invent a private answer, a relationship, a loop policy, an entity schedule, "
                    "or a hidden/evidence/knowledge gate. Stop at those authorial hinges.\n\n"
                    f"AUTHOR DIRECTION:\n{author_direction}"
                )[:8000]
            elif mission and mission != author_direction:
                developer_brief = f"ARCHITECT MISSION:\n{mission}\n\nLATEST AUTHOR DIRECTION:\n{author_direction}"[:8000]
            develop_body: dict[str, object] = {
                "brief": developer_brief,
                "scope": scopes,
                # The callee recomputes and exact-matches this metadata before
                # use.  It cannot become a client-selected worker escalation.
                "work_order": work_order.as_dict(),
                # Completion may make public connective additions only.  This
                # is enforced in `apply_develop_proposal`, not merely asked of
                # the model in prose.
                "public_only": completion_active,
            }
            if completion_active and isinstance(active_plan.get("public_population_task"), dict):
                # The card-development endpoint recomputes and validates this
                # descriptor before it uses it.  Passing it here only selects
                # the special sanitizer-backed cast task; it does not grant
                # the model extra authority.
                develop_body["public_population_task"] = active_plan["public_population_task"]
            if body.get("model"):
                develop_body["model"] = body["model"]
            # Same explicit author retry for the generic development route.
            # `develop_story_card` owns provider resolution and returns its
            # safe route metadata in either a success or retryable timeout.
            if body.get("use_fallback") is True:
                develop_body["use_fallback"] = True
            result = await develop_story_card(key, develop_body)
            if isinstance(result, JSONResponse):
                try:
                    failure = json.loads(result.body.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    failure = {"error": "Architect development pass failed"}
                return JSONResponse({**failure, "phase": "develop", "action": action,
                                     "bounded": {"model_calls": 1, "maximum_model_calls": 1}},
                                    status_code=result.status_code)
            model_route = result.get("model_route") if isinstance(result.get("model_route"), dict) else None
            updated_sections = [str(section) for section in (result.get("updated_sections") or []) if str(section)]
            model_changed = bool(updated_sections or result.get("patch") or (result.get("created") or {}).get("characters"))
            try:
                latest = ctx._read_story_data(key)
            except FileNotFoundError:
                latest = raw
            if updated_sections:
                reply = "I made one additive pass over " + ", ".join(updated_sections) + "."
            else:
                reply = "I checked that direction against the existing canon; it did not justify a safe automatic rewrite."
        else:
            return JSONResponse({"error": "the saved Architect work order has no executable worker"}, status_code=409)

        # The generic development path is given only ``safe_message``.  Keep
        # any accompanying hidden author material in the card, wrapped, but
        # never make it part of a generic co-author's next context window.
        # ``story_interview`` already owns this exact preservation behavior for
        # protected turns, so avoid duplicating notes on that path.
        if hidden_notes and action not in {"interview", "knowledge"}:
            latest = deepcopy(latest)
            latest_fields = dict(latest.get("fields") or {})
            notes = [str(item) for item in (latest_fields.get("author_notes") or [])
                     if isinstance(item, str) and item.strip()]
            for note in hidden_notes:
                wrapped = wrap_model_hidden(note)
                if wrapped not in notes:
                    notes.append(wrapped)
            latest_fields["author_notes"] = notes[-24:]
            latest["fields"] = latest_fields

        # A state write is intentionally separate from the canon write above:
        # the development/interview boundary owns Story validation; this one
        # only remembers the Architect's mission and the next safe question.
        # Keep the in-memory state accumulated for this turn.  The validated
        # canon write above deliberately does not know about the new author
        # message yet, so reloading its older state here would drop the very
        # history/pending-question record this coordinator exists to retain.
        state = normalize_architect_state(state)
        state["mission"] = state.get("mission") or author_direction
        latest_card_revision = card_revision(latest)
        # Any canon change consumes an authorization granted for the previous
        # snapshot.  The sole exception is the protected plan interview below,
        # which creates a fresh, post-commit authorization for its own plan.
        if latest_card_revision != live_card_revision or action == "knowledge":
            state.pop("worker_authorization", None)
        answered_target = answered.get("target") if isinstance(answered.get("target"), dict) else {}
        if (
            action == "interview"
            and (
                str(answered.get("id") or "") == "authoring-plan-investigation"
                or str(answered_target.get("plan_kind") or "") == "investigation"
            )
            and safe_message
        ):
            state["worker_authorization"] = {
                "plan_id": "authoring-plan-investigation",
                "card_revision": latest_card_revision,
            }
        if mechanical_adjustments:
            state = append_architect_action(state, {
                "kind": "normalize", "section": "world",
                "updated_sections": sorted({str(item.get("section") or "world") for item in mechanical_adjustments}),
                "outcome": "changed",
            })
        if action in {"develop", "knowledge"} and not model_changed:
            target = answered.get("target") if isinstance(answered.get("target"), dict) else {}
            gap_ids = [str(item) for item in (target.get("gap_ids") or []) if str(item)]
            if not gap_ids:
                known_ids = {
                    str(item.get("id")) for item in (report.get("gaps") or [])
                    if isinstance(item, dict) and item.get("id")
                }
                if str(answered.get("id") or "") in known_ids:
                    gap_ids = [str(answered["id"])]
            if gap_ids:
                state = block_gap_ids(state, gap_ids)
                reply = (
                    "That director-only assignment did not establish a safe new observer fact, so I left the "
                    "knowledge boundary for your decision instead of forcing it."
                    if action == "knowledge" else
                    "That bounded pass would have to rewrite established canon, so I marked it as a "
                    "decision instead of repeating the same automatic attempt."
                )
        latest_names = display_names(latest)
        latest_report, next_question = architect_observation(latest, names=latest_names, state=state)
        if action == "develop" and not model_changed and answered.get("id") == "starting-spark":
            next_question = {
                "id": "clarify-starting-spark",
                "section": "world",
                "mode": "protected_interview",
                "text": ("I need one concrete anchor before I can build safely: name a place, a person, "
                         "or the immediate problem in the opening."),
                "target": {"section": "world"},
                "scopes": [],
            }
        state = append_architect_action(state, {
            "kind": action,
            "section": answered.get("section"),
            "sections": answered.get("scopes") or [],
            "updated_sections": updated_sections,
            "question_id": answered.get("id"),
            "outcome": ("changed" if model_changed else "note" if action == "assess" else "no_change"),
        })
        state["pending_question"] = next_question
        state = append_architect_history(state, role="architect", text=next_question["text"])
        try:
            latest = persist_state(latest, state)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save Architect state: {exc}"}, status_code=400)
        public = public_story_card(ctx, key, latest)
        next_plan = completion_plan(latest, latest_report, state)
        # This is intentionally only an opaque readiness signal for the
        # authoring surface.  The next request still re-derives and validates
        # the exact Director task from the live card; no private task details
        # cross into the UI merely because its plan is ready to apply.
        execution_ready = bool(state.get("worker_authorization") and next_plan.get("protected_tasks"))
        return with_author_plan({
            "ok": True,
            "phase": "awaiting_author",
            "action": action,
            "reply": reply,
            "question": next_question,
            "card": public,
            "story": public,
            "gaps": latest_report,
            "plan": next_plan,
            "model_route": model_route,
            "bounded": {"model_calls": model_calls, "maximum_model_calls": 1},
            "architect": {"mission": state.get("mission", ""), "history": state.get("history", []),
                          "actions": state.get("actions", []), "execution_ready": execution_ready,
                          "work_order": work_order.as_dict()},
        }, latest, latest_report, state)

    @app.post("/api/stories/{key}/weave-bonds")
    def weave_bonds(key: str, body: dict):
        """PROPOSE the relationship web (nothing saved — the roster reviews and accepts).
        The register is daylight-over-depth: every bond gets an innocent, specific surface
        read per side AND a hidden undercurrent rooted in the characters' wounds/lies/secrets,
        plus a trajectory for when the truth surfaces. Existing bonds are respected (only new
        pairs are proposed). Returns {bonds: [Relationship-shaped dicts]}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        cast_keys = [m.get("character") for m in sd.get("cast") or [] if m.get("character")]
        if len(cast_keys) < 2:
            return JSONResponse({"error": "need at least two cast members"}, status_code=400)
        loc_names = {l.get("id"): l.get("name") or l.get("id") for l in sd.get("locations") or []}
        homes = {m.get("character"): loc_names.get(m.get("home"), "") for m in sd.get("cast") or []}
        lines = []
        for ck in cast_keys:
            ch = ctx.base_settings.characters.get(ck)
            f = (getattr(ch, "fields", None) or {}) if ch else {}
            nm = getattr(ch, "name", ck) or ck
            bits = [f"{ck} ({nm})"]
            for fk in ("role", "want", "lie", "contradiction", "wound", "secret", "temperament"):
                if (f.get(fk) or "").strip():
                    bits.append(f"  {fk}: {str(f[fk]).strip()[:220]}")
            if homes.get(ck):
                bits.append(f"  lives at: {homes[ck]}")
            lines.append("\n".join(bits))
        existing = {(r.get("source"), r.get("target")) for r in sd.get("relationships") or []}
        existing |= {(t, s) for (s, t) in existing}
        parts = sd.get("premise_parts") or {}
        ctx_text = "\n".join(filter(None, [
            f"Premise: {sd.get('premise') or ''}",
            f"Tone: {sd.get('tone') or ''}",
            "\n".join(f"{k}: {v}" for k, v in parts.items() if (v or '').strip()),
            "\nCAST (their hidden harnesses — the undercurrents grow FROM these):",
            "\n".join(lines),
            f"\nBonds that already exist (do NOT re-propose these pairs): "
            f"{', '.join(f'{s}-{t}' for s, t in sorted(existing)) or '(none)'}",
        ]))
        stances = ["devoted", "warm", "neutral", "strained", "hostile"]
        schema = {"type": "object", "additionalProperties": False, "required": ["bonds"],
                  "properties": {"bonds": {"type": "array", "maxItems": 8, "items": {
                      "type": "object", "additionalProperties": False,
                      "required": ["source", "target", "nature", "stance", "dynamic",
                                   "target_stance", "target_dynamic", "potential", "trajectory"],
                      "properties": {
                          "source": {"type": "string", "enum": cast_keys},
                          "target": {"type": "string", "enum": cast_keys},
                          "nature": {"type": "string", "maxLength": 40,
                                     "description": "PLAIN mundane label, 1-4 words: 'landlady', 'childhood friend', 'rival herbalist'. No poetry."},
                          "stance": {"type": "string", "enum": stances},
                          "dynamic": {"type": "string",
                                      "description": "ONE observable daylight HABIT of source toward target, one short sentence — a thing a bystander could watch: 'steals her pens, denies it badly'. Behavior only, no analysis."},
                          "target_stance": {"type": "string", "enum": stances},
                          "target_dynamic": {"type": "string",
                                             "description": "target's observable habit toward source, one short sentence, same rules"},
                          "potential": {"type": "string",
                                        "description": "the UNDERCURRENT: what is secretly true between them RIGHT NOW, grown from a named wound/lie — a fact, not a prediction. 1-2 sentences."},
                          "trajectory": {"type": "string", "description": "from → to: how the bond turns when the hidden thing surfaces (1 sentence; predictions live HERE, not in potential)"},
                      }}}}}
        system = (
            "You weave the RELATIONSHIP WEB for a character-driven story. The register is daylight "
            "innocence over hidden depth: the surface is light and CONCRETE — running jokes, petty "
            "thefts, borrowed things never returned, dumb shared rituals — while underneath, every "
            "bond carries something secretly true, grown from the characters' named wounds and lies.\n"
            "Field discipline:\n"
            "- nature = a label a census would record. dynamic = an observable habit, filmable.\n"
            "- potential = a present-tense hidden FACT (who knows what, who is really what, what "
            "actually happened between them). NOT a prediction.\n"
            "- trajectory = the prediction: from → to when the hidden fact surfaces.\n"
            "The best undercurrents make the innocent surface RE-READ as something else entirely "
            "once known. Asymmetry is good: the two sides may misread each other. Not every bond is "
            "dark. Propose only bonds that matter; skip pairs with nothing real between them.")
        # Reasoning ON for construction quality; fall back to non-thinking if the reasoning
        # channel breaks structured output.
        out = {}
        for effort in ("high", "none"):
            provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": effort})
            if provider is None or not hasattr(provider, "generate_text"):
                return JSONResponse({"error": "no text model available"}, status_code=400)
            try:
                out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
                break
            except Exception as exc:  # noqa: BLE001
                if effort == "none":
                    return JSONResponse({"error": f"weave failed: {exc}"}, status_code=500)
        bonds = []
        for b in out.get("bonds") or []:
            s, t = b.get("source"), b.get("target")
            if not s or not t or s == t or (s, t) in existing:
                continue
            existing.add((s, t)); existing.add((t, s))   # dedupe within the proposal too
            bonds.append({"id": f"r-{s}-{t}", **b})
        if bonds:   # pipe into the work queue: proposals survive navigation until reviewed
            from ..records.cards import set_pending
            try:
                ctx.update_story_fields(key, {"fields": set_pending(_story_fields(key), "bonds", bonds)})
            except FileNotFoundError:
                pass   # draft story (genesis, not committed) — review stays in-page only
        return {"bonds": bonds}

    @app.post("/api/stories/{key}/conditions/generate")
    def conditions_generate(key: str, body: dict):
        """PROPOSE the setting's recurring STAGES — the modes this world moves through (seasons,
        event-states, place-states) that will visibly change daily life and switch on situational
        character content. Reasoned from the premise/tone/philosophy + the existing geography.
        Not saved — the Map tab reviews and keeps them. Returns {conditions: [Condition-shaped]}."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        sd = st.model_dump()
        parts = sd.get("premise_parts") or {}
        locs = ", ".join(l.get("name") or l.get("id") for l in (sd.get("locations") or [])) or "(none yet)"
        have = [(c.get("name") or "").strip() for c in (sd.get("conditions") or []) if (c.get("name") or "").strip()]
        ctx_text = "\n".join(filter(None, [
            f"PREMISE: {sd.get('premise') or ''}",
            f"TONE: {sd.get('tone') or ''}",
            f"PHILOSOPHY: {parts.get('philosophy') or ''}",
            f"PLACES: {locs}",
            f"ALREADY HAVE (don't repeat): {', '.join(have)}" if have else "",
        ]))
        schema = {"type": "object", "additionalProperties": False, "required": ["conditions"],
                  "properties": {"conditions": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
                      "type": "object", "additionalProperties": False,
                      "required": ["name", "kind", "description", "effect"],
                      "properties": {
                          "name": {"type": "string", "description": "the stage, plainly named — 'The flood season', 'The deep snows'"},
                          "kind": {"type": "string", "enum": ["seasonal", "event", "place"]},
                          "description": {"type": "string", "description": "what it IS — the objective world-change, 1-2 sentences"},
                          "effect": {"type": "string", "description": "how it bends DAILY LIFE: what stops, what becomes dangerous or possible, what ordinary people do differently while it holds"},
                      }}}}}
        system = (
            "You define the recurring STAGES a story-world moves through — the modes it enters and "
            "leaves that reshape ordinary life while they last. Think seasons (deep snow, flood, "
            "drought), event-states (a siege, a festival, a plague), and place-states (a dungeon "
            "opens beneath the city, the tide exposes a causeway). Each must: recur or toggle (a "
            "persistent MODE, not a one-off plot beat), visibly change what people can and can't do, "
            "and grow from THIS world's specifics — its geography, its central pressure. Concrete "
            "and lived, never generic 'the weather changes'. These are the stages that will bring out "
            "different sides of the cast, so make each one a genuinely different way to live.")
        provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "high"})
        if provider is None or not hasattr(provider, "generate_text"):
            provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        try:
            out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
        except Exception:  # noqa: BLE001 — reasoning channel can break structured output
            try:
                provider = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
                out = (provider.generate_text(system=system, prompt=ctx_text, emits=schema).data) or {}
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"condition gen failed: {exc}"}, status_code=500)
        conds, seen = [], {c.lower() for c in have}
        for c in out.get("conditions") or []:
            nm = (c.get("name") or "").strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            conds.append({"id": re.sub(r"[^\w]+", "_", nm.lower()).strip("_") or f"cond{len(conds)}",
                          "name": nm, "kind": (c.get("kind") or "").strip(),
                          "description": (c.get("description") or "").strip(),
                          "effect": (c.get("effect") or "").strip()})
        if conds:   # pipe into the work queue: proposals survive navigation until reviewed
            from ..records.cards import set_pending
            try:
                ctx.update_story_fields(key, {"fields": set_pending(_story_fields(key), "conditions", conds)})
            except FileNotFoundError:
                pass   # draft story (genesis, not committed) — review stays in-page only
        return {"conditions": conds}

    @app.get("/api/stories/{key}/conditions/usage")
    def conditions_usage(key: str):
        """LINT the setting stages: how many cast exemplars each stage would activate
        (`when:<id>` tags across the cast's banks), plus ORPHANS — when: ids bound to no
        existing stage (a renamed/deleted condition silently kills its content otherwise).
        Returns {usage: {cond_id: count}, orphans: {when_id: count}}."""
        from ...server.services import lorebook_store as _LS
        from ..pipeline.character_scaffold import entry_when
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        known = {c.id for c in (st.conditions or []) if c.id}
        usage = {cid: 0 for cid in known}
        orphans: dict[str, int] = {}
        for m in st.cast:
            scope = re.sub(r"[^\w\-]+", "_", str(m.character))
            for e in _LS.load_lorebook(ctx.root, scope):
                w = entry_when(e)
                if not w:
                    continue
                if w in known:
                    usage[w] += 1
                else:
                    orphans[w] = orphans.get(w, 0) + 1
        return {"usage": usage, "orphans": orphans}

    # ── The WORK QUEUE — pending approvals + card todos as one ordered, non-locking list ──
    def _story_fields(key: str) -> dict:
        return dict((ctx._read_story_data(key).get("fields")) or {})

    @app.get("/api/stories/{key}/pending")
    def pending_get(key: str):
        """This story's pending approvals ({kind: {items}}) — generation output awaiting
        a human decision, persisted so review survives navigation. See stories/queue.py."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {"pending": _story_fields(key).get("pending") or {}}

    @app.put("/api/stories/{key}/pending/{kind}")
    def pending_put(key: str, kind: str, body: dict):
        """Set one kind's pending items (the review surfaces call this as the user keeps or
        dismisses proposals; an empty list clears the kind and its queue entry)."""
        from ..records.cards import PENDING_KINDS, set_pending
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if kind not in PENDING_KINDS:
            return JSONResponse({"error": f"unknown pending kind '{kind}'"}, status_code=400)
        fields = set_pending(_story_fields(key), kind, (body or {}).get("items") or [])
        ctx.update_story_fields(key, {"fields": fields})
        return {"pending": fields["pending"]}

    @app.get("/api/stories/{key}/queue")
    def story_queue(key: str):
        """The ordered work queue: for each card layer (overview → cast), pending approvals
        first, then the layer's todos. Advisory order — every item deep-links to its tab."""
        from ..records.cards import build_card
        from ..records.cards import build_queue
        from ...server.services.prompts import style_anchor
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        return {"items": build_queue(card, _story_fields(key).get("pending"))}

    @app.get("/api/stories/{key}/card")
    def story_card(key: str):
        """The story's CONTEXT CARD — the layered spine every generator reads (see
        stories/card.py). One layer per tab (overview → cast), each with content +
        the step's `todo` checklist. The cast layer's per-sprite deep zoom is
        POST /api/characters/{key}/sprite-stack."""
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..records.cards import build_card
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        card["story"] = key
        return card

    @app.post("/api/stories/{key}/card/{layer}")
    def story_card_patch(key: str, layer: str, body: dict):
        """Mutate ONE card layer — the chokepoint narrative functions (plot dialogue,
        play consolidation) route through. The patch is filtered to the layer's
        whitelisted story fields (card.LAYER_FIELDS) and lands via the validated
        update path. Returns the rebuilt layer so callers can re-check the step."""
        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        from ..records.cards import build_card, layer_patch_fields
        try:
            fields = layer_patch_fields(layer, body or {})
        except KeyError:
            return JSONResponse({"error": f"layer '{layer}' has no patchable story fields"},
                                status_code=400)
        if not fields:
            return JSONResponse({"error": "nothing patchable for this layer in the body"},
                                status_code=400)
        try:
            ctx.update_story_fields(key, fields)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        st = ctx.base_settings.stories.get(key)
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        lay = next((l for l in card["layers"] if l["id"] == layer), None)
        return {"ok": True, "layer": lay}

    def _rebuilt_layer(key: str, layer: str):
        st = ctx.base_settings.stories.get(key)
        from ..records.cards import build_card
        from ...server.services.prompts import style_anchor
        manifests = {m.character: ctx.portrait_manifest(m.character) for m in st.cast}
        card = build_card(st.model_dump(), manifests, global_style=style_anchor(ctx.root))
        return next((l for l in card["layers"] if l["id"] == layer), None)

    _SECTION_BRIEF = {
        "overview": "the WORLD at its highest level — designed top-down, not enumerated. Two things carry "
                    "it: the PRINCIPLE (premise_parts/root — the one law this world runs on, its defining "
                    "trait: 'reality runs on a spendable life-energy', 'the gods are dead and magic answers "
                    "to whoever takes it') and the CONFLICT (premise_parts/question — the unresolvable "
                    "dilemma that principle forces, with two defensible sides). The premise/tone/themes "
                    "follow from those. Edit with merge on premise_parts (key = root or question). When the "
                    "writer RESHAPES the world (a new principle, a different premise), update "
                    "premise_parts/root, premise_parts/question, AND premise together in the SAME turn so "
                    "they stay consistent — never rewrite the premise prose while leaving root or question "
                    "describing the old world. 'Change the principle' means edit premise_parts/root (and "
                    "re-derive the question), not the premise text. Do NOT "
                    "enumerate factions, traditions, or populations here — those particulars belong to the "
                    "cast, locations, and scenes, made when the story needs them. Never good-vs-evil; both "
                    "sides of the conflict must be righteous.",
        "map": "the WORLD — locations (each an item with an id) and the recurring setting conditions/stages.",
        "relationships": "the CAST & fixed BONDS — relationship items (each with source/target/nature and the "
                         "hidden potential/trajectory). Warmth drifts in play; you set the fixed structure.",
        "plot": "the PROGRESSION — arcs and the storyboard beats (the staged plan).",
    }

    @app.post("/api/stories/{key}/card/{layer}/chat")
    async def story_card_chat(key: str, layer: str, body: dict):
        """The SECTION COLLABORATOR — a thinking partner AND editor for ONE section, using
        HASH-ANCHORED (hashline) ops in the OhMyPi style.

        The model is shown an ANCHORED view of the editable nodes (path + #hash + preview).
        To change something it returns `ops`: each points at a node by slash-path AND cites
        the #hash it saw there. We re-read the live story, recompute each node's hash, and
        REJECT the op if the node drifted since the model read it — a stale read can no
        longer silently clobber a field edited elsewhere. Stale ops come back in
        `rejected` for the client to surface; the others apply through the existing
        whole-Story-validated write path.

        Returns {reply, applied:[{path,op}], rejected:[{path,reason, current_hash?}],
                 before:{top_field:old}, layer?}. `before` holds the pre-edit top-level
        fields for the client's Undo. Body {messages:[{role,text}]}."""
        import asyncio
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ...server.services import config_files as _cf
        from ..records.cards import LAYER_FIELDS
        from ..records.anchors import anchored_view, apply_ops, any_stale_rejections, merge_results, partial_reply
        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if layer not in LAYER_FIELDS:
            return JSONResponse({"error": f"layer '{layer}' isn't editable by chat"}, status_code=400)
        messages = [m for m in ((body or {}).get("messages") or []) if isinstance(m, dict) and m.get("text")]
        if not messages:
            return JSONResponse({"error": "say something"}, status_code=400)
        # EFFICIENT ROUTING: hand the editor ONLY this layer's raw editable fields — the actual
        # arcs/bonds/conditions/premise_parts with their REAL ids/values, each tagged with a
        # content-hash anchor. The model edits by pointing at these anchors, so it never has to
        # re-send a whole list to change one item. Nothing else from the story is loaded.
        allowed = list(LAYER_FIELDS[layer])
        try:
            raw = ctx._read_story_data(key)
        except FileNotFoundError:
            raw = st.model_dump()
        # The anchored view is the model's map of what it may touch. Built from the LIVE
        # story so the anchors match what `apply_ops` will verify against right after.
        view = anchored_view(raw, allowed)
        # Structured `ops` (a real array — not a string-encoded patch). Each op names its
        # target by slash-path and cites the #hash it saw; merge/set/remove cover every
        # edit at field, item, and sub-field granularity under one vocabulary.
        schema = {"type": "object", "additionalProperties": False, "required": ["reply", "ops", "suggestions"],
                  "properties": {
                      "reply": {"type": "string", "description": "your conversational turn to the writer — "
                                "an ANSWER if they asked a question, a brief note if you made a change. Keep "
                                "it SHORT; put the concrete options in `suggestions`, not a wall of prose"},
                      "suggestions": {"type": "array", "description": "a short list (2–5) of concrete things "
                                "the writer could ADDRESS OR DEVELOP next — forks, gaps, open threads, or "
                                "directions to pursue. Each is a brief actionable phrase the writer can pick "
                                "to run with. In INTERVIEW MODE these are the forks. EMPTY only if truly "
                                "nothing is open.",
                                "items": {"type": "string"}},
                      "ops": {"type": "array", "description": "SURGICAL edits — one entry per node you "
                              "change. EMPTY if you're only discussing. Each entry: "
                              "{path, anchor, op, value}.",
                              "items": {"type": "object", "additionalProperties": False,
                                        "required": ["path", "op"],
                                        "properties": {
                                            "path": {"type": "string", "description": "slash-path of the "
                                                      "node, exactly as shown in the ANCHORED SECTION"},
                                            "anchor": {"type": "string", "description": "the #hash shown "
                                                       "beside that path (copy it). OMIT only when CREATING "
                                                       "a brand-new node that isn't in the view yet."},
                                            "op": {"type": "string", "enum": ["set", "merge", "remove"],
                                                   "description": "set=replace the node's value; "
                                                   "merge=deep-merge an object into a dict node; "
                                                   "remove=delete the node"},
                                            "value": {"description": "the new value for set/merge "
                                                       "(string, object, list…). OMIT for remove."}}}}}}
        # A blank story hasn't found its core question yet — the chat runs an INTERVIEW (below)
        # instead of waiting for edit commands. "Thin" = no premise and no premise_parts.question.
        _pp = (raw.get("premise_parts") or {}) if isinstance(raw, dict) else {}
        _thin = not (raw.get("premise") or "").strip() and not (_pp.get("question") or "").strip()
        system = (
            "You are the writer's COLLABORATOR on ONE section of their story bible — both a thinking "
            "partner and an editor. Choose your mode from the writer's LATEST message:\n"
            "• DISCUSSION — they ask a question, want your read, want to brainstorm, or ask you to weigh "
            "in: ANSWER substantively and specifically in `reply`, like a sharp co-writer who knows this "
            "story. Set `ops` to []. Do NOT edit just because you're talking. Questions ('what's the "
            "tension?', 'is this premise strong?', 'who is X?') get an answer, never an edit.\n"
            "• CHANGE — they explicitly ask you to change / add / remove / rewrite something: emit `ops`, "
            "one per node you change. Each op POINTS at its target by the slash-path from the ANCHORED "
            "SECTION and cites the #hash shown beside it. Three ops cover everything:\n"
            "   - set: replace the node's value (a string, a list, a whole object…).\n"
            "   - merge: deep-merge a JSON OBJECT into a dict node (use this to update ONE key of "
            "premise_parts without disturbing the others — value:{root:'…'}).\n"
            "   - remove: delete the node (a list item by id, a dict key).\n"
            "ANCHOR RULE: copy the #hash exactly as shown. OMIT `anchor` ONLY when creating a node that "
            "isn't in the view (a brand-new premise_parts key, a new condition). If your anchor is stale "
            "the edit is rejected — the writer will be told which paths changed, and can ask you again.\n"
            "You may edit at ANY granularity: a top-level field (premise), one dict key "
            "(premise_parts/root), one list item by id (arcs/arc-2), or one sub-field of an item "
            "(arcs/arc-2/premise). Prefer the SMALLEST change — set the one sub-field, not the whole item.\n"
            "REPLY MUST MATCH OPS — never say in `reply` that you changed, updated, added, or removed "
            "something unless you emitted an op for it THIS turn. If you only rewrote the premise, do not "
            "claim you also updated the principle or the question. Saying 'Done' while the ops are empty "
            "(or don't cover what you claim) is a hard failure — the writer trusts the reply.\n"
            "Only these top-level fields are editable: " + ", ".join(allowed) + ". Keep prose concrete and "
            "in the story's voice. NAMES: every faction, creed, religion, order or organization is ONE "
            "coined word — never two words, never 'The <Adjective> <Noun>' (Crownsworn, Unbound, "
            "Emberwake — NOT 'Harvest Binding').\n"
            "THEMATIC CORE: this story's cast should share ONE quiet, plainly-lived human question — never "
            "a debate topic, never a word like 'betrayal' or 'worth existing' stated outright. If it sounds "
            "like a thesis, it is wrong; it is a plain feeling stated plainly ('loving people you know you "
            "will lose', 'wanting to belong somewhere'). This question is never spoken aloud inside the "
            "story's own content — it lives entirely in what people do, notice, and can't let go of. When "
            "you write or edit a character, their concrete specifics — a power, a habit, an object, a fear, "
            "a wound — should function as a STANCE toward that question made literal, not an independent "
            "trait bolted on next to a personality. Rewrite's Shizuru isn't 'a character about impermanence' "
            "with impermanence as a theme note; her power is healing that erases the memory of the person "
            "healed — the cost of her gift IS the theme, mechanically, not metaphorically. Her psychological "
            "backstory can stay understated; what can't is that everything about her resonates with the "
            "same one idea. Aim for that: pick the concrete detail that makes this person's specifics "
            "inseparable from the story's question, not a detail that happens to sit next to it.\n"
            "ALWAYS, in BOTH modes: keep `reply` short and fill `suggestions` with 2–5 concrete things "
            "the writer could address or develop next — the open threads, gaps, or directions that follow "
            "from where the story is now. These are optional picks the writer can run with, not commands. "
            "Leave `suggestions` empty only when there is genuinely nothing open.\n"
            f"SECTION: {_SECTION_BRIEF.get(layer, layer)}")
        if _thin and layer in ("overview", "map"):
            system += (
                "\n\nINTERVIEW MODE — the story is blank. The objective is to help the writer build a "
                "rich story from nothing by offering FORKS — concrete directions the story could take — "
                "and developing whichever one the writer picks. Lead; do not wait for edit commands.\n\n"
                "FORKS — a story gets rich by accumulating specifics, lorebook-style, not by nailing one "
                "dramatic spine. Each turn, put a few forks on the table, then develop the one the writer "
                "chooses and offer the next. A fork can be any of: a defining trait or law of the world; a "
                "central tension or question the story turns on; a character with a want and a secret; a "
                "place with a history; a relationship under strain; a recurring texture or motif. A central "
                "dilemma (the axioms below) is ONE fork among these, never the required destination — many "
                "good stories are cozy, exploratory, or character-driven and never pose one. Follow the "
                "writer; do not funnel every story toward a moral choice.\n"
                "PITCH WORLD FORKS AS KERNELS — one compressed sentence about HOW THE WORLD IS MADE: the "
                "foundational condition that constitutes it. VARY THE KIND across the set. At least one "
                "must be FULLY GROUNDED with no speculative or magical element whatsoever — a real "
                "material condition of the world (a climate, an economy, a technology, a political order), "
                "e.g. 'the world is dying: warming is desertifying the land, the cities flood, the old "
                "currencies collapsed and everything trades in crypto'. Spread the rest across the other "
                "registers — social, spiritual, mysterious, metaphysical — so magic is one option among "
                "many, never the default. A kernel names the constituting condition and STOPS: it does "
                "NOT enumerate society, customs, or factions — those are uncovered after a pick. The "
                "strongest kernels hold a mystery: something beyond the surface.\n\n"
                "DEFINITIONS\n"
                "World root: HOW THE WORLD IS MADE — the foundational condition that constitutes it and "
                "from which everything grows, stated as a standing fact about the world, not an event. It "
                "need NOT be magical: it can be ecological, economic, technological, social, spiritual, "
                "mysterious, or metaphysical — whatever makes this world's substrate unlike ours. A range "
                "of kinds — Rewrite (metaphysical): the world is alive and incarnates its own life-energy "
                "as familiars. The Wandering Inn (systemic + mystery): the world runs on Diablo-style "
                "leveling — Classes, Levels, Skills — but magic is something older, beyond the system. "
                "Alien Stage (social): human voices are farmed as entertainment by an alien overclass. "
                "Fully grounded (ecological + economic): the world is dying — warming is desertifying the "
                "land, the great cities are flooding, the old currencies collapsed and everything now "
                "trades in crypto. A "
                "mere circumstance ('a failing harvest') is an event IN a world; a root is the property of "
                "the world that generates such events. In grounded fiction the root is a systemic condition "
                "(a company town, an occupation), not a metaphysics — but still a standing trait, not an "
                "incident.\n"
                "Core question: the unresolvable dilemma the world root forces a specific person to answer "
                "under pressure. It is not a theme. A theme ('forgiveness', 'freedom') is a category; a "
                "core question is a forced choice between two defensible but incompatible answers.\n"
                "Concrete situation: a specific circumstance — particular place, time, and people — where "
                "the root makes the choice unavoidable. An abstract formulation ('freedom vs. security') "
                "is never a situation; it is a seminar topic.\n\n"
                "AXIOMS — WHEN the writer takes the central-tension fork, these sharpen it into something "
                "that lands. They do NOT apply to the other forks; skip them entirely if the story has no "
                "dilemma.\n"
                "1. SYMMETRY OF SIDES. A core question must have two defensible sides. A decent person "
                "could choose either, and suffer for it. If one side is obviously correct, the question "
                "is under-derived: continue until you can name the person who would take the losing side "
                "and construct the argument for why they are not wrong to. Operation: when the writer "
                "states a one-sided position, ask who is destroyed by it and who would oppose them on "
                "defensible grounds.\n"
                "2. THE ANSWER COSTS THE WINNER. The choice must cost the person who makes it — not in "
                "what they lose, but in what they become by making it. A costless resolution is excluded. "
                "Operation: for any position the writer commits to, ask what the protagonist becomes by "
                "holding it, and what that transformation costs over time.\n"
                "3. ACCRUAL OVER DETONATION. Profundity compounds through sustained pressure, not a "
                "single crisis. Operation: ask what carrying the question for years does to a person; "
                "prefer slow erosion over a detonating event.\n"
                "4. CONDITIONAL ANTAGONISM. Where the genre permits, the opposing force is an indifferent "
                "condition (a process, an ecology, a law), not an agent with intent. An antagonist that "
                "can be bargained with or defeated is weaker than one that cannot, because indifference "
                "resists no argument. Operation: when the writer proposes a villain, test whether the "
                "same pressure could be exerted by a non-agentive condition, and prefer it where it can.\n"
                "5. THE DIGNITY GAP. The most load-bearing character is often the one the diegetic world "
                "has devalued or misclassified — and whose actual interiority contradicts that "
                "classification. The gap between assigned status and real personhood is the structural "
                "source of the reader's re-evaluation. Operation: identify which character the writer's "
                "world underestimates, and ask what it would take for them to be correctly seen.\n"
                "6. THE LIE OVER THE WOUND. A character's false belief (the adaptive distortion produced "
                "by past harm) is structurally more useful than the harm itself, because the belief "
                "generates daily behavior whereas the wound is inert backstory. Operation: given a "
                "character's wound, derive the false belief it installed, the behavior it dictates, and "
                "the event that would falsify it.\n\n"
                "HARD CONSTRAINTS\n"
                "C1. Stay concrete. Instantiate every fork in specifics — a particular world, place, "
                "person, or thing — never an abstract label. For a tension: 'the last free city falls "
                "unless it adopts the methods it is fighting', not 'freedom vs. security'.\n"
                "C2. Never use capitalized abstract-noun oppositions, 'X versus Y' framings, or "
                "theme-word labels. These are the exact failure signature of unoriginal output.\n"
                "C3. One fork or question per turn. Then stop and wait.\n"
                "C4. Do not answer for the writer. You offer forks and develop their pick; they decide.\n"
                "C5. Keep `ops` empty while exploring. Commit only once the writer settles on something.\n\n"
                "PUT THE FORKS IN `suggestions` — the forks you offer are the `suggestions` list, one "
                "fork per item, each a short concrete phrase the writer can pick. `reply` is just the "
                "one-line framing around them, never a long enumeration.\n"
                "OPENING MOVE — if the writer has provided no material yet: greet in one line in `reply`, "
                "then put three to four DISTINCT forks in `suggestions`, MIXED in kind — e.g. a world with "
                "a defining trait, a character with a want and a secret, a place with a history, a central "
                "tension. Each fork is ONE kernel sentence — the principle, not its worked-out "
                "consequences. Range widely: some speculative, some grounded, some dramatic, some quiet. Do "
                "not offer bare theme-words or abstract oppositions (C1, C2), and do not make every option "
                "a dilemma.\n\n"
                "PROCEDURE\n"
                "When the writer picks or supplies a fork, develop it into something specific in `reply` "
                "(one or two lines), then put the NEXT forks in `suggestions`. If the fork is a central "
                "tension, sharpen it with the axioms — who is destroyed by it (A1), what it costs the "
                "chooser (A2), is the antagonist conditional (A4), what false belief it implies (A6). For "
                "any other fork, add a concrete detail and a reason it matters.\n"
                "AGREEMENT SIGNAL: when the writer accepts a formulation, says it's right, or picks an "
                "option, treat it as SETTLED and COMMIT it. Continuing to interrogate past agreement is a "
                "failure mode — the interview must produce committed material, not talk indefinitely.\n\n"
                "COMMIT — on agreement, write what the writer settled on to the fields you own (merge, so "
                "you touch only what changed): premise = a sentence on what the story is; premise_parts/root "
                "= the world's defining trait, IF the story has one; premise_parts/question = a central "
                "tension, ONLY IF the writer took that fork — never invent one. Commit only what's actually "
                "settled and leave the rest blank; a cozy story may commit a premise and nothing else. Cast, "
                "places, and plot are built later by their own editors. Do not commit on the first message; "
                "offer forks first, then commit each piece as the writer agrees.")
        convo = "\n".join(f"{'Writer' if m.get('role') == 'user' else 'You'}: {m['text']}" for m in messages)
        _roles = _cf.load_text_roles(ctx.root)
        _PROVIDER_ROLE = _roles.get("director") or _roles.get("narrator")

        # SSE plumbing — the model streams its `reply` text so the writer watches it form
        # in the chat window; the ops apply/persist happens after and lands in a final event.
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _emit_reply(text: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "reply", "text": text})

        def _run(view, note="", stream=False):
            """Run the editor with a given anchored view. `note` appends a freshness
            instruction; `stream=True` pushes reply-so-far deltas to the SSE queue.

            RACES effort levels concurrently instead of retrying sequentially.
            `reasoning_effort: high` on this model reliably produces near-EMPTY /
            malformed STRUCTURED output (a 48-char reply, no suggestions, sometimes
            nothing) — the reasoning channel eats the answer. Measured: high → ~empty;
            low → fast (~5s) + clean; none → works but slow/verbose. Firing `low`
            (streamed — the common-case fast path) alongside two silent `none` backups
            and taking whichever returns valid output FIRST turns a worst-case ~4
            sequential round-trips into ~1 (the model is a cloud connection, so extra
            concurrent calls cost API usage, not GPU contention)."""
            prompt = (f"ANCHORED SECTION (path  #hash  preview):\n{view}\n\n"
                      f"EDITABLE FIELDS: {', '.join(allowed)}\n\nCONVERSATION:\n{convo}\n\n"
                      f"{note}Answer or edit per the writer's LATEST message.")

            def attempt(effort, streamed):
                p = ctx.text_provider_for(_PROVIDER_ROLE, {"reasoning_effort": effort})
                if p is None:
                    return None                   # no provider configured for this role
                on_delta = None
                if streamed:
                    acc: list[str] = []
                    def on_delta(t, acc=acc):
                        acc.append(t)
                        _emit_reply(partial_reply("".join(acc)))
                try:
                    return (p.generate_text(system=system, prompt=prompt, emits=schema,
                                            on_delta=on_delta).data) or {}
                except Exception:  # noqa: BLE001 — reasoning channel can break structured output
                    return {}

            # Only `low` streams — the expected fast+clean path. The `none` backups run
            # silently; if one wins the race its full reply lands as one chunk (interleaving
            # multiple concurrent streams into the same SSE queue would garble the text).
            plan = (("low", stream), ("none", False), ("none", False))
            ex = ThreadPoolExecutor(max_workers=len(plan))
            futs = [ex.submit(attempt, effort, streamed) for effort, streamed in plan]
            result, saw_provider = {}, False
            try:
                for fut in as_completed(futs):
                    out = fut.result()
                    if out is None:
                        continue
                    saw_provider = True
                    if out.get("reply") or out.get("ops") or out.get("suggestions"):
                        result = out
                        break
            finally:
                ex.shutdown(wait=False)           # don't block on abandoned losers
            return result if saw_provider else None

        def compute():
            """The blocking model→apply→persist path (run off the event loop). Returns the
            final result dict the client applies; reply text has already streamed live."""
            out = _run(view, stream=True)
            if out is None:
                return {"error": "no editor model configured", "_status": 400}
            reply = (out.get("reply") or "").strip()
            suggestions = [s.strip() for s in (out.get("suggestions") or [])
                           if isinstance(s, str) and s.strip()]
            ops = out.get("ops") if isinstance(out.get("ops"), list) else []
            # Re-read the LIVE story right before applying, so anchors are checked against the
            # freshest state (not the snapshot the model read). apply_ops mutates `live` in place
            # and returns {applied, rejected, before} — `before` holds the OLD top-level field
            # values for the client's Undo; `live` now holds the NEW merged values to persist.
            try:
                live = ctx._read_story_data(key)
            except FileNotFoundError:
                live = raw
            result = apply_ops(live, ops)

            # ── Stale-anchor recovery (one retry) ─────────────────────────────────
            # If any op failed PURELY due to staleness (the node drifted since the model read it
            # — a concurrent edit), re-show the model the FRESH anchored view and ask it to
            # re-emit just those ops with the updated anchors. Structural failures (a path that's
            # gone, a merge on a non-object) are NOT retried — they'd loop. We persist once
            # (after the retry) so the writer sees a single coherent apply. `live` already holds
            # any first-pass applied mutations in memory (nothing persisted yet) — the retry
            # applies ON TOP of that state, so nothing is lost.
            if any_stale_rejections(result["rejected"]):
                stale_paths = [r["path"] for r in result["rejected"] if any_stale_rejections([r])]
                fresh_view = anchored_view(live, allowed)
                note = (f"NOTE: your prior edit(s) to {', '.join(stale_paths)} were STALE — those "
                        "nodes changed since you read them. The fresh anchored view above has the "
                        "CURRENT #hashes. Re-emit ONLY the op(s) for those path(s) with the updated "
                        "anchors, or reply that you can't.\n\n")
                r2 = _run(fresh_view, note=note)
                if r2:
                    ops2 = r2.get("ops") if isinstance(r2.get("ops"), list) else []
                    if ops2:
                        retry_result = apply_ops(live, ops2)   # on the already-mutated state
                        result = merge_results(result, retry_result)
                        if r2.get("reply") and not reply:
                            reply = r2["reply"].strip()

            applied, rejected, before = result["applied"], result["rejected"], result["before"]
            if not applied:
                # Nothing landed — reply only. Surface rejections so the client can show why.
                return {"reply": reply, "suggestions": suggestions, "applied": [], "rejected": rejected, "before": {}}
            # Persist the NEW values (live was mutated by apply_ops) through the validated write
            # path — `before` (the old values) goes back to the client for Undo.
            try:
                ctx.update_story_fields(key, {f: live.get(f) for f in before})
            except Exception as exc:  # noqa: BLE001 — validation rejected the merged story → don't corrupt
                return {"reply": reply, "suggestions": suggestions, "applied": [], "rejected": rejected,
                        "before": {}, "error": f"couldn't apply: {exc}"}
            return {"reply": reply, "suggestions": suggestions, "applied": applied, "rejected": rejected,
                    "before": before, "layer": _rebuilt_layer(key, layer)}

        # Drive compute() off the event loop; stream reply deltas, then the final result.
        async def run():
            try:
                data = await run_in_threadpool(compute)
                loop.call_soon_threadsafe(q.put_nowait, {"type": "result", "data": data})
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.delete("/api/stories/{key}")
    def delete_story(key: str):
        import shutil
        from ...server.services import story_store as _SS
        safe = re.sub(r"[^\w\-]+", "", key)
        if not _SS.story_exists(ctx.root, safe):
            # fall back to legacy on-disk forms (pre-migration yaml/json/folder)
            yaml_p = ctx.story_dir() / f"{safe}.yaml"
            legacy_json = ctx.story_dir() / f"{safe}.json"
            folder = ctx.story_dir() / safe
            if not yaml_p.is_file() and not legacy_json.is_file() and not (folder / "story.json").is_file():
                return JSONResponse({"error": "no such story"}, status_code=404)
        # delete from the relational store (the source of truth)
        _SS.delete_story(ctx.root, safe)
        # also sweep any lingering legacy on-disk forms + the assets folder
        yaml_p = ctx.story_dir() / f"{safe}.yaml"
        legacy_json = ctx.story_dir() / f"{safe}.json"
        folder = ctx.story_dir() / safe
        if yaml_p.is_file():
            yaml_p.unlink()
        if legacy_json.is_file():
            legacy_json.unlink()                         # legacy flat form
        shutil.rmtree(folder, ignore_errors=True)        # folder form: story's embedded chars' assets
        ctx.reload_settings()
        removed = ctx.prune_orphan_characters()  # cascade: any pre-migration global-pool leftovers
        return {"ok": True, "removed_characters": removed}

    @app.get("/api/stories/{key}/export")
    def export_story(key: str):
        """Download the whole story as ONE portable bundle: authored aggregate + copackaged
        library cards + referenced personas + every session (beats, play cards, history) +
        image assets. Import anywhere with POST /api/stories/import."""
        from ...server.services import story_bundle as _SB
        safe = re.sub(r"[^\w\-]+", "", key)
        if not _SS.story_exists(ctx.root, safe):
            return JSONResponse({"error": "no such story"}, status_code=404)
        bundle = _SB.export_story(ctx.root, safe)
        return JSONResponse(bundle, headers={
            "Content-Disposition": f'attachment; filename="{safe}.story.json"'})

    @app.post("/api/stories/import")
    async def import_story(body: dict):
        """Restore a bundle produced by the export route. Never clobbers: an existing story
        key is suffixed (_2, _3…) and session/card/persona collisions are remapped."""
        from ...server.services import story_bundle as _SB
        try:
            summary = _SB.import_story(ctx.root, body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        ctx.reload_settings()
        return summary

    @app.post("/api/stories/{key}/regenerate-cast")
    async def regenerate_cast(key: str, body: dict):
        """DESTRUCTIVE: re-derive the whole cast from the story's storyboard, STREAMED live as a
        job (roster pass + each character). Step 1 distils the source card into ONE clean BASE
        CHARACTER CARD; step 2 re-extracts supporting NPCs in that structure. Old story-bound
        characters (+ portraits) are deleted and the cast rewritten; the source card is untouched.
        Returns {job} — consume /api/jobs/<id>/stream to watch + know when it's done."""
        import shutil

        from ..pipeline import extract_characters, extract_protagonist

        st = ctx.base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, systems = ctx.builder_ctx(body or {}, "characters")
        if provider is None:
            return JSONResponse({"error": systems}, status_code=400)

        # Resolve the protagonist source: the imported source card if it still exists, else fall
        # back to the story's current primary cast member.
        source = (st.fields or {}).get("source_character")
        prot_key = source if (source and source in ctx.base_settings.characters) else None
        if prot_key is None:
            prot_key = next((m.character for m in st.cast if m.primary), None) \
                or (st.cast[0].character if st.cast else None)
        prot = ctx.base_settings.characters.get(prot_key) if prot_key else None
        board = {"logline": st.storyboard.logline, "premise": st.premise, "tone": st.tone,
                 "beats": [b.model_dump() for b in st.storyboard.beats]}

        def work(emit, cancelled):
            prot_data = None
            if prot is not None:
                prot_data = extract_protagonist(
                    provider, name=prot.name, persona=prot.system or "",
                    extras=ctx.card_extras(prot, prot_key),
                    systems=systems, on_event=emit)
            out = extract_characters(
                provider, name=(prot_data["name"] if prot_data else st.name),
                persona=(prot_data["persona"] if prot_data else ""),
                board=board, extras=ctx.card_extras(prot, prot_key) if prot else {},
                systems=systems,
                reference_card=(prot_data["persona"] if prot_data else ""), on_event=emit)
            npcs = out.get("npcs", [])
            if cancelled():
                return {"cancelled": True}

            # Compose the RICH base prompt for EVERYONE via the shared ✨ pipeline (single
            # appearance authority) — in parallel — so the regenerated cast matches the manual ✨
            # button (skin-tone/eye-demeanor/cooccur/etc.) with no extra step.
            from concurrent.futures import ThreadPoolExecutor
            people = ([("__prot__", prot_data)] if prot_data else []) \
                + [(str(i), n) for i, n in enumerate(npcs)]

            from ..pipeline import compose_base_prompt as _compose_base_prompt
            _bp_cfg = ctx.load_story_builder()
            _bp_prov = ctx.stage_provider("base_image")
            _bp_sys = (_bp_cfg.get("systems") or {})

            def _bp(item):
                pid, p = item
                if cancelled():
                    return (pid, "")
                emit({"type": "phase", "label": f"Rendering appearance — {p.get('name', '?')}"})
                try:
                    r = _compose_base_prompt(_bp_prov, p.get("name", ""), p.get("persona", ""),
                                             p.get("appearance", ""), p.get("role", ""),
                                             systems=_bp_sys)
                except Exception as exc:  # noqa: BLE001 — one character must not sink the whole regen
                    emit({"type": "phase", "label": f"{p.get('name', '?')}: appearance failed ({exc})"})
                    return (pid, "")
                bp = r.get("prompt", "") if isinstance(r, dict) else ""
                if isinstance(r, dict):
                    h = (r.get("features") or {}).get("height_cm")
                    if h:
                        p["height_cm"] = h        # same dict write_npc persists -> fields.height_cm
                if bp:
                    emit({"type": "item", "name": p.get("name", "?"), "text": bp})
                return (pid, bp)

            bps = {}
            if people:
                with ThreadPoolExecutor(max_workers=min(len(people), 6)) as ex:
                    bps = dict(ex.map(_bp, people))

            emit({"type": "phase", "label": "Saving the cast…"})
            # Build the whole new cast, commit the story yaml, THEN delete the old members —
            # transactional: a failed write throws before the story is touched.
            created: list[str] = []
            cast = []
            if prot_data is not None:
                # No ref_from: the protagonist's base image is GENERATED like everyone else (the
                # source card stays the STYLE anchor via source_character, not the literal image).
                pkey = ctx.write_npc(prot_data, story_key=key, base_prompt=bps.get("__prot__", ""))
                cast.append({"character": pkey, "primary": True}); created.append(pkey)
            for i, npc in enumerate(npcs):
                nk = ctx.write_npc(npc, story_key=key, base_prompt=bps.get(str(i), ""))
                cast.append({"character": nk, "primary": False}); created.append(nk)
            ctx.update_story_fields(key, {"cast": cast})   # DB-backed or YAML — routed + validated
            keep = {source, *created}
            from ...server.services import card_store as _CS, story_store as _SS
            cdir = ctx.char_dir()
            # Sweep EVERY character bound to THIS story that isn't part of the new cast — not just the
            # previous st.cast — so duplicate/orphan members left by earlier or cancelled regenerations
            # (e.g. a stale 'kaia_nakumura' beside the new 'kaia_nakumura_2') are cleared automatically.
            for ck, ch in list(ctx.base_settings.characters.items()):
                if ck in keep or (ch.fields or {}).get("story") != key:
                    continue
                safe = re.sub(r"[^\w\-]+", "", ck)
                _SS.delete_character(ctx.root, key, ck)   # embedded record, if any
                _CS.delete_character(ctx.root, safe)      # global-pool record (was the yaml)
                for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                    f = cdir / fn
                    if f.is_file():
                        f.unlink()
                shutil.rmtree(ctx.portrait_dir(ck), ignore_errors=True)
            ctx.reload_settings()

            # Auto-plan wardrobes using scene-based reasoning — one call per location.
            emit({"type": "phase", "label": "Planning scene wardrobes…"})
            try:
                w_prov, w_sys = ctx.builder_ctx({}, "wardrobe")
                if w_prov is not None:
                    from ..pipeline import plan_story_wardrobe as _plan_story_wardrobe
                    from ..pipeline.wardrobe import compose_outfit_prompt as _cop
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    full_story = st.model_dump()
                    name_to_key_regen = {}
                    cast_details_regen = []
                    for ckey in created:
                        ch_r = ctx.base_settings.characters.get(ckey)
                        if ch_r is None:
                            continue
                        name_to_key_regen[ch_r.name.lower()] = ckey
                        cast_details_regen.append({
                            "name": ch_r.name,
                            "persona": ch_r.system or "",
                            "appearance": (ch_r.fields or {}).get("appearance", ""),
                            "key": ckey,
                        })
                    scene_plans = _plan_story_wardrobe(
                        w_prov, story=full_story, cast=cast_details_regen,
                        systems=w_sys, on_event=emit)
                    all_w: list[dict] = []
                    for scene in scene_plans:
                        for o in scene.get("outfits") or []:
                            cn = (o.get("character") or "").lower()
                            ck = name_to_key_regen.get(cn) or next(
                                (k for n, k in name_to_key_regen.items()
                                 if cn and (cn in n or n in cn)), None)
                            if not ck:
                                continue
                            ch_r = ctx.base_settings.characters.get(ck)
                            all_w.append({
                                **o,
                                "name": o.get("outfit_name") or o.get("name") or "Outfit",
                                "_char_key": ck,
                                "_persona": (ch_r.system or "") if ch_r else "",
                                "_appearance": ((ch_r.fields or {}).get("appearance", "")) if ch_r else "",
                            })
                    def _ref(o):
                        try:
                            r = _cop(w_prov, o["_persona"], o["_appearance"],
                                     o.get("name", ""), o.get("concept") or "")
                            if r.get("attire"):
                                o["attire_prompt"] = r["attire"]
                                o["unified"] = r.get("unified", False)
                        except Exception:  # noqa: BLE001
                            pass
                        return o
                    if all_w:
                        with _TPE(max_workers=min(len(all_w), 8)) as _ex:
                            all_w = list(_ex.map(_ref, all_w))
                    by_char_regen: dict[str, list] = {}
                    for o in all_w:
                        by_char_regen.setdefault(o["_char_key"], []).append(o)
                    for ckey, woutfits in by_char_regen.items():
                        if cancelled():
                            break
                        ch_r = ctx.base_settings.characters.get(ckey)
                        emit({"type": "phase", "label": f"Applying {ch_r.name if ch_r else ckey}'s wardrobe"})
                        _apply_manifest(ctx, ckey, {"outfits": woutfits, "replace": True},
                                        provider=w_prov)
            except Exception as exc:  # noqa: BLE001
                emit({"type": "phase", "label": f"Wardrobe planning skipped ({exc})"})

            emit({"type": "phase", "label": f"Done — {len(cast)} cast members"})
            return {"ok": True, "cast": [m["character"] for m in cast], "created": created}

        job = _start_stream_job("cast", "Regenerate cast", st.name, f"stories/{key}/cast", work)
        return {"job": job.id}
