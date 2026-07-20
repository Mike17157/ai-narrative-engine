"""One-pass, opt-in Story-card development.

The interview is deliberately incremental.  This module is the complementary
author-controlled path for when a writer wants a useful *starting set* in one
call: a small map, a few real character cards, and a playable first-day
baseline.  It never lets a model write a raw Story document.  Instead it turns
one constrained proposal into an additive candidate, validates the whole
aggregate, and lets the route commit it atomically.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Iterable

from ...config.schema import Character, Story, story_reference_errors
from .dramatic_kernel import (DramaticKernelError, character_core_map,
                              normalize_character_cores, normalize_scene_kernel)
from .scene_contract import assert_playable_scene_offer, ensure_player_participant


class DevelopError(ValueError):
    """A proposal could not be safely applied to the current card."""


# The UI can pass its section names directly.  ``starter``/``setup`` are the
# useful all-at-once action; focused scopes never mutate neighbouring sections.
_SCOPE_ALIASES = {
    "starter": {"world", "premise", "cast", "first_day", "time_system"},
    "setup": {"world", "premise", "cast", "first_day", "time_system"},
    "all": {"world", "premise", "cast", "first_day", "time_system"},
    "world": {"world"},
    "premise": {"premise"},
    "opening": {"premise"},
    "cast": {"cast"},
    "people": {"cast"},
    "first_day": {"first_day"},
    "day_one": {"first_day"},
    "day-one": {"first_day"},
    "time_system": {"time_system"},
    "schedule": {"time_system"},
}


def normalize_scope(value: Any) -> set[str]:
    """Return canonical editable sections or raise a helpful input error."""
    raw = value if isinstance(value, list) else [value or "starter"]
    out: set[str] = set()
    unknown: list[str] = []
    for item in raw:
        key = str(item or "").strip().lower().replace(" ", "_")
        if not key:
            continue
        mapped = _SCOPE_ALIASES.get(key)
        if mapped is None:
            unknown.append(key)
        else:
            out.update(mapped)
    if unknown:
        raise DevelopError("unknown development scope: " + ", ".join(sorted(set(unknown))))
    return out or set(_SCOPE_ALIASES["starter"])


def _obj(properties: dict[str, Any], required: Iterable[str] | None = None) -> dict[str, Any]:
    """Strict object schema helper compatible with OpenAI-style JSON schema."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required if required is not None else properties),
        "properties": properties,
    }


_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}


def develop_schema() -> dict[str, Any]:
    """Schema for a compact authoring proposal, not a raw Story write.

    Every object is strict and every key is required.  Empty strings/lists mean
    "leave this alone"; that keeps the schema usable with providers that require
    strict JSON-schema objects while preserving a focused mutation boundary.
    """
    policy = _obj({
        "trigger": _STRING,
        "restart": _STRING,
        "preserve": _obj({
            "runtime": _STRINGS,
            "memories": _STRINGS,
            "clear_memories_for_others": {"type": "boolean"},
        }),
        "victims_return_after_end": {"type": "boolean"},
    })
    loop = _obj({
        "start": _STRING,
        "reset": _STRING,
        "memory": _STRING,
        "returner": _STRING,
        "end_condition": _STRING,
        "policy": policy,
    })
    entity = _obj({
        "description": _STRING,
        "knowledge": _STRING,
        "limitations": _STRING,
        "tactic": _STRING,
        "objective": _STRING,
    })
    world = _obj({
        "genre": _STRING,
        "setting": _STRING,
        "atmosphere": _STRING,
        "history": _STRING,
        "customs": _STRING,
        "technology": _STRING,
        "background": _STRING,
        "loop": loop,
        "entity": entity,
    })
    location = _obj({"id": _STRING, "name": _STRING, "description": _STRING})
    period = _obj({
        "id": _STRING,
        "slots": _STRINGS,
        "state": _STRING,
        "capabilities": _STRINGS,
        "constraint": _STRING,
    })
    time_system = _obj({"slots": _STRINGS, "entity_periods": {"type": "array", "items": period}})
    knowledge = _obj({"protagonist": _STRING, "entity": _STRING, "public": _STRING})
    scene_role = _obj({"character": _STRING, "role": _STRING})
    event = _obj({
        "id": _STRING,
        "when": _STRING,
        "location": _STRING,
        "participants": _STRINGS,
        "visible": _STRING,
        "hook": _STRING,
        "theme": _STRING,
        "tone": _STRING,
        "roles": {"type": "array", "items": scene_role},
        "hidden": _STRING,
        "entity_action": {"type": "boolean"},
        "entity_period": _STRING,
        "trigger": _STRING,
        "evidence": _STRING,
        "knowledge": knowledge,
    })
    first_day = _obj({
        "objective": _STRING,
        "opening_time": _STRING,
        "opening_location": _STRING,
        "opening_present": _STRINGS,
        "events": {"type": "array", "items": event},
    })
    character = _obj({
        "name": _STRING,
        "role": _STRING,
        "appearance": _STRING,
        "persona": _STRING,
        "personality": _STRING,
        "background": _STRING,
        "connection": _STRING,
        "want": _STRING,
        "wound": _STRING,
        "lie": _STRING,
        "secret": _STRING,
        "primary": {"type": "boolean"},
        "home": _STRING,
    })
    character_core = _obj({"character": _STRING, "core": _STRING})
    relationship = _obj({
        "source": _STRING,
        "target": _STRING,
        "nature": _STRING,
        "dynamic": _STRING,
        "stance": _STRING,
        "target_dynamic": _STRING,
        "target_stance": _STRING,
        "note": _STRING,
        "potential": _STRING,
        "trajectory": _STRING,
    })
    return _obj({
        "message": _STRING,
        "world": world,
        "premise": _STRING,
        "locations": {"type": "array", "items": location},
        "start": _STRING,
        "time_system": time_system,
        "first_day_plan": first_day,
        "characters": {"type": "array", "items": character},
        "character_cores": {"type": "array", "items": character_core},
        "relationships": {"type": "array", "items": relationship},
        "open_questions": _STRINGS,
    })


