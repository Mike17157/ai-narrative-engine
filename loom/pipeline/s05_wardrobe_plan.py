"""Step 05 — Wardrobe plan: generate outfit concepts for one character across the story."""

from __future__ import annotations

from ._helpers import WARDROBE_SCHEMA, _call, _sys


def plan_wardrobe(provider, *, char_name: str, persona: str, appearance: str, story: dict,
                  systems: dict | None = None, on_event=None) -> dict:
    """Guess the outfits this character needs across the story (short attire concepts only).

    A dedicated second pass (s06_wardrobe_refine) details each concept into unified prose.
    The emotion sprite set is a FIXED canonical taxonomy composed separately — NOT planned here.

    Returns { outfits: [{name, concept}] }.
    """
    beats = story.get("storyboard", {}).get("beats") or story.get("beats") or []
    nl = (char_name or "").lower()
    arc = [b for b in beats if any(nl in (c or "").lower() for c in b.get("characters", []))] or beats
    beat_lines = "\n".join(f"- {b.get('summary','')}" for b in arc[:24])
    ctx = (f"STORY: {story.get('premise','')}\nTONE: {story.get('tone','')}\n\n"
           f"CHARACTER: {char_name}\nPERSONA:\n{persona or '(none)'}\n"
           f"APPEARANCE: {appearance or '(infer)'}\n\n"
           f"THIS CHARACTER'S BEATS:\n{beat_lines or '(use the story overall)'}\n\n"
           f"Plan {char_name}'s outfits across the story.")
    if on_event:
        on_event({"type": "phase", "label": f"Planning {char_name}'s wardrobe"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    out = _call(provider, _sys(systems or {}, "wardrobe"), ctx, WARDROBE_SCHEMA, "wardrobe", on_delta=dl)
    outfits = [{"name": o.get("name", ""), "concept": o.get("concept", "")}
               for o in out.get("outfits", []) if o.get("name")]
    return {"outfits": outfits}
