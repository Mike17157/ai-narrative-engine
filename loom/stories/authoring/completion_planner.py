"""Deterministic, privacy-safe planning for an Architect completion pass.

The Story Architect is allowed to be helpful without turning into an
unbounded background writer.  This module makes that boundary explicit: it
looks at the *structural* card-gap report and sorts remaining work into either
public, additive scaffolding or an authorial hinge that needs a human choice.

It deliberately does not return prose from the card or gap report.  A caller
can use its ids to select an existing safe edit operation, but it cannot learn
hidden event text, private arc truth, or author notes through the plan.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from copy import deepcopy
import re
from typing import Any


# These scopes are the only public additive work a completion pass may queue.
# Time rules, relationships, and character/thematic interiority are excluded
# even when a generic developer happens to support a similarly named scope.
_SAFE_SCOPE_ORDER = ("world", "premise", "cast", "first_day")
_SAFE_SCOPES = frozenset(_SAFE_SCOPE_ORDER)

_AUTHORIAL_SECTIONS = frozenset({"arcs", "time_system"})
_AUTHORIAL_PREFIXES = (
    "arc-",
    "theme-",
    "knowledge-",
    "evidence-",
    "relationship-",
    "relationships-",
    "cast-relationship-",
    "scene-relationship-",
)

# A completion pass can introduce only ordinary public people who make an
# already-authored place playable.  These are intentionally roles, not names,
# motives, family connections, or mystery functions.  The actual name and one
# visual note may come from the one bounded model call, then the sanitizer
# below removes every private/interior field before the card boundary sees it.
_MAX_PUBLIC_SUPPORTERS = 2
_PUBLIC_SUPPORT_RULES = (
    (frozenset({"ferry", "boat", "ship"}), "ferry-crew", "ferry crew member"),
    (frozenset({"dock", "harbor", "harbour", "pier", "port", "marina"}), "dock-attendant", "dock attendant"),
    (frozenset({"square", "plaza"}), "market-vendor", "market vendor"),
    (frozenset({"cafe", "coffee", "diner", "restaurant", "teahouse", "tea"}), "cafe-worker", "cafe worker"),
    (frozenset({"market", "shop", "store", "bakery"}), "shop-worker", "shop worker"),
    (frozenset({"library", "bookshop", "bookstore"}), "librarian", "librarian"),
    (frozenset({"clinic", "hospital", "pharmacy"}), "clinic-worker", "clinic worker"),
)
_NON_PUBLIC_LOCATION_TOKENS = frozenset({
    "altar", "cave", "chamber", "crypt", "entity", "hidden", "sacrifice",
    "secret", "shrine", "sleeping", "temple", "tomb",
})
_PLAYER_OBSERVER_ALIASES = frozenset({"player", "protagonist", "returner", "you"})


def _text(value: Any, *, limit: int = 240) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_text(item) for item in value if _text(item)]


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    return bool(value)


def _has_story_seed(card: Mapping[str, Any]) -> bool:
    fields = _mapping(card.get("fields"))
    return bool(
        _text(card.get("premise"))
        or _mapping(card.get("world"))
        or bool(card.get("locations"))
        or _mapping(card.get("time_system"))
        or card.get("cast")
        or _mapping(fields.get("first_day_plan"))
    )


def _has_gated_day_one(card: Mapping[str, Any]) -> bool:
    """Return whether a Day One plan contains non-public story mechanics.

    The planner inspects only the *presence* of a gate.  It must not pass the
    value along, because a hidden event, evidence, or knowledge rule is a
    director-only fact.  Once such a gate exists, a generic completion pass is
    not allowed to alter the first-day plan as a batch.
    """
    fields = _mapping(card.get("fields"))
    plan = _mapping(fields.get("first_day_plan"))
    events = plan.get("events")
    if not isinstance(events, list):
        return False
    for raw_event in events:
        event = _mapping(raw_event)
        if not event:
            continue
        if bool(event.get("entity_action")):
            return True
        if any(_has_value(event.get(name)) for name in ("hidden", "trigger", "evidence", "entity_period")):
            return True
        if _has_value(_mapping(event.get("knowledge"))):
            return True
    return False


def _gated_event_locations(card: Mapping[str, Any]) -> set[str]:
    """Return only the public location ids attached to protected Day One events.

    A supporting character may be safely rooted at another, entirely public
    place even when a story has a hidden scene somewhere else.  What it may
    not do is silently turn an already-protected scene location into a new
    witness pool.  This helper therefore reads only the gate *shape* and the
    existing public location id, never a hidden fact or its evidence.
    """
    fields = _mapping(card.get("fields"))
    plan = _mapping(fields.get("first_day_plan"))
    protected: set[str] = set()
    for raw_event in plan.get("events") or []:
        event = _mapping(raw_event)
        if not event or not _event_has_private_gate(event):
            continue
        location_id = _text(event.get("location"), limit=160)
        if location_id:
            protected.add(location_id)
    return protected


def _public_day_one_locations(card: Mapping[str, Any]) -> set[str]:
    """Return declared locations that already host a non-private Day One scene."""
    fields = _mapping(card.get("fields"))
    plan = _mapping(fields.get("first_day_plan"))
    locations: set[str] = set()
    for raw_event in plan.get("events") or []:
        event = _mapping(raw_event)
        if not event or _event_has_private_gate(event):
            continue
        location_id = _text(event.get("location"), limit=160)
        if location_id:
            locations.add(location_id)
    return locations


def _population_gap_present(report: Mapping[str, Any]) -> bool:
    """Whether the safe completion plan has a public reason to populate cast."""
    gaps = report.get("gaps") if isinstance(report.get("gaps"), list) else []
    for raw_gap in gaps:
        gap = _mapping(raw_gap)
        ident = _text(gap.get("id"), limit=240)
        if ident in {"cast-missing", "cast-unintroduced"}:
            return True
        if ident.startswith("readiness-opening-cast-unspecified-"):
            return True
    return False


def _existing_cast_homes_and_roles(card: Mapping[str, Any]) -> tuple[set[str], set[str], int]:
    """Read only public roster metadata used to avoid duplicate support slots."""
    homes: set[str] = set()
    roles: set[str] = set()
    count = 0
    for raw_member in card.get("cast") or []:
        member = _mapping(raw_member)
        key = _text(member.get("character") or member.get("key"), limit=160)
        if key and key != "player":
            count += 1
        home = _text(member.get("home"), limit=160)
        if home:
            homes.add(home)
        role = _text(member.get("role"), limit=160).lower()
        if role:
            roles.add(role)
    # ``cast_details`` is available on a public card projection and is useful
    # when this helper is called from a UI/API boundary rather than raw storage.
    for raw_detail in card.get("cast_details") or []:
        detail = _mapping(raw_detail)
        role = _text(detail.get("role"), limit=160).lower()
        if role:
            roles.add(role)
    return homes, roles, count


def build_public_baseline_population_task(
    card: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    *,
    maximum_characters: int = _MAX_PUBLIC_SUPPORTERS,
) -> dict[str, Any] | None:
    """Return a finite, public-only supporting-cast task or ``None``.

    This is a *task descriptor*, not a second agent and not a canonical card
    mutation.  A coordinator may feed it to its existing single bounded cast
    proposal call, then run :func:`sanitize_public_baseline_population` before
    applying that proposal.  The descriptor never includes scene text, hidden
    event data, private character fields, or relationship instructions.

    The task skips any location already used by a protected Day One event, so
    it cannot silently add a possible witness to that scene.  It also requires
    an existing central cast member and a real cast/readiness gap: a normal
    completion click must not turn every story into a populated town or make a
    supporting role the story's accidental protagonist.
    """
    source = _mapping(card)
    observed = _mapping(report)
    if not source or not _population_gap_present(observed):
        return None
    try:
        requested = int(maximum_characters)
    except (TypeError, ValueError):
        requested = _MAX_PUBLIC_SUPPORTERS
    requested = max(0, min(_MAX_PUBLIC_SUPPORTERS, requested))
    if not requested:
        return None

    homes, existing_roles, existing_count = _existing_cast_homes_and_roles(source)
    if not existing_count:
        return None
    gated_locations = _gated_event_locations(source)
    public_scene_locations = _public_day_one_locations(source)
    if not public_scene_locations:
        return None
    # Keep the generated supporting layer tiny even when a blank card has many
    # public locations.  The primary/central cast remains an author decision.
    capacity = min(requested, max(0, 3 - existing_count))
    if not capacity:
        return None

    archetypes: list[dict[str, str]] = []
    used_roles: set[str] = set()
    for raw_location in source.get("locations") or []:
        location = _mapping(raw_location)
        location_id = _text(location.get("id"), limit=160)
        if (not location_id or location_id in homes or location_id in gated_locations
                or location_id not in public_scene_locations):
            continue
        tokens = set(_location_tokens(location_id)) | set(_location_tokens(location.get("name")))
        if not tokens or tokens & _NON_PUBLIC_LOCATION_TOKENS:
            continue
        for keywords, role_id, role in _PUBLIC_SUPPORT_RULES:
            if not (tokens & keywords) or role in existing_roles or role in used_roles:
                continue
            archetypes.append({
                "id": f"public-{role_id}-{_scene_slug(location_id) or 'location'}",
                "role": role,
                "location_id": location_id,
            })
            used_roles.add(role)
            break
        if len(archetypes) >= capacity:
            break

    if not archetypes:
        return None
    return {
        "kind": "public_supporting_cast",
        "scope": "cast",
        "maximum_characters": len(archetypes),
        "archetypes": archetypes,
        # Structured constraints rather than prose are easy for a coordinator
        # to enforce and cannot accidentally make a model the source of hidden
        # canon.  They are all invariant across stories.
        "constraints": {
            "public_only": True,
            "allow_relationships": False,
            "allow_private_fields": False,
            "allow_primary": False,
        },
    }


def _safe_public_supporter_text(value: Any, *, limit: int) -> str:
    text = _text(value, limit=limit)
    # Do not turn explicit model-hidden wrapper syntax into a new visible card
    # field.  The generic cast proposal has no authority to add hidden data.
    if "[[" in text or "]]" in text:
        return ""
    return text


def sanitize_public_baseline_population(
    proposal: Mapping[str, Any] | None,
    task: Mapping[str, Any] | None,
    *,
    existing_names: Collection[str] = (),
) -> dict[str, Any]:
    """Return a cast-only, public-safe proposal fragment for one task.

    The output is compatible with ``apply_develop_proposal`` but purposely
    contains no world, scene, relationship, secret, wound, lie, want, or
    personality fields.  It binds each accepted generated name to the exact
    role/location selected by the deterministic task and caps the result at
    two.  Thus one model call may suggest ordinary names/appearance, but cannot
    use this path to manufacture central canon or an information-gated fact.
    """
    raw_task = _mapping(task)
    if raw_task.get("kind") != "public_supporting_cast":
        return {"characters": [], "relationships": []}
    raw_archetypes = raw_task.get("archetypes")
    if not isinstance(raw_archetypes, list):
        return {"characters": [], "relationships": []}
    archetypes: list[dict[str, str]] = []
    for raw_archetype in raw_archetypes[:_MAX_PUBLIC_SUPPORTERS]:
        archetype = _mapping(raw_archetype)
        role = _safe_public_supporter_text(archetype.get("role"), limit=120)
        location_id = _safe_public_supporter_text(archetype.get("location_id"), limit=160)
        ident = _safe_public_supporter_text(archetype.get("id"), limit=160)
        if role and location_id and ident:
            archetypes.append({"id": ident, "role": role, "location_id": location_id})
    if not archetypes:
        return {"characters": [], "relationships": []}

    incoming = _mapping(proposal).get("characters")
    if not isinstance(incoming, list):
        return {"characters": [], "relationships": []}
    seen_names = {_safe_public_supporter_text(name, limit=120).lower()
                  for name in existing_names if _safe_public_supporter_text(name, limit=120)}
    characters: list[dict[str, Any]] = []
    for raw_person, archetype in zip(incoming, archetypes):
        person = _mapping(raw_person)
        name = _safe_public_supporter_text(person.get("name"), limit=120)
        if not name or name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        characters.append({
            "name": name,
            "role": archetype["role"],
            # A visual note is public surface description.  Every field that
            # could encode a secret, trauma, desire, or model instruction is
            # deliberately reset to empty at this mutation boundary.
            "appearance": _safe_public_supporter_text(person.get("appearance"), limit=240),
            "persona": "",
            "personality": "",
            "background": "",
            "connection": "",
            "want": "",
            "wound": "",
            "lie": "",
            "secret": "",
            "primary": False,
            "home": archetype["location_id"],
        })
    return {"characters": characters, "relationships": []}


def build_director_knowledge_task(
    card: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    *,
    blocked_gap_ids: Collection[str] = (),
) -> dict[str, Any] | None:
    """Return one narrow private-knowledge assignment task, if it is safe.

    This task is deliberately *not* a generic development scope.  Its model
    can inspect one already-authored protected scene through the focused
    Director boundary, then may fill only blank ``knowledge`` entries for
    people who are already present in that scene.  It cannot create a hidden
    action, invent an off-screen witness, or alter the scene's public surface.

    The returned descriptor contains ids and observer keys only.  In
    particular, it never puts the protected scene text, evidence, or entity
    information into an Architect plan or generic model prompt.
    """
    source = _mapping(card)
    observed = _mapping(report)
    blocked = {_text(item, limit=240) for item in blocked_gap_ids if _text(item, limit=240)}
    fields = _mapping(source.get("fields"))
    plan = _mapping(fields.get("first_day_plan"))
    events = plan.get("events") if isinstance(plan.get("events"), list) else []
    by_id = {
        _text(_mapping(event).get("id"), limit=160): _mapping(event)
        for event in events
        if _text(_mapping(event).get("id"), limit=160)
    }
    cast_keys = {
        _text(_mapping(member).get("character") or _mapping(member).get("key"), limit=160)
        for member in source.get("cast") or []
        if _text(_mapping(member).get("character") or _mapping(member).get("key"), limit=160)
    }
    for raw_gap in observed.get("gaps") or []:
        gap = _mapping(raw_gap)
        gap_id = _text(gap.get("id"), limit=240)
        if not gap_id or gap_id in blocked or not gap_id.startswith("knowledge-gate-"):
            continue
        related = [_text(item, limit=160) for item in (gap.get("related") or []) if _text(item, limit=160)]
        event_id = related[0] if related else gap_id.removeprefix("knowledge-gate-")
        event = by_id.get(event_id)
        if not event or not _event_has_private_gate(event) or _has_value(_mapping(event.get("knowledge"))):
            continue
        observers: list[str] = []
        for raw_person in _strings(event.get("participants") or event.get("present")):
            person = "player" if raw_person.lower() in _PLAYER_OBSERVER_ALIASES else raw_person
            if person == "player" or person in cast_keys:
                if person not in observers:
                    observers.append(person)
        if not observers:
            # No existing on-scene observer means selecting one would itself
            # be private canon.  Leave the original authorial hinge intact.
            continue
        return {
            "id": f"director-knowledge-{_scene_slug(event_id) or 'scene'}",
            "kind": "director_knowledge_boundary",
            "section": "first_day",
            "gap_id": gap_id,
            "event_id": event_id,
            "observer_ids": observers,
            "constraints": {
                "existing_event_only": True,
                "existing_observers_only": True,
                "fill_blank_knowledge_only": True,
            },
        }
    return None


def _location_tokens(value: Any) -> tuple[str, ...]:
    """Normalize a public location label for conservative phrase matching."""
    if not isinstance(value, str):
        return ()
    # Possessive punctuation should not stop ``island's dock`` from matching
    # an explicitly declared ``island-dock``.  Do not stem or synonym-expand:
    # that would turn a mechanical repair into an invented location choice.
    words = re.findall(r"[a-z0-9]+", value.lower())
    return tuple(word for word in words if word != "s")


def _contains_token_phrase(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _token_phrase_indexes(haystack: tuple[str, ...], needle: tuple[str, ...]) -> tuple[int, ...]:
    if not needle or len(needle) > len(haystack):
        return ()
    return tuple(index for index in range(len(haystack) - len(needle) + 1)
                 if haystack[index:index + len(needle)] == needle)


def _unambiguous_start_location(card: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Return ``(location_id, ambiguous)`` for a prose loop opening.

    Only multi-word phrases (or an exact id/name) qualify.  A generic one-word
    coincidence such as "home" in an opening sentence is not enough to pick a
    location automatically.  The caller can report the unresolved decision.
    """
    loop = _mapping(_mapping(card.get("world")).get("loop"))
    opening_tokens = _location_tokens(loop.get("start"))
    if not opening_tokens:
        return None, False
    # A statement such as "aboard the electric ferry as it approaches the
    # island dock" names two locations, but the ``aboard`` phrase is an
    # explicit starting-position relation.  Prefer that direct grammar before
    # treating all incidental mentions as ambiguous.
    direct: dict[str, set[str]] = {"aboard": set(), "on": set(), "at": set(), "in": set()}
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for raw_location in card.get("locations") or []:
        location = _mapping(raw_location)
        ident = _text(location.get("id"), limit=160)
        if not ident:
            continue
        for candidate in (_location_tokens(ident), _location_tokens(location.get("name"))):
            if not candidate:
                continue
            candidates.append((ident, candidate))
            for index in _token_phrase_indexes(opening_tokens, candidate):
                preposition = opening_tokens[index - 1] if index else ""
                if preposition in {"the", "a", "an"} and index >= 2:
                    preposition = opening_tokens[index - 2]
                if preposition in direct:
                    direct[preposition].add(ident)
    # ``aboard`` is particularly clear for a vehicle opening; lower-precedence
    # prepositions are only accepted if exactly one declared location matches.
    for preposition in ("aboard", "on", "at", "in"):
        if len(direct[preposition]) == 1:
            return next(iter(direct[preposition])), False
        if len(direct[preposition]) > 1:
            return None, True

    matches: set[str] = set()
    for ident, candidate in candidates:
        if not candidate:
            continue
        # Exact label/id is authoritative.  A phrase embedded in prose must
        # contain enough specificity to make the repair mechanical.
        if candidate == opening_tokens or (
            len(candidate) >= 2 and _contains_token_phrase(opening_tokens, candidate)
        ):
            matches.add(ident)
    if len(matches) == 1:
        return next(iter(matches)), False
    return None, len(matches) > 1


