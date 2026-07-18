"""Canonical, privacy-aware design documents for thematic character arcs.

The story card has two very different readers:

* the author/director needs to know the contradiction a character cannot yet
  articulate, and the possible turn that could eventually expose it;
* the narrator needs only the character's sincere belief, defensive habit,
  visible tell, and pressure that is presently on stage.

This module is the narrow boundary between those jobs. It normalizes the
author document once, derives a deliberately thin narrator-safe outline, a
compact author-card outline, and an even thinner runtime surface. Do not add
private fields to a model-facing projection: a language model that can see a
secret will eventually narrate it.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Mapping

from .dramatic_kernel import _person_key


class ArcDesignValidationError(ValueError):
    """The shape cannot safely be made into an arc-design document."""


_TURN_KINDS = {"pressure", "reversal", "revelation", "choice", "aftermath"}


def _mapping(value: Any, *, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ArcDesignValidationError(f"{path} must be an object")
    return dict(value)


def _list(value: Any, *, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ArcDesignValidationError(f"{path} must be a list")
    return value


def _text(value: Any, *, path: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ArcDesignValidationError(f"{path} must be text")
    value = value.strip()
    if required and not value:
        raise ArcDesignValidationError(f"{path} must not be empty")
    return value


def _texts(value: Any, *, path: str) -> list[str]:
    out: list[str] = []
    for index, item in enumerate(_list(value, path=path)):
        text = _text(item, path=f"{path}[{index}]")
        if text:
            out.append(text)
    return out


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _unique_id(value: str, *, fallback: str, seen: set[str]) -> str:
    base = _slug(value, fallback=fallback)
    candidate = base
    count = 2
    while candidate in seen:
        candidate = f"{base}-{count}"
        count += 1
    seen.add(candidate)
    return candidate


def _requirements(value: Any, *, path: str) -> dict[str, Any]:
    raw = _mapping(value, path=path)
    flags = raw.get("flags")
    if flags is None:
        return {}
    if not isinstance(flags, Mapping):
        raise ArcDesignValidationError(f"{path}.flags must be an object")
    return {"flags": deepcopy(dict(flags))}


def _pressure_point(raw: Any, *, index: int, seen: set[str], path: str) -> dict[str, Any]:
    point = _mapping(raw, path=path)
    public = _text(point.get("public_pressure") or point.get("pressure"), path=f"{path}.public_pressure")
    return {
        "id": _unique_id(_text(point.get("id"), path=f"{path}.id") or public,
                         fallback=f"pressure-{index + 1}", seen=seen),
        "scene_id": _text(point.get("scene_id"), path=f"{path}.scene_id"),
        "when": _text(point.get("when"), path=f"{path}.when"),
        "public_pressure": public,
        "requires": _requirements(point.get("requires"), path=f"{path}.requires"),
    }


def _turning_point(raw: Any, *, index: int, seen: set[str], path: str) -> dict[str, Any]:
    point = _mapping(raw, path=path)
    kind = _text(point.get("kind"), path=f"{path}.kind") or "pressure"
    if kind not in _TURN_KINDS:
        raise ArcDesignValidationError(
            f"{path}.kind must be one of: {', '.join(sorted(_TURN_KINDS))}"
        )
    public = _text(point.get("public_surface") or point.get("surface"), path=f"{path}.public_surface")
    return {
        "id": _unique_id(_text(point.get("id"), path=f"{path}.id") or public or kind,
                         fallback=f"turn-{index + 1}", seen=seen),
        "kind": kind,
        "scene_id": _text(point.get("scene_id"), path=f"{path}.scene_id"),
        "when": _text(point.get("when"), path=f"{path}.when"),
        # Explicitly public: this is the only turning-point description the
        # live narrator is ever allowed to receive.
        "public_surface": public,
        # Private fields remain in the canonical author document but are never
        # included in arc_public_outline() or narrator_arc_surface().
        "private_pressure": _text(point.get("private_pressure"), path=f"{path}.private_pressure"),
        "revelation": _text(point.get("revelation"), path=f"{path}.revelation"),
        "changes": _text(point.get("changes"), path=f"{path}.changes"),
        # This is a public targeting gate, not a private thought.  It stops a
        # shared arc's ferry-bell beat from being injected into every person
        # who happens to have a thread under that theme.
        "characters": _texts(point.get("characters"), path=f"{path}.characters"),
        "requires": _requirements(point.get("requires"), path=f"{path}.requires"),
    }


def _thread(raw: Any, *, index: int, parent_owner: str, path: str) -> dict[str, Any]:
    thread = _mapping(raw, path=path)
    pressure_seen: set[str] = set()
    thread_id = _text(thread.get("id"), path=f"{path}.id")
    character = _text(thread.get("character") or thread.get("owner") or parent_owner,
                      path=f"{path}.character")
    return {
        "id": _slug(thread_id or character or f"thread-{index + 1}",
                    fallback=f"thread-{index + 1}"),
        "character": character,
        "want": _text(thread.get("want"), path=f"{path}.want"),
        "protective_strategy": _text(thread.get("protective_strategy") or thread.get("protection"),
                                      path=f"{path}.protective_strategy"),
        "blind_spot": _text(thread.get("blind_spot") or thread.get("starting_belief"),
                               path=f"{path}.blind_spot"),
        "unacknowledged_need": _text(thread.get("unacknowledged_need") or thread.get("need"),
                                      path=f"{path}.unacknowledged_need"),
        "limitation": _text(thread.get("limitation"), path=f"{path}.limitation"),
        "visible_tell": _text(thread.get("visible_tell") or thread.get("tell"), path=f"{path}.visible_tell"),
        "pressure_points": [
            _pressure_point(item, index=point_index, seen=pressure_seen,
                            path=f"{path}.pressure_points[{point_index}]")
            for point_index, item in enumerate(_list(thread.get("pressure_points"), path=f"{path}.pressure_points"))
        ],
        "recognition": _text(thread.get("recognition"), path=f"{path}.recognition"),
        "possible_outcomes": _texts(thread.get("possible_outcomes"), path=f"{path}.possible_outcomes"),
        "relationship_targets": _texts(thread.get("relationship_targets") or thread.get("relationships"),
                                        path=f"{path}.relationship_targets"),
    }


def normalize_arc_design(raw: Any, *, card: Any | None = None) -> dict[str, Any]:
    """Return a complete canonical design without exposing it to the narrator.

    This accepts incomplete author drafts: semantic incompleteness is reported
    by :func:`arc_design_issues` in the Director UI instead of making a useful
    early interview turn impossible to save.  Invalid container/type shapes
    still fail closed at the mutation boundary.
    """
    design = _mapping(raw, path="arc_design")
    version = design.get("version", 1)
    if version != 1:
        raise ArcDesignValidationError("arc_design.version must be 1")

    themes: list[dict[str, str]] = []
    theme_seen: set[str] = set()
    for index, item in enumerate(_list(design.get("themes"), path="arc_design.themes")):
        theme = _mapping(item, path=f"arc_design.themes[{index}]")
        label = _text(theme.get("label") or theme.get("name") or theme.get("theme"),
                      path=f"arc_design.themes[{index}].label")
        themes.append({
            "id": _unique_id(_text(theme.get("id"), path=f"arc_design.themes[{index}].id") or label,
                             fallback=f"theme-{index + 1}", seen=theme_seen),
            "label": label,
            "question": _text(theme.get("question") or theme.get("dramatic_question"),
                              path=f"arc_design.themes[{index}].question"),
            "pressure": _text(theme.get("pressure"), path=f"arc_design.themes[{index}].pressure"),
        })

    arcs: list[dict[str, Any]] = []
    arc_seen: set[str] = set()
    for index, item in enumerate(_list(design.get("arcs"), path="arc_design.arcs")):
        arc = _mapping(item, path=f"arc_design.arcs[{index}]")
        title = _text(arc.get("title") or arc.get("name"), path=f"arc_design.arcs[{index}].title")
        owner = _text(arc.get("owner") or arc.get("character"), path=f"arc_design.arcs[{index}].owner")
        turning_seen: set[str] = set()
        threads = [
            _thread(thread, index=thread_index, parent_owner=owner,
                    path=f"arc_design.arcs[{index}].character_threads[{thread_index}]")
            for thread_index, thread in enumerate(
                _list(arc.get("character_threads") or arc.get("threads"),
                      path=f"arc_design.arcs[{index}].character_threads")
            )
        ]
        arcs.append({
            "id": _unique_id(_text(arc.get("id"), path=f"arc_design.arcs[{index}].id") or title or owner,
                             fallback=f"arc-{index + 1}", seen=arc_seen),
            "title": title,
            "theme_id": _text(arc.get("theme_id"), path=f"arc_design.arcs[{index}].theme_id"),
            "owner": owner,
            "dramatic_question": _text(arc.get("dramatic_question") or arc.get("question"),
                                        path=f"arc_design.arcs[{index}].dramatic_question"),
            "starting_belief": _text(arc.get("starting_belief") or arc.get("belief"),
                                      path=f"arc_design.arcs[{index}].starting_belief"),
            "truth": _text(arc.get("truth"), path=f"arc_design.arcs[{index}].truth"),
            "stakes": _text(arc.get("stakes"), path=f"arc_design.arcs[{index}].stakes"),
            "turning_points": [
                _turning_point(point, index=point_index, seen=turning_seen,
                               path=f"arc_design.arcs[{index}].turning_points[{point_index}]")
                for point_index, point in enumerate(
                    _list(arc.get("turning_points"), path=f"arc_design.arcs[{index}].turning_points")
                )
            ],
            "character_threads": threads,
        })

    return {"version": 1, "themes": themes, "arcs": arcs}


def _cast_keys(card: Any | None) -> set[str]:
    if card is None:
        return set()
    if hasattr(card, "model_dump"):
        card = card.model_dump()
    if not isinstance(card, Mapping):
        return set()
    keys: set[str] = set()
    for member in card.get("cast") or []:
        if isinstance(member, str) and member.strip():
            keys.add(member.strip())
        elif isinstance(member, Mapping):
            for value in (member.get("character"), member.get("key")):
                if isinstance(value, str) and value.strip():
                    keys.add(value.strip())
                    break
    return keys


def protagonist_arc(raw: Any) -> dict[str, Any] | None:
    """Return the arc owned by the protagonist ('player'/'you'/'protagonist'/'returner'),
    or None. An arc is inherently about one person; the protagonist's own arc is the one
    every other character's arc, every scene, and the macro play-pacing arc relates back to
    as a facet or contrast — never a free-floating theme invented independently of anyone's
    actual life."""
    try:
        design = normalize_arc_design(raw)
    except ArcDesignValidationError:
        return None
    for arc in design["arcs"]:
        owner = arc.get("owner") or ""
        if owner and _person_key(owner, label="owner") == "player":
            return arc
    return None


def _issue(code: str, severity: str, path: str, message: str, fix: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "path": path, "message": message, "fix": fix}


def arc_design_issues(raw: Any, *, card: Any | None = None) -> list[dict[str, str]]:
    """Return Director-facing completeness diagnostics for a valid arc draft."""
    try:
        design = normalize_arc_design(raw, card=card)
    except ArcDesignValidationError as exc:
        return [_issue("arc_design_invalid", "error", "fields.arc_design", str(exc),
                       "Use the canonical theme-and-arc document.")]
    issues: list[dict[str, str]] = []
    themes = {item["id"] for item in design["themes"]}
    cast = _cast_keys(card)
    if not themes:
        issues.append(_issue("theme_missing", "warning", "fields.arc_design.themes",
                             "No thematic question has been written yet.",
                             "Add one theme with a human question the story can pressure."))
    if not design["arcs"]:
        issues.append(_issue("character_arcs_missing", "warning", "fields.arc_design.arcs",
                             "No character pressure has been authored yet.",
                             "Give one named person a blind spot, limitation, and possible turn."))
    elif cast and protagonist_arc(raw) is None:
        issues.append(_issue("protagonist_arc_missing", "warning", "fields.arc_design.arcs",
                             "No arc is owned by the protagonist yet — every other arc is "
                             "currently about someone else.",
                             "Give the protagonist their own ordinary backstory and arc first; "
                             "everything else relates back to it."))
    seen_threads: set[str] = set()
    for arc_index, arc in enumerate(design["arcs"]):
        prefix = f"fields.arc_design.arcs[{arc_index}]"
        if arc["theme_id"] and arc["theme_id"] not in themes:
            issues.append(_issue("arc_theme_unknown", "error", f"{prefix}.theme_id",
                                 f"Arc '{arc['title'] or arc['id']}' names an unknown theme id '{arc['theme_id']}'.",
                                 "Reference an id from fields.arc_design.themes."))
        if not arc["theme_id"]:
            issues.append(_issue("arc_theme_missing", "warning", f"{prefix}.theme_id",
                                 f"Arc '{arc['title'] or arc['id']}' is not tied to a theme yet.",
                                 "Attach the arc to the theme it tests."))
        if not arc["turning_points"]:
            issues.append(_issue("arc_turn_missing", "warning", f"{prefix}.turning_points",
                                 f"Arc '{arc['title'] or arc['id']}' has no possible pressure or turn.",
                                 "Add an optional scene/time pressure point; do not make it mandatory."))
        for thread_index, thread in enumerate(arc["character_threads"]):
            thread_path = f"{prefix}.character_threads[{thread_index}]"
            character = thread["character"]
            if not character:
                issues.append(_issue("arc_thread_character_missing", "error", f"{thread_path}.character",
                                     "A character thread needs a named cast key.",
                                     "Choose a character already on the Story card."))
            elif cast and character not in cast:
                issues.append(_issue("arc_thread_character_unknown", "error", f"{thread_path}.character",
                                     f"Character thread '{character}' is not in the story cast.",
                                     "Use an existing cast key or add the person to the cast first."))
            elif character in seen_threads:
                issues.append(_issue("arc_thread_duplicate", "warning", f"{thread_path}.character",
                                     f"{character} has more than one thread in this arc design.",
                                     "Combine overlapping pressures unless the distinction is intentional."))
            elif character:
                seen_threads.add(character)
            missing = [field for field in ("blind_spot", "protective_strategy", "limitation", "visible_tell")
                       if not thread[field]]
            if missing:
                issues.append(_issue("arc_thread_incomplete", "warning", thread_path,
                                     f"{character or 'This character'} still needs: {', '.join(missing)}.",
                                     "Write the outward pattern that lets the character resist easy resolution."))
            if not thread["pressure_points"]:
                issues.append(_issue("arc_thread_pressure_missing", "warning", f"{thread_path}.pressure_points",
                                     f"{character or 'This character'} has no scene pressure yet.",
                                     "Attach a public pressure to a possible scene or time window."))
    return issues


def arc_public_outline(raw: Any) -> dict[str, Any]:
    """Derive a structural public-card summary without arc content.

    Arc titles, dramatic questions, and a theme's freeform question are
    author/model text. They can accidentally state the answer to a private
    character problem, so this projection exposes only the safe topology of
    which cast member is connected to which public theme.
    """
    try:
        design = normalize_arc_design(raw)
    except ArcDesignValidationError:
        return {}
    themes = [
        {key: item[key] for key in ("id", "label") if item.get(key)}
        for item in design["themes"]
    ]
    theme_labels = {item["id"]: item["label"] for item in design["themes"]}
    arcs = []
    for arc in design["arcs"]:
        arcs.append({
            "owner": arc["owner"],
            "theme_id": arc["theme_id"],
            "theme": theme_labels.get(arc["theme_id"], ""),
        })
    return {"themes": themes, "arcs": arcs}


def author_arc_outline(raw: Any) -> dict[str, Any]:
    """Derive the compact storyline material an author needs on the card.

    This is intentionally richer than :func:`arc_public_outline`, which is
    suitable for generic readers and narrators.  It lets the author identify a
    storyline by title, its theme and thematic question, and the specific
    dramatic question being tested.  It still omits every private pressure:
    truths, beliefs, character threads, turn details, revelations, and
    recognition are never copied here.
    """
    try:
        design = normalize_arc_design(raw)
    except ArcDesignValidationError:
        return {}

    themes = {
        item["id"]: {
            key: item[key]
            for key in ("id", "label", "question")
            if item.get(key)
        }
        for item in design["themes"]
        if item.get("id")
    }
    arcs: list[dict[str, Any]] = []
    for arc in design["arcs"]:
        theme_id = arc.get("theme_id", "")
        theme = themes.get(theme_id, {"id": theme_id} if theme_id else {})
        arcs.append({
            "id": arc["id"],
            "title": arc["title"],
            "owner": arc["owner"],
            "theme": deepcopy(theme),
            "dramatic_question": arc["dramatic_question"],
        })
    return {"arcs": arcs}


def _gate_open(requirements: Mapping[str, Any], flags: Mapping[str, Any]) -> bool:
    expected = requirements.get("flags") if isinstance(requirements, Mapping) else None
    if not isinstance(expected, Mapping):
        return True
    for key, desired in expected.items():
        actual = flags.get(str(key))
        if isinstance(desired, (list, tuple, set)):
            if actual not in desired:
                return False
        elif actual != desired:
            return False
    return True


def _matches_window(item: Mapping[str, Any], *, scene_id: str, slot: str,
                    flags: Mapping[str, Any]) -> bool:
    required_scene = str(item.get("scene_id") or "").strip()
    required_slot = str(item.get("when") or "").strip()
    if required_scene and required_scene != scene_id:
        return False
    if required_slot and required_slot != slot:
        return False
    return _gate_open(item.get("requires") or {}, flags)


def narrator_arc_surface(raw: Any, *, scene_id: str = "", slot: str = "",
                         present: Iterable[str] = (), flags: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return only the behavioral pressure a narrator may use *this* turn.

    Deliberately absent: ``truth``, ``unacknowledged_need``, ``recognition``,
    ``private_pressure``, turning-point ``revelation``, and ``changes``.  Even
    a revelation beat is represented merely as an *available public pressure*;
    it never compels a confession or resolves an arc automatically.
    """
    try:
        design = normalize_arc_design(raw)
    except ArcDesignValidationError:
        return []
    present_keys = {str(item) for item in present if str(item)}
    if not present_keys:
        return []
    current_flags = dict(flags or {})
    out: list[dict[str, Any]] = []
    for arc in design["arcs"]:
        turns = [
            point for point in arc["turning_points"]
            if _matches_window(point, scene_id=scene_id, slot=slot, flags=current_flags)
            and point["public_surface"]
        ]
        for thread in arc["character_threads"]:
            if thread["character"] not in present_keys:
                continue
            pressure = [
                point["public_pressure"] for point in thread["pressure_points"]
                if _matches_window(point, scene_id=scene_id, slot=slot, flags=current_flags)
                and point["public_pressure"]
            ]
            thread_turns = [
                point for point in turns
                if not point.get("characters") or thread["character"] in point["characters"]
            ]
            out.append({
                "character": thread["character"],
                # A blind spot is a director diagnosis: it can contain the
                # very truth the character is not ready to name.  Do not use
                # it as a convenient fallback here.  Only the separately
                # authored, sincerely held *surface* belief is safe for a
                # narrator to voice or act through.
                "starting_belief": arc["starting_belief"],
                "protective_strategy": thread["protective_strategy"],
                "limitation": thread["limitation"],
                "visible_tell": thread["visible_tell"],
                "public_pressure": pressure,
                "possible_public_turns": [
                    {"kind": point["kind"], "surface": point["public_surface"]}
                    for point in thread_turns
                ],
            })
    return out


