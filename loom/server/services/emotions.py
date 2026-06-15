"""Canonical emotion taxonomy — a FIXED, comprehensive range driving the sprite set.

Grounded in **Plutchik's wheel of emotions**: 8 primary emotions at 3 intensities each (24
graded states), plus the most useful dyads / social emotions (~8). A fixed vocabulary with
STABLE keys means a character's emotion sprites are consistent across outfits and the runtime
director can switch sprite by an exact emotion key (or snap a detected affect to the nearest
canonical one). This replaces the old variable per-outfit `DEFAULT_EMOTIONS` set.

Each entry: `key` (stable slug, used for filenames + lookup), `label` (display), `hint` (a few
face cues steering the per-character expression-prompt generation — see
AppContext.compose_expressions).
"""
from __future__ import annotations

import re

EMOTIONS: list[dict] = [
    # -- Plutchik primaries × 3 intensities (24) -----------------------------
    {"key": "serenity", "label": "Serenity", "hint": "soft relaxed smile, calm eyes, gentle"},
    {"key": "joy", "label": "Joy", "hint": "happy, smile, bright eyes"},
    {"key": "ecstasy", "label": "Ecstasy", "hint": "wide open-mouth smile, sparkling eyes, sheer delight"},
    {"key": "acceptance", "label": "Acceptance", "hint": "warm soft eyes, faint smile, content"},
    {"key": "trust", "label": "Trust", "hint": "open relaxed expression, soft gaze, gentle smile"},
    {"key": "admiration", "label": "Admiration", "hint": "wide adoring eyes, slight blush, parted lips"},
    {"key": "apprehension", "label": "Apprehension", "hint": "slightly worried eyes, tense brow, small frown"},
    {"key": "fear", "label": "Fear", "hint": "wide scared eyes, raised eyebrows, open mouth, sweatdrop"},
    {"key": "terror", "label": "Terror", "hint": "terrified wide eyes, trembling, pale, tears, screaming"},
    {"key": "distraction", "label": "Distraction", "hint": "unfocused eyes, slightly raised brow, mild surprise"},
    {"key": "surprise", "label": "Surprise", "hint": "raised eyebrows, wide eyes, open mouth, surprised"},
    {"key": "amazement", "label": "Amazement", "hint": "shocked wide eyes, dropped jaw, surprised"},
    {"key": "pensiveness", "label": "Pensiveness", "hint": "downcast eyes, faint frown, wistful"},
    {"key": "sadness", "label": "Sadness", "hint": "sad, frown, teary eyes, lowered brows"},
    {"key": "grief", "label": "Grief", "hint": "crying, streaming tears, anguished, clenched eyes"},
    {"key": "boredom", "label": "Boredom", "hint": "half-closed eyes, flat mouth, unamused, bored"},
    {"key": "disgust", "label": "Disgust", "hint": "wrinkled nose, narrowed eyes, grimace, sneer"},
    {"key": "loathing", "label": "Loathing", "hint": "deep scowl, bared teeth, intense revulsion"},
    {"key": "annoyance", "label": "Annoyance", "hint": "slight frown, furrowed brow, sidelong glance, pout"},
    {"key": "anger", "label": "Anger", "hint": "angry, furrowed brow, gritted teeth, glaring"},
    {"key": "rage", "label": "Rage", "hint": "furious, bared teeth, blazing eyes, shouting, veins"},
    {"key": "interest", "label": "Interest", "hint": "curious raised brow, attentive eyes, faint smile"},
    {"key": "anticipation", "label": "Anticipation", "hint": "eager eyes, slight smile, leaning in"},
    {"key": "vigilance", "label": "Vigilance", "hint": "intense focused stare, narrowed alert eyes, serious"},
    # -- dyads / social emotions (8) -----------------------------------------
    {"key": "love", "label": "Love", "hint": "soft loving eyes, blush, tender smile, heart"},
    {"key": "desire", "label": "Desire (enticed)", "hint": "half-lidded seductive eyes, blush, parted lips, sultry smile"},
    {"key": "greed", "label": "Greed", "hint": "covetous grin, gleaming wide eyes, drooling, scheming"},
    {"key": "optimism", "label": "Optimism", "hint": "hopeful bright eyes, confident smile, lifted brows"},
    {"key": "remorse", "label": "Remorse", "hint": "downturned guilty eyes, frown, teary, looking away"},
    {"key": "contempt", "label": "Contempt", "hint": "smug half-lidded eyes, one raised brow, smirk, looking down"},
    {"key": "awe", "label": "Awe", "hint": "wide wonderstruck eyes, parted lips, sparkles"},
    {"key": "disappointment", "label": "Disappointment", "hint": "lowered gaze, slight frown, sigh, deflated"},
]

EMOTION_KEYS: list[str] = [e["key"] for e in EMOTIONS]
EMOTION_LABELS: dict[str, str] = {e["key"]: e["label"] for e in EMOTIONS}
EMOTION_HINTS: dict[str, str] = {e["key"]: e["hint"] for e in EMOTIONS}


def slug(name: str) -> str:
    """Normalize an emotion name to its stable key form (matches the manifest/file convention)."""
    return re.sub(r"[^\w\-]+", "-", str(name or "").lower()).strip("-")
