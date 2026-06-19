"""Step 06 — Wardrobe refine: generate unified appearance+outfit prose per outfit (parallel)."""

from __future__ import annotations

import re as _re
from concurrent.futures import ThreadPoolExecutor


def compose_outfit_prompt(provider, persona: str, base_appearance: str, outfit_name: str,
                          brief_concept: str = "") -> dict:
    """Generate a UNIFIED prose prompt for ONE outfit.

    Embeds character appearance + outfit in a single coherent 80-150 word passage.
    Returns {attire, unified} where attire is the complete prose prompt.
    unified=True tells the render pipeline not to prepend appearance separately.
    """
    from loom.server.services.prompts import _UNIFIED_OUTFIT_SYSTEM, UNIFIED_OUTFIT_SCHEMA
    if provider is None:
        return {"attire": "", "unified": False}
    context = "\n\n".join(p for p in [
        f"CHARACTER PERSONA:\n{persona}" if persona else "",
        (f"BASE APPEARANCE (physical traits — for integration into the outfit prompt):\n{base_appearance}"
         if base_appearance else ""),
        f"OUTFIT NAME: {outfit_name}" if outfit_name else "",
        f"OUTFIT CONCEPT: {brief_concept}" if brief_concept else "",
    ] if p)
    try:
        data = (provider.generate_text(
            system=_UNIFIED_OUTFIT_SYSTEM,
            prompt=context,
            emits=UNIFIED_OUTFIT_SCHEMA,
        ).data) or {}
    except Exception:  # noqa: BLE001
        return {"attire": "", "unified": False}
    prose = _re.sub(r"\s+", " ", (data.get("prompt") or "").strip())
    if not prose:
        return {"attire": "", "unified": False}
    return {"attire": prose, "unified": True}


def refine_outfits(provider, outfits: list, persona: str, base_appearance: str,
                   emit=None) -> list:
    """Generate a unified prose prompt for EVERY outfit in PARALLEL.

    Each outfit gets a dedicated LLM call. Brief planning concepts seed each call but
    the model generates the complete prompt from scratch.
    Outfits whose generation fails keep their concept as a fallback attire_prompt.
    """
    outfits = [dict(o) for o in (outfits or [])]
    if not outfits:
        return outfits

    def _one(o):
        concept = o.get("concept") or o.get("attire_prompt") or o.get("prompt") or ""
        try:
            r = compose_outfit_prompt(provider, persona, base_appearance, o.get("name", ""), concept)
            if r.get("attire"):
                o["attire_prompt"] = r["attire"]
                o["unified"] = r.get("unified", False)
        except Exception:  # noqa: BLE001
            pass
        if emit:
            emit({"type": "item", "name": o.get("name", "outfit"),
                  "text": o.get("attire_prompt", o.get("concept", ""))})
        return o

    with ThreadPoolExecutor(max_workers=len(outfits)) as ex:
        return list(ex.map(_one, outfits))
