"""Canonical emotion taxonomy for the dating-sim / visual-novel pipeline.

Two pools, both stable-keyed so sprite filenames never orphan:

  NORMAL_KEYS  — 31 everyday emotions suitable for any character rating.
  NSFW_KEYS    — 15 intimacy/adult emotions added on top for mature characters.

The full EMOTIONS list is the union (46 entries). Each entry carries:
  key   — stable slug used for filenames and runtime lookup
  label — display name
  hint  — concise face-cue tags that steer expression-prompt generation
  va    — (valence, arousal) on ±1 axes (Russell circumplex).

V-A IS DISPLAY METADATA ONLY. It is used to:
  • sort the carousel X-axis in a sensible left-to-right circumplex sweep
  • plot points in AffectScatter
  • provide a sensible default ordering in compose_affect_range output

Runtime sprite selection uses DIRECT KEY LOOKUP — the director names an
emotion key from the character's affect.range; no coordinate snap occurs.
nearest_emotion() is kept as a legacy fallback only.
"""
from __future__ import annotations

import math
import re

EMOTIONS: list[dict] = [
    # -- Normal / everyday emotions (29) -----------------------------------------
    # Suitable for any character rating. These are the base dating-sim vocabulary.
    {"key": "neutral",      "label": "Neutral",      "hint": "relaxed resting face, flat closed mouth, soft unfocused eyes",
     "va": (0.02, 0.02)},
    {"key": "happy",        "label": "Happy",        "hint": "bright warm smile, cheerful open eyes, content and at ease",
     "va": (0.72, 0.22)},
    {"key": "amused",       "label": "Amused",       "hint": "suppressed laugh, crinkled eyes, playful smirk, stifling a smile",
     "va": (0.75, 0.28)},
    {"key": "excited",      "label": "Excited",      "hint": "sparkling wide eyes, big open smile, flushed, lit up with energy",
     "va": (0.72, 0.80)},
    {"key": "proud",        "label": "Proud",        "hint": "chin lifted, direct confident gaze, satisfied dignified smile",
     "va": (0.65, 0.12)},
    {"key": "hopeful",      "label": "Hopeful",      "hint": "soft upward gaze, tentative smile, brows lifted, earnest longing",
     "va": (0.65, 0.48)},
    {"key": "intrigued",    "label": "Intrigued",    "hint": "one raised brow, slight lean forward, half-smile, quietly captivated",
     "va": (0.45, 0.10)},
    {"key": "curious",      "label": "Curious",      "hint": "wide bright eyes, raised brow, slight head tilt, attentive lean",
     "va": (0.28, 0.38)},
    {"key": "blushed",      "label": "Blushed",      "hint": "rosy flushed cheeks, slightly averted gaze, warm flustered expression",
     "va": (0.45, -0.05)},
    {"key": "shy",          "label": "Shy",          "hint": "downcast soft eyes, small timid smile, slightly hunched, gentle coloring",
     "va": (0.25, -0.18)},
    {"key": "sacred",       "label": "Sacred",       "hint": "soft closed eyes or reverent downward gaze, hushed gentle stillness, awed reverence",
     "va": (0.55, -0.80)},
    {"key": "longing",      "label": "Longing",      "hint": "wistful faraway gaze, aching softness in eyes, distant dreaming look",
     "va": (-0.10, -0.20)},
    {"key": "lustful",      "label": "Lustful",      "hint": "darkened heavy eyes, parted lips, flushed face, heated consuming look",
     "va": (0.55, 0.62)},
    {"key": "pleasure",     "label": "Pleasure",     "hint": "blissful closed eyes, flushed cheeks, soft open mouth, euphoric half-smile",
     "va": (0.82, 0.65)},
    {"key": "confused",     "label": "Confused",     "hint": "head tilted, furrowed brow, questioning squint, uncertain expression",
     "va": (-0.12, 0.28)},
    {"key": "shocked",      "label": "Shocked",      "hint": "jaw dropped, eyes wide as plates, hands raised, frozen in disbelief",
     "va": (-0.15, 0.90)},
    {"key": "begging",      "label": "Begging",      "hint": "pleading wide eyes, hands pressed together, desperate imploring look",
     "va": (-0.28, 0.78)},
    {"key": "embarrassed",  "label": "Embarrassed",  "hint": "bright blush, averted eyes, sheepish awkward smile",
     "va": (-0.35, 0.50)},
    {"key": "guilty",       "label": "Guilty",       "hint": "downcast guilty eyes, pressed lips, weighted ashamed expression",
     "va": (-0.50, -0.25)},
    {"key": "sad",          "label": "Sad",          "hint": "downturned eyes, small frown, damp lashes, quiet sorrow",
     "va": (-0.62, -0.18)},
    {"key": "tired",        "label": "Tired",        "hint": "heavy half-lidded eyes, slack relaxed expression, slow blink, worn and drained",
     "va": (-0.18, -0.68)},
    {"key": "exhausted",    "label": "Exhausted",    "hint": "heavy drooping eyelids, slack jaw, hollow drained stare, fully spent",
     "va": (-0.22, -0.88)},
    {"key": "annoyed",      "label": "Annoyed",      "hint": "flat frown, creased brow, impatient look, mild irritation",
     "va": (-0.38, 0.18)},
    {"key": "disappointed", "label": "Disappointed", "hint": "downcast eyes, slight frown, deflated expression, let down",
     "va": (-0.42, -0.32)},
    {"key": "frustrated",   "label": "Frustrated",   "hint": "jaw clenched, lips pressed, furrowed brow, pressured strained stare",
     "va": (-0.45, 0.62)},
    {"key": "disgusted",    "label": "Disgusted",    "hint": "wrinkled nose, curled upper lip, recoiling look of repulsion",
     "va": (-0.60, 0.08)},
    {"key": "scorn",        "label": "Scorn",        "hint": "curled lip, cold narrowed eyes, contemptuous head tilt, ice-cold superiority",
     "va": (-0.65, 0.28)},
    {"key": "angry",        "label": "Angry",        "hint": "clenched jaw, tight glare, hard pressed lips, controlled fury",
     "va": (-0.68, 0.50)},
    {"key": "rage",         "label": "Rage",         "hint": "furious, bared teeth, blazing eyes, shouting, veins",
     "va": (-0.92, 0.92)},
    {"key": "scared",       "label": "Scared",       "hint": "wide fearful eyes, raised inner brows, trembling parted lips, shrinking back in fright",
     "va": (-0.70, 0.78)},
    {"key": "aggrieved",    "label": "Aggrieved",    "hint": "wounded indignant frown, hurt reproachful eyes, tight downturned mouth, feeling wronged",
     "va": (-0.58, 0.30)},
    # -- NSFW / intimacy emotions (15) -------------------------------------------
    # Added on top of the normal set for mature character ratings.
    {"key": "anticipation", "label": "Anticipation", "hint": "eager eyes, slight smile, leaning in, barely containing excitement",
     "va": (0.52, 0.42)},
    {"key": "desire",       "label": "Desire",       "hint": "half-lidded seductive eyes, blush, parted lips, sultry smile",
     "va": (0.42, 0.55)},
    {"key": "teasing",      "label": "Teasing",      "hint": "sly smile, one raised brow, playful gleam, tongue-tip or biting lip",
     "va": (0.32, 0.50)},
    {"key": "comfort",      "label": "Comfort",      "hint": "soft relaxed face, gentle closed-mouth smile, eyes softly lidded, warmly settled",
     "va": (0.65, -0.45)},
    {"key": "relief",       "label": "Relief",       "hint": "exhale of relief, eyes shut or cast down, tension leaving face, small grateful smile",
     "va": (0.72, -0.62)},
    {"key": "ecstasy",      "label": "Ecstasy",      "hint": "wide open-mouth smile, sparkling eyes, sheer overwhelming delight",
     "va": (0.95, 0.85)},
    {"key": "arousal",      "label": "Arousal",      "hint": "heavy-lidded eyes, flushed cheeks, parted lips, breathless expression",
     "va": (0.50, 0.80)},
    {"key": "intensity",    "label": "Intensity",    "hint": "wide unwavering stare, heavy breath, fully flushed, completely consumed",
     "va": (0.05, 0.90)},
    {"key": "release",      "label": "Release",      "hint": "eyes closing, jaw loosening, tension draining from face, soft exhale expression",
     "va": (0.60, -0.05)},
    {"key": "submission",   "label": "Submission",   "hint": "soft downcast eyes, relaxed parted lips, slightly bowed head, docile yielding",
     "va": (0.22, -0.35)},
    {"key": "arrogant",     "label": "Arrogant",     "hint": "chin raised, smug grin, one brow arched high, dismissive tilt",
     "va": (-0.20, 0.30)},
    {"key": "condescension","label": "Condescension","hint": "slight smirk, lowered eyelids, head angled down, calm superiority",
     "va": (-0.45, -0.18)},
    {"key": "discomfort",   "label": "Discomfort",   "hint": "strained grimace, tense brow, averted gaze, lips pressed tight",
     "va": (-0.55, 0.48)},
    {"key": "humiliation",  "label": "Humiliation",  "hint": "burning blush, downcast eyes, trembling lip, visible shame",
     "va": (-0.65, 0.60)},
    {"key": "pain",         "label": "Pain",         "hint": "screwed-shut eyes or teary, bared teeth or bitten lip, furrowed brow, pained tension",
     "va": (-0.78, 0.82)},
]

