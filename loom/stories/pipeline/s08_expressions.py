"""Step 08 — Expressions: compose face-expression booru tags for every canonical emotion."""

from __future__ import annotations


def compose_expressions(provider, persona: str) -> dict:
    """Face-only booru expression tags for the FIXED canonical emotion taxonomy.

    Personalized to the persona — ONE structured call. A persona's expression of an emotion
    is outfit-independent, so this is composed ONCE per character and reused across outfits.
    Returns {key: face_tags} for every EMOTION_KEY (missing keys fall back to hint cues).
    """
    from loom.server.services.emotions import EMOTIONS, EMOTION_KEYS, EMOTION_HINTS
    from loom.server.services.prompts import _EXPRESSION_SYSTEM
    fallback = {k: EMOTION_HINTS[k] for k in EMOTION_KEYS}
    if provider is None:
        return fallback
    schema = {"type": "object", "additionalProperties": False, "required": EMOTION_KEYS,
              "properties": {k: {"type": "string"} for k in EMOTION_KEYS}}
    listing = "\n".join(f"- {e['key']} ({e['label']}): cues — {e['hint']}" for e in EMOTIONS)
    system = _EXPRESSION_SYSTEM + (
        "\n\nYou are given a FIXED list of emotions. For EVERY emotion key, output how THIS "
        "character's face shows it as 3-7 booru expression tags (face/eyes/eyebrows/mouth + "
        "emotion tags), personalized to the persona. Return exactly one field per emotion key.")
    prompt = f"CHARACTER PERSONA:\n{persona}\n\nEMOTIONS (give a face prompt for each):\n{listing}"
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        data = {}
    return {k: (str(data.get(k) or "").strip() or fallback[k]) for k in EMOTION_KEYS}
