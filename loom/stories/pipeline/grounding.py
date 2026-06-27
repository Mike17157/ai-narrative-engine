"""Grounding & anti-genericness toolkit for character/story generation.

Why this exists: a single structured LLM call returns the MEAN of its training data — ornate
epithets, abstract theme-word salad, cliché "The Adjective Noun" titles. The research-backed fixes,
all gathered here so the pipeline can share them:

  • CRAFT / PSYCHE retrieval — inject the existing _craft lorebook + an IPIP Big-Five behavioural
    lorebook into generation prompts (we already author the workshop this way; generation flew blind).
  • POSITIVE concreteness targets — phrase the anti-fluff guidance as things to DO, not bans. Telling
    a model "don't be clichéd" raises clichés (the "Pink Elephant" effect, arXiv:2402.07896).
  • CLICHÉ POST-FILTER — bans belong at filter time, not in the prompt: detect the fluff shapes and
    regenerate-on-match (looks_cliche / clichés_in).
  • IPIP Big-Five facets (public domain, ipip.ori.org) — ground each character in concrete behaviour
    instead of the model's averaged priors. One source of truth here; the lorebook is seeded from it.

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


# ── IPIP Big-Five facets — the behavioural grounding layer (public domain, ipip.ori.org) ─────────
# domain -> list of (facet, HIGH behavioural marker, LOW behavioural marker). Distilled from the
# IPIP-NEO facet scales; markers are plain observable tendencies, not adjectives.
BIG_FIVE: dict[str, list[tuple[str, str, str]]] = {
    "Openness": [
        ("Imagination", "drifts into vivid daydreams, invents possibilities", "stays with the literal and concrete"),
        ("Artistic interest", "stops to notice beauty, moved by art/music", "indifferent to art, unmoved by scenery"),
        ("Emotionality", "feels emotions strongly and names them", "rarely notices or shares feelings"),
        ("Adventurousness", "seeks the unfamiliar, changes routine", "clings to the familiar, dislikes change"),
        ("Intellect", "chases abstract ideas and puzzles", "avoids theory, prefers the practical"),
        ("Liberalism", "questions authority and tradition", "defends the established way of doing things"),
    ],
    "Conscientiousness": [
        ("Self-efficacy", "confident they can handle tasks", "doubts their own competence"),
        ("Orderliness", "keeps things tidy and planned", "leaves mess, works in chaos"),
        ("Dutifulness", "keeps promises, follows the rules", "bends rules, lets obligations slide"),
        ("Achievement-striving", "drives hard toward goals", "content to do the minimum"),
        ("Self-discipline", "finishes what they start", "abandons tasks, easily distracted"),
        ("Cautiousness", "thinks before acting", "acts on impulse, then deals with it"),
    ],
    "Extraversion": [
        ("Friendliness", "warms to people fast", "keeps a reserved distance"),
        ("Gregariousness", "seeks crowds and company", "drained by groups, prefers solitude"),
        ("Assertiveness", "takes charge, speaks up", "hangs back, lets others lead"),
        ("Activity level", "always busy, fast-paced", "unhurried, slow and deliberate"),
        ("Excitement-seeking", "courts risk and thrill", "avoids danger and loud stimulation"),
        ("Cheerfulness", "laughs easily, radiates good mood", "rarely shows joy, flat affect"),
    ],
    "Agreeableness": [
        ("Trust", "assumes others mean well", "suspects hidden motives"),
        ("Sincerity", "plain and straight with people", "manipulates, flatters to get their way"),
        ("Altruism", "goes out of their way to help", "puts their own needs first"),
        ("Cooperation", "yields to keep the peace", "stands their ground, picks fights"),
        ("Modesty", "downplays themselves", "claims credit, talks themselves up"),
        ("Sympathy", "softened by others' pain", "hard-nosed, unmoved by sob stories"),
    ],
    "Neuroticism": [
        ("Anxiety", "expects things to go wrong, worries", "stays calm, untroubled"),
        ("Anger", "flares up fast when crossed", "slow to anger, lets things go"),
        ("Depression", "sinks into low moods", "rarely downcast, bounces back"),
        ("Self-consciousness", "fears judgement, easily embarrassed", "unbothered by what others think"),
        ("Immoderation", "gives in to cravings and urges", "resists temptation easily"),
        ("Vulnerability", "panics under pressure", "steady in a crisis"),
    ],
}


def facet_palette() -> str:
    """A compact Big-Five facet menu for prompts: forces full-person coverage so a character is built
    from a deliberate, distinctive trait PROFILE — not the model's default 'brooding hero' average."""
    lines = ["BIG-FIVE FACET PALETTE — give this character a DISTINCTIVE position on these 30 facets "
             "(most people are mixed: high on some, low on others). Pick a handful that define them and "
             "express each through concrete behaviour, not the trait word:"]
    for domain, facets in BIG_FIVE.items():
        lines.append(f"  {domain}: " + "; ".join(
            f"{f} (high: {hi} / low: {lo})" for f, hi, lo in facets))
    return "\n".join(lines)