_PRIVATE_GUARD_STOPWORDS = {
    "about", "after", "again", "author", "because", "before", "being", "character",
    "could", "does", "from", "have", "into", "just", "more", "never", "only",
    "private", "secret", "should", "that", "their", "there", "they", "this", "when",
    "with", "would", "your",
}


def _private_guard_terms(*values: str) -> list[str]:
    """Return internal-only lexical cues for a post-generation safety check.

    These terms deliberately never enter a public outline, the narrator surface,
    or a model prompt.  They let the runtime spot an accidental direct disclosure
    without teaching the narrator what the disclosure is.
    """
    out: list[str] = []
    for value in values:
        for word in re.findall(r"[a-zA-Z]{4,}", value.lower()):
            if word not in _PRIVATE_GUARD_STOPWORDS and word not in out:
                out.append(word)
    return out[:18]


def active_arc_resistance_locks(raw: Any, *, scene_id: str = "", slot: str = "",
                                present: Iterable[str] = (),
                                flags: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return internal runtime locks for present characters with unresolved arcs.

    A matching ``pressure`` or ``choice`` makes a dramatic moment available; it
    is *not* permission to reveal the thing a character cannot yet name.  A
    disclosure opens only when an author has supplied a ``revelation`` point
    with a non-empty flag requirement and the Director/runtime has satisfied
    that requirement.  This preserves player agency (they can press, leave,
    persuade, or change the situation) without letting a direct question turn
    a possible arc into a compulsory confession.

    ``guard_terms`` are private server-only detector cues.  Callers must never
    serialize them into model context or a player-facing response.
    """
    try:
        design = normalize_arc_design(raw)
    except ArcDesignValidationError:
        return []
    present_keys = {str(item) for item in present if str(item)}
    if not present_keys:
        return []
    current_flags = dict(flags or {})
    locks: list[dict[str, Any]] = []
    for arc in design["arcs"]:
        for thread in arc["character_threads"]:
            character = thread["character"]
            if character not in present_keys:
                continue
            # An ungated revelation is a *possible* authored scene beat, not
            # an automatic outcome.  The Director must deliberately set the
            # named condition flag before this private boundary opens.
            reveal_open = any(
                point["kind"] == "revelation"
                and bool((point.get("requires") or {}).get("flags"))
                and _matches_window(point, scene_id=scene_id, slot=slot, flags=current_flags)
                and (not point.get("characters") or character in point["characters"])
                for point in arc["turning_points"]
            )
            private_material = (
                arc["truth"], thread["blind_spot"], thread["unacknowledged_need"],
                thread["recognition"],
            )
            if reveal_open or not any(private_material):
                continue
            locks.append({
                "character": character,
                "protective_strategy": thread["protective_strategy"],
                "limitation": thread["limitation"],
                "visible_tell": thread["visible_tell"],
                # This never becomes narrator-facing text.  It is strictly a
                # lexical last-resort detector if a model ignores the prompt.
                "guard_terms": _private_guard_terms(*private_material),
            })
    return locks
