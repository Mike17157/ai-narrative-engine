"""Bounded, deterministic routing for the primary Story Architect surface.

The Architect is deliberately not an autonomous background writer.  It observes
the current card, chooses one author decision worth asking for, and may make at
most one additive co-authoring pass in response to a high-level author message.
That keeps the useful *agentic* part (routing and follow-through) without
letting a model silently iterate until it has invented a different story.

This module is model-free.  It never sees hidden event text or private arc
answers; those remain behind the API boundary that decides whether a turn may
use the generic co-author or the protected focused interviewer.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping

from .arc_design import protagonist_arc
from .completion_planner import build_completion_plan
from .gap_analysis import analyze_story_card
from ..visibility import strip_model_hidden


STATE_VERSION = 2
MAX_HISTORY = 16
MAX_ACTIONS = 16
_SAFE_SECTIONS = frozenset({"world", "premise", "cast", "first_day", "time_system"})
_STARTER_SCOPES = ("world", "premise", "cast", "first_day", "time_system")
_PLAN_PHASES = frozenset({"safe_baseline", "protected_baseline", "author_decision", "ready"})
_PROTECTED_PLAN_TASK_KINDS = frozenset({"director_knowledge_boundary"})
# A protected worker may act only after the Architect has committed the
# corresponding author-level plan to canonical card state.  Keep this small
# whitelist separate from worker task ids: task ids are re-derived from the
# live card immediately before execution.
_WORKER_AUTHORIZATION_PLAN_IDS = frozenset({"authoring-plan-investigation"})
_AUTHOR_PLAN_SECTION_IDS = (
    "opening",
    "cast",
    "scene_flow",
    "mystery_boundary",
    "arc_theme",
    "build_order",
)


def _text(value: Any, *, limit: int = 1600) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _list_of_dicts(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[-limit:] if isinstance(item, Mapping)]


def _completion_plan(value: Any) -> dict[str, Any]:
    """Keep the persisted plan compact, typed, and free of card prose.

    The UI can render this author-only operational snapshot after a reload.  It
    deliberately stores ids/reasons/counts only; exposing a gap's prose here
    would make the plan another accidental channel for private card material.
    """
    raw = dict(value) if isinstance(value, Mapping) else {}
    phase = _text(raw.get("phase"), limit=40)
    if phase not in _PLAN_PHASES:
        return {}
    scopes = [str(item) for item in (raw.get("safe_scopes") or [])
              if str(item) in _SAFE_SECTIONS]
    gap_ids: list[str] = []
    for item in raw.get("safe_gap_ids") or []:
        ident = _text(item, limit=160)
        if ident and ident not in gap_ids:
            gap_ids.append(ident)
    hinges: list[dict[str, Any]] = []
    for raw_hinge in raw.get("authorial_hinges") or []:
        hinge = dict(raw_hinge) if isinstance(raw_hinge, Mapping) else {}
        ident = _text(hinge.get("id"), limit=160)
        section = _text(hinge.get("section"), limit=40)
        reason = _text(hinge.get("reason"), limit=80)
        if ident and section in _SAFE_SECTIONS | {"arcs"} and reason:
            hinges.append({"id": ident, "section": section, "reason": reason,
                           "blocked": bool(hinge.get("blocked"))})
        if len(hinges) >= MAX_ACTIONS:
            break
    raw_summary = dict(raw.get("summary")) if isinstance(raw.get("summary"), Mapping) else {}

    protected_tasks: list[dict[str, Any]] = []
    for raw_task in raw.get("protected_tasks") or []:
        task = dict(raw_task) if isinstance(raw_task, Mapping) else {}
        kind = _text(task.get("kind"), limit=80)
        ident = _text(task.get("id"), limit=160)
        section = _text(task.get("section"), limit=40)
        gap_id = _text(task.get("gap_id"), limit=160)
        event_id = _text(task.get("event_id"), limit=160)
        if (kind not in _PROTECTED_PLAN_TASK_KINDS or not ident or section != "first_day"
                or not gap_id or not event_id):
            continue
        observers: list[str] = []
        for raw_observer in task.get("observer_ids") or []:
            observer = _text(raw_observer, limit=160)
            if observer and observer not in observers:
                observers.append(observer)
        if not observers:
            continue
        protected_tasks.append({
            "id": ident,
            "kind": kind,
            "section": section,
            "gap_id": gap_id,
            "event_id": event_id,
            "observer_ids": observers[:8],
            "constraints": {
                "existing_event_only": True,
                "existing_observers_only": True,
                "fill_blank_knowledge_only": True,
            },
        })
        if len(protected_tasks) >= 1:
            break

    public_population_task: dict[str, Any] | None = None
    raw_population = raw.get("public_population_task")
    population = dict(raw_population) if isinstance(raw_population, Mapping) else {}
    if population.get("kind") == "public_supporting_cast" and _text(population.get("scope"), limit=40) == "cast":
        archetypes: list[dict[str, str]] = []
        for raw_archetype in population.get("archetypes") or []:
            archetype = dict(raw_archetype) if isinstance(raw_archetype, Mapping) else {}
            ident = _text(archetype.get("id"), limit=160)
            role = _text(archetype.get("role"), limit=120)
            location_id = _text(archetype.get("location_id"), limit=160)
            if ident and role and location_id:
                archetypes.append({"id": ident, "role": role, "location_id": location_id})
            if len(archetypes) >= 2:
                break
        if archetypes:
            public_population_task = {
                "kind": "public_supporting_cast",
                "scope": "cast",
                "maximum_characters": len(archetypes),
                "archetypes": archetypes,
                "constraints": {
                    "public_only": True,
                    "allow_relationships": False,
                    "allow_private_fields": False,
                    "allow_primary": False,
                },
            }

    def bounded_count(value: Any) -> int:
        try:
            return max(0, min(MAX_ACTIONS, int(value or 0)))
        except (TypeError, ValueError):
            return 0

    summary = {
        "safe_task_count": bounded_count(raw_summary.get("safe_task_count")),
        "authorial_hinge_count": bounded_count(raw_summary.get("authorial_hinge_count")),
        "blocked_task_count": bounded_count(raw_summary.get("blocked_task_count")),
    }
    result = {
        "phase": phase,
        "safe_scopes": list(dict.fromkeys(scopes)),
        "safe_gap_ids": gap_ids[:MAX_ACTIONS],
        "authorial_hinges": hinges,
        "summary": summary,
    }
    if protected_tasks:
        result["protected_tasks"] = protected_tasks
    if public_population_task:
        result["public_population_task"] = public_population_task
    return result


def normalize_architect_state(value: Any) -> dict[str, Any]:
    """Normalize persisted Architect working state without trusting its shape.

    The state is author-only operational context, not Story canon.  Keeping it
    small and typed lets a client resume the same conversation after a reload
    while preventing malformed/stale state from choosing arbitrary edit paths.
    """
    raw = dict(value) if isinstance(value, Mapping) else {}
    pending = raw.get("pending_question")
    if not isinstance(pending, Mapping):
        pending = None
    else:
        pending = {
            "id": _text(pending.get("id"), limit=160),
            "section": _text(pending.get("section"), limit=40),
            "mode": _text(pending.get("mode"), limit=40),
            "text": _text(pending.get("text"), limit=2000),
            "target": deepcopy(pending.get("target")) if isinstance(pending.get("target"), Mapping) else {},
            "scopes": [str(item) for item in (pending.get("scopes") or [])
                       if str(item) in _SAFE_SECTIONS][: len(_SAFE_SECTIONS)],
        }
        if not pending["id"] or pending["section"] not in _SAFE_SECTIONS | {"arcs"}:
            pending = None
    history: list[dict[str, str]] = []
    for item in _list_of_dicts(raw.get("history"), limit=MAX_HISTORY):
        role = _text(item.get("role"), limit=20)
        text = _text(item.get("text"), limit=1600)
        if role in {"author", "architect"} and text:
            history.append({"role": role, "text": text})
    actions: list[dict[str, Any]] = []
    for item in _list_of_dicts(raw.get("actions"), limit=MAX_ACTIONS):
        kind = _text(item.get("kind"), limit=40)
        if kind not in {"develop", "interview", "knowledge", "assess", "reconcile", "normalize"}:
            continue
        action = {"kind": kind}
        section = _text(item.get("section"), limit=40)
        if section in _SAFE_SECTIONS | {"arcs"}:
            action["section"] = section
        sections = [str(s) for s in (item.get("sections") or []) if str(s) in _SAFE_SECTIONS]
        if sections:
            action["sections"] = sections[: len(_SAFE_SECTIONS)]
        updated = [str(s) for s in (item.get("updated_sections") or []) if str(s)]
        if updated:
            action["updated_sections"] = updated[:8]
        question_id = _text(item.get("question_id"), limit=160)
        if question_id:
            action["question_id"] = question_id
        outcome = _text(item.get("outcome"), limit=24)
        if outcome in {"changed", "no_change", "note", "waiting"}:
            action["outcome"] = outcome
        actions.append(action)
    blocked_gap_ids: list[str] = []
    for item in raw.get("blocked_gap_ids") or []:
        ident = _text(item, limit=160)
        if ident and ident not in blocked_gap_ids:
            blocked_gap_ids.append(ident)
        if len(blocked_gap_ids) >= MAX_ACTIONS:
            break
    worker_authorization: dict[str, str] | None = None
    raw_authorization = raw.get("worker_authorization")
    if isinstance(raw_authorization, Mapping):
        plan_id = _text(raw_authorization.get("plan_id"), limit=160)
        revision = _text(raw_authorization.get("card_revision"), limit=80)
        if plan_id in _WORKER_AUTHORIZATION_PLAN_IDS and revision:
            # Deliberately project only this capability token.  In particular,
            # no task id, observer id, gap id, or author prose may survive in
            # operational state where it could be replayed against a later card.
            worker_authorization = {"plan_id": plan_id, "card_revision": revision}
    result = {
        "version": STATE_VERSION,
        "mission": _text(raw.get("mission"), limit=4000),
        "card_revision": _text(raw.get("card_revision"), limit=80),
        "pending_question": pending,
        "history": history[-MAX_HISTORY:],
        "actions": actions[-MAX_ACTIONS:],
        # A blocked gap is not an error and not canon.  It means a bounded,
        # additive pass found that this particular structural omission can only
        # be resolved by revising an established fact.  Keep it out of later
        # automatic passes until the card itself changes or the author chooses
        # that revision explicitly.
        "blocked_gap_ids": blocked_gap_ids,
        "plan": _completion_plan(raw.get("plan")),
    }
    if worker_authorization:
        result["worker_authorization"] = worker_authorization
    return result


def card_revision(card: Mapping[str, Any]) -> str:
    """Return a stable, author-private fingerprint of canonical card state.

    The Architect state itself is excluded so recording a question does not
    invalidate that question.  Any independent Story-card edit changes the
    value and makes the pending target stale before it can steer a model pass.
    The digest is operational metadata only; it is never part of public/model
    card projections.
    """
    source = deepcopy(dict(card))
    fields = source.get("fields")
    if isinstance(fields, dict):
        fields.pop("architect_state", None)
    try:
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(source)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _has_story_seed(card: Mapping[str, Any]) -> bool:
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    return bool(
        _text(card.get("premise"))
        or card.get("world")
        or card.get("locations")
        or card.get("start")
        or card.get("cast")
        or card.get("themes")
        or card.get("time_system")
        or (fields.get("first_day_plan") if isinstance(fields, Mapping) else None)
        or (fields.get("arc_design") if isinstance(fields, Mapping) else None)
    )


def _display_name(key: str, names: Mapping[str, str] | None) -> str:
    return _text((names or {}).get(key), limit=120) or key.replace("-", " ").replace("_", " ").title()


def _first_cast_name(card: Mapping[str, Any], names: Mapping[str, str] | None) -> str:
    for member in card.get("cast") or []:
        if isinstance(member, str):
            key = member
        elif isinstance(member, Mapping):
            key = _text(member.get("character") or member.get("key"), limit=120)
        else:
            key = ""
        if key and key != "player":
            return _display_name(key, names)
    return "this person"


def _author_safe_text(value: Any, *, limit: int = 240) -> str:
    """Return compact public prose for an Architect-plan presentation.

    The Architect plan is an authoring UI contract, not a new channel for
    protected card content.  Callers use this only for explicitly public
    fields (premise, visible beats, location names and theme labels), but
    stripping inline model-hidden spans here makes that boundary fail closed
    even on old or hand-authored cards.
    """
    if not isinstance(value, str):
        return ""
    value = strip_model_hidden(value) or ""
    return " ".join(value.split())[:limit].strip()


def _author_location_name(card: Mapping[str, Any], location_id: Any) -> str:
    """Resolve one public location id to its display name without prose data."""
    ident = _author_safe_text(str(location_id or ""), limit=120)
    if not ident:
        return ""
    for raw_location in card.get("locations") or []:
        location = dict(raw_location) if isinstance(raw_location, Mapping) else {}
        if _author_safe_text(str(location.get("id") or ""), limit=120) == ident:
            return _author_safe_text(location.get("name"), limit=120) or ident.replace("-", " ").replace("_", " ")
    return ident.replace("-", " ").replace("_", " ")


def _author_cast_names(card: Mapping[str, Any], names: Mapping[str, str] | None) -> list[str]:
    """Return at most a few public display names from the compact cast list."""
    result: list[str] = []
    for member in card.get("cast") or []:
        if isinstance(member, str):
            key = member
        elif isinstance(member, Mapping):
            key = _text(member.get("character") or member.get("key"), limit=120)
        else:
            key = ""
        if not key or key in {"player", "protagonist", "returner", "you"}:
            continue
        name = _display_name(key, names)
        if name and name not in result:
            result.append(name)
        if len(result) >= 4:
            break
    return result


def _author_plan_events(card: Mapping[str, Any]) -> list[dict[str, str]]:
    """Read only public Day One scene-offer coordinates for the plan surface."""
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
    events: list[dict[str, str]] = []
    for raw_event in plan.get("events") or []:
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        visible = _author_safe_text(event.get("visible"), limit=180)
        hook = _author_safe_text(event.get("hook"), limit=160)
        theme = _author_safe_text(event.get("theme"), limit=100)
        tone = _author_safe_text(event.get("tone"), limit=100)
        when = _author_safe_text(event.get("when"), limit=80)
        location = _author_location_name(card, event.get("location"))
        if visible or hook or theme or tone or when or location:
            events.append({"visible": visible, "hook": hook, "theme": theme, "tone": tone,
                           "when": when, "location": location})
        if len(events) >= 6:
            break
    return events


def _author_has_mystery_boundary(card: Mapping[str, Any], plan: Mapping[str, Any]) -> bool:
    """Inspect only the *shape* of a protected mystery boundary.

    A plan presentation may say that an investigation boundary is present; it
    must never serialize the hidden action, evidence, trigger, observer fact,
    or entity schedule that caused that conclusion.
    """
    if plan.get("protected_tasks"):
        return True
    for hinge in plan.get("authorial_hinges") or []:
        if not isinstance(hinge, Mapping):
            continue
        reason = _text(hinge.get("reason"), limit=80)
        ident = _text(hinge.get("id"), limit=160)
        if reason == "private_story_logic" or ident.startswith(("knowledge-", "evidence-")):
            return True
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    day = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
    for raw_event in day.get("events") or []:
        if not isinstance(raw_event, Mapping):
            continue
        # Presence—not content—is the only protected detail this presentation
        # is allowed to observe.
        if bool(raw_event.get("entity_action")):
            return True
        if any(name in raw_event and bool(raw_event.get(name)) for name in (
            "hidden", "trigger", "evidence", "entity_period", "knowledge",
        )):
            return True
    return False


def _author_plan_focus(question: Mapping[str, Any]) -> str:
    """Map legacy one-question routing into a visible plan section."""
    ident = _text(question.get("id"), limit=160)
    if ident in {"starting-spark", "authoring-plan-opening"}:
        return "opening"
    if ident == "authoring-plan-relationships":
        return "cast"
    if ident in {"authoring-plan-investigation"}:
        return "mystery_boundary"
    if ident in {"authoring-plan-character-arc", "creative-next-step"}:
        return "arc_theme"
    if ident in {"authoring-plan-loop", "authoring-plan-threat-schedule", "authoring-plan-scene-offer"}:
        return "scene_flow"
    if ident == "authoring-plan-public-baseline":
        return "build_order"
    return "build_order"


def _author_plan_comment_id(section_id: str) -> str:
    return {
        "opening": "architect-plan-opening",
        "cast": "architect-plan-cast",
        "scene_flow": "architect-plan-scenes",
        "mystery_boundary": "architect-plan-mystery",
        "arc_theme": "architect-plan-arcs",
        "build_order": "architect-plan-build-order",
    }.get(section_id, "architect-plan-build-order")


def _author_plan_status(*, section_id: str, focus: str, established: bool,
                        plan: Mapping[str, Any]) -> str:
    """Return a small display state rather than an implementation diagnostic."""
    if section_id == focus:
        return "needs_direction"
    if section_id == "build_order":
        if plan.get("safe_scopes"):
            return "ready_to_build"
        if plan.get("phase") == "ready":
            return "ready"
        return "planned"
    return "established" if established else "to_shape"


def _authorial_plan_question(
    card: Mapping[str, Any],
    hinge: Mapping[str, Any],
    *,
    names: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Turn one authorial *plan* decision into a useful Architect prompt.

    Gap diagnostics are worker-facing implementation notes.  They are useful
    to the planner, but asking an author to resolve a compiler alias or an
    inferred location makes the Architect feel like a leaking task runner.
    This mapper deliberately speaks in story-level decisions instead.  The
    exact diagnostic id remains operational metadata in the completion plan;
    it is never the question the conversation asks.
    """
    reason = _text(hinge.get("reason"), limit=80)
    section = _text(hinge.get("section"), limit=40) or "world"
    ident = _text(hinge.get("id"), limit=160)
    first_name = _first_cast_name(card, names)

    if reason == "relationship_truth":
        return {
            "id": "authoring-plan-relationships",
            "section": "cast",
            "mode": "protected_interview",
            "text": ("Before I ask workers to connect the cast, what single relationship should make the "
                     "opening emotionally different from a story of strangers?"),
            "target": {"section": "cast", "plan_kind": "relationships"},
            "scopes": [],
        }
    if reason == "character_or_theme_truth":
        fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
        arc_design_raw = fields.get("arc_design") if isinstance(fields, Mapping) else None
        if protagonist_arc(arc_design_raw) is None:
            # An arc is inherently about one person, and the protagonist's own arc is the
            # one everything else relates back to — ask about them first, before any
            # supporting character, in the same ordinary-backstory register the rest of
            # authoring uses (not a dramaturgy question, a life question).
            protagonist_name = _display_name("player", names)
            return {
                "id": "authoring-plan-character-arc",
                "section": "arcs",
                "mode": "protected_interview",
                "text": (f"Tell me something ordinary about {protagonist_name}'s life before this "
                         "story starts — a family situation, a pressure they grew up under, "
                         "something they wanted and maybe couldn't have. Ground it in what's "
                         "already true about this world if that fits."),
                "target": {"section": "arcs", "plan_kind": "character_arc"},
                "scopes": [],
            }
        return {
            "id": "authoring-plan-character-arc",
            "section": "arcs",
            "mode": "protected_interview",
            "text": (f"Before I shape {first_name}'s arc, what are they unable to admit about themselves, "
                     "and what ordinary pressure should make that limitation matter?"),
            "target": {"section": "arcs", "plan_kind": "character_arc"},
            "scopes": [],
        }
    if reason == "loop_or_reset_rule":
        return {
            "id": "authoring-plan-loop",
            "section": "world",
            "mode": "protected_interview",
            "text": ("Before I schedule the story, what must stay true about the reset: where the player "
                     "returns, who remembers, and what can eventually change it?"),
            "target": {"section": "world", "plan_kind": "loop"},
            "scopes": [],
        }
    if reason == "time_or_entity_rule":
        return {
            "id": "authoring-plan-threat-schedule",
            "section": "time_system",
            "mode": "protected_interview",
            "text": ("Before I place scenes around the threat, when can it act, what makes that window "
                     "dangerous, and what keeps the rules fair to the player?"),
            "target": {"section": "time_system", "plan_kind": "threat_schedule"},
            "scopes": [],
        }
    if reason == "scene_offer_quality":
        # The diagnostic id is derived from the scene's public stable id.  Aim
        # the repair at that one item so its model snapshot excludes adjacent
        # hidden/evidence/knowledge fields and the server can merge only that
        # public scene back into the canonical Day One plan.
        scene_id = ident.removeprefix("scene-offer-")
        return {
            "id": "authoring-plan-scene-offer",
            "section": "first_day",
            "mode": "protected_interview",
            "text": (
                "One Day One beat is still a general fact instead of a scene the player can enter. "
                "What concrete person, object, incident, or pressure is in front of the player, and "
                "what can they immediately choose, ask, inspect, or refuse?"
            ),
            "target": {
                "section": "first_day",
                "plan_kind": "scene_flow",
                "item": {"kind": "scene", "id": scene_id},
            },
            "scopes": [],
        }
    if reason == "scene_placement":
        return {
            "id": "authoring-plan-opening",
            "section": "first_day",
            "mode": "protected_interview",
            "text": ("Before I place the opening scenes, who is physically with the player at the start, "
                     "and when should the first meaningful reunion or encounter happen?"),
            "target": {"section": "first_day", "plan_kind": "opening_presence"},
            "scopes": [],
        }
    if reason == "private_story_logic" or ident.startswith(("knowledge-", "evidence-")):
        return {
            "id": "authoring-plan-investigation",
            "section": "first_day",
            "mode": "protected_interview",
            "text": ("Before I build the investigation, what should the player be able to notice, pursue, "
                     "and plausibly misunderstand before the private answer comes into view?"),
            "target": {"section": "first_day", "plan_kind": "investigation"},
            "scopes": [],
        }
    if reason == "blocked_scaffold":
        return {
            "id": "authoring-plan-revision",
            "section": section if section in _SAFE_SECTIONS | {"arcs"} else "world",
            "mode": "protected_interview",
            "text": ("The current plan has reached an established fact that implementation should not guess "
                     "around. What story-level fact would you like to revise before I continue?"),
            "target": {"section": section, "plan_kind": "revision"},
            "scopes": [],
        }
    return {
        "id": "authoring-plan-next-decision",
        "section": section if section in _SAFE_SECTIONS | {"arcs"} else "world",
        "mode": "protected_interview",
        "text": ("Before I build farther, what single story decision should guide the next pass—what must "
                 "the player experience, learn, or be unable to avoid?"),
        "target": {"section": section, "plan_kind": "next_decision"},
        "scopes": [],
    }


