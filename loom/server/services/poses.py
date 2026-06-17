"""Pose system — body language is GENERATED per character (not a static library) + global shot geometry.

Body language is personality-driven (a timid character's anger ≠ a brash one's), so — like the face
expression tags (compose_expressions) — each character's pose per emotion is composed from their persona
and stored on their manifest as `pose_prompts`. There is deliberately NO static per-emotion pose library.

What stays global here is **shot geometry** (camera crop + latent canvas), which is not personality:
- FRAMING_TAGS: the camera crop tag set (cowboy 3/4 vs full body), front-view anchored.
- ASPECT_DIMS: latent canvas per aspect.
- GEOMETRY_DEFAULTS: default {framing, aspect} per emotion (standing → cowboy/portrait; neutral / base
  → full body/tall). Overridable in configs/poses.json (geometry only).
"""

from __future__ import annotations

# camera CROP tag sets — both carry the front-view / facing-viewer / straight-on anchors that keep the
# angle steady (esp. for low-cfg/turbo models that ignore negatives).
FRAMING_TAGS: dict[str, str] = {
    "cowboy": ("solo, cowboy shot, front view, facing viewer, straight-on, looking at viewer, "
               "simple background, grey background"),
    "fullbody": ("solo, full body, front view, facing viewer, straight-on, looking at viewer, "
                 "full body shot, head to toe, feet visible, simple background, grey background"),
}
# latent canvas per aspect (width, height) — /8-aligned. `tall` = the full-body recommendation.
ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "portrait": (832, 1216),
    "tall": (952, 1536),
    "landscape": (1216, 832),
    "square": (1024, 1024),
}

_DEFAULT_FRAMING = "cowboy"
_DEFAULT_ASPECT = "portrait"
# Neutral / emotionless renders (the base + outfit reference) want a full-body, generic standing stance —
# not personality, so it's a small constant rather than a composed pose.
NEUTRAL_POSE = "standing, standing straight, arms at sides, relaxed posture"


def geometry_default(key: str) -> dict:
    """Default shot geometry for an emotion: neutral/base = full body on a tall canvas; everything else
    = a 3/4 cowboy shot on a portrait canvas. (Overridable per emotion in configs/poses.json.)"""
    if key == "neutral":
        return {"framing": "fullbody", "aspect": "tall"}
    return {"framing": _DEFAULT_FRAMING, "aspect": _DEFAULT_ASPECT}


def resolve_geometry(key: str, overrides: dict | None) -> dict:
    """Merge the geometry default for `key` with a configs/poses.json override ({framing?, aspect?})."""
    g = geometry_default(key)
    ov = (overrides or {}).get(key) or {}
    if isinstance(ov, dict):
        if ov.get("framing") in FRAMING_TAGS:
            g["framing"] = ov["framing"]
        if ov.get("aspect") in ASPECT_DIMS:
            g["aspect"] = ov["aspect"]
    return g


# System prompt for composing a character's body language per emotion — THOROUGH, persona-personalized,
# front-facing & sprite-safe. Output is grounded to real booru tags afterwards.
_POSE_SYSTEM = (
    "You choose BODY-LANGUAGE tags for an anime character feeling a given EMOTION, personalized to their "
    "persona — how THIS character physically carries that feeling (a timid character's anger is withdrawn, "
    "arms crossed, half-turned away; a brash one's is fists up, chest out, leaning in). Be THOROUGH: cover "
    "overall stance/posture, weight distribution, shoulders, arms and hands, head/chin tilt, and the energy "
    "level. Output ONE line of 6-12 lowercase, comma-separated Danbooru tags describing ONLY the body/pose "
    "(stance, arms, hands, posture, gesture) — NOT the face, hair, clothing, background, framing, camera, or "
    "the character's name. Keep it front-facing and standing unless the emotion clearly implies otherwise. "
    "No sentences, no trailing period."
)
