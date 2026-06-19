"""Step 10 — Affect range: curate which emotions a character can display as portrait sprites."""

from __future__ import annotations

import math


def compose_affect_range(provider, persona: str, nsfw: bool = False,
                         systems: dict | None = None) -> dict:
    """Curate a character's EMOTIONAL EXPRESSION RANGE.

    ONE structured call to the cheap 'emotion'-stage model. Returns {range: [key1, key2, ...]}
    — a list of emotion key strings sorted by circumplex angle so the carousel X-axis has a
    stable, sensible left-to-right order.

    On any failure falls back to the full NORMAL_KEYS set so sprite lookup never breaks.
    """
    from loom.server.services.emotions import (
        EMOTIONS, EMOTION_KEYS, NORMAL_KEYS, EMOTION_COORDS,
    )
    from loom.server.services.prompts import _AFFECT_SYSTEM
    pool = EMOTION_KEYS if nsfw else NORMAL_KEYS
    fallback = {"range": NORMAL_KEYS[:]}
    if provider is None:
        return fallback
    system = (systems or {}).get("emotion") or _AFFECT_SYSTEM
    schema = {
        "type": "object", "additionalProperties": False, "required": ["range"],
        "properties": {"range": {
            "type": "array", "minItems": 6, "maxItems": len(pool),
            "items": {"type": "string", "enum": pool},
        }},
    }
    pool_emotions = [e for e in EMOTIONS if e["key"] in set(pool)]
    listing = "\n".join(f"- {e['key']}: {e['hint']}" for e in pool_emotions)
    prompt = (f"CHARACTER PERSONA:\n{persona}\n\n"
              f"AVAILABLE EMOTION KEYS:\n{listing}\n\n"
              f"Return the emotion keys for this character's range.")
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        return fallback
    raw = data.get("range") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return fallback
    seen: set[str] = set()
    pool_set = set(pool)
    cleaned = [k for k in raw if isinstance(k, str) and k in pool_set and not seen.add(k)]  # type: ignore[func-returns-value]
    if not cleaned:
        return fallback
    if "neutral" in pool_set and "neutral" not in seen:
        cleaned.append("neutral")
    cleaned.sort(key=lambda k: math.atan2(EMOTION_COORDS[k][1], EMOTION_COORDS[k][0]))
    return {"range": cleaned}