# Stable key lists — the two pools a character's affect.range draws from.
NORMAL_KEYS: list[str] = [e["key"] for e in EMOTIONS if e["key"] not in {
    "anticipation", "desire", "teasing", "comfort", "relief", "ecstasy",
    "arousal", "intensity", "release", "submission", "arrogant",
    "condescension", "discomfort", "humiliation", "pain",
}]

NSFW_KEYS: list[str] = [e["key"] for e in EMOTIONS if e["key"] not in set(NORMAL_KEYS)]

EMOTION_KEYS: list[str] = [e["key"] for e in EMOTIONS]
EMOTION_LABELS: dict[str, str] = {e["key"]: e["label"] for e in EMOTIONS}
EMOTION_HINTS: dict[str, str] = {e["key"]: e["hint"] for e in EMOTIONS}

# CORE set (24) — the DEFAULT render taxonomy: rendering all 44 is slow and redundant, so this
# is a curated spread across the Russell circumplex (every quadrant, low→high arousal) for
# MAXIMUM emotional variety in the fewest sprites. "Render all" uses this; the per-cell ↻ can
# still render any of the full 44.
# INTIMACY pool — hidden from a character's default emotion set (the affect generator sometimes
# assigns these to SFW characters, e.g. a teen; they showed as "untracked" emotions). Rendered/
# shown only when a caller explicitly opts in (mature characters).
INTIMACY_KEYS: set[str] = set(NSFW_KEYS) | {"lustful", "pleasure", "begging"}


