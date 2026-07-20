"""Interactive manga-production agent for the manuscript surface.

The agent chats about the story's current structure and may propose exactly one
pipeline action per turn (bake prose -> plan manga panels -> render panels).
The endpoint calls the model; this module holds the prompt, the output schema,
the structure digest the model reasons over, and the deterministic validators
that keep a proposed action honest.  No FastAPI imports so the logic stays
unit-testable.
"""

from __future__ import annotations

from typing import Any


MANGA_AGENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "suggestions", "action"],
    "properties": {
        "reply": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "action": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["kind"],
                    "properties": {
                        "kind": {"enum": ["bake", "manga-plan", "manga-render"]},
                        "note": {"type": "string"},
                    },
                },
            ],
        },
    },
}


MANGA_AGENT_SYSTEM = """You are a manga production assistant and art director embedded in a story studio's
manuscript view. You receive a JSON structure digest of the story (prologue sections,
played scenes, baked chapters, manga panels) and the recent conversation.

Your job:
- Explain the current state and structure of the story when asked.
- Discuss what to do next and help the author think through the production.
- Propose at most ONE action per turn via the `action` field, choosing only from
  the allowed kinds: "bake", "manga-plan", "manga-render". Propose an action only
  when it is currently applicable and clearly wanted; otherwise set action to null.
- NEVER claim an action has already run. You only propose it; the user confirms
  and executes it in the interface.
- Keep replies concise: under ~150 words, plain and warm, no markdown headers.
- Give 2-4 short suggestion chips phrased as things the user could say next.

The production pipeline runs in this order:
1. bake: played scenes become finished chapter prose.
2. manga-plan: baked chapters become one reviewable panel plan per chapter.
3. manga-render: saved panel plans are rendered into images.
Explain this order when it helps the author understand why an earlier step must
happen first. Return only the requested structured data."""


_ACTION_LABELS = {"bake": "bake", "manga-plan": "manga-plan", "manga-render": "manga-render"}

_ACTION_UNAVAILABLE = {
    "bake": "(Bake isn't available yet — play some scenes first.)",
    "manga-plan": "(Planning manga needs baked chapters — bake the prose first.)",
    "manga-render": "(There's nothing new to render — plan the manga panels first.)",
}


def structure_digest(story_fields: dict, world: dict) -> dict:
    """Summarize the manuscript state the agent reasons over."""
    story_fields = story_fields or {}
    world = world or {}

    prologue: list[str] = []
    for section in ((story_fields.get("prologue") or {}).get("sections") or []):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if not title:
            title = str(section.get("text") or "").strip()[:60]
        if title:
            prologue.append(title)

    scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(world.get("manuscript") or []):
        if not isinstance(scene, dict):
            continue
        pages = scene.get("pages") or []
        beats = [str(page.get("beat")).strip() for page in pages
                 if isinstance(page, dict) and str(page.get("beat") or "").strip()]
        scenes.append({"index": index, "location": str(scene.get("loc") or "").strip(),
                       "pages": len(pages), "beats": beats})

    baked_chapters: list[dict[str, Any]] = []
    for index, chapter in enumerate(world.get("baked_story") or []):
        if not isinstance(chapter, dict):
            continue
        text = str(chapter.get("text") or "")
        baked_chapters.append({"index": index,
                               "title": str(chapter.get("title") or "").strip(),
                               "words": len(text.split())})

    panels: list[dict[str, Any]] = []
    for panel in ((world.get("manga_plan") or {}).get("panels") or []):
        if not isinstance(panel, dict):
            continue
        panels.append({"id": str(panel.get("id") or ""),
                       "chapter": panel.get("chapter"),
                       "title": str(panel.get("title") or "").strip(),
                       "shot": str(panel.get("shot") or "").strip(),
                       "characters": [str(c) for c in panel.get("characters") or []],
                       "moment": str(panel.get("moment") or "").strip(),
                       "rendered": bool(panel.get("image"))})

    rendered_count = sum(1 for panel in panels if panel["rendered"])
    status = {
        "has_scenes": bool(scenes),
        "has_baked": bool(baked_chapters),
        "has_plan": bool(panels),
        "rendered_count": rendered_count,
        "panel_count": len(panels),
    }
    if scenes and not baked_chapters:
        status["next_step"] = "bake"
    elif baked_chapters and not panels:
        status["next_step"] = "manga-plan"
    elif panels and rendered_count < len(panels):
        status["next_step"] = "manga-render"
    else:
        status["next_step"] = "discuss"
    return {"prologue": prologue, "scenes": scenes, "baked_chapters": baked_chapters,
            "panels": panels, "status": status}


