"""Story interview adapter for the shared plain-text ``CMD`` transport."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Iterable, Mapping

from ...commands import parse_cmd_block, split_cmd_response
from .dramatic_kernel import (DramaticKernelError, character_core_map,
                              normalize_character_cores, normalize_scene_kernel)
from .scene_contract import ensure_player_participant, validate_changed_scene_offers

_PATCH = re.compile(r"```(?:json)?\s*PATCH\s*\n(.*?)```", re.I | re.S)
_NEXT_FOCUS = re.compile(
    r"(?im)^\s*(?:<!--\s*)?`?(?:NEXT(?:[_\s]*FOCUS)?)[\s:=]+"
    r"(world|premise|first_day|time_system|cast|arcs)`?\s*(?:-->)?\s*$"
)

# These are deliberately Story-card fields, not an intermediate interview schema.
# A focused card edit is a real write boundary, not merely a hint for the model.
SECTIONS: dict[str, set[str]] = {
    # The overview is the public story kernel. It may keep the setting,
    # premise, setting texture, and immediate opening in agreement without
    # becoming permission to touch private arcs or director-only mechanics.
    "overview": {"world", "locations", "start", "premise", "fields"},
    "world": {"world", "locations", "start", "fields"},
    "premise": {"premise", "fields"},
    "first_day": {"fields"},
    "time_system": {"time_system", "fields"},
    "cast": {"cast", "relationships", "fields"},
    # ``fields.arc_design`` owns the canonical theme-and-storyline document.
    # The root ``themes`` list is retained only as a legacy derived index for
    # older runtime surfaces; it is never the authoring source of truth.
    "arcs": {"themes", "fields"},
    "relationships": {"relationships", "fields"},
    "interview": {"world", "locations", "start", "time_system", "premise", "themes", "cast", "relationships", "fields"},
}
# ``author_notes`` is the general author/director-only lane.  The interviewer
# may classify an explicitly established fact into it; it is stored wrapped so
# every later model projection redacts it automatically.
_FIELD_KEYS = {"status", "open_questions", "first_day_plan", "arc_design", "player_abilities", "character_cores", "character_wounds", "author_notes"}

_SYNTHETIC_BOOTSTRAPS = {
    "I’m starting a new story. Invite me to share any useful starting spark.",
    "I'm starting a new story. Invite me to share any useful starting spark.",
    "This is an existing story card. Read what is already established and identify the single most important thing it still needs before it can drive playable scenes.",
}


def is_synthetic_bootstrap(text: str) -> bool:
    """True for legacy UI scaffolding that was accidentally stored as author canon."""
    return (text or "").strip() in _SYNTHETIC_BOOTSTRAPS


def normalize_interview_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep only real author/interviewer turns, removing old UI bootstrap noise.

    Prior versions posted their automatic opening prompt as a user message.  This
    filter both keeps that contamination out of new model context and cleans it
    out the next time an affected story is saved.
    """
    out: list[dict[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            continue
        text = message["text"].strip()
        if not text or is_synthetic_bootstrap(text):
            continue
        role = message.get("role")
        out.append({"role": role if role in {"user", "assistant"} else "user", "text": text})
    return out


# The primary interviewer speaks natural prose plus CMD.  Small models will
# occasionally omit the command while still claiming an update was saved.  The
# fallback uses a tiny flat schema: robust across providers, auditable, and
# restricted to paths that a focused card edit may actually change.
_RECOVERY_PATHS: dict[str, tuple[str, ...]] = {
    "overview": (
        "premise", "world.genre", "world.setting", "world.biome", "world.culture",
        "world.atmosphere", "world.history", "world.customs", "world.technology", "world.background",
        "start", "locations", "fields.first_day_plan",
    ),
    "world": (
        "world.genre", "world.setting", "world.biome", "world.culture", "world.atmosphere", "world.history",
        "world.customs", "world.technology", "world.background",
        "start", "locations",
        "fields.player_abilities",
        "world.loop.start", "world.loop.reset", "world.loop.memory",
        "world.loop.returner", "world.loop.end_condition", "world.loop.persistence",
        "world.loop.victims_return_after_end", "world.loop.policy",
        "world.entity.description", "world.entity.knowledge", "world.entity.limitations",
        "world.entity.tactic", "world.entity.objective",
    ),
    "premise": ("premise",),
    "first_day": ("fields.first_day_plan",),
    "time_system": ("time_system",),
    "cast": ("cast", "relationships", "fields.character_cores", "fields.character_wounds"),
    "arcs": ("themes", "fields.arc_design"),
}


def recovery_schema(focus: str) -> dict[str, Any]:
    """Schema for a no-prose repair pass after the CMD protocol is omitted."""
    paths = _RECOVERY_PATHS.get(focus)
    if not paths:
        raise ValueError(f"unknown interview focus: {focus}")
    return {
        "type": "object", "additionalProperties": False, "required": ["changes"],
        "properties": {
            "changes": {
                "type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["path", "value"],
                    "properties": {
                        "path": {"type": "string", "enum": list(paths)},
                        "value": {"type": "string"},
                    },
                },
            },
        },
    }


def patch_from_recovery(focus: str, data: dict[str, Any]) -> dict[str, Any]:
    """Convert validated flat evidence changes back to a focused Story patch."""
    allowed = set(_RECOVERY_PATHS.get(focus) or ())
    patch: dict[str, Any] = {}
    changes = (data.get("changes") or []) if isinstance(data, dict) else []
    for change in changes:
        if not isinstance(change, dict):
            continue
        path, value = change.get("path"), change.get("value")
        if path not in allowed or not isinstance(value, str) or not value.strip():
            continue
        if path in {"time_system", "fields.first_day_plan", "fields.arc_design", "fields.player_abilities", "fields.character_cores", "fields.character_wounds", "themes", "cast", "relationships", "locations", "world.loop.policy"}:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        else:
            # A plain-text path (world.history, premise, ...) occasionally arrives
            # double-encoded — the model wraps its own answer in an extra pair of
            # quotes despite the schema already declaring it a JSON string. Unwrap
            # that one layer rather than persisting the literal quote marks; prose
            # that merely contains a quote character isn't valid JSON on its own,
            # so this only fires on the genuine double-encoding.
            try:
                unwrapped = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(unwrapped, str):
                    value = unwrapped
        nodes = path.split(".")
        root = nodes.pop(0)
        if not nodes:
            patch[root] = value
            continue
        target = patch.setdefault(root, {})
        for node in nodes[:-1]:
            target = target.setdefault(node, {})
        target[nodes[-1]] = value
    return parse_patch_block(patch, SECTIONS[focus])


def _normalize_world_shape(world: dict[str, Any]) -> dict[str, Any]:
    """Keep loop rules together even when a conversational model uses old flat aliases."""
    normalized = deepcopy(world)
    loop = normalized.get("loop")
    if not isinstance(loop, dict):
        loop = {}
    aliases = {
        "loop_end_condition": "end_condition",
        "reset_persistence": "persistence",
        "victims_return_after_end": "victims_return_after_end",
    }
    for old, nested in aliases.items():
        if old in normalized:
            loop.setdefault(nested, normalized.pop(old))
    if loop:
        normalized["loop"] = loop
    return normalized


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge nested world rules without erasing established loop/entity facts."""
    out = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def parse_patch(text: str, allowed: set[str]) -> dict[str, Any]:
    """Extract one object from a human-readable interview response.

    Expected form::

        Here is the proposed biome…
        ```PATCH
        {"world": {"biome": "inhabited island"}}
        ```
    """
    match = _PATCH.search(text or "")
    if not match:
        raise ValueError("response contains no PATCH block")
    try:
        patch = json.loads(match.group(1).strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"PATCH block is not valid JSON: {exc.msg}") from exc
    if not isinstance(patch, dict):
        raise ValueError("PATCH must be a JSON object")
    unknown = set(patch) - set(allowed)
    if unknown:
        raise ValueError(f"PATCH changes fields outside this interview section: {', '.join(sorted(unknown))}")
    return patch


def apply_patch(card: dict[str, Any], patch: dict[str, Any], section: str = "interview") -> dict[str, Any]:
    """Validate and merge an interview patch into a real Story-card document.

    The model never gets a raw write: this is the small, typed boundary between
    authorial prose and the normal Story mutation path.
    """
    if section not in SECTIONS:
        raise ValueError(f"unknown interview section: {section}")
    patch = parse_patch_block(patch, SECTIONS[section])
    result = deepcopy(card)
    normalized_arc_design: dict[str, Any] | None = None
    # A model reliably keeps `core`/`wound` prose it just wrote close to the cast
    # item it's about, instead of the separate fields.character_cores/
    # character_wounds commands the prompt asks for — measured live, not a
    # hypothetical. Extracted here rather than depended on in the prompt, since
    # prompt compliance on this point is exactly what's unreliable.
    stray_cores: dict[str, str] = {}
    stray_wounds: dict[str, str] = {}
    for key, value in patch.items():
        if key == "world":
            if not isinstance(value, dict):
                raise ValueError("world PATCH must be an object")
            result["world"] = _normalize_world_shape(_deep_merge(result.get("world") or {}, value))
        elif key == "locations":
            if not isinstance(value, list) or not all(isinstance(item, dict) and item.get("id") for item in value):
                raise ValueError("locations PATCH must be a list of objects with stable ids")
            existing = [item for item in (result.get("locations") or []) if isinstance(item, dict)]
            replacements = {str(item["id"]): item for item in value}
            result["locations"] = [
                _deep_merge(item, replacements.pop(str(item.get("id")), {})) if item.get("id") in replacements else item
                for item in existing
            ] + list(replacements.values())
        elif key == "start":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("start PATCH must be a location id")
            result["start"] = value.strip()
        elif key == "themes":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("themes PATCH must be a list of text labels")
            # Legacy compatibility only. New Storyline edits establish their
            # theme through fields.arc_design below, which then refreshes this
            # small label index deterministically.
            result["themes"] = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif key == "time_system":
            if not isinstance(value, dict) or not isinstance(value.get("entity_periods"), list) \
               or not all(isinstance(period, dict) for period in value["entity_periods"]):
                raise ValueError("time_system must be an object with entity_periods")
            result["time_system"] = {**(result.get("time_system") or {}), **value}
        elif key == "premise":
            if not isinstance(value, str):
                raise ValueError("premise PATCH must be text")
            result["premise"] = value.strip()
        elif key in ("cast", "relationships"):
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError(f"{key} PATCH must be a list of objects")
            identity = "character" if key == "cast" else "id"
            if key == "cast":
                cleaned = []
                for item in value:
                    item = dict(item)
                    character = str(item.get(identity) or "").strip()
                    core = item.pop("core", None)
                    wound = item.pop("wound", None)
                    if character and isinstance(core, str) and core.strip():
                        stray_cores[character] = core.strip()
                    if character and isinstance(wound, str) and wound.strip():
                        stray_wounds[character] = wound.strip()
                    cleaned.append(item)
                value = cleaned
            existing = [item for item in (result.get(key) or []) if isinstance(item, dict)]
            replacements = {item.get(identity): item for item in value if item.get(identity)}
            merged = [replacements.pop(item.get(identity), item) for item in existing]
            result[key] = merged + list(replacements.values())
        elif key == "fields":
            if not isinstance(value, dict) or set(value) - _FIELD_KEYS:
                allowed = ", ".join(sorted(_FIELD_KEYS))
                raise ValueError(f"fields PATCH may only change {allowed}")
            fields = dict(result.get("fields") or {})
            if "status" in value:
                if value["status"] not in ("interviewing", "active"):
                    raise ValueError("interview status must be interviewing or active")
                fields["status"] = value["status"]
            if "open_questions" in value:
                questions = value["open_questions"]
                if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
                    raise ValueError("open_questions must be a list of text")
                fields["open_questions"] = [q.strip() for q in questions if q.strip()]
            if "first_day_plan" in value:
                plan = value["first_day_plan"]
                if not isinstance(plan, dict) or not isinstance(plan.get("events"), list) \
                   or not all(isinstance(event, dict) for event in plan["events"]):
                    raise ValueError("first_day_plan must be an object with an events list")
                existing_plan = fields.get("first_day_plan")
                existing_events = (existing_plan.get("events") if isinstance(existing_plan, dict) else [])
                # A direct focused interview and the batch Architect both
                # write to this canonical plan.  Apply the same public scene
                # contract here so an abstract lore sentence cannot slip into
                # the playable catalog through the conversational route.
                # Legacy events are allowed untouched; new or public-surface
                # changed events must have a place, people, pressure, and an
                # immediate player-facing way in.
                normalized_plan = deepcopy(plan)
                try:
                    normalized_events = [
                        normalize_scene_kernel(ensure_player_participant(event))
                        for event in plan["events"]
                    ]
                except DramaticKernelError as exc:
                    raise ValueError(str(exc)) from exc
                try:
                    validate_changed_scene_offers(existing_events, normalized_events)
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
                normalized_plan["events"] = normalized_events
                fields["first_day_plan"] = normalized_plan
            if "character_cores" in value:
                try:
                    incoming_cores = normalize_character_cores(character_core_map(value["character_cores"]))
                except DramaticKernelError as exc:
                    raise ValueError(str(exc)) from exc
                current_cores = fields.get("character_cores")
                merged_cores = dict(current_cores) if isinstance(current_cores, dict) else {}
                merged_cores.update(incoming_cores)
                fields["character_cores"] = merged_cores
            if "character_wounds" in value:
                # Same shape/validation as character_cores (a text map keyed by
                # character, no [[hidden]] smuggling) — but private author material,
                # never popped into a generic model's card (see model_story_card), and
                # a full ordinary-backstory PARAGRAPH, not a one-line surface core, so
                # it gets a much longer cap than character_cores' default 360.
                try:
                    incoming_wounds = normalize_character_cores(
                        character_core_map(value["character_wounds"]), limit=1600)
                except DramaticKernelError as exc:
                    raise ValueError(str(exc)) from exc
                current_wounds = fields.get("character_wounds")
                merged_wounds = dict(current_wounds) if isinstance(current_wounds, dict) else {}
                merged_wounds.update(incoming_wounds)
                fields["character_wounds"] = merged_wounds
            if "arc_design" in value:
                # Normalize at the authoring boundary.  This keeps the runtime
                # from having to interpret ad-hoc interview prose and gives the
                # public-card redactor one canonical private document to strip.
                try:
                    from .arc_design import normalize_arc_design

                    normalized_arc_design = normalize_arc_design(value["arc_design"], card=result)
                    fields["arc_design"] = normalized_arc_design
                except ValueError:
                    raise
                except Exception as exc:  # noqa: BLE001 - surface a usable interview error
                    raise ValueError(f"arc_design is invalid: {exc}") from exc
            if "player_abilities" in value:
                abilities = value["player_abilities"]
                if not isinstance(abilities, list) or not all(isinstance(item, (str, dict)) for item in abilities):
                    raise ValueError("player_abilities must be a list of named ability objects or strings")
                # Preserve the authored scope/limits verbatim; runtime normalizes
                # a safe read-only projection and never infers abilities from
                # character prose or an entity schedule.
                fields["player_abilities"] = deepcopy(abilities)
            if "author_notes" in value:
                notes = value["author_notes"]
                if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
                    raise ValueError("author_notes must be a list of text")
                from ..visibility import wrap_model_hidden

                existing = [str(note) for note in (fields.get("author_notes") or [])
                            if isinstance(note, str) and note.strip()]
                for note in notes:
                    note = note.strip()
                    if not note:
                        continue
                    wrapped = wrap_model_hidden(note)
                    if wrapped not in existing:
                        existing.append(wrapped)
                fields["author_notes"] = existing[-24:]
            result["fields"] = fields
    if stray_cores or stray_wounds:
        result_fields = dict(result.get("fields") or {})
        if stray_cores:
            merged_cores = dict(result_fields.get("character_cores") or {})
            for character, core in stray_cores.items():
                merged_cores.setdefault(character, core)  # an explicit fields.character_cores command wins
            result_fields["character_cores"] = normalize_character_cores(merged_cores)
        if stray_wounds:
            merged_wounds = dict(result_fields.get("character_wounds") or {})
            for character, wound in stray_wounds.items():
                merged_wounds.setdefault(character, wound)
            result_fields["character_wounds"] = normalize_character_cores(merged_wounds, limit=1600)
        result["fields"] = result_fields
    if normalized_arc_design is not None:
        # The Storyline document owns theme membership. Preserve the historic
        # root field only as a compact compatibility/search index so it cannot
        # drift away from the arc the author is actually editing.
        result["themes"] = list(dict.fromkeys(
            item["label"]
            for item in normalized_arc_design["themes"]
            if isinstance(item.get("label"), str) and item["label"].strip()
        ))
    return result


def restrict_to_existing_scene_knowledge(
    card: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    event_ids: Iterable[str],
    observer_ids: Iterable[str],
) -> dict[str, Any]:
    """Fail closed to additions of blank knowledge on named protected scenes.

    The normal focused interview can edit a whole Day One plan because a human
    explicitly chose that scope.  An Architect-owned knowledge pass is much
    narrower: it may give an already-present observer a director-only fact
    about an already-protected event, but it may not create a witness, rewrite
    an action, or even change a public sentence.  Returning a rebuilt plan
    from the source card means every unapproved model field is discarded.

    ``observer_ids`` are supplied by the deterministic completion planner and
    consist only of scene participants already on the live card.  The helper
    never trusts a model-proposed actor name.
    """
    source = dict(card) if isinstance(card, Mapping) else {}
    incoming = dict(patch) if isinstance(patch, Mapping) else {}
    fields = source.get("fields") if isinstance(source.get("fields"), Mapping) else {}
    source_plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
    source_events = source_plan.get("events") if isinstance(source_plan.get("events"), list) else []
    proposed_fields = incoming.get("fields") if isinstance(incoming.get("fields"), Mapping) else {}
    proposed_plan = proposed_fields.get("first_day_plan") if isinstance(proposed_fields.get("first_day_plan"), Mapping) else {}
    proposed_events = proposed_plan.get("events") if isinstance(proposed_plan.get("events"), list) else []
    allowed_events = {str(value).strip() for value in event_ids if str(value).strip()}
    allowed_observers = {str(value).strip() for value in observer_ids if str(value).strip()}
    if not allowed_events or not allowed_observers:
        return {}

    proposed_by_id = {
        str(event.get("id") or "").strip(): event
        for event in proposed_events
        if isinstance(event, Mapping) and str(event.get("id") or "").strip()
    }
    rebuilt = deepcopy(dict(source_plan))
    rebuilt_events = rebuilt.get("events") if isinstance(rebuilt.get("events"), list) else []
    changed = False
    for index, current_raw in enumerate(source_events):
        if not isinstance(current_raw, Mapping) or index >= len(rebuilt_events):
            continue
        current = dict(current_raw)
        event_id = str(current.get("id") or "").strip()
        if event_id not in allowed_events:
            continue
        # A knowledge assignment makes sense only for an existing private
        # layer.  Check the shape, not any private prose.
        protected = bool(current.get("entity_action")) or any(
            bool(current.get(name)) for name in ("hidden", "trigger", "evidence", "entity_period")
        )
        if not protected:
            continue
        # The request's whitelist is necessary but not sufficient: derive the
        # actual on-scene observers again from the live event so a forged API
        # payload cannot turn an off-screen cast member into a secret witness.
        scene_observers = {
            ("player" if str(value).strip().lower() in {"player", "protagonist", "returner", "you"}
             else str(value).strip())
            for value in (current.get("participants") or current.get("present") or [])
            if isinstance(value, str) and value.strip()
        }
        event_observers = allowed_observers & scene_observers
        if not event_observers:
            continue
        candidate = proposed_by_id.get(event_id)
        proposed_knowledge = candidate.get("knowledge") if isinstance(candidate, Mapping) else None
        if not isinstance(proposed_knowledge, Mapping):
            continue
        existing_knowledge = current.get("knowledge") if isinstance(current.get("knowledge"), Mapping) else {}
        knowledge = deepcopy(dict(existing_knowledge))
        added = False
        for raw_observer, raw_fact in proposed_knowledge.items():
            observer = str(raw_observer or "").strip()
            fact = raw_fact.strip()[:600] if isinstance(raw_fact, str) else ""
            if (not observer or observer not in event_observers or not fact
                    or "[[" in fact or "]]" in fact or str(knowledge.get(observer) or "").strip()):
                continue
            knowledge[observer] = fact
            added = True
        if added:
            revised_event = dict(current)
            revised_event["knowledge"] = knowledge
            rebuilt_events[index] = revised_event
            changed = True
    if not changed:
        return {}
    rebuilt["events"] = rebuilt_events
    return {"fields": {"first_day_plan": rebuilt}}


def parse_patch_block(patch: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Validate an already-decoded PATCH; kept separate to make endpoint tests simple."""
    if not isinstance(patch, dict):
        raise ValueError("PATCH must be a JSON object")
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"PATCH changes fields outside this interview section: {', '.join(sorted(unknown))}")
    return patch