def is_intimacy(key: str) -> bool:
    return key in INTIMACY_KEYS


CORE_KEYS: list[str] = [
    "neutral", "happy", "excited", "amused", "proud", "hopeful", "teasing",   # positive spread
    "curious", "shy", "blushed", "longing", "comfort",                        # social / warm
    "confused", "shocked", "scared", "embarrassed", "guilty",                 # surprise / fear / self-conscious
    "sad", "aggrieved", "tired", "annoyed", "disappointed", "frustrated", "disgusted",  # negative low→mid
    "angry", "rage",                                                          # negative high
]
EMOTION_COORDS: dict[str, tuple[float, float]] = {e["key"]: e["va"] for e in EMOTIONS}

# Calm, mildly pleasant — used for base/outfit images and as the no-emotion fallback.
NEUTRAL_COORD: tuple[float, float] = (0.15, -0.50)


def slug(name: str) -> str:
    """Normalize an emotion name to its stable key form."""
    return re.sub(r"[^\w\-]+", "-", str(name or "").lower()).strip("-")


def _coord_of(item: dict) -> tuple[float, float]:
    if "va" in item:
        v, a = item["va"]
    else:
        v, a = float(item.get("valence", 0.0)), float(item.get("arousal", 0.0))
    return max(-1.0, min(1.0, v)), max(-1.0, min(1.0, a))


def nearest_emotion(valence: float, arousal: float,
                    range_with_coords: list[dict]) -> str | None:
    """Legacy fallback: snap a {valence, arousal} coord to the nearest key.

    Only called when the director emits raw V-A instead of a direct key.
    New code should pass emotion keys directly (see PLAY_SCHEMA)."""
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
    """Full emotion set as a display-ready range, sorted by circumplex angle.

    Returns [{emotion, valence, arousal}, ...] — the shape the frontend expects
    for carousel ordering and AffectScatter. Used as the fallback when a character
    has no authored affect.range."""
    ks = keys or EMOTION_KEYS
    out = [{"emotion": k, "valence": EMOTION_COORDS[k][0], "arousal": EMOTION_COORDS[k][1]}
           for k in ks if k in EMOTION_COORDS]
    out.sort(key=lambda e: math.atan2(e["arousal"], e["valence"]))
    return out


def range_to_display(keys: list[str]) -> list[dict]:
    """Convert a list of emotion keys to the display-ready [{emotion, label, valence, arousal}]
    shape, sorted by circumplex angle. Enriches a stored list-of-strings range before sending
    to the frontend (carousel ordering, AffectScatter)."""
    out = [{"emotion": k, "label": EMOTION_LABELS[k],
            "valence": EMOTION_COORDS[k][0], "arousal": EMOTION_COORDS[k][1]}
           for k in keys if k in EMOTION_COORDS]
    out.sort(key=lambda e: math.atan2(e["arousal"], e["valence"]))
    return out
