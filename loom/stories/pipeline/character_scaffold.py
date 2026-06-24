"""Character psyche scaffold — build a character through CONVERSATION, one at a time.

Instead of describing traits/emotions abstractly, we encapsulate a character with concrete
EXEMPLARS the LLM can imitate, accreted through a back-and-forth interview and written to the
character's own lorebook (scope = character key). Three exemplar types:

  • life     — a vignette from their past: a specific situation + what they actually did
  • saying   — a characteristic line in their own voice
  • reaction — a common reaction, shaped "when <situation> → they <do/say>"

The Big Five / McAdams science stays BEHIND the scenes (a coverage check so the exemplars span
the whole person and stay distinct from the rest of the cast) — never written as prose.
"""
from __future__ import annotations

import re

from ...config.schema import LoreEntry

FACET_TYPES = ("life", "saying", "reaction")

# The interview agent. It co-develops ONE character with the writer, proposing plausible
# backstory and reacting — then commits agreed beats as exemplars (the `facets` array).
INTERVIEW_SYSTEM = (
    "You are a character-development partner. You and the writer are bringing ONE character to "
    "life through conversation — gradually, like two people who know them talking it over.\n\n"
    "HOW YOU WORK:\n"
    "• Propose PLAUSIBLE, specific backstory and behaviour — concrete moments, not adjectives. "
    "Offer a possibility, then ask the writer if it fits or what they'd change.\n"
    "• Build through EXAMPLES, never abstract trait/emotion description. Show who they are via: a "
    "vignette from their past (life), a line they'd actually say (saying), or how they reliably "
    "react to a kind of situation (reaction).\n"
    "• Keep your spoken reply SHORT and conversational — one idea or question at a time. Don't "
    "lecture or list.\n"
    "• Privately make sure the character stays well-rounded and DISTINCT (different drives, voice, "
    "and reactions from a generic person) — but never name traits or theories to the writer.\n\n"
    "COMMITTING EXEMPLARS:\n"
    "Whenever a concrete detail is AGREED or clearly settled this turn, add it to `facets`. Each "
    "facet is one of: \n"
    "  life     — title + a vivid 1-3 sentence scene from their past (what happened, what they did)\n"
    "  saying   — title + the actual line, in their voice\n"
    "  reaction — title + 'When <situation type>, they <do/say>...'\n"
    "Give 3-6 keywords per facet (trigger words for later retrieval). If nothing is settled yet "
    "(still brainstorming), return an empty `facets` array and keep talking. Never commit vague "
    "or merely-proposed ideas — only what the writer has accepted or stated."
)

_FACET_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["type", "title", "keywords", "content"],
    "properties": {
        "type": {"type": "string", "enum": list(FACET_TYPES)},
        "title": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "content": {"type": "string"},
    },
}

INTERVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "facets"],
    "properties": {
        "reply": {"type": "string"},
        "facets": {"type": "array", "items": _FACET_ITEM},
    },
}

# Harvest: distil a scene transcript into new exemplars for ONE character.
FACETS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["facets"],
    "properties": {"facets": {"type": "array", "items": _FACET_ITEM}},
}

REFINE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "keywords", "content"],
    "properties": {
        "title": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "content": {"type": "string"},
    },
}


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "facet"


def facet_to_entry(facet: dict) -> LoreEntry | None:
    """Map a generated facet dict → a LoreEntry in the character's lorebook. `facet` groups
    by type so the cards UI can section them and retrieval can cap one-per-type per turn."""
    ftype = (facet.get("type") or "").strip().lower()
    if ftype not in FACET_TYPES:
        return None
    title = (facet.get("title") or "").strip()
    content = (facet.get("content") or "").strip()
    if not content:
        return None
    kws = [str(k).strip() for k in (facet.get("keywords") or []) if str(k).strip()]
    return LoreEntry(
        id=f"{ftype}-{_slug(title or content)}",
        title=title or ftype.capitalize(),
        keywords=kws,
        content=content,
        facet=ftype,
        source="interview",
    )


def _facet_digest(entries: list[LoreEntry]) -> str:
    """Compact summary of what's already established, fed back so the agent doesn't repeat."""
    if not entries:
        return "(nothing established yet)"
    by_type: dict[str, list[str]] = {t: [] for t in FACET_TYPES}
    for e in entries:
        by_type.setdefault(e.facet or "life", []).append(e.title or e.content[:40])
    lines = []
    for t in FACET_TYPES:
        if by_type.get(t):
            lines.append(f"{t}: " + "; ".join(by_type[t]))
    return "\n".join(lines) or "(nothing established yet)"


def facet_digest(entries: list[LoreEntry]) -> str:
    return _facet_digest(entries)


# ── Improv: put 2-3 characters in a scene and let them bounce off each other ──────
# Behaviour reveals personality better than self-description. A lean round-robin (no
# director/secrets machinery) keeps it controllable and local-friendly.

