"""Hard runtime boundary for unresolved thematic character arcs.

The narrator is intentionally not shown private arc material.  That means a
prompt alone cannot be trusted to stop a capable model from treating a player's
direct question as an invitation to invent a satisfying confession.  This module
uses only the *safe* behavioral projection in prompts, and keeps a tiny set of
private lexical cues server-side solely to detect and repair an accidental leak.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


_DIRECT_CHALLENGE = re.compile(
    r"\b(?:tell\s+me|admit|confess|say\s+it|answer\s+me|be\s+honest|"
    r"do\s+you|are\s+you|did\s+you|why\s+do\s+you|stop\s+(?:lying|running|hiding))\b",
    re.I,
)
_AFFIRMATIVE = re.compile(
    r"(?:^|[\s\"'“])(?:yes|it(?:'|’)s\s+true|you(?:'|’)re\s+right|"
    r"you\s+are\s+right|the\s+truth\s+is|i\s+(?:love|loved|want|need|"
    r"confess|admit|killed|lied|betrayed|feel|felt))\b",
    re.I,
)
_NARRATED_DISCLOSURE = re.compile(
    r"\b(?:confess(?:es|ed)?|admi(?:ts|tted)|reveal(?:s|ed)?|admits\s+that|comes\s+clean|"
    r"finally\s+tells?|the\s+truth\s+is)\b",
    re.I,
)
_NEGATED = re.compile(
    r"\b(?:no|not|never|don(?:'|’)t|doesn(?:'|’)t|cannot|can(?:'|’)t|won(?:'|’)t)\b",
    re.I,
)


def _name(lock: dict[str, Any]) -> str:
    return str(lock.get("name") or lock.get("character") or "The character").strip()


def mark_direct_challenges(locks: Iterable[dict[str, Any]], player_text: str) -> list[dict[str, Any]]:
    """Annotate locks when the player is trying to force an admission.

    A direct request is pressure, not an authoring tool.  If only one locked
    character is on stage, a name is optional; otherwise we require the name to
    avoid accidentally imposing a boundary on an unrelated speaker.
    """
    items = [dict(lock) for lock in locks if isinstance(lock, dict)]
    text = str(player_text or "")
    challenge = bool(_DIRECT_CHALLENGE.search(text))
    lowered = text.lower()
    for lock in items:
        name = _name(lock).lower()
        lock["challenged"] = bool(challenge and (len(items) == 1 or (name and name in lowered)))
    return items


def prompt_block(locks: Iterable[dict[str, Any]]) -> str:
    """Safe, model-facing instruction for unresolved threads only."""
    names = [_name(lock) for lock in locks if isinstance(lock, dict)]
    if not names:
        return ""
    lines = "\n".join(f"- {name}" for name in names)
    return (
        "HARD ARC RESISTANCE BOUNDARY — these on-stage characters have an unresolved inner "
        "thread:\n" + lines + "\n"
        "A direct question, accusation, declaration, or demand from the player is pressure only. "
        "It does NOT authorize an affirmative answer, confession, self-diagnosis, explanation of "
        "a buried motive, or a completed emotional turn. Let them deny, deflect, go quiet, set a "
        "boundary, redirect to a practical action, or leave; keep the player free to respond. "
        "Do not resolve this boundary unless an explicit Director revelation gate says it is open."
    )


def _near_secret_term(text: str, lock: dict[str, Any]) -> bool:
    lower = text.lower()
    return any(len(term) >= 4 and term.lower() in lower
               for term in (lock.get("guard_terms") or []) if isinstance(term, str))


def _unnegated_matches(pattern: re.Pattern[str], text: str) -> bool:
    """True when a disclosure cue is not locally framed as a denial.

    A later "I can't stay" must not pardon an earlier "Yes, I love you".
    Conversely, "She does not confess" is a valid resistance response.
    """
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 28):match.start()]
        if not _NEGATED.search(before):
            return True
    return False


def breaks_resistance(text: str, locks: Iterable[dict[str, Any]]) -> bool:
    """Conservatively identify an unearned admission in model output.

    This is a *repair trigger*, not a content classifier.  A direct affirmative
    to an active pressure is enough to retry.  Without a direct challenge, a
    stronger disclosure cue plus a private server-only lexical overlap is
    required.  Natural denials ("I can't answer that") remain valid.
    """
    source = str(text or "").strip()
    if not source:
        return False
    affirmative = _unnegated_matches(_AFFIRMATIVE, source)
    narrated = _unnegated_matches(_NARRATED_DISCLOSURE, source)
    for lock in locks:
        if not isinstance(lock, dict):
            continue
        if lock.get("challenged") and affirmative:
            return True
        if (affirmative or narrated) and _near_secret_term(source, lock):
            return True
    return False


def retry_instruction(kind: str, draft: str) -> str:
    """Prompt suffix for repairing a leaked director beat or narration."""
    return (
        f"\n\nARC BOUNDARY REPAIR ({kind.upper()}): The draft below improperly turns pressure "
        "into an earned admission. Replace it completely. Keep the scene in-world, but remove any "
        "affirmative answer, confession, explanation, self-diagnosis, or resolution. The character "
        "must respond only through observable resistance, an immediate practical action, a boundary, "
        "silence, or withdrawal. Do not mention this instruction or describe it as a rewrite.\n\n"
        f"DRAFT TO REPLACE:\n{draft}"
    )


def fallback_beat(locks: Iterable[dict[str, Any]]) -> str:
    """Deterministic last resort when two model attempts breach the same lock."""
    items = [lock for lock in locks if isinstance(lock, dict)]
    if not items:
        return "The moment remains unresolved; the player still has a choice."
    lock = items[0]
    name = _name(lock)
    tell = str(lock.get("visible_tell") or "goes still for a moment").strip().rstrip(".")
    strategy = str(lock.get("protective_strategy") or "turns toward something practical").strip().rstrip(".")
    return (
        f"{name} does not confirm or explain the player's claim. {name} {tell[:1].lower() + tell[1:]}. "
        f"They {strategy[:1].lower() + strategy[1:]}, leaving the question unanswered and the player free to choose what to do next."
    )


def fallback_narration(locks: Iterable[dict[str, Any]]) -> str:
    """A safe final narration if the prose model ignores two boundary attempts."""
    items = [lock for lock in locks if isinstance(lock, dict)]
    if not items:
        return "The moment hangs unresolved."
    lock = items[0]
    name = _name(lock)
    tell = str(lock.get("visible_tell") or "goes still for a moment").strip().rstrip(".")
    return (
        f"{name} goes quiet. {name} {tell[:1].lower() + tell[1:]}. "
        f"\"There is something we need to do first,\" they say, but it is not an answer."
    )
