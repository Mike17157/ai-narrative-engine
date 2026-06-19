"""Step 09 — Poses: compose body-language booru tags for every canonical emotion."""

from __future__ import annotations


def compose_poses(provider, persona: str) -> dict:
    """Body-language booru tags for the FIXED emotion taxonomy, personalized to the persona.

    ONE structured call (mirrors compose_expressions). Each emotion → THIS character's
    stance/limbs/energy, grounded to real tags.
    Returns {emotion_key: pose_tags} (missing keys → empty string).
    """
    from loom.server.services.emotions import EMOTIONS, EMOTION_KEYS
    from loom.server.services.poses import _POSE_SYSTEM
    if provider is None:
        return {k: "" for k in EMOTION_KEYS}
    schema = {"type": "object", "additionalProperties": False, "required": EMOTION_KEYS,
              "properties": {k: {"type": "string"} for k in EMOTION_KEYS}}
    listing = "\n".join(f"- {e['key']} ({e['label']})" for e in EMOTIONS)
    system = _POSE_SYSTEM + (
        "\n\nYou are given a FIXED list of emotions. For EVERY emotion key, output how THIS "
        "character's BODY carries it as thorough body-language tags. "
        "Return exactly one field per emotion key.")
    prompt = f"CHARACTER PERSONA:\n{persona}\n\nEMOTIONS (give a body-language prompt for each):\n{listing}"
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        data = {}
    return {k: str(data.get(k) or "").strip() for k in EMOTION_KEYS}