def _improv_system(name: str, persona: str, digest: str) -> str:
    return (
        f"You ARE {name}. Stay strictly in character; never narrate others' private thoughts.\n\n"
        f"WHO YOU ARE:\n{persona or '(sketch them from what is shown)'}\n\n"
        f"ESTABLISHED ABOUT YOU:\n{digest}\n\n"
        "Respond ONLY as your next beat in the scene — what you say and do, concise (1-3 "
        "sentences), in your own voice. Mix dialogue and action. Do not write for anyone else."
    )


def improv_turn(provider, *, name: str, persona: str, digest: str, situation: str,
                transcript: list[dict]) -> str:
    log = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript) or "(the scene opens)"
    prompt = (f"SCENE: {situation}\n\nSO FAR:\n{log}\n\nWhat does {name} say and do next?")
    res = provider.generate_text(system=_improv_system(name, persona, digest), prompt=prompt)
    return (res.text or "").strip()


def run_improv(provider, *, situation: str, cast: list[dict], rounds: int = 2) -> list[dict]:
    """`cast`: [{name, persona, digest}]. Returns a transcript [{speaker, text}]."""
    transcript: list[dict] = []
    present = [c for c in cast if c.get("name")]
    for _ in range(max(1, rounds)):
        for c in present:
            text = improv_turn(provider, name=c["name"], persona=c.get("persona", ""),
                               digest=c.get("digest", ""), situation=situation, transcript=transcript)
            if text:
                transcript.append({"speaker": c["name"], "text": text})
    return transcript


HARVEST_SYSTEM = (
    "You distil what a scene REVEALED about one character into concrete exemplars — never "
    "abstract trait talk. Only capture what the character actually showed in the transcript. "
    "Each exemplar is one of: life (a vivid past-moment it implies), saying (a line in their "
    "voice, ideally drawn from what they said), or reaction (a 'When <situation>, they <do/say>' "
    "pattern the scene demonstrates). 3-6 keywords each. Output structured JSON only."
)


def harvest_prompt(name: str, transcript: list[dict], existing: list[LoreEntry]) -> str:
    log = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript)
    return (
        f"CHARACTER: {name}\n\nALREADY ESTABLISHED (don't duplicate):\n{_facet_digest(existing)}\n\n"
        f"SCENE TRANSCRIPT:\n{log}\n\n"
        f"Distil NEW exemplars about {name} that this scene revealed. Output structured JSON only.")


# ── Contrast: sharpen what makes ONE character distinct from the rest of the cast ──

CONTRAST_SYSTEM = (
    "You sharpen what makes ONE character DISTINCT from the others in their cast. Given the "
    "target and the rest of the cast, invent a few NEW concrete exemplars (life / saying / "
    "reaction) that DIFFERENTIATE the target — a voice, drive, or reaction the others don't "
    "have. Never duplicate what's already established for the target. Don't make them a mere "
    "opposite of someone; make them specifically, idiosyncratically themselves. 3-6 keywords "
    "each. Output structured JSON only."
)


def contrast_prompt(name: str, persona: str, existing: list[LoreEntry],
                    others: list[dict]) -> str:
    """`others`: [{name, persona, digest}] — the rest of the cast to diverge from."""
    cast = []
    for o in others:
        snippet = (o.get("persona") or "")[:240]
        cast.append(f"— {o['name']}: {snippet}\n  established: {o.get('digest', '(none)')}")
    return (
        f"TARGET: {name}\nPERSONA:\n{persona or '(thin)'}\n\n"
        f"ALREADY ESTABLISHED FOR {name} (don't duplicate):\n{_facet_digest(existing)}\n\n"
        f"THE REST OF THE CAST:\n" + ("\n".join(cast) or "(no one else)") + "\n\n"
        f"Invent 2-4 NEW exemplars that make {name} clearly distinct from the others above. "
        "Output structured JSON only.")


def interview_prompt(name: str, persona: str, entries: list[LoreEntry],
                     messages: list[dict]) -> str:
    """Flatten persona + established exemplars + the conversation into one user turn."""
    convo = []
    for m in messages or []:
        who = "Writer" if (m.get("role") == "user") else "You"
        txt = (m.get("content") or "").strip()
        if txt:
            convo.append(f"{who}: {txt}")
    parts = [
        f"CHARACTER: {name}",
        f"PERSONA / WHAT WE KNOW:\n{persona or '(thin — develop them with the writer)'}",
        f"EXEMPLARS ESTABLISHED SO FAR:\n{_facet_digest(entries)}",
        "CONVERSATION:\n" + ("\n".join(convo) if convo else "(the interview is just starting)"),
        "Respond to the writer's latest message (or, if the interview is just starting, open it "
        "with a specific, inviting question about who this character is). Commit any newly-agreed "
        "exemplars in `facets`. Output structured JSON only.",
    ]
    return "\n\n".join(parts)
