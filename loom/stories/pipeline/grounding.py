"""Grounding & anti-genericness toolkit for character/story generation.

Why this exists: a single structured LLM call returns the MEAN of its training data — ornate
epithets, abstract theme-word salad, cliché "The Adjective Noun" titles. The research-backed fixes,
all gathered here so the pipeline can share them:

  • LITERARY-MINIMALIST guidance — one lean, standing nudge (MINIMALISM) toward restraint and
    concreteness, injected where the deleted "craft lorebook" used to inject storytelling theory. A
    codified craft corpus just pulled output back toward the trained mean (generic); a spare stance
    that says "say less, be specific" pushes the other way.
  • POSITIVE concreteness targets — phrase the anti-fluff guidance as things to DO, not bans. Telling
    a model "don't be clichéd" raises clichés (the "Pink Elephant" effect, arXiv:2402.07896).
  • CLICHÉ POST-FILTER — bans belong at filter time, not in the prompt: detect the fluff shapes and
    regenerate-on-match (looks_cliche / clichés_in).
  • ADAPTATION basis — build each character from a wound→lie→coping chain + attachment + want/need +
    if-then signatures (Level-2 characteristic adaptations), NOT a Big-Five trait profile. Traits are
    "the psychology of the stranger" (McAdams) — independent dials that average a person into a type
    and can't hold a contradiction; adaptations are tensions that GENERATE behaviour. See ADAPTATION.

Pure/data module — retrieval helpers lazy-import the store so there's no import cycle.
Self-check: python -m loom.stories.pipeline.grounding
"""
from __future__ import annotations

import re

# ── Positive concreteness targets (append to authoring system prompts) ──────────────────────────
# Phrased as DO, not DON'T (Pink Elephant). The one hard "don't" is enforced by the post-filter below.
CONCRETENESS = (
    "WRITE CONCRETE — a specific person observed, not a concept in a costume:\n"
    "• Give them a plain name and a plain function (a job or a relationship), in ordinary words.\n"
    "• Anchor every trait in ONE observable particular: a thing they own and why, a habit their hands "
    "have, one line they actually say in plain speech, what they do when afraid or caught out.\n"
    "• Put in ONE real contradiction — something they do that cuts against what they claim or value.\n"
    "• Prefer the small and ordinary to the cosmic. A real person doing a real thing in a real room "
    "lands deeper than any prophecy. Use plain words; if a sentence sounds impressive, make it true "
    "and specific instead."
)

# ── Literary-minimalist stance — the standing guidance where the craft lorebook used to inject theory.
# One lean nudge toward restraint + concreteness; DO-phrased. Replaces the deleted _craft corpus.
MINIMALISM = (
    "WRITE WITH RESTRAINT — the literary-minimalist stance: say less, trust the reader more.\n"
    "• Prefer the plain word to the impressive one. If a sentence sounds like writing, make it true "
    "and specific instead.\n"
    "• State, don't underline. Give the concrete detail and stop; let the reader feel its weight "
    "without being told what to feel.\n"
    "• Leave things out. Imply emotion through action and object; cut the explanation, the adjective "
    "stack, the recap of what just happened.\n"
    "• No ornament: no epithets, no abstract-noun grandeur, no 'the very fabric of'. One exact image "
    "beats a paragraph of atmosphere."
)

# ── Cliché post-filter (regenerate-on-match) ────────────────────────────────────────────────────
# Genuine fantasy-fluff markers we keep seeing. Bans live HERE (filter time), never in the prompt.
_CLICHE_PHRASES = [
    "tapestry", "harbinger", "living relic", "coiled serpent", "the very fabric", "echoes of",
    "symphony of", "dance of", "testament to", "indomitable", "a vacuum", "whispers of",
    "the weight of", "the cost of", "beneath his skin", "beneath her skin", "fragile thread",
    "double-edged", "unbreakable bond", "shadow of his former", "shadow of her former",
]
# "The <cliché-adjective> <cliché-noun>" titles, and "... of <cliché-noun>" titles.
_CLICHE_ADJ = {"fractured", "shattered", "hollow", "gilded", "broken", "fading", "dying", "last",
               "forgotten", "eternal", "silent", "crimson", "golden", "final", "unspoken", "twisted",
               "veiled", "endless", "burning", "frozen", "bitter", "sacred"}
_CLICHE_NOUN = {"veil", "mask", "shadow", "shadows", "ashes", "bones", "cage", "throne", "crown",
                "flame", "embers", "requiem", "lament", "reckoning", "threshold", "abyss", "void",
                "covenant", "accord", "legacy", "prophecy", "destiny", "oblivion"}
_THE_ADJ_NOUN = re.compile(r"^\s*the\s+(\w+)\s+(\w+)\s*$", re.I)
_OF_NOUN = re.compile(r"\bof\s+(\w+)\s*$", re.I)


def cliches_in(text: str) -> list[str]:
    """Every fluff marker found in `text` (lowercased phrases). Empty list = clean."""
    if not text:
        return []
    low = text.lower()
    hits = [p for p in _CLICHE_PHRASES if p in low]
    m = _THE_ADJ_NOUN.match(text)
    if m and (m.group(1).lower() in _CLICHE_ADJ or m.group(2).lower() in _CLICHE_NOUN):
        hits.append(f"cliché title pattern: {text.strip()!r}")
    m2 = _OF_NOUN.search(text)
    if m2 and m2.group(1).lower() in _CLICHE_NOUN:
        hits.append(f"cliché 'of {m2.group(1)}' title: {text.strip()!r}")
    return hits


