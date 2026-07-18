"""Closed-world authority for player supernatural actions.

The player is free to *try* or claim anything in prose.  That text is not an
authoring channel: an impossible effect exists only when the story explicitly
gave the player a matching capability.  This module is deliberately small and
deterministic so the same boundary is shared by the director, writer, scribe,
history projection, and state write-back.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping


_WORD = re.compile(r"[a-z][a-z0-9'\-]*", re.I)

# These are deliberately concrete supernatural/action terms, not vague dramatic
# language.  In particular, ``cast a glance`` and ``summon my courage`` remain
# ordinary prose.  The list is a grounding backstop, not a genre classifier.
_POWER_TERMS = (
    "fireball", "spell", "magic", "magical", "sorcery", "wizardry", "witchcraft",
    "telekinesis", "telekinetic", "psychokinesis", "psychic", "mind reading",
    "read minds", "mind control", "superpower", "super power", "pyrokinesis",
    "hydrokinesis", "electrokinesis", "lightning", "laser", "mana", "chakra",
    "teleport", "teleportation", "levitate", "levitation", "shapeshift",
    "shape-shift", "transform into", "turn into a", "fly", "flying",
    "conjure", "manifest", "summon", "invoke", "unleash", "awaken my power",
)
_METAPHOR_PATTERNS = (
    r"\bcast\s+(?:a\s+)?glance\b",
    r"\bsummon\s+(?:my\s+)?courage\b",
    r"\bpower\s+of\s+(?:friendship|love|hope)\b",
    r"\bmagic\s+(?:trick|show)\b",
)
_VISIBLE_WORDS = re.compile(
    r"\b(?:pose|gesture|hand(?:s)?|raise|raised|point|pointed|wave|waved|stance|"
    r"plant(?:ed)?\s+my\s+feet|reach(?:ed)?|arm(?:s)?|palm|fingers?)\b", re.I,
)
_GROUNDED_MARKERS = (
    "nothing happens", "nothing supernatural", "nothing answers", "nothing responds",
    "does not happen", "doesn't happen", "no magic", "no supernatural", "unchanged",
    "empty air", "empty hand", "no effect", "fails to", "fails", "only a gesture",
    "only the gesture", "just a gesture", "just the gesture", "no flame", "no fireball",
)
_POSITIVE_POWER = re.compile(
    r"\b(?:fireball|flame|flames|magic|magical|spell|lightning|laser|telekin(?:esis|etic)|"
    r"psychic|pyrokinesis|superpower|power|mana|chakra)\b.{0,80}\b(?:erupts?|bursts?|"
    r"leaps?|flies?|hits?|strikes?|scorches?|burns?|forms?|appears?|manifests?|answers?|"
    r"works?|takes\s+hold)\b|\b(?:erupts?|bursts?|leaps?|flies?|hits?|strikes?|"
    r"scorches?|burns?|forms?|appears?|manifests?)\b.{0,80}\b(?:fireball|flame|flames|"
    r"magic|spell|lightning|laser)\b",
    re.I,
)
_REALITY_OVERRIDE = re.compile(
    r"(?:^\s*[!/](?:set|edit|retcon|canon)\b|"
    r"\b(?:retcon|rewrite|edit|override|alter|change)\s+(?:the\s+)?(?:story|canon|card|lore|world|facts?)\b|"
    r"\b(?:ooc|out[- ]of[- ]character)\s*[:\-]|"
    r"\b(?:i|we)\s+(?:declare|decree|establish|make\s+it\s+so)\s+(?:that\s+)?(?:the\s+)?(?:canon|story|world)\b)",
    re.I,
)
_GROUNDED_OVERRIDE_MARKERS = (
    "you say", "you tell", "you claim", "you insist", "you assert", "you announce",
    "says", "replies", "answers", "asks", "stares", "frowns", "laughs", "shakes",
    "brushes", "ignores", "doesn't", "does not", "unchanged", "no change", "not true",
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_text(item) for item in value if _text(item)]


def _slug(value: str, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return token or fallback


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(value or "")}


def compile_player_capabilities(source: Mapping[str, Any] | dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize the explicit player-only capability registry.

    The registry deliberately lives outside ``time_system.entity_periods``:
    those capabilities belong to an antagonist/entity and must never become a
    reusable player spell list.  It accepts either a Story ``fields`` mapping
    or a compiled contract (``player_capabilities``).
    """
    source = dict(source or {})
    raw = source.get("player_capabilities")
    if raw is None:
        raw = source.get("player_abilities")
    # Calling this with a whole Story/card is convenient in tests and tooling.
    if raw is None and isinstance(source.get("fields"), Mapping):
        return compile_player_capabilities(source["fields"])
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if isinstance(item, str):
            label = _text(item)
            data: dict[str, Any] = {}
        elif isinstance(item, Mapping):
            data = dict(item)
            label = _text(data.get("name") or data.get("label") or data.get("id"))
        else:
            continue
        if not label:
            continue
        ident = _slug(_text(data.get("id")) or label, f"player-ability-{index + 1}")
        if ident in seen:
            continue
        seen.add(ident)
        aliases = _strings(data.get("aliases") or data.get("keywords"))
        # A label is always an invocation surface.  For e.g. "Candle spark",
        # matching both words also works when the player phrases it naturally.
        surfaces = list(dict.fromkeys([label, ident.replace("-", " "), *aliases]))
        out.append({
            "id": ident,
            "name": label,
            "aliases": aliases,
            "surfaces": surfaces,
            # Descriptions/limits are safe prose for narrator guidance; they
            # do not themselves broaden invocation matching.
            "description": _text(data.get("description") or data.get("narrator_surface")
                                 or data.get("effect")),
            "limits": _text(data.get("limits") or data.get("constraint")),
        })
    return out


