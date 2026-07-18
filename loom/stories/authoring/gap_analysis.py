"""Deterministic, read-only action planning for an authored Story card.

This is deliberately a *card* diagnostic rather than a story-writing agent.  It
can say which connections have not been authored yet, but it never guesses the
missing relationship, secret, scene outcome, or character truth.  That makes it
safe to use as the observation step of an agent loop and useful on a partial
card where an LLM would otherwise fill the holes with canon.

The public result contains only stable ids, counts, and actionable questions.
In particular, hidden event text and the private portions of ``arc_design`` are
read only to determine structure; they never appear in a gap record.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from .scene_contract import scene_offer_errors


_SEVERITY_ORDER = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
_READINESS_SECTIONS = (
    ("fields.first_day_plan", "first_day"),
    ("fields.arc_design", "arcs"),
    ("fields.player_abilities", "world"),
    ("fields.player_capabilities", "world"),
    ("time_system", "time_system"),
    ("relationships", "cast"),
    ("cast", "cast"),
    ("locations", "world"),
    ("start", "premise"),
    ("world", "world"),
)
_KNOWLEDGE_ACTORS = {
    "player", "protagonist", "public", "everyone", "entity", "director", "narrator",
}
_SCENE_OFFER_QUALITY_ERRORS = {
    "a visible situation or pressure",
    "a player-facing hook",
    "a player-facing choice or action",
    "an immediate encounter instead of a standalone lore fact",
    "a concrete clue or encounter instead of an abstract lore summary",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_text(item) for item in value if _text(item)]


def _slug(value: Any, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", _text(value).lower()).strip("-")
    return token or fallback


def _cast_keys(card: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for item in card.get("cast") or []:
        if isinstance(item, str):
            key = _text(item)
        else:
            value = _mapping(item)
            key = _text(value.get("character") or value.get("key"))
        if key and key not in out:
            out.append(key)
    return out


def _events(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    plan = _mapping(fields.get("first_day_plan"))
    raw_events = plan.get("events")
    if not isinstance(raw_events, list):
        return []
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, raw in enumerate(raw_events):
        event = _mapping(raw)
        if not event:
            continue
        base = _slug(event.get("id") or event.get("scene_id") or event.get("title")
                     or event.get("event") or event.get("visible"), f"day-1-event-{index + 1}")
        ident = base
        suffix = 2
        while ident in used:
            ident = f"{base}-{suffix}"
            suffix += 1
        used.add(ident)
        out.append({"id": ident, "index": index, "value": event})
    return out


def _section_for_path(path: str) -> str:
    for prefix, section in _READINESS_SECTIONS:
        if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
            return section
    return "world"


def _safe_readiness(compiled: Mapping[str, Any]) -> dict[str, Any]:
    issues = []
    for raw in compiled.get("issues") or []:
        if not isinstance(raw, Mapping):
            continue
        path = _text(raw.get("path"))
        issues.append({
            "code": _text(raw.get("code")) or "readiness_issue",
            "severity": "blocker" if raw.get("severity") == "error" else "warning",
            "section": _section_for_path(path),
            "path": path,
            "message": _text(raw.get("message")) or "This authored constraint is incomplete.",
            "fix": _text(raw.get("fix")),
        })
    return {
        "ready": bool(compiled.get("ready")),
        "blockers": [item for item in issues if item["severity"] == "blocker"],
        "warnings": [item for item in issues if item["severity"] != "blocker"],
    }


def _add(gaps: list[dict[str, Any]], seen: set[str], *, ident: str, section: str,
         title: str, detail: str, severity: str, suggested_scope: str,
         paths: list[str] | None = None, related: list[str] | None = None) -> None:
    """Append one stable, non-canonical action only once."""
    if ident in seen:
        return
    seen.add(ident)
    gaps.append({
        "id": ident,
        "section": section,
        "title": title,
        "detail": detail,
        "severity": severity if severity in _SEVERITY_ORDER else "medium",
        "suggested_scope": suggested_scope,
        "paths": list(paths or []),
        "related": list(related or []),
    })


def _add_readiness_gaps(gaps: list[dict[str, Any]], seen: set[str], readiness: Mapping[str, Any]) -> None:
    for item in [*(readiness.get("blockers") or []), *(readiness.get("warnings") or [])]:
        if not isinstance(item, Mapping):
            continue
        code = _text(item.get("code")) or "issue"
        path = _text(item.get("path"))
        severity = "blocker" if item.get("severity") == "blocker" else "high"
        _add(
            gaps, seen,
            ident=f"readiness-{_slug(code, 'issue')}-{_slug(path, 'card')}",
            section=_text(item.get("section")) or "world",
            title="Resolve a play-readiness constraint",
            detail=_text(item.get("message")) or "This authored constraint is incomplete.",
            severity=severity,
            suggested_scope=_text(item.get("fix")) or "Make the smallest explicit card decision that satisfies this constraint.",
            paths=[path] if path else [],
        )


def _relationship_pairs(card: Mapping[str, Any]) -> tuple[set[frozenset[str]], set[str]]:
    pairs: set[frozenset[str]] = set()
    endpoints: set[str] = set()
    for raw in card.get("relationships") or []:
        relation = _mapping(raw)
        source, target = _text(relation.get("source")), _text(relation.get("target"))
        if source:
            endpoints.add(source)
        if target:
            endpoints.add(target)
        if source and target and source != target:
            pairs.add(frozenset((source, target)))
    return pairs, endpoints


def _arc_structure(fields: Mapping[str, Any], card: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    raw = fields.get("arc_design")
    if raw is None:
        return None, []
    try:
        from .arc_design import ArcDesignValidationError, arc_design_issues, normalize_arc_design

        design = normalize_arc_design(raw, card=card)
        return design, arc_design_issues(raw, card=card)
    except Exception:  # A partial private draft still deserves a safe, generic repair item.
        return {}, [{
            "code": "arc_design_invalid", "severity": "error", "path": "fields.arc_design",
            "message": "The private arc document is not structurally usable yet.",
            "fix": "Repair its themes, character keys, and pressure-point structure.",
        }]


def _safe_arc_issue_text(issue: Mapping[str, Any]) -> tuple[str, str]:
    """Translate private-arc diagnostics without echoing author-only text.

    ``arc_design_issues`` can legitimately mention an arc title in a human
    explanation.  Titles and dramatic questions are not part of the public
    arc projection, so this observer returns a structural description keyed by
    code instead of copying that explanation out of the private document.
    """
    code = _text(issue.get("code"))
    details = {
        "arc_design_invalid": "The private arc document needs a valid canonical structure before it can guide runtime pressure.",
        "theme_missing": "The arc document has no thematic question to connect character pressure to.",
        "character_arcs_missing": "The arc document has no character-pressure arc yet.",
        "arc_theme_unknown": "An arc refers to a theme that is not defined in the same private document.",
        "arc_theme_missing": "An arc is not connected to a thematic question yet.",
        "arc_turn_missing": "An arc has no possible pressure or turn connected to a scene or time window.",
        "arc_thread_character_missing": "A character pressure thread has no named cast owner.",
        "arc_thread_character_unknown": "A character pressure thread points to someone who is not in the story cast.",
        "arc_thread_duplicate": "One character has overlapping pressure threads that should be distinguished or combined.",
        "arc_thread_incomplete": "A character pressure thread lacks an outward resistance detail needed for play.",
        "arc_thread_pressure_missing": "A character pressure thread is not connected to any possible scene pressure.",
    }
    detail = details.get(code, "A private arc connection is structurally incomplete.")
    fixes = {
        "arc_design_invalid": "Repair the themes, character keys, and pressure-point structure without exposing private truth.",
        "theme_missing": "Add one human question the story can pressure.",
        "character_arcs_missing": "Give one named person a protective strategy, limitation, visible tell, and possible pressure scene.",
        "arc_thread_incomplete": "Write only the missing outward pattern; keep the buried cause author-only.",
    }
    return detail, fixes.get(code, "Write the smallest missing structural link without exposing private character truth.")


def analyze_story_card(card: Mapping[str, Any] | dict[str, Any] | None) -> dict[str, Any]:
    """Return a safe, deterministic action plan for a Story card.

    No key in this result contains raw hidden event material, author notes, or
    a character's private arc truth.  The function only derives structural
    coverage and explicit compiler readiness findings; it never changes
    ``card`` and makes no model call.
    """
    source = deepcopy(_mapping(card))
    fields = _mapping(source.get("fields"))
    cast = _cast_keys(source)
    cast_set = set(cast)
    events = _events(fields)
    event_ids = {event["id"] for event in events}
    relationships, relationship_endpoints = _relationship_pairs(source)

    try:
        from ..runtime.scenario_compiler import compile_authored_scenario

        compiled = compile_authored_scenario(source)
    except Exception:
        compiled = {"ready": False, "issues": [{
            "code": "card_shape_invalid", "severity": "error", "path": "",
            "message": "The partial card cannot yet be compiled into a playable scenario.",
            "fix": "Repair the malformed card field before relying on runtime readiness.",
        }]}
    readiness = _safe_readiness(compiled)

    gaps: list[dict[str, Any]] = []
    seen: set[str] = set()
    _add_readiness_gaps(gaps, seen, readiness)

    # A legacy card may predate the public scene-offer contract.  The runtime
    # can still compile such an item, but an Architect that treats a vague lore
    # placeholder as a finished scene will never direct the author back to it.
    # Inspect only the public offer shape here: the helper deliberately ignores
    # hidden actions, evidence, knowledge, and entity periods.  That makes this
    # a safe author-facing repair signal even for a protected scene.
    for event in events:
        public_errors = set(scene_offer_errors(event["value"]))
        if not (public_errors & _SCENE_OFFER_QUALITY_ERRORS):
            continue
        ident = event["id"]
        _add(
            gaps,
            seen,
            ident=f"scene-offer-{ident}",
            section="first_day",
            severity="high",
            title="Turn a general beat into a playable scene",
            detail=(
                "This Day One item needs one concrete, player-enterable moment "
                "rather than a general fact, atmosphere note, or promise of lore."
            ),
            suggested_scope=(
                "Name the present person, object, incident, or pressure the player can "
                "actually respond to, then state the immediate choice or question it creates."
            ),
            paths=[f"fields.first_day_plan.events[{event['index']}]"],
            related=[ident],
        )

    # ── People and introductions ──────────────────────────────────────────
    if not cast:
        _add(gaps, seen, ident="cast-missing", section="cast", severity="high",
             title="Choose the first person the player can meaningfully meet",
             detail="The card has no cast member to carry a relationship, reaction, or character arc.",
             suggested_scope="Add one named character card to the story cast, then decide where they first appear.",
             paths=["cast"])
    introduced = {
        participant for event in events
        for participant in _strings(event["value"].get("participants") or event["value"].get("present"))
        if participant in cast_set
    }
    unintroduced = [key for key in cast if key not in introduced]
    if cast and events and unintroduced:
        _add(gaps, seen, ident="cast-unintroduced", section="first_day", severity="low",
             title="Decide whether every current cast member has an entry point",
             detail="Some cast members are not connected to any authored Day One scene yet.",
             suggested_scope="Either place these people in a future scene deliberately or keep them out of the starting cast until their entrance is known.",
             paths=["cast", "fields.first_day_plan.events"], related=unintroduced)

    # ── Relationships ─────────────────────────────────────────────────────
    if len(cast) >= 2 and not relationships:
        _add(gaps, seen, ident="relationships-missing", section="cast", severity="high",
             title="Connect the initial cast",
             detail="The story names multiple people but no established relationship tells the runtime how they stand toward one another.",
             suggested_scope="Write the smallest concrete bond, debt, history, or disagreement that changes how two people behave in the opening.",
             paths=["relationships"], related=cast)
    unknown_endpoints = sorted(endpoint for endpoint in relationship_endpoints if endpoint not in cast_set)
    if unknown_endpoints:
        _add(gaps, seen, ident="relationship-endpoint-unknown", section="cast", severity="high",
             title="Resolve relationship endpoints that are not in the cast",
             detail="At least one relationship points to a person the Story card does not currently cast.",
             suggested_scope="Add the person to cast or retarget the relationship to the existing stable character key.",
             paths=["relationships", "cast"], related=unknown_endpoints)
    isolated = [key for key in cast if key not in relationship_endpoints]
    if len(cast) >= 2 and isolated:
        _add(gaps, seen, ident="cast-relationship-isolation", section="cast", severity="medium",
             title="Give the isolated cast a reason to matter",
             detail="Some cast members have no authored bond to anyone else on the Story card.",
             suggested_scope="Decide whether each person is intentionally independent; otherwise add one relationship that can affect a scene.",
             paths=["relationships"], related=isolated)
    # Co-presence with no bond is an especially useful missing connection: it
    # is more specific than asking the author to fill the whole relationship web.
    unlinked_pairs: list[str] = []
    for event in events:
        people = [person for person in _strings(event["value"].get("participants") or event["value"].get("present"))
                  if person in cast_set]
        for index, person in enumerate(people):
            for other in people[index + 1:]:
                if frozenset((person, other)) not in relationships:
                    label = "--".join(sorted((person, other)))
                    if label not in unlinked_pairs:
                        unlinked_pairs.append(label)
    if unlinked_pairs:
        _add(gaps, seen, ident="scene-relationship-link-missing", section="cast", severity="medium",
             title="Clarify why the people sharing a scene affect one another",
             detail="At least one Day One scene puts cast members together without an authored relationship link.",
             suggested_scope="Add only the relationship needed to make their on-stage behavior legible; do not invent a full social graph.",
             paths=["relationships", "fields.first_day_plan.events"], related=unlinked_pairs[:8])

    # ── Scene-to-knowledge links ───────────────────────────────────────────
    gated_events = []
    knowledge_events = 0
    evidence_events = 0
    for event in events:
        value, ident = event["value"], event["id"]
        is_gated = bool(_text(value.get("hidden"))) or bool(value.get("entity_action"))
        knowledge = _mapping(value.get("knowledge"))
        evidence = value.get("evidence")
        if knowledge:
            knowledge_events += 1
        if _text(evidence) or (isinstance(evidence, (list, tuple)) and any(_text(item) for item in evidence)):
            evidence_events += 1
        if not is_gated:
            continue
        gated_events.append(ident)
        if not knowledge:
            _add(gaps, seen, ident=f"knowledge-gate-{ident}", section="first_day", severity="high",
                 title="Assign who can know the hidden part of this scene",
                 detail="A scene has a hidden or entity-driven layer, but no knowledge boundary says who can know it directly.",
                 suggested_scope="Name only the observers who know the fact and what the player can learn later; keep the hidden content private.",
                 paths=[f"fields.first_day_plan.events[{event['index']}].knowledge"], related=[ident])
        else:
            unknown_knowers = sorted(key for key in knowledge
                                     if key not in _KNOWLEDGE_ACTORS and key not in cast_set)
            if unknown_knowers:
                _add(gaps, seen, ident=f"knowledge-observer-unknown-{ident}", section="first_day", severity="high",
                     title="Resolve an unknown knowledge observer",
                     detail="This scene's knowledge gate names someone who is not a cast key or a supported shared observer.",
                     suggested_scope="Use an existing character key, or a shared role such as player, protagonist, public, or entity.",
                     paths=[f"fields.first_day_plan.events[{event['index']}].knowledge"], related=unknown_knowers)
        if not (_text(evidence) or (isinstance(evidence, (list, tuple)) and any(_text(item) for item in evidence))):
            _add(gaps, seen, ident=f"evidence-link-{ident}", section="first_day", severity="medium",
                 title="Leave a discoverable trace for the hidden scene layer",
                 detail="A private event has no authored evidence connecting it to something the player could notice or investigate.",
                 suggested_scope="Choose one concrete public trace, consequence, or witness; it need not reveal the answer.",
                 paths=[f"fields.first_day_plan.events[{event['index']}].evidence"], related=[ident])

    # ── Theme and character pressure ───────────────────────────────────────
    public_themes = [item for item in _strings(source.get("themes")) if item]
    design, arc_issues = _arc_structure(fields, source)
    if cast and not public_themes and not (design and design.get("themes")):
        _add(gaps, seen, ident="theme-missing", section="arcs", severity="high",
             title="Name the human question the story will pressure",
             detail="The card has people and scenes, but no theme connects their choices to a larger emotional question.",
             suggested_scope="Add one short public theme label; keep any private answer in the Director-only arc design.",
             paths=["themes", "fields.arc_design.themes"])
    if cast and design is None:
        _add(gaps, seen, ident="arc-design-missing", section="arcs", severity="high",
             title="Give one core character a resistant arc",
             detail="No private theme-and-character pressure document exists yet, so the runtime has no authored account of what a person cannot easily acknowledge.",
             suggested_scope="Start with one named cast member's protective strategy, limitation, visible tell, and a possible pressure scene—never a forced confession.",
             paths=["fields.arc_design"], related=cast[:1])
    for issue in arc_issues:
        if not isinstance(issue, Mapping):
            continue
        code = _slug(issue.get("code"), "arc-issue")
        path = _text(issue.get("path"))
        detail, suggested_scope = _safe_arc_issue_text(issue)
        _add(gaps, seen, ident=f"arc-{code}-{_slug(path, 'design')}", section="arcs",
             severity="blocker" if issue.get("severity") == "error" else "medium",
             title="Complete a character-arc connection",
             detail=detail,
             suggested_scope=suggested_scope,
             paths=[path] if path else ["fields.arc_design"])
    if isinstance(design, Mapping) and design:
        threads = [thread for arc in design.get("arcs") or [] if isinstance(arc, Mapping)
                   for thread in (arc.get("character_threads") or []) if isinstance(thread, Mapping)]
        threaded = {str(thread.get("character") or "") for thread in threads if thread.get("character")}
        if cast and not threaded:
            _add(gaps, seen, ident="arc-character-thread-missing", section="arcs", severity="high",
                 title="Attach the theme to a specific character",
                 detail="The arc document exists, but no cast member has a character pressure thread yet.",
                 suggested_scope="Choose one person and describe their outward protection, limitation, and visible tell before planning any outcome.",
                 paths=["fields.arc_design.arcs"], related=cast[:1])
        for arc in design.get("arcs") or []:
            if not isinstance(arc, Mapping):
                continue
            for point in arc.get("turning_points") or []:
                if not isinstance(point, Mapping):
                    continue
                scene_id = _text(point.get("scene_id"))
                if scene_id and scene_id not in event_ids:
                    _add(gaps, seen, ident=f"arc-scene-link-{_slug(scene_id, 'scene')}", section="arcs", severity="medium",
                         title="Connect an arc pressure to an authored scene",
                         detail="A possible character turn names a scene that is not on the current Day One plan.",
                         suggested_scope="Add that scene to the plan, retarget the pressure, or deliberately leave it for a later day.",
                         paths=["fields.arc_design.arcs", "fields.first_day_plan.events"], related=[scene_id])

    gaps.sort(key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 9), item["section"], item["id"]))
    counts = {severity: sum(1 for item in gaps if item["severity"] == severity)
              for severity in _SEVERITY_ORDER}
    return {
        "version": 1,
        "read_only": True,
        "summary": {
            "total": len(gaps),
            **counts,
            "message": ("The card has no detected structural gaps." if not gaps
                        else f"{len(gaps)} actionable card connection{'s' if len(gaps) != 1 else ''} remain."),
        },
        "gaps": gaps,
        "readiness": readiness,
        "coverage": {
            "cast": {"count": len(cast), "introduced": len(introduced), "unintroduced": unintroduced},
            "scenes": {"day_one_events": len(events)},
            "relationships": {"count": len(relationships), "isolated": isolated},
            "knowledge": {"gated_events": len(gated_events), "with_knowledge": knowledge_events,
                          "with_evidence": evidence_events},
            "arcs": {
                "configured": design is not None,
                "themes": len((design or {}).get("themes") or []) if isinstance(design, Mapping) else 0,
                "threads": len([thread for arc in ((design or {}).get("arcs") or []) if isinstance(arc, Mapping)
                                for thread in (arc.get("character_threads") or []) if isinstance(thread, Mapping)]),
            },
        },
    }