def build_develop_prompt(
    card: dict[str, Any],
    *,
    brief: str,
    scopes: set[str],
    public_only: bool = False,
) -> str:
    """Build the one long-context authoring request.

    The exact card is included so the model can complement it, but the
    application layer remains the actual preservation guarantee.
    """
    requested = ", ".join(sorted(scopes))
    public_only_rule = """
PUBLIC-ONLY COMPLETION MODE:
- Do not propose entity or loop rules, time/entity periods, hidden scene material, triggers, evidence, knowledge gates, character wounds/lies/secrets, or relationships.
- A scene proposal may contain only its public id, time, location, participants, visible situation, player-facing hook, compact theme/tone, and participant roles.
- The application will reject private mechanics even if you return them, so stop and leave them for an author question.
""" if public_only else ""
    return f"""You are the story's co-author, invoked explicitly to make one bounded development pass over a live Story card.

The current Story card is canon. Preserve it exactly: do not rewrite, reinterpret, rename, or replace any established fact, location, character, bond, schedule, or event. Fill only genuinely missing material and connective tissue in the requested sections. This is a starting situation, not a complete plot or a rigid day script. A high-level author brief is direction, never permission to silently retcon existing canon.

REQUESTED SECTIONS: {requested}
AUTHOR'S BRIEF: {brief or '(Use the established card; do not invent a new premise.)'}

Return a proposal with these principles:
- Use short stable lowercase-kebab ids for new locations, time periods, and events. Refer to locations by id.
- Propose at most four genuinely useful people. Use a name only for a distinct person; never create a placeholder or duplicate an established name. They need a role, a concrete presence, and a playable pressure or connection. Give a person a short `character_cores` entry only when there is a public, immediately useful tension or driving posture; it is a compact surface, never a hidden wound, secret, diagnosis, or future arc outcome. Keys use `player` or an established character name/key.
- Reuse an established person's exact name. If the card calls someone `Shuri`, propose `Shuri`, not an invented surname or a replacement version of that person.
- Relationships are only between named cast members; put a player-to-character tie in that character’s `connection` field. First-day participant lists use character NAMES and may use `player` for the player/Returner.
- Keep the public world readable by role: `setting` describes the present-day place, `atmosphere` its lived texture, `history` established island/community past, `customs` recurring ordinary practice, and `technology` the material era. Use `background` only for remaining current public context. A named playable site belongs in `locations`, not in a lore paragraph.
- Day One is a handful of possible scene offers, not a scene-by-scene railroad and not a lore list. Every NEW event must be a concrete encounter the player can enter: give it a real `when`, an established `location`, `participants` including `player`, a `visible` sentence showing what is happening and what makes it press on the player, and a separate `hook` that names the immediate question, choice, or action the player can take. Give it a terse public `theme` (the tension in this moment), `tone` (how it feels on stage), and `roles` (each on-page participant's immediate function, keyed by that participant). These are scene-facing and may not contain secrets or psychological diagnoses. A history fact by itself is never an event. Put island history, traditions, and background in the world/location material; use history in a scene only when it is embodied as a clue, person, object, rumor, or place the player can inspect, question, follow, or respond to.
- `hidden` is director-only context: it can hold a private character fact or a secret clue. Set `entity_action` to true only when the entity actively does something in that scene; only those actions need an entity period compatible with the scene time. Set it false for private facts, character reactions, and background already true before the scene.
- When referring to an existing Day One event id, never rewrite its scene. You may only supply a missing public `evidence` trace or blank `knowledge` entries; leave every other field empty in that proposal item.
- If this is a loop story, make its policy executable: trigger, restart, memory rules, and what resets. If it is not a loop story, leave loop fields empty.
- Empty strings and empty lists mean no proposal for that field. Do not add generic filler or overwrite a fact just because it could be phrased better.
- PROSE DISCIPLINE, for EVERY text field: tight declarative COMPLETE sentences with terminal punctuation — one idea per field. Budgets: `visible`/`hook`/location `description`/`setting`/`atmosphere` ≤ 40 words; relationship `dynamic`/`persona`/`connection` ≤ 25 words. Never a fragment, never a clause that trails off mid-thought. If a thought runs long, drop the sentence — never cut one short. The narrator reads these fields verbatim on every turn; every word must be load-bearing.

{public_only_rule}

CURRENT STORY CARD:
{_json(card)}"""


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return not bool(value)