def _strip_next_focus(text: str) -> tuple[str, str]:
    """Remove the interviewer's private next-target marker from reader prose.

    The marker describes where the author should edit *next*, rather than a
    fact about the story. Keeping it out of the patch prevents UI navigation
    state from becoming canon or polluting the saved conversation.
    """
    matches = list(_NEXT_FOCUS.finditer(text or ""))
    focus = matches[-1].group(1).lower() if matches else ""
    return _NEXT_FOCUS.sub("", text or "").strip(), focus


def split_response_with_focus(text: str) -> tuple[str, dict[str, Any], str]:
    """Return reader prose, a decoded card patch, and an optional next target.

    An interviewer may append ``NEXT_FOCUS: cast`` after its CMD block. It is
    a compact private transport understood by the client, stripped before the
    reply is rendered or saved. Old model output remains valid and returns an
    empty target.
    """
    source, next_focus = _strip_next_focus(text)
    prose, commands = split_cmd_response(source)
    if commands is not None:
        return prose, commands_to_patch(commands), next_focus
    if _PATCH.search(source or ""):
        return _PATCH.sub("", source).strip(), parse_patch(source, SECTIONS["interview"]), next_focus
    return (source or "").strip(), {}, next_focus


def split_response(text: str) -> tuple[str, dict[str, Any]]:
    """Return reader-facing prose and a decoded command/PATCH without exposing it.

    CMD is the resilient primary transport for text models. PATCH remains accepted
    for replies produced before the transport change. A prose-only reply is valid:
    the route records the author's answer as interview evidence instead of failing.
    """
    prose, patch, _next_focus = split_response_with_focus(text)
    return prose, patch


