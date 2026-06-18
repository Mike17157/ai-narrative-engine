"""Canonical emotion taxonomy — a FIXED, comprehensive range driving the sprite set.

Grounded in **Plutchik's wheel of emotions**: 8 primary emotions at 3 intensities each (24
graded states), plus the most useful dyads / social emotions (~8). A fixed vocabulary with
STABLE keys means a character's emotion sprites are consistent across outfits and the runtime
director can switch sprite by an exact emotion key (or snap a detected affect to the nearest
canonical one). This replaces the old variable per-outfit `DEFAULT_EMOTIONS` set.

Each entry: `key` (stable slug, used for filenames + lookup), `label` (display), `hint` (a few
face cues steering the per-character expression-prompt generation — see
AppContext.compose_expressions).

--- Valence / Arousal substrate ------------------------------------------------
Each key also carries a canonical **(valence, arousal)** coordinate on ±1 axes (Russell's
circumplex — Plutchik's wheel IS a circumplex, so every key maps to one). These are the
translation layer: the runtime director emits per-character {valence, arousal}; pure math
(`nearest_emotion`) snaps those coords to the closest emotion key in a character's range. The
32 keys carry the nuance (rage ≠ terror ≠ desire are distinct keys); V-A only SELECTS among
them. A character's `affect.range` (see AppContext.compose_affect_range) is a personality-rooted
SUBSET of these coords, nudged per persona — authored once and cached.
"""
from __future__ import annotations

import math
import re

EMOTIONS: list[dict] = [
    # -- Plutchik primaries × 3 intensities (24) -----------------------------
    # Each `va` is the canonical (valence, arousal) on ±1 axes (Russell circumplex).
    # Intensity scales arousal (calm mild → activated extreme) and saturates valence.
    {"key": "serenity", "label": "Serenity", "hint": "soft relaxed smile, calm eyes, gentle",
     "va": (0.78, -0.68)},
    {"key": "joy", "label": "Joy", "hint": "happy, smile, bright eyes",
     "va": (0.82, 0.38)},
    {"key": "ecstasy", "label": "Ecstasy", "hint": "wide open-mouth smile, sparkling eyes, sheer delight",
     "va": (0.95, 0.85)},
    {"key": "acceptance", "label": "Acceptance", "hint": "warm soft eyes, faint smile, content",
     "va": (0.55, -0.42)},
    {"key": "trust", "label": "Trust", "hint": "open relaxed expression, soft gaze, gentle smile",
     "va": (0.62, -0.30)},
    {"key": "admiration", "label": "Admiration", "hint": "wide adoring eyes, slight blush, parted lips",
     "va": (0.58, 0.18)},
    {"key": "apprehension", "label": "Apprehension", "hint": "slightly worried eyes, tense brow, small frown",
     "va": (-0.42, 0.32)},
    {"key": "fear", "label": "Fear", "hint": "wide scared eyes, raised eyebrows, open mouth, sweatdrop",
     "va": (-0.62, 0.72)},
    {"key": "terror", "label": "Terror", "hint": "terrified wide eyes, trembling, pale, tears, screaming",
     "va": (-0.82, 0.95)},
    {"key": "distraction", "label": "Distraction", "hint": "unfocused eyes, slightly raised brow, mild surprise",
     "va": (0.05, 0.22)},
    {"key": "surprise", "label": "Surprise", "hint": "raised eyebrows, wide eyes, open mouth, surprised",
     "va": (0.10, 0.62)},
    {"key": "amazement", "label": "Amazement", "hint": "shocked wide eyes, dropped jaw, surprised",
     "va": (0.35, 0.85)},
    {"key": "pensiveness", "label": "Pensiveness", "hint": "downcast eyes, faint frown, wistful",
     "va": (-0.38, -0.38)},
    {"key": "sadness", "label": "Sadness", "hint": "sad, frown, teary eyes, lowered brows",
     "va": (-0.70, -0.28)},
    {"key": "grief", "label": "Grief", "hint": "crying, streaming tears, anguished, clenched eyes",
     "va": (-0.85, 0.52)},
    {"key": "boredom", "label": "Boredom", "hint": "half-closed eyes, flat mouth, unamused, bored",
     "va": (-0.35, -0.70)},
    {"key": "disgust", "label": "Disgust", "hint": "wrinkled nose, narrowed eyes, grimace, sneer",
     "va": (-0.68, 0.18)},
    {"key": "loathing", "label": "Loathing", "hint": "deep scowl, bared teeth, intense revulsion",
     "va": (-0.88, 0.58)},
    {"key": "annoyance", "label": "Annoyance", "hint": "slight frown, furrowed brow, sidelong glance, pout",
     "va": (-0.45, 0.30)},
    {"key": "anger", "label": "Anger", "hint": "angry, furrowed brow, gritted teeth, glaring",
     "va": (-0.75, 0.68)},
    {"key": "rage", "label": "Rage", "hint": "furious, bared teeth, blazing eyes, shouting, veins",
     "va": (-0.92, 0.92)},
    {"key": "interest", "label": "Interest", "hint": "curious raised brow, attentive eyes, faint smile",
     "va": (0.35, 0.20)},
    {"key": "anticipation", "label": "Anticipation", "hint": "eager eyes, slight smile, leaning in",
     "va": (0.52, 0.42)},
    {"key": "vigilance", "label": "Vigilance", "hint": "intense focused stare, narrowed alert eyes, serious",
     "va": (0.12, 0.62)},
    # -- dyads / social emotions (8) -----------------------------------------
    {"key": "love", "label": "Love", "hint": "soft loving eyes, blush, tender smile, heart",
     "va": (0.80, 0.12)},
    {"key": "desire", "label": "Desire (enticed)", "hint": "half-lidded seductive eyes, blush, parted lips, sultry smile",
     "va": (0.42, 0.55)},
    {"key": "greed", "label": "Greed", "hint": "covetous grin, gleaming wide eyes, drooling, scheming",
     "va": (0.08, 0.55)},
    {"key": "optimism", "label": "Optimism", "hint": "hopeful bright eyes, confident smile, lifted brows",
     "va": (0.70, 0.32)},
    {"key": "remorse", "label": "Remorse", "hint": "downturned guilty eyes, frown, teary, looking away",
     "va": (-0.62, -0.10)},
    {"key": "contempt", "label": "Contempt", "hint": "smug half-lidded eyes, one raised brow, smirk, looking down",
     "va": (-0.55, 0.08)},
    {"key": "awe", "label": "Awe", "hint": "wide wonderstruck eyes, parted lips, sparkles",
     "va": (0.48, 0.55)},
    {"key": "disappointment", "label": "Disappointment", "hint": "lowered gaze, slight frown, sigh, deflated",
     "va": (-0.48, -0.42)},
]