def _meaningful(value: Any) -> bool:
    """Whether a structured-output value carries an actual proposed fact.

    Strict JSON schemas make models emit empty objects, false booleans, and
    blank strings for fields they do not intend to use.  A public-only pass
    must tolerate that shape while rejecting a real private fact at the
    application boundary.
    """
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful(item) for item in value)
    return value is True or (value is not None and bool(value))


def _assert_public_only_proposal(proposal: dict[str, Any]) -> None:
    """Reject a completion proposal that tries to add director-only canon.

    Prompt wording and a redacted input card are not sufficient protection:
    an output model can still manufacture a private entity rule, knowledge
    gate, or character secret.  Autonomous completion is intentionally
    restricted to public connective material, so private mechanics must be
    authored through the protected interview/director paths instead.

    Keep the error deliberately generic.  Returning a model's attempted
    private value in a validation message would itself create an information
    disclosure path.
    """
    world = proposal.get("world") if isinstance(proposal.get("world"), dict) else {}
    if _meaningful(world.get("entity")) or _meaningful(world.get("loop")):
        raise DevelopError("public completion cannot introduce private story mechanics")
    if _meaningful(proposal.get("time_system")):
        raise DevelopError("public completion cannot introduce private story mechanics")
    if _meaningful(proposal.get("relationships")):
        raise DevelopError("public completion cannot introduce private story mechanics")

    for raw_character in proposal.get("characters") or []:
        character = raw_character if isinstance(raw_character, dict) else {}
        if any(_meaningful(character.get(field)) for field in ("want", "wound", "lie", "secret")):
            raise DevelopError("public completion cannot introduce private story mechanics")

    plan = proposal.get("first_day_plan") if isinstance(proposal.get("first_day_plan"), dict) else {}
    for raw_event in plan.get("events") or []:
        event = raw_event if isinstance(raw_event, dict) else {}
        if any(_meaningful(event.get(field)) for field in (
            "hidden", "entity_action", "entity_period", "trigger", "evidence", "knowledge",
        )):
            raise DevelopError("public completion cannot introduce private story mechanics")