def _term_matches(text: str, surface: str) -> bool:
    surface = _text(surface).lower()
    if not surface:
        return False
    text = text.lower()
    if " " in surface:
        if re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", text):
            return True
        terms = _tokens(surface)
        return len(terms) >= 2 and terms <= _tokens(text)
    return bool(re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", text))


def _matched_capabilities(text: str, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [cap for cap in capabilities
            if any(_term_matches(text, surface) for surface in (cap.get("surfaces") or []))]


def _claimed_terms(text: str) -> list[str]:
    low = (text or "").lower()
    if any(re.search(pattern, low) for pattern in _METAPHOR_PATTERNS):
        # Strip only the idiom; an actual second claim in the same action still
        # remains visible to the normal detector below.
        for pattern in _METAPHOR_PATTERNS:
            low = re.sub(pattern, "", low)
    found: list[str] = []
    for term in _POWER_TERMS:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", low):
            found.append(term)
    # "I use my powers to ..." is a claim even when no specific school is named.
    if re.search(r"\b(?:my|the)\s+(?:power|powers|ability|abilities)\b", low):
        found.append("power")
    return list(dict.fromkeys(found))


def _reality_override(text: str) -> str:
    """Return the explicit out-of-world rewrite attempt, if one was made.

    This stays narrow on purpose.  A player can bluff, misremember, persuade,
    or ask for something impossible in-character.  We only intercept text that
    claims authorial control over the card/world itself; character reactions
    remain the right way to handle ordinary dialogue and unreliable claims.
    """
    match = _REALITY_OVERRIDE.search(text or "")
    return match.group(0).strip() if match else ""


def _capability_words(capabilities: list[dict[str, Any]]) -> set[str]:
    words: set[str] = set()
    for cap in capabilities:
        for surface in cap.get("surfaces") or []:
            words |= _tokens(str(surface))
    return words


def assess_player_action(text: str, capabilities: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Classify a player action without interpreting it as fact.

    A blocked action is still playable: it becomes a spoken claim and/or an
    observable physical attempt.  The returned prompt block is intentionally
    usable by both the logical director and the prose narrator.
    """
    text = _text(text)
    capabilities = list(capabilities or [])
    claimed_terms = _claimed_terms(text)
    override = _reality_override(text)
    authorized = _matched_capabilities(text, capabilities)
    allowed_words = _capability_words(authorized)
    # A matching limited ability does not authorize a separate spell in the
    # same sentence ("spark a wick and throw a fireball").
    unearned_terms = [term for term in claimed_terms
                      if term not in allowed_words and term not in {"power", "powers", "ability", "abilities"}]
    power_blocked = bool(claimed_terms) and (not authorized or bool(unearned_terms))
    blocked = power_blocked or bool(override)
    visible = bool(_VISIBLE_WORDS.search(text))
    action_surface = "a visible gesture or attempted pose" if visible else "a spoken claim or ordinary attempt"
    claimed_label = ", ".join(claimed_terms[:4]) or "an unestablished supernatural effect"
    if power_blocked:
        prompt_block = (
            "PLAYER ACTION AUTHORITY — the player has not established the claimed capability "
            f"({claimed_label}). Treat their text only as {action_surface}. Nothing supernatural "
            "or impossible happens because they asserted it; the invented effect does not occur. "
            "Present characters may notice the "
            "words and visible gesture, and react in character (concern, confusion, amusement, "
            "or indifference as fits), but they never witness the invented effect. Do not create "
            "a power, magical object, inventory item, fact, flag, or future permission from this attempt."
        )
        history_text = (
            f"{text} [Grounding: this was only {action_surface}; no supernatural effect occurred.]"
        )
    elif override:
        prompt_block = (
            "PLAYER REALITY AUTHORITY — the player attempted to rewrite canon or issue an out-of-world "
            f"edit ({override}). Treat it only as a spoken claim or attempted assertion; it does not change "
            "the Story card, facts, relationships, inventory, powers, or world rules. Present characters may "
            "correct, ignore, question, laugh at, or brush off what they heard, but never accept it as true merely "
            "because the player stated it. Do not create a fact, flag, item, relationship change, or future permission."
        )
        history_text = f"{text} [Grounding: this was a claim, not a change to canon.]"
    elif authorized:
        guide = []
        for cap in authorized[:2]:
            detail = cap["name"] + (f" — {cap['description']}" if cap.get("description") else "")
            if cap.get("limits"):
                detail += f" Limits: {cap['limits']}"
            guide.append(detail)
        prompt_block = (
            "PLAYER CAPABILITY AUTHORITY — the following player capability is established and may "
            "be honored only at its authored scope; do not add a broader effect, a new spell, or a "
            "new ability: " + "; ".join(guide) + "."
        )
        history_text = text
    else:
        prompt_block = (
            "PLAYER ACTION AUTHORITY — player prose describes choices, speech, and ordinary attempts. "
            "It never creates an unestablished ability, object, fact, or world rule."
        )
        history_text = text
    return {
        "blocked": blocked,
        "claimed_terms": claimed_terms,
        "kind": "unsupported_power" if power_blocked else ("reality_override" if override else "ordinary"),
        "reality_override": override,
        "authorized_capabilities": authorized,
        "prompt_block": prompt_block,
        "history_text": history_text,
        "safe_action": history_text if blocked else text,
        "action_surface": action_surface,
        # Kept deliberately generic: caller/UI copy may describe what the
        # character can see without repeating the invented effect as if real.
        "grounded_action": action_surface,
    }


def breaks_player_reality(output: str, guard: Mapping[str, Any] | None) -> bool:
    """True when a model treated a blocked assertion as a real power effect."""
    if not isinstance(guard, Mapping) or not guard.get("blocked"):
        return False
    text = _text(output).lower()
    if not text:
        return False
    if guard.get("kind") == "reality_override":
        # A safe answer frames the assertion as something the player said or
        # tried, then lets people react.  A bare declarative continuation is
        # not allowed to promote an OOC edit into canon.
        return not any(marker in text for marker in _GROUNDED_OVERRIDE_MARKERS)
    # A positive effect defeats even a token "nothing" elsewhere in the reply.
    if _POSITIVE_POWER.search(text):
        return True
    claimed = [str(term).lower() for term in (guard.get("claimed_terms") or []) if term]
    if any(term in text for term in claimed):
        return not any(marker in text for marker in _GROUNDED_MARKERS)
    # Models sometimes paraphrase "fireball" as "flame" without repeating the
    # exact claim; treat a bare magical result as a violation unless grounded.
    if any(term in text for term in ("magic", "magical", "flame", "flames", "lightning", "teleport")):
        return not any(marker in text for marker in _GROUNDED_MARKERS)
    return False


def retry_instruction(stage: str, candidate: str) -> str:
    # The caller only supplies a candidate today, so keep the generic repair
    # usable for both a fake power and a reality override.
    return (
        f"\n\nREPAIR REQUIRED: Your {stage} treated a player assertion as a change to reality. "
        "Rewrite it so only the spoken claim or visible attempt occurs; do not make an unestablished power "
        "or a canon rewrite real. Keep any character reaction grounded in what they actually saw or heard.\n"
        f"NONCOMPLIANT {stage.upper()}:\n{candidate}"
    )


def fallback_beat(guard: Mapping[str, Any] | None) -> str:
    if (guard or {}).get("kind") == "reality_override":
        return (
            "1. The player makes the claim, but the established situation does not change.\n"
            "2. Anyone present reacts only to what they said or attempted to assert.\n"
            "3. No new fact, relationship, power, item, or world rule is created."
        )
    surface = str((guard or {}).get("action_surface") or "a visible attempt")
    return (
        f"1. The player makes {surface}.\n"
        "2. No supernatural effect occurs; anyone present can only react to the words or gesture.\n"
        "3. The real situation remains in front of them, with no new power or magical fact established."
    )


def fallback_narration(guard: Mapping[str, Any] | None) -> str:
    if (guard or {}).get("kind") == "reality_override":
        return (
            "You make the claim. The established situation does not change; anyone watching can only "
            "react to what you said, not to a new fact you asserted into existence."
        )
    surface = str((guard or {}).get("action_surface") or "the attempt")
    return (
        f"You make {surface}. Nothing answers it. The air and the situation remain unchanged; "
        "anyone watching can only react to what you said or did, not to an impossible effect."
    )


def sanitize_state_deltas(deltas: list[dict[str, Any]] | None,
                          guard: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Drop state writes that could turn a blocked claim into canon.

    Character reactions and a scene log still matter, but a rejected attempt
    cannot create items, facts, flags, details, travel, promises, or an entity
    state.  This is intentionally stronger than keyword filtering: a creative
    scribe must not evade the boundary with a euphemistic flag name.
    """
    copied = [deepcopy(item) for item in (deltas or []) if isinstance(item, dict)]
    if not isinstance(guard, Mapping) or not guard.get("blocked"):
        return copied
    allowed = {"log", "mood", "rel"}
    return [item for item in copied if str(item.get("op") or "") in allowed]