def _reset_clearly_returns_to_opening(value: Any) -> bool:
    """Recognize only explicit return-to-opening reset language.

    Mentioning a ferry somewhere in a reset explanation is not enough: it may
    simply describe the world that resets.  These narrow patterns intentionally
    prefer an author question over a false claim about the runtime restart.
    """
    text = _text(value, limit=4000).lower()
    if not text:
        return False
    has_reset = bool(re.search(r"\b(reset(?:s|ting)?|restart(?:s|ing)?|return(?:s|ed|ing)?|wake(?:s|n|ning)?)\b", text))
    if not has_reset:
        return False
    if re.search(r"\b(opening|first\s+scene|beginning|start\s+of\s+(?:the\s+)?day)\b", text):
        return True
    # Require a directional or repetition verb close to ferry.  This accepts
    # "back to the ferry" but rejects "the world and ferry revert".
    return bool(re.search(
        r"\b(?:back\s+to|return(?:s|ed|ing)?\s+to|restart(?:s|ing)?\s+(?:on|at)|wake(?:s|n|ning)?\s+(?:on|at))\s+(?:the\s+)?ferry\b",
        text,
    ))


def _architect_confirmed_opening_restart(card: Mapping[str, Any]) -> bool:
    """Recognize a narrow, explicit answer to the active loop-restart prompt.

    Architect history is author-only operational state.  It is useful evidence
    for a deterministic migration, but it must not become free-form generic
    model context.  We therefore accept only an ``author`` message containing
    a direct opening/first-scene restart statement *while the latest Architect
    question/action is the loop-restart diagnostic*.
    """
    fields = _mapping(card.get("fields"))
    state = _mapping(fields.get("architect_state"))
    if not state:
        return False
    pending = _mapping(state.get("pending_question"))
    latest_question_id = _text(pending.get("id"), limit=240)
    if not latest_question_id:
        actions = state.get("actions")
        if isinstance(actions, list):
            for raw_action in reversed(actions):
                action = _mapping(raw_action)
                candidate = _text(action.get("question_id"), limit=240)
                if candidate:
                    latest_question_id = candidate
                    break
    # New Architect conversations use a plan-level prompt rather than leaking
    # the compiler's ``readiness-loop-restart-*`` diagnostic.  Both ids name
    # the same narrow author decision, so the deterministic normalizer may
    # trust the explicit answer in either case.
    if not (
        latest_question_id.startswith("readiness-loop-restart-")
        or latest_question_id == "authoring-plan-loop"
    ):
        return False

    direct_answer = re.compile(
        r"\b(?:it|the\s+loop|the\s+story|we|i)\s+"
        r"(?:start(?:s|ing)?|return(?:s|ed|ing)?|restart(?:s|ing)?|reset(?:s|ting)?|go(?:es|ing)?\s+back)"
        r"(?:\s+(?:at|to|on|back\s+to))?\s+(?:the\s+)?"
        r"(?:opening|first\s+scene(?:\s+of\s+the\s+day)?|start\s+of\s+the\s+day)\b",
        re.IGNORECASE,
    )
    history = state.get("history")
    if not isinstance(history, list):
        return False
    return any(
        _text(_mapping(item).get("role"), limit=30) == "author"
        and bool(direct_answer.search(_text(_mapping(item).get("text"), limit=1600)))
        for item in history
        if isinstance(item, Mapping)
    )