def psyche_entries() -> list[dict]:
    """Lorebook entries (one per facet) built from BIG_FIVE — the seed for the `_psyche` book."""
    out = []
    for domain, facets in BIG_FIVE.items():
        for facet, hi, lo in facets:
            out.append({
                "id": re.sub(r"[^a-z]+", "-", f"{domain}-{facet}".lower()).strip("-"),
                "title": f"{domain}: {facet}",
                "priority": 4,
                "keywords": [facet.lower(), domain.lower()] + facet.lower().split("-"),
                "content": (f"{domain} facet '{facet}'. A HIGH-{facet} person {hi}; a LOW-{facet} "
                            f"person {lo}. Show the level through a concrete action, never the label."),
            })
    return out


# ── Retrieval helpers (lazy import — no cycle with lorebook_store) ───────────────────────────────
def _retrieve_block(root, query: str, scope: str, k: int, header: str) -> str:
    try:
        from ...server.services.lorebook_store import retrieve, top_by_priority
        from ...server.services.lorebook import format_lore_block
    except Exception:  # noqa: BLE001 — pipeline must run even if the store is unavailable
        return ""
    try:
        hits = retrieve(root, query or "", [scope], top_k=k)
        if not hits:
            hits = top_by_priority(root, scope, k)
        return format_lore_block(hits, header=header) if hits else ""
    except Exception:  # noqa: BLE001
        return ""


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


# Per-PART anchor craft (by entry id): the principles bound to each authoring stage — the theory
# that actually governs THAT part, fed deterministically (bypasses retrieve()'s fuzzy ranking +
# one-per-facet collapsing) so it always lands. Each part gets its own lens, not a blunt dump:
#   premise    → the organic seed + the moral argument the whole thing will prove
#   character  → the INNER life (weakness/need, wound, lie, flawed theory) — not opponent design
#   antagonist → the opponent built to attack the hero's weakness over the same moral question
#   arc        → the CHANGE spine (arc shapes, moral argument, Truby's 7 steps)
#   storyboard → STRUCTURE/causality (7 steps, promise→payoff, escalation, try-fail)
#   chapters   → an arc's beats: escalate via causality, LAND the climax as self-revelation
#   ending     → the climax itself: self-revelation + world-bound stakes, surprising-yet-inevitable
_SECTION_ANCHORS = {
    "premise":    ["designing-principle", "moral-argument"],
    "character":  ["want-vs-need", "the-ghost", "lie-the-character-believes", "sacred-flaw"],
    "antagonist": ["opponent-attacks-weakness", "four-corner-opposition", "antagonist-mirror"],
    "arc":        ["arc-types", "growth-cycles", "moral-argument", "seven-key-steps"],
    "storyboard": ["seven-key-steps", "promise-progress-payoff", "escalation-causality"],
    "chapters":   ["self-revelation", "world-bound-climax", "escalation-causality", "try-fail-cycles"],
    "ending":     ["self-revelation", "world-bound-climax", "inevitable-surprising-ending"],
}
_CRAFT_HEADER = "CRAFT NOTES — modern storytelling principles to apply here:"


def craft_notes(root, query: str, k: int = 6, section: str = "") -> str:
    """Modern story-craft principles (the _craft lorebook) for an authoring prompt. When `section`
    is given, its ANCHOR entries are guaranteed first (deterministic — not subject to retrieval
    ranking), then query-retrieval fills in for breadth. Without a section it's pure retrieval."""
    anchors = _SECTION_ANCHORS.get(section, [])
    if not anchors:
        return _retrieve_block(root, query, "_craft", k, _CRAFT_HEADER)
    try:
        from ...server.services.lorebook_store import load_lorebook, retrieve, top_by_priority
        from ...server.services.lorebook import format_lore_block
    except Exception:  # noqa: BLE001
        return ""
    try:
        by_id = {e.id: e for e in load_lorebook(root, "_craft")}
        chosen, seen = [], set()
        for aid in anchors:                          # guaranteed anchors, in order
            e = by_id.get(aid)
            if e and e.id not in seen:
                chosen.append(e); seen.add(e.id)
        total = max(k, len(chosen))                  # keep the block ≈ k: anchors + a little breadth
        hits = retrieve(root, query or "", ["_craft"], top_k=k) or top_by_priority(root, "_craft", k)
        for e in hits:                               # retrieval fills the remaining slots
            if len(chosen) >= total:
                break
            if e.id not in seen:
                chosen.append(e); seen.add(e.id)
        return format_lore_block(chosen, header=_CRAFT_HEADER) if chosen else ""
    except Exception:  # noqa: BLE001
        return ""


def psyche_notes(root, query: str, k: int = 5) -> str:
    """Retrieved Big-Five behavioural markers (the _psyche lorebook) for a character prompt."""
    return _retrieve_block(root, query, "_psyche", k,
                           "PSYCHE NOTES — ground traits in these behaviours:")


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
    assert len(psyche_entries()) == 30
    assert "FACET PALETTE" in facet_palette() and "Anxiety" in facet_palette()
    print("grounding demo ok —", len(psyche_entries()), "facets")


if __name__ == "__main__":
    demo()