def _public_baseline_plan_question(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Ask for intent once, then leave public scaffold details to workers."""
    scopes = [str(scope) for scope in (plan.get("safe_scopes") or []) if str(scope) in _SAFE_SECTIONS]
    section = scopes[0] if scopes else "world"
    gap_ids = [_text(item, limit=160) for item in (plan.get("safe_gap_ids") or [])]
    return {
        "id": "authoring-plan-public-baseline",
        "section": section,
        "mode": "develop",
        "text": ("Before I send implementation work into the public baseline, what should the opening make "
                 "the player feel, notice, and want to do next?"),
        # The ids are only retry bookkeeping.  They are not displayed as
        # author questions and do not include card prose or private facts.
        "target": {"section": section, "plan_kind": "public_baseline", "gap_ids": gap_ids},
        "scopes": scopes,
    }


def _protected_baseline_plan_question() -> dict[str, Any]:
    """Keep a Director-only task from leaking as a worker implementation prompt."""
    return {
        "id": "authoring-plan-investigation",
        "section": "first_day",
        "mode": "protected_interview",
        "text": ("Before I build the investigation, what should the player be able to notice, pursue, "
                 "and plausibly misunderstand before the private answer comes into view?"),
        "target": {"section": "first_day", "plan_kind": "investigation"},
        "scopes": [],
    }


def _plan_question(
    card: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    names: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Return the one user-facing decision for an operational completion plan."""
    if plan.get("safe_scopes"):
        return _public_baseline_plan_question(plan)
    if plan.get("protected_tasks"):
        return _protected_baseline_plan_question()
    hinges = [item for item in (plan.get("authorial_hinges") or []) if isinstance(item, Mapping)]
    if hinges:
        # The planner preserves diagnostics in report order for auditability;
        # the conversation should instead settle the highest-leverage story
        # decision first.  An investigation contract usefully constrains later
        # scene placement, whereas a missing generated location does not.
        def priority(hinge: Mapping[str, Any]) -> int:
            ident = _text(hinge.get("id"), limit=160)
            reason = _text(hinge.get("reason"), limit=80)
            if reason in {"loop_or_reset_rule", "time_or_entity_rule"}:
                return 0
            if reason == "character_or_theme_truth":
                return 1
            if reason == "relationship_truth":
                return 2
            if reason == "private_story_logic" or ident.startswith(("knowledge-", "evidence-")):
                return 3
            if reason == "scene_offer_quality":
                return 4
            if reason == "scene_placement":
                return 5
            if reason == "blocked_scaffold":
                return 6
            return 7

        return _authorial_plan_question(card, min(hinges, key=priority), names=names)
    return creative_question(card, names=names)


def creative_question(card: Mapping[str, Any], *, names: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Keep a structurally complete card conversational instead of declaring it done."""
    first_name = _first_cast_name(card, names)
    return {
        "id": "creative-next-step",
        "section": "arcs",
        "mode": "protected_interview",
        "text": (f"The card can run. What feeling or difficult choice do you most want {first_name} to reach next, so I can shape the next pressure without locking in the outcome?"),
        "target": {"section": "arcs"},
        "scopes": [],
    }


def starting_question() -> dict[str, Any]:
    return {
        "id": "starting-spark",
        "section": "world",
        "mode": "develop",
        "text": "Give me any starting spark—a scene, person, image, problem, or mood—and I’ll build the first coherent pass around it.",
        "target": {"section": "world"},
        "scopes": list(_STARTER_SCOPES),
    }


def choose_question(card: Mapping[str, Any], report: Mapping[str, Any], *, names: Mapping[str, str] | None = None,
                    blocked_gap_ids: set[str] | None = None) -> dict[str, Any]:
    """Choose one author-facing plan decision, never a worker diagnostic.

    The gap report still drives the deterministic completion plan, but a
    missing alias, inferred location, or other compiler detail is a task for
    a worker/normalizer—not a question the author should need to translate.
    The only exception is a genuinely authorial hinge, which is rendered as a
    story-level decision by :func:`_authorial_plan_question`.
    """
    if not _has_story_seed(card):
        return starting_question()
    plan = build_completion_plan(card, report, blocked_gap_ids=blocked_gap_ids or ())
    return _plan_question(card, plan, names=names)


def author_plan_presentation(
    card: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    names: Mapping[str, str] | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Architect's complete, commentable author-facing plan.

    The primary Architect used to surface a single next question.  That makes
    a capable coordinator feel like a form: an author cannot see the shape of
    the story or comment on a later concern until they have answered whatever
    the gap sorter happened to place first.  This function projects the same
    deterministic completion plan into six stable, high-level sections.  It
    deliberately contains no compiler gap ids/text and no protected event,
    knowledge, evidence, entity, or arc material.

    ``comment_id`` is an API-safe handle for a section-level comment.  It is
    not a raw story-card path and never grants a caller arbitrary edit scope.
    ``plan_comment_target`` below validates it again against this live plan
    before a model is called.
    """
    normalized = normalize_architect_state(state)
    plan = build_completion_plan(
        card,
        report,
        blocked_gap_ids=set(normalized.get("blocked_gap_ids") or ()),
    )
    question = _plan_question(card, plan, names=names)
    focus = _author_plan_focus(question)
    fields = card.get("fields") if isinstance(card.get("fields"), Mapping) else {}
    day = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), Mapping) else {}
    events = _author_plan_events(card)
    cast_names = _author_cast_names(card, names)
    premise = _author_safe_text(card.get("premise"), limit=280)
    opening_time = _author_safe_text(day.get("opening_time"), limit=80)
    opening_location = _author_location_name(card, day.get("opening_location") or card.get("start"))
    opening_present = [
        _display_name(str(item), names)
        for item in (day.get("opening_present") or [])
        if isinstance(item, str) and item not in {"player", "protagonist", "returner", "you"}
    ][:4]
    arc_design = fields.get("arc_design") if isinstance(fields.get("arc_design"), Mapping) else {}
    canonical_themes = arc_design.get("themes") if isinstance(arc_design.get("themes"), list) else []
    theme_source = canonical_themes or (card.get("themes") or [])
    themes = [
        _author_safe_text(
            item.get("label") or item.get("name") or item.get("theme")
            if isinstance(item, Mapping) else item,
            limit=100,
        )
        for item in theme_source
        if _author_safe_text(
            item.get("label") or item.get("name") or item.get("theme")
            if isinstance(item, Mapping) else item,
            limit=100,
        )
    ][:3]
    arc_configured = bool(arc_design)
    mystery_boundary = _author_has_mystery_boundary(card, plan)

    if opening_time or opening_location:
        opening_bits = ["The opening is placed"]
        if opening_time:
            opening_bits.append(f"in {opening_time}")
        if opening_location:
            opening_bits.append(f"at {opening_location}")
        opening_summary = " ".join(opening_bits) + "."
        if opening_present:
            opening_summary += " On the page: " + ", ".join(opening_present) + "."
        elif cast_names:
            opening_summary += " The on-page companion is still open."
    elif premise:
        opening_summary = f"The opening is anchored by: {premise}"
    else:
        opening_summary = "The story still needs its first concrete image, person, place, or problem."
    if events and events[0].get("visible"):
        opening_summary = f"{opening_summary} First visible beat: {events[0]['visible']}"

    if cast_names:
        cast_summary = "The current cast is " + ", ".join(cast_names) + "."
    else:
        cast_summary = "No on-card cast has been placed yet; the plan can begin with the people the opening needs."

    slots = [event["when"] for event in events if event.get("when")]
    locations = [event["location"] for event in events if event.get("location")]
    if events:
        flow_summary = f"Day One currently has {len(events)} possible scene{'s' if len(events) != 1 else ''}."
        if slots:
            flow_summary += " It moves through " + " → ".join(dict.fromkeys(slots)) + "."
        if locations:
            flow_summary += " Public places in view: " + ", ".join(list(dict.fromkeys(locations))[:3]) + "."
        scene_themes = [event["theme"] for event in events if event.get("theme")]
        if scene_themes:
            flow_summary += " Its immediate tensions include " + ", ".join(list(dict.fromkeys(scene_themes))[:3]) + "."
        hooked = sum(1 for event in events if event.get("hook"))
        if hooked == len(events):
            flow_summary += " Each offer has a player-facing way in."
        elif hooked:
            flow_summary += " Some offers still need a player-facing way in."
        else:
            flow_summary += " The next pass should turn these beats into encounters with a player-facing way in."
    else:
        flow_summary = "Day One has not been laid out as a public sequence yet; the plan will keep it flexible rather than scripting every action."

    if mystery_boundary:
        mystery_summary = (
            "The mystery has an author-controlled boundary: the player gets a fair surface to investigate, "
            "while the answer stays out of narrator context until it is earned."
        )
    else:
        mystery_summary = (
            "The mystery boundary is still open. Decide what the player can notice and pursue before the "
            "underlying answer becomes available."
        )

    protagonist_lead = ""
    protagonist_own_arc = protagonist_arc(arc_design)
    if isinstance(protagonist_own_arc, Mapping):
        # `starting_belief` is the narrator-safe half of an arc (see
        # arc_design.narrator_arc_surface) — safe to lead the author-facing plan with too.
        belief = _author_safe_text(protagonist_own_arc.get("starting_belief"), limit=200)
        if belief:
            protagonist_name = _display_name("player", names)
            protagonist_lead = f"{protagonist_name}'s own arc is established — they believe: {belief}. "

    if themes:
        arc_summary = protagonist_lead + "The current thematic direction is " + ", ".join(themes) + "."
        if arc_configured:
            arc_summary += " Character pressure is being tracked separately from what the narrator may reveal."
        else:
            arc_summary += " The character pressure that gives it emotional force is still open for author direction."
    elif arc_configured:
        arc_summary = protagonist_lead + "A private character-pressure design exists; its public thematic label still needs to be made legible."
    else:
        arc_summary = protagonist_lead + "Theme and character pressure are still open; the plan leaves room for flaws and delayed recognition rather than forcing a tidy arc."

    build_summary = (
        "The Architect will establish the playable public surface first, then hold relationship, mystery, "
        "and character-pressure decisions for author approval."
    )
    if plan.get("safe_scopes"):
        build_summary = "A bounded public baseline is ready to build once its overall direction is approved."
    elif plan.get("phase") == "ready":
        build_summary = "The current plan is structurally ready; future passes can deepen the story without replacing established canon."

    raw_sections = [
        ("opening", "Opening", opening_summary, {"section": "first_day", "plan_kind": "opening_presence"},
         "Comment on the opening image, who is present, or when the first meaningful encounter lands.",
         bool(opening_time or opening_location or events)),
        ("cast", "Cast", cast_summary, {"section": "cast", "plan_kind": "cast_direction"},
         "Comment on who belongs in the story, what role they serve, or which connection should matter.", bool(cast_names)),
        ("scene_flow", "Scene flow", flow_summary, {"section": "first_day", "plan_kind": "scene_flow"},
         "Comment on the public sequence, pacing, places, or the kinds of scenes you want available.", bool(events)),
        ("mystery_boundary", "Mystery boundary", mystery_summary,
         {"section": "first_day", "plan_kind": "investigation"},
         "Comment on what the player may notice, pursue, or plausibly misunderstand.", mystery_boundary),
        ("arc_theme", "Storylines", arc_summary,
         {"section": "arcs", "plan_kind": "character_arc"},
         "Comment on a storyline's theme, a character's limitation, or the pressure that should test them.", bool(themes or arc_configured)),
        ("build_order", "Build order", build_summary, {"section": "world", "plan_kind": "build_order"},
         "Comment on what the Architect should prioritize, preserve, or leave deliberately unresolved.", bool(plan.get("safe_scopes") or plan.get("phase") == "ready")),
    ]
    sections = [
        {
            "id": section_id,
            "label": label,
            "status": _author_plan_status(
                section_id=section_id,
                focus=focus,
                established=established,
                plan=plan,
            ),
            "summary": summary,
            "comment_id": _author_plan_comment_id(section_id),
            "target": target,
            "comment_hint": hint,
        }
        for section_id, label, summary, target, hint, established in raw_sections
    ]
    by_id = {item["id"]: item for item in sections}
    focus_section = by_id.get(focus, by_id["build_order"])
    if not _has_story_seed(card):
        plan_summary = "Bring any story spark. The Architect will turn it into a complete plan before implementation begins."
    elif premise:
        plan_summary = f"Plan for: {premise}"
    else:
        plan_summary = "A story plan is taking shape around the established setting, people, and playable first day."
    next_summary = {
        "opening": "Establish the opening's concrete public shape before workers place it.",
        "cast": "Set the people and connections that must carry emotional weight before workers extend the roster.",
        "scene_flow": "Set the public sequence and time pressure before workers place supporting scenes.",
        "mystery_boundary": "Set what the player can investigate before a protected knowledge pass is allowed.",
        "arc_theme": "Set the theme and human limitation before workers turn it into pressure on the page.",
        "build_order": "Confirm the intended order; public scaffolding can then be built in one bounded pass.",
    }.get(focus, "Comment on the part of the plan you want to shape next.")
    return {
        "version": 1,
        "title": "Story plan",
        "summary": plan_summary,
        "comment_prompt": "Comment on any part of this plan. The Architect will update the relevant direction, then hand bounded implementation work to the right worker.",
        "sections": sections,
        "build_order": [
            {"id": item["id"], "label": item["label"], "status": item["status"]}
            for item in sections
        ],
        "next_step": {
            "id": focus_section["id"],
            "label": focus_section["label"],
            "summary": next_summary,
            "comment_id": focus_section["comment_id"],
        },
    }