def _event_has_private_gate(event: Mapping[str, Any]) -> bool:
    if bool(event.get("entity_action")):
        return True
    if any(_has_value(event.get(name)) for name in ("hidden", "trigger", "evidence", "entity_period")):
        return True
    return _has_value(_mapping(event.get("knowledge")))


def _scene_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _event_public_text(event: Mapping[str, Any]) -> str:
    for name in ("visible", "event", "title"):
        text = _text(event.get(name), limit=4000)
        if text:
            return text
    return ""


def _event_location_matches(event: Mapping[str, Any], card: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Resolve an existing public event phrase to one declared location only.

    Full name/id matches win.  A unique distinctive single word (``ferry`` or
    ``dock``) can also be enough, because older cards frequently use those
    concise public scene titles.  Generic words such as ``home`` or ``island``
    cannot choose a location by themselves.
    """
    text_tokens = _location_tokens(_event_public_text(event))
    if not text_tokens:
        return None, False
    full_matches: set[str] = set()
    word_matches: dict[str, set[str]] = {}
    generic = {"a", "an", "and", "at", "chamber", "home", "house", "in", "island", "of", "on", "room", "the", "to", "town", "village"}
    for raw_location in card.get("locations") or []:
        location = _mapping(raw_location)
        ident = _text(location.get("id"), limit=160)
        if not ident:
            continue
        aliases = (_location_tokens(ident), _location_tokens(location.get("name")))
        for alias in aliases:
            if len(alias) >= 2 and _contains_token_phrase(text_tokens, alias):
                full_matches.add(ident)
            # A location's final noun gives a conservative shorthand: ferry
            # -> electric-ferry and dock -> island-dock.  Do *not* use an
            # arbitrary first token such as a character's name in Shuri's
            # Home; that would confuse a person mention with a location.
            word = alias[-1] if alias else ""
            if len(word) >= 4 and word not in generic and word in text_tokens:
                word_matches.setdefault(word, set()).add(ident)
    if len(full_matches) == 1:
        return next(iter(full_matches)), False
    if len(full_matches) > 1:
        return None, True
    candidates = set().union(*word_matches.values()) if word_matches else set()
    if len(candidates) == 1:
        return next(iter(candidates)), False
    return None, len(candidates) > 1


def _time_slots(card: Mapping[str, Any]) -> tuple[str, ...]:
    configured = tuple(_strings(_mapping(card.get("time_system")).get("slots")))
    return configured or ("morning", "evening", "night")


def _event_opening_slot(event: Mapping[str, Any], card: Mapping[str, Any]) -> str:
    raw: Any = event.get("slots") if "slots" in event else event.get("when", event.get("time"))
    values = _strings(raw)
    if isinstance(raw, str) and "," in raw:
        values = [_text(item) for item in raw.split(",") if _text(item)]
    if not values:
        return ""
    slots = _time_slots(card)
    by_lower = {slot.lower(): slot for slot in slots}
    aliases = {
        "dawn": "morning", "day": "morning", "daytime": "morning", "midday": "morning",
        "noon": "morning", "afternoon": "morning", "sunset": "evening", "dusk": "evening",
        "late evening": "evening", "late-night": "night", "late night": "night", "midnight": "night",
    }
    normalized = [by_lower.get(value.lower(), by_lower.get(aliases.get(value.lower(), ""), "")) for value in values]
    return normalized[0] if normalized and all(normalized) else ""


def _restart_is_existing_shortcut(value: Any, card: Mapping[str, Any]) -> bool:
    """Whether a restart value is already a supported literal/location form."""
    if isinstance(value, Mapping):
        # A custom object may be incomplete, but replacing it from a terse
        # history line would be a destructive guess.  Let compiler diagnostics
        # and the author resolve it.
        return True
    restart = _text(value, limit=4000)
    if not restart:
        return False
    if restart.lower() == "opening":
        return True
    aliases = {
        _text(location.get(field), limit=240).lower()
        for raw_location in card.get("locations") or []
        for location in [_mapping(raw_location)]
        for field in ("id", "name")
        if _text(location.get(field), limit=240)
    }
    return restart.lower() in aliases


def _record(section: str, ident: str, message: str) -> dict[str, str]:
    # Fixed operational messages intentionally do not echo the author/director
    # prose that triggered the classification.
    return {"section": section, "id": ident, "message": message}


def normalize_mechanical_setup(card: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Apply only provably mechanical completion repairs to a Story card.

    This is deliberately narrower than a model development pass.  It never
    invents a map location, a scene, a time window, a character presence, or a
    secret.  Every attempted repair is either:

    * an unambiguous reference from existing prose to an existing public
      location;
    * an explicit reset-to-opening statement mapped to the runtime's literal
      ``"opening"`` convention; or
    * removal of the known-invalid ``entity`` runtime state token when the
      canonical entity already lives in ``world.entity``.

    It returns a deep-copied card, compact adjustment records, and unresolved
    records for cases that should become Architect questions rather than model
    guesses.  The function performs no I/O and makes no model call.
    """
    source = deepcopy(_mapping(card))
    adjustments: list[dict] = []
    unresolved: list[dict] = []

    world = _mapping(source.get("world"))
    loop = _mapping(world.get("loop"))

    # 1. Story.start is a runtime-facing map reference.  Recover it only from
    # a unique declared location clearly named in the prose loop opening.
    if not _text(source.get("start")) and _text(loop.get("start"), limit=4000):
        location_id, ambiguous = _unambiguous_start_location(source)
        if location_id:
            source["start"] = location_id
            adjustments.append(_record(
                "world", "normalize-start-location",
                "Set the runtime opening location from the unambiguous declared loop-start location.",
            ))
        else:
            unresolved.append(_record(
                "world",
                "opening-location-ambiguous" if ambiguous else "opening-location-unresolved",
                "The loop opening does not identify one declared location clearly enough to set story.start automatically.",
            ))

    # 2. A loop reset must say where the runtime resumes.  Accept only
    # explicit opening/first-scene/ferry-return language; ordinary reset prose
    # remains an authorial decision.  An explicit answer in the active
    # Architect loop-restart conversation is equally authoritative evidence,
    # and can replace the common bad-model output that copied reset prose into
    # the typed ``policy.restart`` field.
    if loop:
        raw_policy = loop.get("policy")
        policy = _mapping(raw_policy)
        restart_value = policy.get("restart")
        restart = _text(restart_value)
        reset_says_opening = _reset_clearly_returns_to_opening(loop.get("reset"))
        author_says_opening = _architect_confirmed_opening_restart(source)
        can_replace_restart = not _restart_is_existing_shortcut(restart_value, source)
        if (reset_says_opening or author_says_opening) and can_replace_restart:
            # Do not overwrite a malformed scalar ``policy`` object itself;
            # its shape is not a mechanical fact.  A missing policy is fine to
            # create because it is the documented canonical container.
            if raw_policy is None or isinstance(raw_policy, Mapping):
                policy["restart"] = "opening"
                loop["policy"] = policy
                world["loop"] = loop
                source["world"] = world
                adjustments.append(_record(
                    "world",
                    "normalize-loop-restart-from-author-answer" if author_says_opening and not reset_says_opening
                    else "normalize-loop-restart",
                    "Set the executable loop restart to the explicitly stated opening.",
                ))
            else:
                unresolved.append(_record(
                    "world", "loop-policy-shape-unresolved",
                    "The loop policy is not in a shape that can be mechanically repaired.",
                ))
        elif (not restart or not _restart_is_existing_shortcut(restart_value, source)) and _text(loop.get("reset"), limit=4000):
            unresolved.append(_record(
                "world", "loop-restart-unresolved",
                "The reset prose does not state an unambiguous runtime restart point.",
            ))

    # 3. ``entity`` is story canon, not a mutable runtime-state field.  Remove
    # only that exact token, preserve the order/content of every other token,
    # and only when a canonical entity is actually present.
    world = _mapping(source.get("world"))
    loop = _mapping(world.get("loop"))
    policy = _mapping(loop.get("policy"))
    preserve = _mapping(policy.get("preserve"))
    runtime = preserve.get("runtime")
    if isinstance(runtime, list) and any(_text(item).lower() == "entity" for item in runtime):
        if _has_value(world.get("entity")):
            cleaned = [deepcopy(item) for item in runtime if _text(item).lower() != "entity"]
            preserve["runtime"] = cleaned
            policy["preserve"] = preserve
            loop["policy"] = policy
            world["loop"] = loop
            source["world"] = world
            adjustments.append(_record(
                "world", "normalize-loop-runtime-entity",
                "Removed the non-runtime entity token while preserving the canonical world entity.",
            ))
        else:
            unresolved.append(_record(
                "world", "loop-runtime-entity-unresolved",
                "The loop names entity runtime persistence, but no canonical world entity makes that repair safe.",
            ))
    elif runtime is not None and not isinstance(runtime, list):
        unresolved.append(_record(
            "world", "loop-runtime-shape-unresolved",
            "The loop runtime-preservation list is not in a shape that can be mechanically repaired.",
        ))

    # 4. Normalize legacy public Day One records without changing their
    # narrative content.  This is intentionally not a scene generator: it can
    # give an existing public event the same stable id the compiler derives,
    # infer the opening time from the first valid event, and assign only a
    # uniquely named declared location.
    fields = _mapping(source.get("fields"))
    plan = _mapping(fields.get("first_day_plan"))
    events = plan.get("events")
    if isinstance(events, list):
        used_ids: set[str] = set()
        duplicate_existing_ids = False
        for raw_event in events:
            event = _mapping(raw_event)
            ident = _text(event.get("id"), limit=4000)
            if not ident:
                continue
            if ident in used_ids:
                duplicate_existing_ids = True
            used_ids.add(ident)

        location_needs_author = False
        private_event_skipped = False
        public_event_without_title = False
        for index, raw_event in enumerate(events):
            if not isinstance(raw_event, Mapping):
                continue
            event = dict(raw_event)
            private = _event_has_private_gate(event)
            ident = _text(event.get("id"), limit=4000)
            if not ident:
                public_text = _event_public_text(event)
                if private:
                    private_event_skipped = True
                elif public_text:
                    base = _scene_slug(public_text)
                    if base:
                        candidate = base
                        suffix = 2
                        while candidate in used_ids:
                            candidate = f"{base}-{suffix}"
                            suffix += 1
                        event["id"] = candidate
                        used_ids.add(candidate)
                        events[index] = event
                        adjustments.append(_record(
                            "first_day", f"normalize-day-one-scene-id-{index + 1}",
                            "Assigned a deterministic stable id to an existing public Day One event.",
                        ))
                    else:
                        public_event_without_title = True
                else:
                    public_event_without_title = True

            if not _has_value(event.get("location")):
                if private:
                    private_event_skipped = True
                else:
                    location_id, ambiguous = _event_location_matches(event, source)
                    if location_id:
                        event["location"] = location_id
                        events[index] = event
                        adjustments.append(_record(
                            "first_day", f"normalize-day-one-location-{index + 1}",
                            "Assigned a uniquely named declared location to an existing public Day One event.",
                        ))
                    elif ambiguous or _event_public_text(event):
                        location_needs_author = True

        first_event = _mapping(events[0]) if events else {}
        opening_is_set = bool(_text(fields.get("opening_time")) or _text(plan.get("opening_time")))
        if not opening_is_set:
            if first_event and not _event_has_private_gate(first_event):
                opening_slot = _event_opening_slot(first_event, source)
                if opening_slot:
                    plan["opening_time"] = opening_slot
                    adjustments.append(_record(
                        "first_day", "normalize-opening-time",
                        "Set the opening time from the first existing public Day One event.",
                    ))
                else:
                    unresolved.append(_record(
                        "first_day", "opening-time-unresolved",
                        "The first public Day One event does not provide one valid opening time slot.",
                    ))
            elif first_event:
                private_event_skipped = True

        # The first public event is the compiler's existing opening fallback.
        # When it now names one declared map location, promote that exact
        # reference into the explicit opening/start fields.  This resolves a
        # loop-start prose sentence that mentions both ferry and dock without
        # guessing between them: the already-ordered public opening event is
        # the authoritative tie-breaker.  A gated or location-less first event
        # remains untouched.
        location_ids = {
            _text(_mapping(location).get("id"), limit=240)
            for location in source.get("locations") or []
            if _text(_mapping(location).get("id"), limit=240)
        }
        first_location = _text(first_event.get("location"), limit=240)
        opening_location_is_set = bool(
            _text(fields.get("opening_location"), limit=240)
            or _text(plan.get("opening_location"), limit=240)
        )
        if (not opening_location_is_set and first_location in location_ids
                and not _event_has_private_gate(first_event)):
            plan["opening_location"] = first_location
            adjustments.append(_record(
                "first_day", "normalize-opening-location",
                "Set the opening location from the first existing public Day One event.",
            ))
        explicit_opening_location = _text(
            fields.get("opening_location") or plan.get("opening_location"), limit=240,
        )
        if not _text(source.get("start"), limit=240) and explicit_opening_location in location_ids:
            source["start"] = explicit_opening_location
            adjustments.append(_record(
                "world", "normalize-start-from-opening-event",
                "Set the runtime start from the explicit public opening event location.",
            ))
            # A loop prose mention may have been ambiguous before the public
            # opening event supplied this deterministic reference.  It is no
            # longer an unresolved runtime-start decision.
            unresolved = [item for item in unresolved
                          if item.get("id") not in {"opening-location-ambiguous", "opening-location-unresolved"}]

        if duplicate_existing_ids:
            unresolved.append(_record(
                "first_day", "day-one-duplicate-id-unresolved",
                "Existing duplicate Day One ids need an author decision before any reference can be repaired.",
            ))
        if public_event_without_title:
            unresolved.append(_record(
                "first_day", "day-one-id-unresolved",
                "At least one public Day One event has no existing visible title from which to derive a stable id.",
            ))
        if location_needs_author:
            unresolved.append(_record(
                "first_day", "day-one-location-unresolved",
                "At least one public Day One event does not name one declared location clearly enough to assign it automatically.",
            ))
        if private_event_skipped:
            unresolved.append(_record(
                "first_day", "day-one-private-mechanics-preserved",
                "A Day One event has protected mechanics, so its id, location, and timing were left for an author decision.",
            ))
        fields["first_day_plan"] = plan
        source["fields"] = fields

    return source, adjustments, unresolved


def _paths(gap: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(_text(value, limit=300) for value in (gap.get("paths") or []) if _text(value, limit=300))


def _readiness_reason(gap: Mapping[str, Any]) -> str:
    """Classify a compiler/readiness issue without trusting descriptive prose."""
    ident = _text(gap.get("id"), limit=240)
    paths = _paths(gap)
    lower_paths = tuple(path.lower() for path in paths)
    # A loop reset is narrative/gameplay law, not a mechanical blank to fill.
    if any(path.startswith("world.loop") or path.startswith("fields.loop_policy") for path in lower_paths):
        return "loop_or_reset_rule"
    if any(path.startswith("time_system") for path in lower_paths):
        return "time_or_entity_rule"
    if "loop" in ident or "entity" in ident or "time" in ident or "period" in ident:
        return "time_or_entity_rule"
    # The remaining compiler diagnostics describe structural normalization
    # (stable ids, declared locations, missing public opening data).  The
    # completion route may repair them only through a deterministic normalizer
    # or an additive public scaffold, never by rewriting private event facts.
    return "structural_normalization"


def _hinge_reason(
    gap: Mapping[str, Any],
    *,
    day_one_is_gated: bool,
    card_has_cast: bool,
) -> str | None:
    """Return a fixed reason when a gap needs the author, otherwise ``None``.

    Keep this intentionally conservative.  An unknown diagnostic is not
    granted generic model authority merely because it has a familiar section
    label.
    """
    ident = _text(gap.get("id"), limit=240)
    section = _text(gap.get("section"), limit=80)
    if section in _AUTHORIAL_SECTIONS:
        return "character_or_theme_truth" if section == "arcs" else "time_or_entity_rule"
    # Reworking an existing Day One outline into a player-enterable encounter
    # changes the scene's public surface.  It is not an additive completion
    # task, even when the event has no private mechanics; send it through the
    # focused first-day editor so the author can approve the real moment.
    if ident.startswith("scene-offer-"):
        return "scene_offer_quality"
    if ident.startswith(_AUTHORIAL_PREFIXES):
        if ident.startswith(("knowledge-", "evidence-")):
            return "private_story_logic"
        if "relationship" in ident:
            return "relationship_truth"
        return "character_or_theme_truth"
    # A structural warning inside a Day One plan is normally normalizable, but
    # not once this card already holds a hidden/knowledge/evidence gate.  A
    # seemingly harmless id or location repair can then affect a protected
    # trigger reference, so the whole Day One batch pauses for the author.
    if section == "first_day" and day_one_is_gated:
        return "private_story_logic"
    # A scene's location or on-stage cast is not merely a missing container
    # field.  It determines who can witness an event, what a character is
    # allowed to know, and (in a mystery) which alibis remain possible.  The
    # Architect can safely *normalize* an existing unambiguous placement, but
    # it must not ask a generic model to choose one from scratch.  Treat an
    # existing cast member's missing entrance the same way: the right answer
    # may be deliberately "not on Day One", rather than an additive gap.
    #
    # A completely empty cast remains an allowed public scaffold task.  That
    # is the narrow case where an autonomous baseline can introduce a simple
    # supporting roster before the author decides individual scene presence.
    lower_paths = tuple(path.lower() for path in _paths(gap))
    if section == "first_day":
        is_scene_placement = (
            any("opening_present" in path for path in lower_paths)
            or any(".location" in path for path in lower_paths)
            or (ident == "cast-unintroduced" and card_has_cast)
        )
        if is_scene_placement:
            return "scene_placement"
    if ident.startswith("readiness-"):
        reason = _readiness_reason(gap)
        return None if reason == "structural_normalization" else reason
    if section in _SAFE_SCOPES:
        return None
    return "unknown_or_private"


def _hinge(gap: Mapping[str, Any], *, reason: str, blocked: bool = False) -> dict[str, Any]:
    """Return a minimal safe public representation of a pending decision."""
    return {
        "id": _text(gap.get("id"), limit=240) or "unidentified-gap",
        "section": _text(gap.get("section"), limit=80) or "world",
        "reason": reason,
        "blocked": bool(blocked),
    }


def build_completion_plan(
    card: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    *,
    blocked_gap_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Choose one bounded batch of scaffold work plus the remaining hinges.

    ``safe_scopes`` is an ordered, finite work list—not a request to recursively
    invoke subagents.  A caller may execute at most one bounded authoring pass
    for the returned scopes, then call this function again against the updated
    card.  Any authorial hinge is returned at once, but it is only *asked* once
    the safe baseline is exhausted.

    ``blocked_gap_ids`` lets the coordinator stop retrying a no-op or failed
    scaffold task.  A blocked item moves to ``authorial_hinges`` rather than
    being silently retried, which keeps completion progress finite and honest.
    """
    source = _mapping(card)
    observed = _mapping(report)
    blocked = {_text(item, limit=240) for item in blocked_gap_ids if _text(item, limit=240)}
    raw_gaps = observed.get("gaps")
    gaps = [
        _mapping(item) for item in raw_gaps
        if isinstance(item, Mapping) and _text(item.get("id"), limit=240)
    ] if isinstance(raw_gaps, list) else []

    # A blank card has no legitimate material for a generic model to expand.
    # The only first task is a high-level spark from the author.
    if not _has_story_seed(source):
        return {
            "phase": "author_decision",
            "safe_scopes": [],
            "safe_gap_ids": [],
            "authorial_hinges": [{
                "id": "starting-spark",
                "section": "world",
                "reason": "starting_spark",
                "blocked": False,
            }],
            "summary": {
                "safe_task_count": 0,
                "authorial_hinge_count": 1,
                "blocked_task_count": 0,
            },
        }

    day_one_is_gated = _has_gated_day_one(source)
    safe_gap_ids: list[str] = []
    safe_sections: set[str] = set()
    hinges: list[dict[str, Any]] = []
    seen_hinges: set[str] = set()
    blocked_count = 0

    for gap in gaps:
        ident = _text(gap.get("id"), limit=240)
        section = _text(gap.get("section"), limit=80)
        if ident in blocked:
            blocked_count += 1
            hinge = _hinge(gap, reason="blocked_scaffold", blocked=True)
            if hinge["id"] not in seen_hinges:
                seen_hinges.add(hinge["id"])
                hinges.append(hinge)
            continue

        reason = _hinge_reason(
            gap,
            day_one_is_gated=day_one_is_gated,
            card_has_cast=bool(source.get("cast")),
        )
        if reason:
            hinge = _hinge(gap, reason=reason)
            if hinge["id"] not in seen_hinges:
                seen_hinges.add(hinge["id"])
                hinges.append(hinge)
            continue

        # A non-gated structural readiness diagnostic is safe to normalize in
        # the scope it names.  All other safe tasks must stay within the
        # public/additive cardinality of the listed scope.
        if section not in _SAFE_SCOPES:
            hinge = _hinge(gap, reason="unknown_or_private")
            if hinge["id"] not in seen_hinges:
                seen_hinges.add(hinge["id"])
                hinges.append(hinge)
            continue
        safe_gap_ids.append(ident)
        safe_sections.add(section)

    safe_scopes = [section for section in _SAFE_SCOPE_ORDER if section in safe_sections]
    population_task: dict[str, Any] | None = None
    protected_tasks: list[dict[str, Any]] = []
    # Once the ordinary public scaffold is complete, a story with a central
    # person can gain at most two location-rooted supporting roles.  This is a
    # distinct, sanitizer-backed task rather than permission for the generic
    # cast writer to invent a social graph.  It intentionally runs after all
    # ordinary safe gaps, one bounded pass at a time.
    if not safe_scopes and "public-supporting-cast" not in blocked:
        population_task = build_public_baseline_population_task(source, observed)
        if population_task:
            safe_scopes = ["cast"]
            safe_gap_ids = ["public-supporting-cast"]
            # ``cast-unintroduced`` is the reason this public task exists;
            # keep unrelated authorial hinges visible, but do not display the
            # same decision as both an automatic pass and a stop condition.
            hinges = [hinge for hinge in hinges if hinge["id"] != "cast-unintroduced"]
            seen_hinges.discard("cast-unintroduced")
    # A private knowledge boundary is eligible only after the public baseline
    # is exhausted.  It remains a distinct, one-scene Director task—not a
    # broadened permission for the generic completion writer.
    if not safe_scopes:
        knowledge_task = build_director_knowledge_task(source, observed, blocked_gap_ids=blocked)
        if knowledge_task:
            protected_tasks.append(knowledge_task)
            hinges = [hinge for hinge in hinges if hinge["id"] != knowledge_task["gap_id"]]
            seen_hinges.discard(knowledge_task["gap_id"])
    phase = (
        "safe_baseline" if safe_scopes
        else "protected_baseline" if protected_tasks
        else "author_decision" if hinges
        else "ready"
    )
    plan = {
        "phase": phase,
        "safe_scopes": safe_scopes,
        "safe_gap_ids": safe_gap_ids,
        "authorial_hinges": hinges,
        "summary": {
            "safe_task_count": len(safe_gap_ids),
            "authorial_hinge_count": len(hinges),
            "blocked_task_count": blocked_count,
        },
    }
    if population_task:
        plan["public_population_task"] = population_task
    if protected_tasks:
        plan["protected_tasks"] = protected_tasks
    return plan