def looks_cliche(text: str) -> str | None:
    """The first reason `text` reads as fluff, or None. For single fields (role, a beat title)."""
    h = cliches_in(text)
    return h[0] if h else None


# ── The ADAPTATION basis — depth as a causal chain of tensions, not a trait profile ──────────────
# Replaces the Big-Five facet palette. Big Five is a Level-1 DISPOSITIONAL model (McAdams' "psychology
# of the stranger") — five independent dials that average a person into a type and can't hold a
# contradiction. Depth lives one layer down, in Level-2 CHARACTERISTIC ADAPTATIONS: a wound → a lie →
# a coping strategy, an attachment style, want-vs-need, and if-then situational signatures. Every link
# here is a TENSION, not a dial; each generates behaviour instead of describing it. Grounding: schema
# therapy (Young — schemas + surrender/avoid/overcompensate modes), attachment (Bowlby/Ainsworth),
# defence maturity (Vaillant), Mischel's if-then behavioural signatures.
ADAPTATION = (
    "BUILD THE CHARACTER FROM A WOUND, NOT A TRAIT LIST — depth is a tension, never a profile. Give "
    "this person a causal chain, each link a specific fact rather than a label:\n"
    "• WOUND — one concrete unmet need or injury from before the story (a thing that happened, named "
    "plainly), not the word 'trauma'.\n"
    "• LIE — the belief the wound installed, in their own words ('I'm only safe if I'm useful').\n"
    "• COPING — how the lie runs their behaviour: do they SURRENDER to it, AVOID what triggers it, or "
    "OVERCOMPENSATE against it? Overcompensation is the richest seam — the arrogant one who feels "
    "worthless, the caretaker who can't accept care.\n"
    "• ATTACHMENT — how they do closeness: secure; anxious (clings, dreads being left); avoidant "
    "(wants it and flees it); or disorganized (wants and fears the same person). Show it in one "
    "relationship, not as a label.\n"
    "• WANT vs. NEED — what they consciously chase versus what would actually heal them; these two "
    "should pull against each other.\n"
    "• IF-THEN — one or two situational contradictions: warm with strangers and cutting with a "
    "sibling, brave at work and a coward at home. A person is their inconsistencies, not an average.\n"
    "Never name the framework in the prose — instantiate it as a specific person doing specific things."
)


def declichify_titles(provider, board: dict) -> dict:
    """Post-filter for storyboard beat titles: regenerate any clichéd ones ("The Last Stand", "The
    Cost of Balance") to name a concrete object or action from that beat. Mutates + returns `board`.
    Bans at filter time, not in the prompt. No-op when nothing is clichéd or the model misbehaves."""
    beats = board.get("beats") or []
    bad = [(i, b) for i, b in enumerate(beats) if looks_cliche(str(b.get("title", "")))]
    if not bad:
        return board
    listing = "\n".join(
        f"[{i}] {b.get('title','')} — {(b.get('summary') or b.get('narrative') or '')[:160]}"
        for i, b in bad)
    schema = {"type": "object", "additionalProperties": False, "required": ["titles"],
              "properties": {"titles": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["index", "title"],
                  "properties": {"index": {"type": "integer"},
                                 "title": {"type": "string", "description": "plain title naming a "
                                           "concrete object or action in this beat — never 'The "
                                           "Adjective Noun'"}}}}}}
    try:
        out = (provider.generate_text(
            system="You retitle story chapters. Replace each clichéd title with a plain one that names "
                   "a concrete object or action actually in that beat. No 'The Adjective Noun' shapes.",
            prompt=f"Retitle these beats:\n{listing}", emits=schema).data) or {}
    except Exception:  # noqa: BLE001
        return board
    for t in out.get("titles", []):
        i = t.get("index")
        new = str(t.get("title", "")).strip()
        if isinstance(i, int) and 0 <= i < len(beats) and new and not looks_cliche(new):
            beats[i]["title"] = new
    return board


def demo() -> None:
    assert looks_cliche("The Fractured Mask")
    assert looks_cliche("The Gilded Cage")
    assert looks_cliche("The Road of Bones")
    assert looks_cliche("his magic a coiled serpent beneath his skin")
    assert looks_cliche("The Living Relic of the line")
    assert not looks_cliche("The Widow Hesta's Orchard")
    assert not looks_cliche("village healer")
    assert not looks_cliche("The Workroom")
    class _NoProv:  # declichify must NOT call the model when nothing is clichéd
        def generate_text(self, **k):
            raise AssertionError("model called for clean titles")
    assert declichify_titles(_NoProv(), {"beats": [{"title": "The Workroom"}]})["beats"][0]["title"] == "The Workroom"
    assert "WOUND" in ADAPTATION and "OVERCOMPENSATE" in ADAPTATION and "IF-THEN" in ADAPTATION
    print("grounding demo ok — adaptation basis + cliché filter")


if __name__ == "__main__":
    demo()
