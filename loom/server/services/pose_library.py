"""Curated body-language POSE library — a palette of REAL Danbooru pose/gesture tags.

Body language is still GENERATED per character (see compose_poses), but the model no longer
improvises tags from nothing — it now PICKS from this curated palette of real, high-frequency
booru pose tags, personalized to the persona. That keeps the output grounded in tags the
Illustrious/Anima models actually understand (mirrors the tag-recompose palette pattern).

The palette is grouped by body FACET so a composed pose can pull one-or-two tags from several
facets (a stance + what the arms do + what the hands do + head tilt + energy). Every tag below
was verified against loom/data/danbooru_tags.csv (general category, high post count); tags are
written in PROMPT form (spaces, not underscores).

POSE_LIBRARY is the shipped default. configs/pose_library.json (if present) overrides it wholesale
— see config_files.load_pose_library — so the library is user-editable without code changes.
"""

from __future__ import annotations

# facet key -> {desc, tags}. Order matters: a composed pose reads stance → limbs → head → energy.
POSE_LIBRARY: dict[str, dict] = {
    "stance": {
        "desc": "overall body posture / base stance",
        "tags": [
            "standing", "standing straight", "leaning forward", "leaning back",
            "leaning to the side", "bent over", "contrapposto", "standing on one leg",
            "wide stance", "sitting", "kneeling", "seiza", "on one knee", "squatting",
            "crouching", "lying", "on back", "on side", "on stomach", "all fours",
            "fetal position", "yokozuwari", "wariza", "arched back",
        ],
    },
    "arms": {
        "desc": "what the arms are doing",
        "tags": [
            "arms at sides", "crossed arms", "arms behind back", "arms behind head",
            "arms up", "spread arms", "outstretched arms", "outstretched arm",
            "locked arms", "w arms", "holding own arm", "hugging own legs",
            "hugging own knees",
        ],
    },
    "hands": {
        "desc": "what the hands / fingers are doing",
        "tags": [
            "hands on own hips", "hand on own hip", "hand on own chest",
            "hands on own chest", "hand on own face", "hand on own cheek",
            "hands on own cheeks", "hand on own head", "hand in own hair",
            "hand on own thigh", "hands on own knees", "hand on own stomach",
            "clenched hand", "clenched hands", "own hands together", "own hands clasped",
            "interlocked fingers", "hand up", "hands up", "hand in pocket",
            "hands in pockets", "outstretched hand", "reaching", "reaching towards viewer",
            "pointing", "pointing at viewer", "pointing at self", "pointing up",
            "finger to mouth", "finger to cheek", "thumbs up", "waving", "salute", "v",
            "shrugging", "covering own mouth", "covering face", "covering own eyes",
        ],
    },
    "head": {
        "desc": "head / gaze orientation",
        "tags": [
            "head tilt", "head down", "head back", "looking at viewer", "looking away",
            "looking back", "looking down", "looking up", "looking to the side",
            "head rest",
        ],
    },
    "legs": {
        "desc": "what the legs are doing (esp. while sitting/lying)",
        "tags": [
            "crossed legs", "legs apart", "legs together", "legs up", "knees up",
            "knees to chest", "crossed ankles", "standing split",
        ],
    },
    "energy": {
        "desc": "whole-body energy / dynamic action — use when the emotion is high-arousal",
        "tags": [
            "trembling", "stretching", "jumping", "running", "walking", "dancing",
            "carrying", "presenting armpit",
        ],
    },
    "intimate": {
        "desc": "suggestive body language — use ONLY for intimacy/NSFW emotions, never for SFW",
        "tags": [
            "presenting", "arched back", "on back", "legs up", "spread legs",
            "bent over", "top-down bottom-up", "hand on own thigh", "kneeling",
            "knees together feet apart",
        ],
    },
}


def pose_palette(library: dict | None = None) -> dict:
    """The active pose library (the configs override if given/non-empty, else the shipped default)."""
    lib = library if isinstance(library, dict) and library else POSE_LIBRARY
    # keep only well-formed facets
    return {k: v for k, v in lib.items() if isinstance(v, dict) and v.get("tags")}


def palette_text(library: dict | None = None, *, include_intimate: bool = True) -> str:
    """Render the palette as facet lines for an LLM prompt, e.g.::

        STANCE (overall body posture / base stance): standing, leaning forward, ...
        ARMS (what the arms are doing): crossed arms, arms behind back, ...

    `include_intimate=False` drops the intimacy facet (for SFW-only character ranges)."""
    lib = pose_palette(library)
    lines = []
    for facet, spec in lib.items():
        if facet == "intimate" and not include_intimate:
            continue
        tags = ", ".join(str(t) for t in spec.get("tags") or [])
        if tags:
            lines.append(f"  {facet.upper()} ({spec.get('desc', '')}): {tags}")
    return "\n".join(lines)