def plan_comment_target(
    card: Mapping[str, Any],
    report: Mapping[str, Any],
    section_id: str,
    *,
    names: Mapping[str, str] | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve a section-level Architect-plan comment to a bounded target.

    This is intentionally a whitelist of the six visible plan sections.  A
    client cannot turn a free-form ``plan_section`` field into arbitrary
    model scope, an item id, or a director-only task.  The build-order tile
    resolves to the live approved plan question, so it preserves the existing
    public-baseline authorization path rather than bypassing it.
    """
    section_id = _text(section_id, limit=80).lower().replace("-", "_")
    # UI clients may send either the human-readable section id or the stable
    # ``comment_id`` published in the presentation.  Normalize both here so
    # a click does not depend on which field the frontend happens to keep.
    section_id = {
        "architect_plan_opening": "opening",
        "architect_plan_cast": "cast",
        "architect_plan_scenes": "scene_flow",
        "architect_plan_mystery": "mystery_boundary",
        "architect_plan_arcs": "arc_theme",
        "architect_plan_build_order": "build_order",
    }.get(section_id, section_id)
    if section_id not in _AUTHOR_PLAN_SECTION_IDS:
        return None
    normalized = normalize_architect_state(state)
    plan = build_completion_plan(
        card,
        report,
        blocked_gap_ids=set(normalized.get("blocked_gap_ids") or ()),
    )
    if section_id == "build_order":
        return _plan_question(card, plan, names=names)
    descriptor = {
        "opening": {
            "id": "architect-plan-opening", "section": "first_day", "mode": "protected_interview",
            "text": "Incorporate the author's opening-plan direction.",
            "target": {"section": "first_day", "plan_kind": "opening_presence"}, "scopes": [],
        },
        "cast": {
            "id": "architect-plan-cast", "section": "cast", "mode": "protected_interview",
            "text": "Incorporate the author's cast-plan direction.",
            "target": {"section": "cast", "plan_kind": "cast_direction"}, "scopes": [],
        },
        "scene_flow": {
            "id": "architect-plan-scenes", "section": "first_day", "mode": "protected_interview",
            "text": "Incorporate the author's scene-flow direction.",
            "target": {"section": "first_day", "plan_kind": "scene_flow"}, "scopes": [],
        },
        "mystery_boundary": {
            "id": "architect-plan-mystery", "section": "first_day", "mode": "protected_interview",
            "text": "Incorporate the author's investigation-plan direction.",
            "target": {"section": "first_day", "plan_kind": "investigation"}, "scopes": [],
        },
        "arc_theme": {
            "id": "architect-plan-arcs", "section": "arcs", "mode": "protected_interview",
            "text": "Incorporate the author's theme-and-character-pressure direction.",
            "target": {"section": "arcs", "plan_kind": "character_arc"}, "scopes": [],
        },
    }
    return deepcopy(descriptor.get(section_id))


def question_for_answer(
    card: Mapping[str, Any], report: Mapping[str, Any], state: Mapping[str, Any], answer_to: str,
    *, names: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Resolve an answer only through the pending question or a live gap.

    An arbitrary client-provided section never gets to steer an Architect turn.
    This prevents a stale UI control from becoming a cross-section edit escape
    hatch while still allowing a page reload to answer a persisted question.
    """
    answer_to = _text(answer_to, limit=160)
    if not answer_to:
        return None
    normalized_state = normalize_architect_state(state)
    pending = normalized_state.get("pending_question")
    if isinstance(pending, Mapping) and answer_to == pending.get("id"):
        return current_pending_question(card, report, state, names=names)
    # A legacy UI may submit a raw diagnostic id.  Translate it to the same
    # plan-level question the current Architect would show; never resurrect a
    # worker question simply because a stale client still knows its id.
    plan = build_completion_plan(
        card,
        report,
        blocked_gap_ids=set(normalized_state.get("blocked_gap_ids") or ()),
    )
    for gap in report.get("gaps") or []:
        if isinstance(gap, Mapping) and answer_to == _text(gap.get("id"), limit=160):
            # A stale client may still name a diagnostic that happens to be an
            # authorial hinge.  It must not jump ahead of a newer public
            # baseline (or a higher-priority hinge): there is exactly one
            # current Architect decision.  Recompute that decision from the
            # live plan rather than resurrecting the diagnostic's old route.
            return _plan_question(card, plan, names=names)
    if answer_to == "starting-spark" and not _has_story_seed(card):
        return starting_question()
    if answer_to == "creative-next-step" and not (report.get("gaps") or []):
        return creative_question(card, names=names)
    return None


def current_pending_question(
    card: Mapping[str, Any], report: Mapping[str, Any], state: Mapping[str, Any],
    *, names: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return the saved question only if it still applies to the live card.

    A reload must be model-free, but deterministic compiler changes can resolve
    an old diagnostic without changing card JSON.  Reclassify a live gap from
    the current report, and let a caller replace every other stale diagnostic
    with the current observation instead of trapping the author behind it.
    """
    pending = normalize_architect_state(state).get("pending_question")
    if not isinstance(pending, Mapping):
        return None
    ident = _text(pending.get("id"), limit=160)
    if not ident:
        return None

    if ident == "starting-spark":
        return starting_question() if not _has_story_seed(card) else None
    if ident == "creative-next-step":
        return creative_question(card, names=names) if not (report.get("gaps") or []) else None
    if ident == "clarify-starting-spark":
        return deepcopy(dict(pending)) if not _has_story_seed(card) else None
    if ident.startswith("revise-established-"):
        target = pending.get("target") if isinstance(pending.get("target"), Mapping) else {}
        blocked = {
            _text(item, limit=160)
            for item in (target.get("blocked_gap_ids") or [])
            if _text(item, limit=160)
        }
        live = {
            _text(gap.get("id"), limit=160)
            for gap in (report.get("gaps") or [])
            if isinstance(gap, Mapping)
        }
        return deepcopy(dict(pending)) if blocked & live else None
    # Any persisted plan question is valid only while the planner still picks
    # that same high-level decision.  This rebases a stale question after a
    # compiler upgrade without ever surfacing a raw worker diagnostic.
    if ident.startswith("authoring-plan-"):
        current = choose_question(card, report, names=names,
                                  blocked_gap_ids=set(normalize_architect_state(state).get("blocked_gap_ids") or ()))
        return current if current.get("id") == ident else None
    # Previous versions persisted raw gap ids (including scene-moved semantic
    # prompts).  Rebase them immediately to the current plan-level question
    # instead of replaying worker prose.  The route owns the subsequent state
    # write, so this helper remains pure for reload and stale-client callers.
    return choose_question(card, report, names=names,
                           blocked_gap_ids=set(normalize_architect_state(state).get("blocked_gap_ids") or ()))


def architect_observation(card: Mapping[str, Any], *, names: Mapping[str, str] | None = None,
                         state: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return deterministic gaps plus the one question that follows from them."""
    report = analyze_story_card(card)
    normalized = normalize_architect_state(state)
    return report, choose_question(card, report, names=names,
                                   blocked_gap_ids=set(normalized.get("blocked_gap_ids") or []))


def append_architect_history(state: Mapping[str, Any], *, role: str, text: str) -> dict[str, Any]:
    next_state = normalize_architect_state(state)
    clean = _text(text, limit=1600)
    if clean and role in {"author", "architect"}:
        next_state["history"] = [*next_state["history"], {"role": role, "text": clean}][-MAX_HISTORY:]
    return next_state


def append_architect_action(state: Mapping[str, Any], action: Mapping[str, Any]) -> dict[str, Any]:
    next_state = normalize_architect_state(state)
    candidate = normalize_architect_state({"actions": [dict(action)]})["actions"]
    if candidate:
        next_state["actions"] = [*next_state["actions"], candidate[0]][-MAX_ACTIONS:]
    return next_state


def block_gap_ids(state: Mapping[str, Any], identifiers: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    """Remember no-op gaps until a later canonical revision makes them fresh.

    This is intentionally a tiny, deterministic substitute for an unbounded
    retry loop.  The author can still explicitly target a revision; automatic
    continuation simply moves to work it can actually perform.
    """
    next_state = normalize_architect_state(state)
    blocked = list(next_state.get("blocked_gap_ids") or [])
    for value in identifiers:
        ident = _text(value, limit=160)
        if ident and ident not in blocked:
            blocked.append(ident)
    next_state["blocked_gap_ids"] = blocked[-MAX_ACTIONS:]
    return next_state


def completion_request(body: Mapping[str, Any], message: str) -> bool:
    """Whether the author explicitly asks for a bounded autonomous pass.

    The API flags make a UI integration unambiguous.  The small phrase set lets
    the primary conversational surface work immediately without a separate
    control: an author can simply say "continue" or "build the baseline".
    """
    if any(body.get(name) is True for name in ("autonomous", "continue", "complete")):
        return True
    mode = _text(body.get("mode"), limit=60).lower().replace("-", "_")
    if mode in {"continue", "autonomous", "autopilot", "complete", "complete_story"}:
        return True
    normalized = " ".join(_text(message, limit=400).lower().replace("_", " ").split())
    return normalized in {
        "continue", "keep going", "go ahead", "autopilot", "complete it", "complete the story",
        "finish it", "finish the story", "build the baseline", "build out the story", "fill in the rest",
    }


def scene_has_semantic_gate(event: Mapping[str, Any]) -> bool:
    """Whether moving a scene needs an author decision rather than auto-sync.

    These fields make a move mean more than a visual placement: they describe
    private knowledge, evidence, a trigger, or an entity action.  The
    reconciliation path may inspect their *shape*, but it must not rewrite or
    expose their contents.
    """
    if bool(event.get("entity_action")):
        return True
    if _text(event.get("hidden")) or _text(event.get("trigger")):
        return True
    if _text(event.get("evidence")):
        return True
    knowledge = event.get("knowledge")
    return isinstance(knowledge, Mapping) and any(_text(value) for value in knowledge.values())


def sync_scene_time_mirrors(
    card: Mapping[str, Any], *, scene_id: str, previous_when: str = "", current_when: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Synchronize only unambiguous arc time mirrors for a moved scene.

    An arc point that formerly mirrored the source scene's old slot can safely
    mirror its new slot.  A point with a different explicit slot could be an
    intentional dramatic offset; it is left untouched and reported as a
    conflict for the Architect to ask about.  No narrative text, private arc
    pressure, knowledge, evidence, or entity rule is modified here.
    """
    source = deepcopy(dict(card))
    fields = source.get("fields")
    if not isinstance(fields, dict):
        return source, {"updated": 0, "conflicts": 0}
    design = fields.get("arc_design")
    if not isinstance(design, dict) or not scene_id:
        return source, {"updated": 0, "conflicts": 0}
    previous_when = _text(previous_when, limit=120)
    current_when = _text(current_when, limit=120)
    updated = 0
    conflicts = 0

    def sync_point(point: Any) -> None:
        nonlocal updated, conflicts
        if not isinstance(point, dict) or _text(point.get("scene_id"), limit=160) != scene_id:
            return
        point_when = _text(point.get("when"), limit=120)
        # A missing slot is a pure derived omission.  Do not fill it when the
        # source is now unplaced, but otherwise it is safe to mirror.
        if not point_when:
            if current_when:
                point["when"] = current_when
                updated += 1
            return
        # Without the old slot supplied by the placement UI, any nonblank
        # point may be intentional.  Leave it alone rather than guessing.
        if not previous_when:
            if point_when != current_when:
                conflicts += 1
            return
        if point_when == previous_when:
            if point_when != current_when:
                point["when"] = current_when
                updated += 1
            return
        if point_when != current_when:
            conflicts += 1

    for arc in design.get("arcs") or []:
        if not isinstance(arc, dict):
            continue
        for point in arc.get("turning_points") or []:
            sync_point(point)
        for thread in arc.get("character_threads") or []:
            if not isinstance(thread, dict):
                continue
            for point in thread.get("pressure_points") or []:
                sync_point(point)
    return source, {"updated": updated, "conflicts": conflicts}