EMOTION_KEYS: list[str] = [e["key"] for e in EMOTIONS]
EMOTION_LABELS: dict[str, str] = {e["key"]: e["label"] for e in EMOTIONS}
EMOTION_HINTS: dict[str, str] = {e["key"]: e["hint"] for e in EMOTIONS}
# Canonical (valence, arousal) on ±1 axes — Russell's circumplex. The translation substrate:
# a {v,a} coordinate snaps (nearest_emotion) to the closest emotion key. The 32 keys carry the
# nuance; V-A only SELECTS among them. See nearest_emotion / canonical_range below.
EMOTION_COORDS: dict[str, tuple[float, float]] = {e["key"]: e["va"] for e in EMOTIONS}

# The neutral / no-strong-emotion coordinate (used for the base + outfit images, and as the
# fallback when the director emits nothing). Calm, mildly pleasant.
NEUTRAL_COORD: tuple[float, float] = (0.15, -0.50)


def slug(name: str) -> str:
    """Normalize an emotion name to its stable key form (matches the manifest/file convention)."""
    return re.sub(r"[^\w\-]+", "-", str(name or "").lower()).strip("-")


def _coord_of(item: dict) -> tuple[float, float]:
    """Read a (valence, arousal) pair from an emotion entry — accepts either an explicit `va`
    tuple, or separate `valence`/`arousal` floats (the persona-rooted range shape), clamped ±1."""
    if "va" in item:
        v, a = item["va"]
    else:
        v, a = float(item.get("valence", 0.0)), float(item.get("arousal", 0.0))
    return max(-1.0, min(1.0, v)), max(-1.0, min(1.0, a))


def nearest_emotion(valence: float, arousal: float,
                    range_with_coords: list[dict]) -> str | None:
    """Snap a {valence, arousal} coordinate to the nearest emotion key in the given range.

    Pure Euclidean nearest over the provided entries (each a dict with an `emotion` key and
    either a `va` tuple or `valence`/`arousal` floats). Returns the emotion key, or None if the
    range is empty. This is the runtime translation step: the director's per-character {v,a} →
    the closest sprite key in THIS character's range. No model call — just math."""
    if not range_with_coords:
        return None
    v, a = float(valence), float(arousal)
    best, best_d = None, None
    for e in range_with_coords:
        key = e.get("emotion") or e.get("key")
        if not key:
            continue
        ev, ea = _coord_of(e)
        d = (ev - v) ** 2 + (ea - a) ** 2
        if best_d is None or d < best_d:
            best, best_d = key, d
    return best


def canonical_range(keys: list[str] | None = None) -> list[dict]:
    """The full emotion taxonomy as a coordinate range, in canonical V-A order —
    `[{emotion, valence, arousal}, ...]`. This is the BACK-COMPAT fallback: a character with no
    persona-rooted `affect.range` snaps against the full 32 at canonical coords, which is
    identical to today's exact-match-on-the-full-set behaviour, so existing sprites never orphan.

    `keys` optionally restricts to a subset (e.g. only keys a character has rendered sprites for),
    preserving the canonical V-A sort."""
    ks = keys or EMOTION_KEYS
    out = [{"emotion": k, "valence": EMOTION_COORDS[k][0], "arousal": EMOTION_COORDS[k][1]}
           for k in ks if k in EMOTION_COORDS]
    # sort by angle around the circumplex (arctan2 of arousal, valence) → a stable, pleasant
    # left-to-right traversal of the wheel for the carousel X axis.
    out.sort(key=lambda e: math.atan2(e["arousal"], e["valence"]))
    return out
