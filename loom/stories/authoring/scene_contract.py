"""Public contract for a first-day *scene offer*.

``fields.first_day_plan.events`` feeds the playable scenario catalog.  It is
not a second lorebook: every newly authored item needs to put the player in a
specific place, at a specific time, facing something they can respond to.

The helpers here deliberately inspect public scene fields only.  They neither
read nor interpret hidden actions, evidence, knowledge gates, or entity
periods, so enforcing a useful public shape never widens a model's access to
private story logic.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from .dramatic_kernel import DramaticKernelError, normalize_scene_kernel


_PLAYER_ALIASES = frozenset({"player", "you", "protagonist", "returner"})

# A hook does not have to use one exact template, but it has to give the player
# an action, question, or decision rather than another encyclopedic sentence.
_HOOK_ACTION = re.compile(
    r"\b(?:ask|answer|approach|choose|decide|decline|enter|follow|help|"
    r"inspect|investigate|leave|listen|look|meet|notice|offer|press|read|"
    r"refuse|respond|return|search|speak|stay|take|talk|wait|whether|why)\b",
    re.IGNORECASE,
)

# These are deliberately narrow.  A plaque, rumor, object, or person can make
# history playable; a bare assertion that "the island was founded..." cannot.
_LORE_FACT_START = re.compile(
    r"^\s*(?:"
    r"(?:the\s+)?(?:island|town|village|harbor|harbour|settlement)\s+(?:"
    r"was|were|has\s+been|had\s+been|is\s+known\s+for|used\s+to\s+be|"
    r"once\s+(?:was|served|worshipped)|has\s+(?:a\s+)?(?:long\s+)?(?:history|legend|tradition|custom)|"
    r"holds\s+(?:a\s+)?(?:history|legend|tradition|custom))\b|"
    r"(?:the\s+)?(?:history|legend|tradition|custom)\s+(?:of|says|tells|is)\b|"
    r"(?:long\s+ago|for\s+centuries|once\s*,?)\b"
    r")",
    re.IGNORECASE,
)

# This is the common failure mode that looks like a scene because it mentions
# an opportunity to learn something, while never putting a person, object, or
# specific occurrence on the page.  A real clue can still be a plaque, a
# dockworker, a flower offering, or a warning—this only blocks the abstract
# "hints surface through conversation or environment" placeholder.
_ABSTRACT_HINT_SURFACE = re.compile(
    r"^\s*(?:subtle\s+)?(?:hints?|clues?|signs?)\s+of\b.*"
    r"\b(?:surface|emerge|appear|come\s+to\s+light|are\s+revealed)\b.*"
    r"\b(?:conversation|environment|the\s+island(?:'s)?\s+(?:history|past|mystery))\b",
    re.IGNORECASE,
)


def _text(value: Any, *, limit: int = 1200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _participants(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item, limit=160) for item in value if _text(item, limit=160)]


def ensure_player_participant(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a public event copy whose offer explicitly includes the player.

    An offered scene is entered by the player even when a proposal names only
    the person waiting there.  Make that convention explicit before the card
    is persisted instead of relying on a narrator to infer an off-screen
    participant.  This touches only the public participant list.
    """
    out = dict(event)
    people = _participants(out.get("participants"))
    if not any(person.casefold() in _PLAYER_ALIASES for person in people):
        people.insert(0, "player")
    out["participants"] = list(dict.fromkeys(people))
    return out


def scene_offer_errors(event: Mapping[str, Any]) -> list[str]:
    """Return public-shape errors for a new or materially changed scene.

    The error text intentionally names fields rather than echoing model prose.
    That keeps the validation path safe to use before or after a protected
    scene gains director-only context.
    """
    source = dict(event) if isinstance(event, Mapping) else {}
    errors: list[str] = []
    try:
        source = normalize_scene_kernel(source)
    except DramaticKernelError as exc:
        errors.append(str(exc))
    if not _text(source.get("id"), limit=160):
        errors.append("a stable id")
    if not _text(source.get("when"), limit=160):
        errors.append("a time")
    if not _text(source.get("location"), limit=160):
        errors.append("a location")
    if not _participants(source.get("participants")):
        errors.append("participants")

    visible = _text(source.get("visible"))
    hook = _text(source.get("hook"))
    if not visible:
        errors.append("a visible situation or pressure")
    if not hook:
        errors.append("a player-facing hook")
    elif "[[" in hook or "]]" in hook:
        errors.append("a public player-facing hook")
    elif "?" not in hook and not _HOOK_ACTION.search(hook):
        errors.append("a player-facing choice or action")
    if "[[" in visible or "]]" in visible:
        errors.append("a public visible situation")
    if visible and _LORE_FACT_START.search(visible):
        errors.append("an immediate encounter instead of a standalone lore fact")
    if visible and _ABSTRACT_HINT_SURFACE.search(visible):
        errors.append("a concrete clue or encounter instead of an abstract lore summary")
    return errors


def assert_playable_scene_offer(event: Mapping[str, Any]) -> None:
    """Reject a proposed event that would not be a playable scene offer."""
    errors = scene_offer_errors(event)
    if errors:
        raise ValueError("first-day scene needs " + ", ".join(errors))


def public_scene_surface(event: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the public fields which distinguish one authored scene offer.

    Existing legacy scenes may legitimately predate the hook requirement.  A
    focused edit that preserves that public surface should still be allowed to
    add a private knowledge annotation; an edit that adds or changes the
    surface must meet the current playable-scene contract.
    """
    source = dict(event) if isinstance(event, Mapping) else {}
    try:
        source = normalize_scene_kernel(source)
    except DramaticKernelError:
        # Let the normal validator report the helpful shape error.  A stable
        # fallback here still makes an invalid role/theme edit count as a
        # changed public surface rather than silently preserving it.
        pass
    participants = tuple(_participants(source.get("participants")))
    roles = source.get("roles") if isinstance(source.get("roles"), Mapping) else {}
    return (
        _text(source.get("when"), limit=160),
        _text(source.get("location"), limit=160),
        participants,
        _text(source.get("visible")),
        _text(source.get("hook")),
        _text(source.get("theme"), limit=180),
        _text(source.get("tone"), limit=180),
        tuple(sorted((str(key), _text(value, limit=280)) for key, value in roles.items())),
    )


def validate_changed_scene_offers(
    existing_events: Any,
    proposed_events: Any,
) -> None:
    """Validate only new or public-surface-changed events in a full plan patch.

    This lets a focused Director/interview pass preserve an old scene while it
    edits private annotations, but prevents a conversational model from
    smuggling a new island-history paragraph into the playable scene catalog.
    """
    old_by_id: dict[str, Mapping[str, Any]] = {}
    if isinstance(existing_events, list):
        for raw_event in existing_events:
            if not isinstance(raw_event, Mapping):
                continue
            ident = _text(raw_event.get("id"), limit=160)
            if ident and ident not in old_by_id:
                old_by_id[ident] = raw_event
    if not isinstance(proposed_events, list):
        return
    for raw_event in proposed_events:
        if not isinstance(raw_event, Mapping):
            raise ValueError("first-day event must be an object")
        event = dict(raw_event)
        ident = _text(event.get("id"), limit=160)
        previous = old_by_id.get(ident)
        if previous is not None and public_scene_surface(previous) == public_scene_surface(event):
            continue
        assert_playable_scene_offer(event)
