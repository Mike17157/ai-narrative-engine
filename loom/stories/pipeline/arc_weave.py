"""Weave THEMED ARCS from a developed cast.

Phase 2 of the character-first model: instead of deriving a story from one protagonist's
emotional spine, we read the cast's established exemplars (life/saying/reaction) and let the
DRAMA arise from their drives, contradictions, and frictions. Each arc carries its own THEMES
(themes live at the arc level now, not the story level) and names which characters drive it.
"""
from __future__ import annotations

from ...config.schema import LoreEntry

ARCS_SYSTEM = (
    "You are a story architect. You are given a CAST — each character defined by concrete "
    "exemplars (moments from their life, things they say, how they react), NOT abstract traits. "
    "Design 2-4 THEMED ARCS: self-contained dramatic units whose conflict arises naturally from "
    "who these characters ARE — their drives, contradictions, and the friction between them.\n\n"
    "For each arc give:\n"
    "• name — evocative, specific\n"
    "• themes — 1-3 themes this arc explores (what it's really ABOUT, beneath the events)\n"
    "• premise — the dramatic situation/tension that forces these characters together (2-3 sentences)\n"
    "• spotlight — the names of the 1-3 characters this arc most belongs to\n"
    "• turn — how the arc changes them (what they confront or lose or accept)\n\n"
    "The arcs should feel like they could ONLY happen to THIS cast — drawn from their exemplars, "
    "not generic beats. Order them so they could build on each other. Output structured JSON only."
)

ARCS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["arcs"],
    "properties": {
        "arcs": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "themes", "premise", "spotlight", "turn"],
                "properties": {
                    "name": {"type": "string"},
                    "themes": {"type": "array", "items": {"type": "string"}},
                    "premise": {"type": "string"},
                    "spotlight": {"type": "array", "items": {"type": "string"}},
                    "turn": {"type": "string"},
                },
            },
        },
    },
}


def _cast_block(name: str, digest: str, persona: str) -> str:
    head = f"### {name}"
    body = digest if digest and digest != "(nothing established yet)" else (persona or "")[:300]
    return f"{head}\n{body}"


def arcs_prompt(cast: list[dict], premise: str = "") -> str:
    """`cast`: [{name, digest, persona}]. `premise`: optional steer for the kind of story."""
    parts = ["THE CAST:\n" + "\n\n".join(_cast_block(c["name"], c.get("digest", ""), c.get("persona", ""))
                                         for c in cast)]
    if (premise or "").strip():
        parts.append("THE WRITER WANTS:\n" + premise.strip())
    parts.append("Weave themed arcs that could only happen to this cast. Output structured JSON only.")
    return "\n\n".join(parts)


def digest_for(entries: list[LoreEntry]) -> str:
    """Richer digest than the headline list — include the actual exemplar content so the
    arc weaver reasons from real material, not just titles."""
    if not entries:
        return "(nothing established yet)"
    lines = []
    for e in entries:
        lines.append(f"- [{e.facet or 'life'}] {e.title}: {e.content}")
    return "\n".join(lines)
