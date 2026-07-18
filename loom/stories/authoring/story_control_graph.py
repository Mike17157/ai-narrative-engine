"""The model-safe graph behind the Story control room.

The canonical Story card remains the source of truth.  This module projects it
into a small, typed graph for navigation and routing: the client renders this
graph, while the server selects a specialist and validates every proposal.
It intentionally contains no Director prose, hidden scene material, or private
arc answers.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .gap_analysis import analyze_story_card
from ..visibility import strip_model_hidden


@dataclass(frozen=True)
class Specialist:
    """One bounded authoring role and its complete, short instruction."""

    id: str
    label: str
    sections: tuple[str, ...]
    protected: bool
    prompt: str


@dataclass(frozen=True)
class ArchitectWorkOrder:
    """One server-derived hand-off through the Story Architect hierarchy.

    This is an independent, story-domain adaptation of the useful Oh My Pi
    discipline: a planner first observes through a read-only scout, one
    bounded worker proposes a change, and a reviewer gate owns verification.
    It is deliberately metadata rather than an alternate agent runtime.  The
    Story card's existing validation and commit boundaries remain authoritative.
    """

    planner: str
    scout: str
    worker: str
    specialist: str
    scopes: tuple[str, ...]
    mode: str
    protected: bool
    public_only: bool
    reviewer: str = "reviewer"
    maximum_model_calls: int = 1

    def reviewer_gates(self) -> tuple[str, ...]:
        gates = ["scope_boundary", "aggregate_card_validation", "reference_integrity"]
        if self.public_only:
            gates.insert(1, "public_visibility_boundary")
        if self.protected:
            gates.insert(1, "protected_authorization_boundary")
        return tuple(gates)

    def as_dict(self) -> dict[str, Any]:
        """Return the safe, client-visible trace; it contains no card prose."""
        return {
            "version": 1,
            "planner": self.planner,
            "scout": self.scout,
            "worker": self.worker,
            "specialist": self.specialist,
            "scopes": list(self.scopes),
            "mode": self.mode,
            "protected": self.protected,
            "public_only": self.public_only,
            "reviewer": self.reviewer,
            "reviewer_gates": list(self.reviewer_gates()),
            "maximum_model_calls": self.maximum_model_calls,
        }


_KERNEL = (
    "You are a focused story-section specialist. Work from the supplied canonical snapshot, "
    "graph connections, target, and author direction. Strengthen only your assigned section "
    "while preserving established canon and graph references. Return a concise author-facing "
    "summary, a structured proposal in your allowed fields, declared edge changes, and one useful "
    "question only when an author decision remains. Write concrete, playable, readable material."
)

SPECIALISTS: tuple[Specialist, ...] = (
    Specialist("architect", "Architect", (), False, _KERNEL + " Build a bounded plan, order dependencies, and select the next specialist. Present the plan before implementation."),
    Specialist("task", "Story task worker", (), False, _KERNEL + " Carry out one approved multi-section public task in the supplied scopes. Keep the proposal additive, ordered, and compact so the parent Architect can validate it in one commit."),
    Specialist("world", "World steward", ("world",), False, _KERNEL + " Shape setting, atmosphere, history, customs, technology, and declared locations. Place durable facts in their named World section."),
    Specialist("opening", "Opening steward", ("premise",), False, _KERNEL + " Shape the immediate opening image, place, moment, and public situation that begins play."),
    Specialist("cast", "Cast & bonds steward", ("cast",), False, _KERNEL + " Shape named people, their present connections, and relationships that create usable social pressure."),
    Specialist("scenes", "Scene steward", ("first_day",), False, _KERNEL + " Shape flexible possible scenes. Give each new scene a time, location, present people, visible pressure, and player-facing choice."),
    Specialist("arcs", "Arc steward", ("arcs",), True, _KERNEL + " Shape character pressure, limitations, and turning points. Keep private workings in protected arc fields and expose a clear public outline."),
    Specialist("director", "Director steward", ("time_system",), True, _KERNEL + " Shape optional protected causality, knowledge boundaries, evidence, and availability rules only when this story establishes them."),
)

_BY_ID = {item.id: item for item in SPECIALISTS}
_SECTION_SPECIALIST = {section: item.id for item in SPECIALISTS for section in item.sections}
_WORK_ORDER_SECTIONS = frozenset({"world", "premise", "cast", "first_day", "arcs", "time_system"})
_WORK_ORDER_SCOPE_ORDER = ("world", "premise", "cast", "first_day", "arcs", "time_system")
_WORK_ORDER_MODES = frozenset({"develop", "protected_interview", "director_knowledge", "mechanical"})
_MECHANICAL_WORK_ORDER_SECTIONS = frozenset({"world", "first_day", "time_system"})


def specialist_for_section(section: str) -> Specialist:
    """Resolve one canonical editing section to its sole specialist."""
    return _BY_ID[_SECTION_SPECIALIST.get(str(section or ""), "architect")]


def specialist_prompt(specialist_id: str) -> str:
    """Return a complete affirmative prompt under the 500-token budget."""
    specialist = _BY_ID.get(specialist_id, _BY_ID["architect"])
    if len(specialist.prompt.split()) > 500:
        raise ValueError(f"specialist prompt exceeds budget: {specialist.id}")
    return specialist.prompt


def architect_hierarchy() -> dict[str, Any]:
    """Describe the constrained Story equivalent of Oh My Pi's agent roles.

    The roles are declarative so a client can explain a hand-off without
    receiving model prompts, card details, private event text, or opaque agent
    transcripts.  Only ``task`` can propose canonical material, and even it
    never commits directly.
    """
    return {
        "version": 1,
        "pattern": "oh-my-pi",
        "root": "architect",
        "roles": [
            {"id": "architect", "source_role": "plan", "tier": "plan", "read_only": True,
             "spawns": ["explore"], "summary": "Turns scout findings into an author-approved work order."},
            {"id": "explore", "source_role": "explore", "tier": "smol", "read_only": True,
             "spawns": [], "summary": "Reads the safe card projection, readiness, and structural gaps."},
            {"id": "librarian", "source_role": "librarian", "tier": "smol", "read_only": True,
             "spawns": [], "summary": "Supplies source-checked research only when an author requests it."},
            {"id": "oracle", "source_role": "oracle", "tier": "slow", "read_only": True,
             "spawns": ["explore"], "summary": "Handles a tightly scoped protected consultation."},
            {"id": "designer", "source_role": "designer", "tier": "designer", "read_only": True,
             "spawns": [], "summary": "Advises on story presentation and visual hand-offs without changing canon."},
            {"id": "quick_task", "source_role": "quick_task", "tier": "smol", "read_only": True,
             "spawns": [], "summary": "Performs deterministic, evidence-preserving setup normalization."},
            {"id": "task", "source_role": "task", "tier": "task", "read_only": False,
             "spawns": [], "summary": "Makes one bounded proposal for the selected Story specialist."},
            {"id": "reviewer", "source_role": "reviewer", "tier": "slow", "read_only": True,
             "spawns": ["explore"], "summary": "Blocks an unsafe proposal through deterministic validation before commit."},
        ],
        "contract": "plan → explore → one bounded task or protected consultation → reviewer → validated commit",
    }


def _normalize_work_order_scopes(scopes: Iterable[str] | str | None, *, section: str = "") -> tuple[str, ...]:
    raw = [scopes] if isinstance(scopes, str) else list(scopes or ())
    raw.append(section)
    selected = {str(item or "").strip() for item in raw if str(item or "").strip()}
    unknown = selected - _WORK_ORDER_SECTIONS
    if unknown:
        raise ValueError("unknown Story Architect scope: " + ", ".join(sorted(unknown)))
    if not selected:
        raise ValueError("a Story Architect work order needs a canonical section")
    return tuple(scope for scope in _WORK_ORDER_SCOPE_ORDER if scope in selected)


def resolve_architect_work_order(
    scopes: Iterable[str] | str | None = None,
    *,
    section: str = "",
    mode: str = "develop",
    public_only: bool = False,
) -> ArchitectWorkOrder:
    """Resolve the only worker path permitted for an Architect hand-off.

    Callers supply only an already-whitelisted canonical section/mode; they do
    not select an agent.  This prevents a stale UI or arbitrary HTTP payload
    from promoting a public task into the Director or another protected worker.
    """
    clean_mode = str(mode or "develop").strip().lower()
    if clean_mode not in _WORK_ORDER_MODES:
        raise ValueError(f"unknown Story Architect work-order mode: {clean_mode}")
    normalized = _normalize_work_order_scopes(scopes, section=section)
    if clean_mode == "director_knowledge" and normalized != ("first_day",):
        raise ValueError("director knowledge work orders may target only first_day")
    if clean_mode == "mechanical" and not set(normalized).issubset(_MECHANICAL_WORK_ORDER_SECTIONS):
        raise ValueError("mechanical work orders may target only world, first_day, or time_system")
    leaf = specialist_for_section(normalized[0]).id if len(normalized) == 1 else "task"
    protected_mode = clean_mode in {"protected_interview", "director_knowledge"}
    if clean_mode == "mechanical":
        worker, leaf, protected = "quick_task", "quick_task", False
    elif protected_mode:
        if clean_mode == "director_knowledge":
            leaf = "director"
        worker, protected = "oracle", True
    else:
        # Explicit batch development remains the existing author-authorized
        # task boundary.  Only a protected Architect mode advertises or
        # receives the Oracle authorization gate.
        worker, protected = "task", False
    return ArchitectWorkOrder(
        planner="architect",
        scout="explore",
        worker=worker,
        specialist=leaf,
        scopes=normalized,
        mode=clean_mode,
        protected=protected,
        public_only=bool(public_only),
    )


def matches_architect_work_order(value: Any, expected: ArchitectWorkOrder) -> bool:
    """Whether a forwarded work order is exactly the one this server derives."""
    return isinstance(value, Mapping) and dict(value) == expected.as_dict()


def work_order_prompt(order: ArchitectWorkOrder) -> str:
    """Give a worker the OMP-style Goal/Constraints/Contract hand-off.

    The prompt is generated from the trusted work order, never from a client
    supplied worker identifier.  It supplements the existing specialist prompt
    and does not grant any mutation authority beyond the API's validators.
    """
    scopes = ", ".join(order.scopes)
    visibility = (
        "Only public, non-secret connective material is eligible for this pass."
        if order.public_only else
        "Preserve all established canon and make only additive material in the authorized scopes."
    )
    return (
        specialist_prompt(order.specialist)
        + "\n\nAUTHORIZED STORY WORK ORDER\n"
        + f"GOAL: Complete one bounded {order.specialist} proposal for: {scopes}.\n"
        + f"CONSTRAINTS: {visibility}\n"
        + "CONTRACT: Return the existing strict structured proposal and leave every unrequested field empty.\n"
        + "TARGET: The supplied public-safe Story card projection.\n"
        + "CHANGE: Add only missing connective material; preserve names, references, and authored order.\n"
        + "ACCEPTANCE: The parent Architect will require scope, visibility, aggregate-card, and reference validation before commit."
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 220) -> str:
    if not isinstance(value, str):
        return ""
    # The graph is a model-safe/public navigation projection.  Redact before
    # truncating so an unmatched hidden opener fails closed too.
    return (strip_model_hidden(value) or "").strip()[:limit]


def _revision(projection: Mapping[str, Any]) -> str:
    """Fingerprint only the already-safe graph projection.

    Hashing the raw Story card would make an otherwise public revision value
    confirm a private edit.  The graph itself is the client's cache contract,
    so it is also the correct revision source.
    """
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _node(*, ident: str, label: str, section: str, summary: str, established: bool,
          gaps: list[dict[str, Any]], protected: bool = False, infrastructure: bool = False) -> dict[str, Any]:
    related = [gap for gap in gaps if gap.get("section") == section]
    status = "needs_author" if related else ("ready" if established else "empty")
    specialist = specialist_for_section(section)
    return {
        "id": ident, "label": label, "section": section, "summary": summary,
        "status": status, "protected": protected, "infrastructure": infrastructure,
        "specialist": None if infrastructure else specialist.id,
        "target": {"section": section},
        "gap_count": len(related),
    }


def build_story_control_graph(card: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project the canonical card into safe navigation nodes and public edges."""
    source = _mapping(card)
    fields = _mapping(source.get("fields"))
    world = _mapping(source.get("world"))
    plan = _mapping(fields.get("first_day_plan"))
    events = [item for item in (plan.get("events") or []) if isinstance(item, Mapping)]
    report = analyze_story_card(source)
    gaps = [dict(item) for item in (report.get("gaps") or []) if isinstance(item, Mapping)]
    locations = [item for item in (source.get("locations") or []) if isinstance(item, Mapping)]
    cast = [item for item in (source.get("cast") or []) if isinstance(item, Mapping)]
    arc_outline = _mapping(fields.get("arc_outline"))
    raw_premise = source.get("premise") if isinstance(source.get("premise"), str) else ""
    public_premise = strip_model_hidden(raw_premise) or ""
    has_director = bool(_mapping(world.get("entity")) or _mapping(world.get("loop"))
                        or any(bool(item.get("hidden") or item.get("knowledge") or item.get("evidence")) for item in events))

    nodes = [
        _node(ident="world", label="World", section="world", established=bool(world or locations), gaps=gaps,
              summary=f"{len(locations)} place{'s' if len(locations) != 1 else ''} and named world sections."),
        _node(ident="opening", label="Opening", section="premise", established=bool(public_premise), gaps=gaps,
              summary=_text(public_premise) or "The first public image and immediate situation."),
        _node(ident="cast", label="Cast & bonds", section="cast", established=bool(cast), gaps=gaps,
              summary=f"{len(cast)} named person{'s' if len(cast) != 1 else ''} on the card."),
        _node(ident="scenes", label="Possible scenes", section="first_day", established=bool(events), gaps=gaps,
              summary=f"{len(events)} player-enterable scene offer{'s' if len(events) != 1 else ''}."),
        _node(ident="arcs", label="Arcs", section="arcs", established=bool(arc_outline or fields.get("arc_design")), gaps=gaps,
              protected=True, summary="Character pressure and public thematic direction."),
        _node(ident="clock", label="Story clock", section="first_day", established=bool(plan.get("opening_time") or any(item.get("when") for item in events)), gaps=[],
              infrastructure=True, summary="A shared scene-ordering and availability axis."),
    ]
    if has_director:
        nodes.append(_node(ident="director", label="Director mechanics", section="time_system", established=True, gaps=gaps,
                           protected=True, summary="Optional protected causality and knowledge boundaries."))

    edges: list[dict[str, str]] = [
        {"source": "world", "target": "opening", "kind": "grounds"},
        {"source": "opening", "target": "scenes", "kind": "begins"},
        {"source": "cast", "target": "scenes", "kind": "appears_in"},
        {"source": "scenes", "target": "arcs", "kind": "pressures"},
        {"source": "clock", "target": "scenes", "kind": "orders"},
    ]
    if has_director:
        edges.extend([
            {"source": "director", "target": "scenes", "kind": "gates"},
            {"source": "director", "target": "arcs", "kind": "protects"},
        ])
    projection = {
        "version": 1,
        "nodes": nodes,
        "edges": edges,
        "agent_hierarchy": architect_hierarchy(),
        "summary": "The Story Control Graph routes each selected node to its bounded specialist.",
    }
    return {"revision": _revision(projection), **projection}