def _action_applicable(kind: str, status: dict) -> bool:
    if kind == "bake":
        return bool(status.get("has_scenes"))
    if kind == "manga-plan":
        return bool(status.get("has_baked"))
    if kind == "manga-render":
        return int(status.get("panel_count") or 0) > int(status.get("rendered_count") or 0)
    return False


def normalize_agent_turn(raw: dict | None, digest: dict) -> dict:
    """Validate the model's structured reply against the current structure.

    The model may propose an action that is not currently applicable; such an
    action is dropped and the reply gains a parenthetical explaining why, so the
    UI never offers a button that would immediately fail.
    """
    raw = raw if isinstance(raw, dict) else {}
    status = (digest or {}).get("status") or {}

    reply = str(raw.get("reply") or "").strip()
    if not reply:
        reply = "Here's where the manuscript stands — ask me about the next production step."

    suggestions: list[str] = []
    for item in raw.get("suggestions") or []:
        text = str(item or "").strip()
        if text and text not in suggestions:
            suggestions.append(text)
    suggestions = suggestions[:4]

    action = None
    candidate = raw.get("action")
    if isinstance(candidate, dict):
        kind = str(candidate.get("kind") or "").strip()
        if kind in _ACTION_LABELS:
            if _action_applicable(kind, status):
                action = {"kind": kind, "note": str(candidate.get("note") or "").strip()}
            else:
                reply = f"{reply} {_ACTION_UNAVAILABLE[kind]}".strip()

    return {"reply": reply, "suggestions": suggestions, "action": action}


def bootstrap_turn(digest: dict) -> dict:
    """First-touch summary with no model call."""
    status = (digest or {}).get("status") or {}
    scenes = len((digest or {}).get("scenes") or [])
    baked = len((digest or {}).get("baked_chapters") or [])
    panels = int(status.get("panel_count") or 0)
    rendered = int(status.get("rendered_count") or 0)
    next_step = status.get("next_step") or "discuss"

    counts = (f"{scenes} played scene{'s' if scenes != 1 else ''}, "
              f"{baked} baked chapter{'s' if baked != 1 else ''}, "
              f"{rendered}/{panels} panels rendered")
    if next_step == "bake":
        reply = (f"Welcome to the production desk. So far: {counts}. "
                 "The next step is baking the played scenes into finished chapter prose.")
        suggestions = ["Bake the prose", "Summarize the story structure",
                       "What does baking change?"]
    elif next_step == "manga-plan":
        reply = (f"Welcome to the production desk. So far: {counts}. "
                 "The prose is baked — next I can plan one manga panel per chapter.")
        suggestions = ["Plan the manga panels", "How do you pick panel compositions?",
                       "Summarize the story structure"]
    elif next_step == "manga-render":
        reply = (f"Welcome to the production desk. So far: {counts}. "
                 "The panel plan is ready — rendering turns it into images.")
        suggestions = ["Render the manga panels", "Walk me through the panel plan",
                       "Summarize the story structure"]
    else:
        reply = (f"Welcome to the production desk. So far: {counts}. "
                 "Ask me about the story structure or the manga pipeline.")
        suggestions = ["Summarize the story structure", "What should I do next?",
                       "How does the manga pipeline work?"]
    return {"reply": reply, "suggestions": suggestions, "action": None}