def _deep_merge_missing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Add proposed detail without changing a nonblank author value."""
    out = deepcopy(existing or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_missing(out[key], value)
        elif key not in out or _blank(out.get(key)):
            if not _blank(value):
                out[key] = deepcopy(value)
    return out


def _world_proposal(value: Any) -> dict[str, Any]:
    """Drop schema-mandated empty branches before they can imply a loop/entity.

    Strict structured output needs every key present.  An all-empty ``loop``
    object must not turn an ordinary story into a broken loop story merely by
    existing, and an all-empty ``entity`` must not demand an entity schedule.
    """
    if not isinstance(value, dict):
        return {}
    out = {key: _string(value.get(key))
           for key in ("genre", "setting", "atmosphere", "history", "customs", "technology", "background")
           if _string(value.get(key))}
    raw_entity = value.get("entity")
    if isinstance(raw_entity, dict):
        entity = {key: _string(raw_entity.get(key))
                  for key in ("description", "knowledge", "limitations", "tactic", "objective")
                  if _string(raw_entity.get(key))}
        if entity:
            out["entity"] = entity
    raw_loop = value.get("loop")
    if not isinstance(raw_loop, dict):
        return out
    loop = {key: _string(raw_loop.get(key))
            for key in ("start", "reset", "memory", "returner", "end_condition")
            if _string(raw_loop.get(key))}
    raw_policy = raw_loop.get("policy")
    if isinstance(raw_policy, dict):
        trigger = _string(raw_policy.get("trigger"))
        restart = _string(raw_policy.get("restart"))
        preserve = raw_policy.get("preserve") if isinstance(raw_policy.get("preserve"), dict) else {}
        runtime = [item for item in (preserve.get("runtime") or []) if _string(item)]
        memories = [item for item in (preserve.get("memories") or []) if _string(item)]
        clear = preserve.get("clear_memories_for_others")
        victims = raw_policy.get("victims_return_after_end")
        # A loop policy exists only when there is actual loop evidence.  Once it
        # exists, keep empty runtime [] as an explicit deliberate reset choice.
        meaningful = bool(trigger or restart or runtime or memories or clear is True or victims is True)
        if meaningful:
            policy: dict[str, Any] = {}
            if trigger:
                policy["trigger"] = trigger
            if restart:
                policy["restart"] = restart
            if runtime or memories or isinstance(clear, bool):
                policy["preserve"] = {"runtime": runtime, "memories": memories}
                if isinstance(clear, bool):
                    policy["preserve"]["clear_memories_for_others"] = clear
            if isinstance(victims, bool):
                policy["victims_return_after_end"] = victims
            loop["policy"] = policy
    if loop:
        out["loop"] = loop
    return out


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_") or "character"


def _new_key(name: str, taken: set[str]) -> str:
    base = _slug(name)
    key, suffix = base, 2
    while key in taken:
        key, suffix = f"{base}_{suffix}", suffix + 1
    taken.add(key)
    return key


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _merge_by_id(existing: list[dict], additions: list[dict], identity: str) -> tuple[list[dict], bool]:
    """Append only genuinely new ids; never mutate a prior authored item."""
    out = deepcopy(existing or [])
    seen = {str(item.get(identity) or "") for item in out if isinstance(item, dict)}
    changed = False
    for item in additions or []:
        if not isinstance(item, dict):
            continue
        ident = _string(item.get(identity))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(deepcopy(item))
        changed = True
    return out, changed


def _character_name_index(card: dict[str, Any], characters: dict[str, dict], known_characters: dict[str, Any]) -> dict[str, str]:
    """Map names already owned by this story to their stable keys.

    A global library character is available only when this story already casts
    that exact key.  Looking through every global name would make a fresh story
    silently inherit an unrelated `Shuri` instead of receiving its own
    story-bound card.
    """
    names: dict[str, str] = {}
    for member in card.get("cast") or []:
        key = member.get("character") if isinstance(member, dict) else ""
        doc = characters.get(key) or known_characters.get(key)
        name = _string((doc or {}).get("name") if isinstance(doc, dict) else getattr(doc, "name", ""))
        if key and name:
            names.setdefault(name.lower(), key)
    # Reuse detached-but-embedded records from this story too.  These are
    # story-owned, unlike the application's global character library.
    for key, doc in characters.items():
        name = _string((doc or {}).get("name") if isinstance(doc, dict) else getattr(doc, "name", ""))
        if key and name:
            names.setdefault(name.lower(), key)
    return names


_NAME_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with",
}


def _card_prose(card: dict[str, Any]) -> str:
    """Return author-facing canon prose without treating map labels as people."""
    leaves: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            if value.strip():
                leaves.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(card.get("world") or {})
    collect(card.get("premise") or "")
    collect(card.get("fields") or {})
    return "\n".join(leaves)


def _preserve_established_name(name: str, card: dict[str, Any]) -> str:
    """Avoid turning a named canon person into a new, fuller-named stranger.

    Character cards can be missing while the person is already established in
    prose.  A model's harmless-looking `Shuri Yukawa` would otherwise create a
    second person beside the author's `Shuri`.  Only collapse a multiword
    proposal when its given name is already present in author-facing canon; an
    exact existing full name remains untouched.
    """
    parts = name.split()
    if len(parts) < 2 or parts[0].lower() in _NAME_STOPWORDS:
        return name
    prose = _card_prose(card)
    if not prose or re.search(rf"\b{re.escape(name)}\b", prose, re.IGNORECASE):
        return name
    first = parts[0]
    if not re.search(rf"\b{re.escape(first)}\b", prose, re.IGNORECASE):
        return name
    # If canon already gives a full, capitalized name with this first token,
    # preserve that precise form instead of choosing the model's alternative.
    full_names = {
        match.group(0)
        for match in re.finditer(rf"\b{re.escape(first)}(?:\s+[A-Z][A-Za-z'’-]*)+\b", prose)
    }
    if len(full_names) == 1:
        return next(iter(full_names))
    return first


def _resolve_person(value: Any, names: dict[str, str], known_keys: set[str]) -> str:
    text = _string(value)
    if not text:
        return ""
    if text.lower() in {"player", "you", "protagonist", "returner"}:
        return "player"
    if text in known_keys:
        return text
    key = names.get(text.lower())
    if key:
        return key
    raise DevelopError(f"proposal refers to an unknown character '{text}'")


def _resolve_people(values: Any, names: dict[str, str], known_keys: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        key = _resolve_person(value, names, known_keys)
        if key and key not in out:
            out.append(key)
    return out


_SAFE_EXISTING_EVENT_KNOWLEDGE_KEYS = ("protagonist", "entity", "public")


def _repair_existing_event_annotations(existing: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Fill only empty, non-canonical annotations on a matching Day One event.

    Matching an existing event id is *not* permission for the batch developer
    to revise the scene.  Its time, place, participants, visible situation,
    hidden/director material, triggers, and entity schedule are established
    canon.  The small exception here lets a structural-gap pass attach a
    missing discoverable trace or knowledge gate without opening a general
    event-rewrite path.

    Returns ``(repaired, added_fields, preserved_fields)``.  ``preserved`` is
    used only to make a no-op proposal explainable to the caller; it never
    exposes the prior values themselves.
    """
    repaired = deepcopy(existing)
    added: list[str] = []
    preserved: list[str] = []

    evidence = _string(incoming.get("evidence"))
    if evidence:
        if _blank(repaired.get("evidence")):
            repaired["evidence"] = evidence
            added.append("evidence")
        else:
            preserved.append("evidence")

    proposed_knowledge = incoming.get("knowledge")
    if not isinstance(proposed_knowledge, dict):
        return repaired, added, preserved

    current_knowledge = repaired.get("knowledge")
    if isinstance(current_knowledge, dict):
        knowledge = deepcopy(current_knowledge)
    elif _blank(current_knowledge):
        knowledge = {}
    else:
        # A non-object, nonblank legacy value is still established data.  Do
        # not coerce or replace it merely because a proposal supplied a modern
        # knowledge object.
        if any(_string(proposed_knowledge.get(key)) for key in _SAFE_EXISTING_EVENT_KNOWLEDGE_KEYS):
            preserved.append("knowledge")
        return repaired, added, preserved

    knowledge_changed = False
    for key in _SAFE_EXISTING_EVENT_KNOWLEDGE_KEYS:
        value = _string(proposed_knowledge.get(key))
        if not value:
            continue
        if _blank(knowledge.get(key)):
            knowledge[key] = value
            knowledge_changed = True
            added.append(f"knowledge.{key}")
        else:
            preserved.append(f"knowledge.{key}")
    if knowledge_changed:
        repaired["knowledge"] = knowledge
    return repaired, added, preserved