def parse_commands(block: str) -> dict[str, Any]:
    """Compatibility entry point for the interview's CMD-to-card adapter."""
    return commands_to_patch(parse_cmd_block(block))


def commands_to_patch(commands: list[dict[str, Any]]) -> dict[str, Any]:
    """Decode the small interview command language.

    ``SET path <JSON>`` replaces one value; ``MERGE path <JSON object>``
    combines an object; ``APPEND path <JSON>`` adds an item.  Paths use dots,
    e.g. ``MERGE world {"genre":"mystery"}`` or
    ``SET fields.open_questions ["Who is Shuri really?"]``.
    """
    patch: dict[str, Any] = {}
    for command in commands:
        op, path, value = command["op"], command["path"], command["value"]
        nodes = path.replace("/", ".").split(".")
        root = nodes.pop(0)
        if root not in SECTIONS["interview"]:
            raise ValueError(f"CMD changes unknown interview field: {root}")
        if not nodes:
            if op == "MERGE":
                if not isinstance(value, dict):
                    raise ValueError("MERGE needs a JSON object")
                patch[root] = {**(patch.get(root) or {}), **value}
            elif op == "APPEND":
                patch.setdefault(root, []).append(value)
            else:
                patch[root] = value
            continue
        target = patch.setdefault(root, {})
        if not isinstance(target, dict):
            raise ValueError(f"CMD path conflicts with {root}")
        for node in nodes[:-1]:
            target = target.setdefault(node, {})
            if not isinstance(target, dict):
                raise ValueError(f"CMD path conflicts with {path}")
        leaf = nodes[-1]
        if op == "MERGE":
            if not isinstance(value, dict):
                raise ValueError("MERGE needs a JSON object")
            target[leaf] = {**(target.get(leaf) or {}), **value}
        elif op == "APPEND":
            target.setdefault(leaf, []).append(value)
        else:
            target[leaf] = value
    return patch