def _event_from_proposal(event: dict[str, Any], *, names: dict[str, str], known_keys: set[str], location_ids: set[str]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise DevelopError("first-day event must be an object")
    out = deepcopy(event)
    event_id = _string(out.get("id"))
    if not event_id:
        raise DevelopError("first-day event needs a stable id")
    when = _string(out.get("when"))
    if not when:
        raise DevelopError(f"first-day scene '{event_id}' needs a time")
    out["when"] = when
    location = _string(out.get("location"))
    if not location:
        raise DevelopError(f"first-day scene '{event_id}' needs a location")
    if location not in location_ids:
        raise DevelopError(f"first-day event '{event_id}' uses unknown location '{location}'")
    out["location"] = location
    out["visible"] = _string(out.get("visible"))
    out["hook"] = _string(out.get("hook"))
    out["participants"] = _resolve_people(out.get("participants"), names, known_keys)
    # A proposed scene offer is something the player can actually choose to
    # enter.  Make their presence explicit even when the co-author only named
    # the person waiting there; this is public scheduling data, not a new
    # relationship or a claim that the player has learned a private fact.
    out = ensure_player_participant(out)
    raw_roles = out.get("roles")
    if isinstance(raw_roles, list):
        resolved_roles: list[dict[str, Any]] = []
        for raw_role in raw_roles:
            if not isinstance(raw_role, dict):
                raise DevelopError("first-day scene roles must be objects")
            item = dict(raw_role)
            person = _resolve_person(item.get("character"), names, known_keys)
            if person:
                item["character"] = person
            resolved_roles.append(item)
        out["roles"] = resolved_roles
    elif isinstance(raw_roles, dict):
        out["roles"] = {
            _resolve_person(person, names, known_keys): value
            for person, value in raw_roles.items()
            if _resolve_person(person, names, known_keys)
        }
    try:
        out = normalize_scene_kernel(out, known_characters=known_keys)
    except DramaticKernelError as exc:
        raise DevelopError(str(exc)) from exc
    # Strict providers always emit the boolean.  The fallback preserves the
    # sensible interpretation for direct callers and older hand-built proposals:
    # an explicit period means a present entity action; an unlabelled hidden note
    # stays director-private rather than becoming a scheduling constraint.
    entity_action = out.get("entity_action")
    if not isinstance(entity_action, bool):
        entity_action = bool(_string(out.get("entity_period")))
    out["entity_action"] = entity_action
    if not entity_action:
        out["entity_period"] = ""
    knowledge = out.get("knowledge")
    if not isinstance(knowledge, dict):
        out["knowledge"] = {"protagonist": "", "entity": "", "public": ""}
    try:
        assert_playable_scene_offer(out)
    except ValueError as exc:
        raise DevelopError(str(exc)) from exc
    return out


@dataclass
class DevelopCandidate:
    story: dict[str, Any]
    characters: dict[str, dict]
    created: list[dict[str, str]]
    updated_sections: list[str]
    message: str
    patch: dict[str, Any]


def apply_develop_proposal(
    card: dict[str, Any], proposal: dict[str, Any], *, story_key: str,
    embedded_characters: dict[str, dict] | None = None,
    known_characters: dict[str, Any] | None = None,
    scopes: set[str] | None = None,
    public_only: bool = False,
) -> DevelopCandidate:
    """Build a validated-in-principle additive candidate without persisting it.

    The caller must run the final Pydantic/cross-reference validation immediately
    before its single transactional save.  Keeping proposal application pure makes
    bad model output unable to create orphan cards.
    """
    if not isinstance(proposal, dict):
        raise DevelopError("developer returned no structured proposal")
    if public_only:
        _assert_public_only_proposal(proposal)
    scopes = set(scopes or _SCOPE_ALIASES["starter"])
    story = deepcopy(card)
    existing_chars = deepcopy(embedded_characters or {})
    known_characters = known_characters or {}
    names = _character_name_index(story, existing_chars, known_characters)
    # Keys in the global library are valid only if this story already refers to
    # them.  New generated records live in the story namespace, which can safely
    # use `shuri` even if another story/library record has that same key.
    story_cast_keys = {
        _string(member.get("character")) for member in (story.get("cast") or [])
        if isinstance(member, dict) and _string(member.get("character"))
    }
    known_keys = set(existing_chars) | story_cast_keys
    taken_keys = set(known_keys)
    created: list[dict[str, str]] = []
    updated_sections: list[str] = []
    patch: dict[str, Any] = {}
    no_op_event_repair = False

    def changed(section: str) -> None:
        if section not in updated_sections:
            updated_sections.append(section)

    if "world" in scopes:
        incoming_world = _world_proposal(proposal.get("world"))
        if isinstance(incoming_world, dict):
            merged = _deep_merge_missing(story.get("world") or {}, incoming_world)
            if merged != (story.get("world") or {}):
                story["world"] = merged
                patch["world"] = deepcopy(merged)
                changed("world")
        locations = proposal.get("locations")
        if isinstance(locations, list):
            valid_locations = []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                loc_id = _string(loc.get("id"))
                if not loc_id:
                    continue
                valid_locations.append({
                    "id": loc_id, "name": _string(loc.get("name")) or loc_id,
                    "description": _string(loc.get("description")),
                })
            merged, did_change = _merge_by_id(story.get("locations") or [], valid_locations, "id")
            if did_change:
                story["locations"] = merged
                patch["locations"] = deepcopy(valid_locations)
                changed("world")
        location_ids = {str(loc.get("id")) for loc in (story.get("locations") or []) if isinstance(loc, dict)}
        proposed_start = _string(proposal.get("start"))
        if _blank(story.get("start")) and proposed_start:
            if proposed_start not in location_ids:
                raise DevelopError(f"start location '{proposed_start}' is not in the proposed map")
            story["start"] = proposed_start
            patch["start"] = proposed_start
            changed("world")

    # Character records are materialized only in this local candidate.  A name
    # already on the card always resolves to its existing record, preventing the
    # repeated-Shuri duplicate-card failure mode.
    if "cast" in scopes:
        loc_ids = {str(loc.get("id")) for loc in (story.get("locations") or []) if isinstance(loc, dict)}
        cast = deepcopy(story.get("cast") or [])
        cast_keys = {m.get("character") for m in cast if isinstance(m, dict) and m.get("character")}
        has_primary = any(bool(m.get("primary")) for m in cast if isinstance(m, dict))
        additions: list[dict[str, Any]] = []
        for person in proposal.get("characters") or []:
            if not isinstance(person, dict):
                continue
            proposed_name = _string(person.get("name"))
            if not proposed_name:
                continue
            # Exact existing cards always win.  Otherwise preserve a person who
            # was established in prose before a model supplied a new surname.
            name = proposed_name if names.get(proposed_name.lower()) else _preserve_established_name(proposed_name, story)
            key = names.get(proposed_name.lower()) or names.get(name.lower())
            if not key:
                key = _new_key(name, taken_keys)
                fields = {
                    field: _string(person.get(field))
                    for field in ("role", "appearance", "personality", "background", "connection",
                                  "want", "wound", "lie", "secret")
                    if _string(person.get(field))
                }
                fields.update({"story": story_key, "_generated": True})
                doc = Character(name=name, system=_string(person.get("persona")), fields=fields,
                                playable=False).model_dump()
                existing_chars[key] = doc
                known_keys.add(key)
                names[name.lower()] = key
                created.append({"key": key, "name": name})
            # Relations and scene participants can still use the model's
            # original spelling; both forms resolve to the same card.
            names.setdefault(proposed_name.lower(), key)
            if key in cast_keys:
                continue
            home = _string(person.get("home"))
            if home and home not in loc_ids:
                home = ""  # a new card must not make an old map invalid
            primary = bool(person.get("primary")) and not has_primary
            if not has_primary and not additions:
                primary = True  # a blank story needs one stable visual/card anchor
            has_primary = has_primary or primary
            additions.append({"character": key, "primary": primary, "home": home})
            cast_keys.add(key)
        if additions:
            story["cast"] = cast + additions
            patch["cast"] = deepcopy(additions)
            changed("cast")

        rels: list[dict[str, Any]] = []
        existing_rel_ids = {str(r.get("id") or "") for r in (story.get("relationships") or []) if isinstance(r, dict)}
        existing_pairs = {
            (str(r.get("source") or ""), str(r.get("target") or ""))
            for r in (story.get("relationships") or []) if isinstance(r, dict)
        }
        relation_number = 1
        for relationship in proposal.get("relationships") or []:
            if not isinstance(relationship, dict):
                continue
            source = _resolve_person(relationship.get("source"), names, known_keys)
            target = _resolve_person(relationship.get("target"), names, known_keys)
            if not source or not target or source == "player" or target == "player":
                continue
            if source not in cast_keys or target not in cast_keys:
                raise DevelopError("relationship participants must both be in the story cast")
            if (source, target) in existing_pairs:
                continue
            while f"develop-bond-{relation_number}" in existing_rel_ids:
                relation_number += 1
            relation = {
                "id": f"develop-bond-{relation_number}", "source": source, "target": target,
                "nature": _string(relationship.get("nature")),
                "dynamic": _string(relationship.get("dynamic")),
                "stance": _string(relationship.get("stance")) or "neutral",
                "target_dynamic": _string(relationship.get("target_dynamic")),
                "target_stance": _string(relationship.get("target_stance")),
                "note": _string(relationship.get("note")),
                "potential": _string(relationship.get("potential")),
                "trajectory": _string(relationship.get("trajectory")),
            }
            existing_rel_ids.add(relation["id"])
            existing_pairs.add((source, target))
            rels.append(relation)
            relation_number += 1
        if rels:
            story["relationships"] = deepcopy(story.get("relationships") or []) + rels
            patch["relationships"] = deepcopy(rels)
            changed("cast")

        # Structured providers return a strict pair list, while the persisted
        # card keeps the compact key-to-core map.  Cores are additive public
        # authoring context, not a reason to overwrite a character's richer
        # private arc pressure or a previously established surface.
        try:
            raw_cores = character_core_map(proposal.get("character_cores"))
            resolved_cores = {
                _resolve_person(person, names, known_keys): core
                for person, core in raw_cores.items()
                if _resolve_person(person, names, known_keys)
            }
            incoming_cores = normalize_character_cores(
                resolved_cores, known_characters=known_keys,
            )
        except DramaticKernelError as exc:
            raise DevelopError(str(exc)) from exc
        if incoming_cores:
            fields = dict(story.get("fields") or {})
            current_cores = fields.get("character_cores")
            # Preserve a malformed legacy value rather than treating a safe
            # additive pass as permission to erase it.  New cards always get
            # the canonical map.
            if current_cores is None:
                merged_cores: dict[str, Any] | None = {}
            elif isinstance(current_cores, dict):
                merged_cores = deepcopy(current_cores)
            else:
                # A malformed legacy value is established data.  A generic
                # additive pass must not use a new public core as a pretext to
                # replace it; an explicit focused edit can repair it later.
                merged_cores = None
            additions = ({
                person: core for person, core in incoming_cores.items()
                if _blank(merged_cores.get(person))
            } if merged_cores is not None else {})
            if additions and merged_cores is not None:
                merged_cores.update(additions)
                fields["character_cores"] = merged_cores
                story["fields"] = fields
                patch.setdefault("fields", {})["character_cores"] = deepcopy(merged_cores)
                changed("cast")

    if "premise" in scopes:
        premise = _string(proposal.get("premise"))
        if _blank(story.get("premise")) and premise:
            story["premise"] = premise
            patch["premise"] = premise
            changed("premise")

    if "time_system" in scopes:
        incoming_time = proposal.get("time_system")
        if isinstance(incoming_time, dict):
            current = deepcopy(story.get("time_system") or {})
            proposed_slots = [_string(slot) for slot in (incoming_time.get("slots") or []) if _string(slot)]
            if _blank(current.get("slots")) and proposed_slots:
                current["slots"] = proposed_slots
            periods = []
            for period in incoming_time.get("entity_periods") or []:
                if not isinstance(period, dict) or not _string(period.get("id")):
                    continue
                periods.append({
                    "id": _string(period.get("id")),
                    "slots": [_string(s) for s in (period.get("slots") or []) if _string(s)],
                    "state": _string(period.get("state")),
                    "capabilities": [_string(s) for s in (period.get("capabilities") or []) if _string(s)],
                    "constraint": _string(period.get("constraint")),
                })
            merged_periods, added = _merge_by_id(current.get("entity_periods") or [], periods, "id")
            if added:
                current["entity_periods"] = merged_periods
            if current != (story.get("time_system") or {}):
                story["time_system"] = current
                patch["time_system"] = deepcopy(current)
                changed("time_system")

    if "first_day" in scopes:
        incoming_plan = proposal.get("first_day_plan")
        if isinstance(incoming_plan, dict):
            current = deepcopy((story.get("fields") or {}).get("first_day_plan") or {})
            loc_ids = {str(loc.get("id")) for loc in (story.get("locations") or []) if isinstance(loc, dict)}
            for field in ("objective", "opening_time", "opening_location"):
                value = _string(incoming_plan.get(field))
                if _blank(current.get(field)) and value:
                    if field == "opening_location" and value not in loc_ids:
                        raise DevelopError(f"opening location '{value}' is not in the proposed map")
                    current[field] = value
            proposed_present = incoming_plan.get("opening_present")
            if _blank(current.get("opening_present")) and isinstance(proposed_present, list) and proposed_present:
                current["opening_present"] = _resolve_people(proposed_present, names, known_keys)
            existing_events = current.get("events")
            if not isinstance(existing_events, list):
                existing_events = []
            # An id can be repaired only when it names exactly one stored
            # event.  Duplicate ids are an authored integrity problem; picking
            # one to mutate would be a stealth rewrite rather than a safe
            # annotation fill.
            existing_event_indices: dict[str, int | None] = {}
            for index, event in enumerate(existing_events):
                if not isinstance(event, dict):
                    continue
                ident = _string(event.get("id"))
                if not ident:
                    continue
                existing_event_indices[ident] = (
                    index if ident not in existing_event_indices else None
                )

            new_events: list[dict[str, Any]] = []
            repair_added: list[str] = []
            repair_preserved: list[str] = []
            repair_attempted = False
            repaired_event_ids: set[str] = set()
            for event in incoming_plan.get("events") or []:
                if not isinstance(event, dict):
                    raise DevelopError("first-day event must be an object")
                ident = _string(event.get("id"))
                if ident in existing_event_indices:
                    # A structured proposal should name an existing scene at
                    # most once.  First-wins avoids a second model item using
                    # a different blank annotation as an indirect rewrite.
                    if ident in repaired_event_ids:
                        continue
                    repaired_event_ids.add(ident)
                    repair_attempted = True
                    index = existing_event_indices[ident]
                    if index is None:
                        # Preserve an ambiguous duplicate untouched.  The
                        # compiler/readiness path can ask the author to repair
                        # its ids through a focused edit.
                        repair_preserved.append(f"{ident}.duplicate_id")
                        continue
                    repaired, added_fields, preserved_fields = _repair_existing_event_annotations(
                        existing_events[index], event,
                    )
                    if added_fields:
                        existing_events[index] = repaired
                        repair_added.extend(f"{ident}.{field}" for field in added_fields)
                    repair_preserved.extend(f"{ident}.{field}" for field in preserved_fields)
                    continue
                new_events.append(_event_from_proposal(
                    event, names=names, known_keys=known_keys, location_ids=loc_ids,
                ))

            merged_events, added = _merge_by_id(existing_events, new_events, "id")
            if added:
                current["events"] = merged_events
            if current and current != ((story.get("fields") or {}).get("first_day_plan") or {}):
                fields = dict(story.get("fields") or {})
                fields["first_day_plan"] = current
                story["fields"] = fields
                patch["fields"] = {"first_day_plan": deepcopy(current)}
                changed("first_day")

            # Keep the no-op explanation local to this narrow repair rule.
            # A later generic no-op message covers proposals that simply did
            # not contain any useful missing material at all.
            if repair_attempted and not repair_added and repair_preserved:
                no_op_event_repair = True
            elif repair_added:
                no_op_event_repair = False

    questions = [_string(question) for question in (proposal.get("open_questions") or []) if _string(question)]
    if questions:
        fields = dict(story.get("fields") or {})
        old_questions = [_string(q) for q in (fields.get("open_questions") or []) if _string(q)]
        additions = [q for q in questions if q not in old_questions]
        if additions:
            fields["open_questions"] = old_questions + additions
            story["fields"] = fields
            patch.setdefault("fields", {})["open_questions"] = fields["open_questions"]

    if updated_sections or created:
        fields = dict(story.get("fields") or {})
        fields["status"] = "interviewing"
        fields.pop("runtime_scenario", None)
        story["fields"] = fields

    message = _string(proposal.get("message"))
    if not updated_sections and not created:
        message = (
            "No changes were made: matching Day One scenes already had established "
            "knowledge or evidence, so their canon was preserved."
            if no_op_event_repair else
            "No changes were made: the proposal contained no missing material within the selected sections."
        )

    return DevelopCandidate(
        story=story, characters=existing_chars, created=created,
        updated_sections=updated_sections, message=message or "Developed the selected story-card sections.",
        patch=patch,
    )


def validate_candidate(candidate: DevelopCandidate, known_characters: dict[str, Any]) -> dict[str, Any]:
    """Validate the full aggregate before any card or character is persisted."""
    try:
        story = Story(**candidate.story).model_dump()
    except Exception as exc:  # Pydantic carries useful all-reference diagnostics.
        raise DevelopError(f"proposal does not form a valid story card: {exc}") from exc
    chars: dict[str, dict] = {}
    try:
        for key, doc in candidate.characters.items():
            chars[key] = Character(**doc).model_dump()
    except Exception as exc:
        raise DevelopError(f"proposal does not form a valid character card: {exc}") from exc
    errors = story_reference_errors(story, set(known_characters) | set(chars))
    if errors:
        raise DevelopError("proposal has invalid card references: " + "; ".join(errors[:4]))
    return {"story": story, "characters": chars}
