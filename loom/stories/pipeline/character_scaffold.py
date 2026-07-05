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

FACET_TYPES = ("life", "saying", "reaction", "guard")
# guard = a stage-gated DEFLECTION: an observable behaviour that steers away from a subject which
# would expose a secret, stated as pure conduct with NO reason ("When the forest comes up, she
# points the way to the mill and talks past it"). Surface/known tier — the narrator executes it
# blind, so the character keeps their own secret while the model playing them never learns it.

# Depth tiers — how deeply an exemplar is held, which gates who ever sees it in play
# (surface = anyone; known = people who know them; secret = self / plot-revealed only).
# Stored as a `tier:<t>` keyword on the entry (no schema/DB change). See play_context gating.
FACET_TIERS = ("surface", "known", "secret")


def entry_tier(entry) -> str:
    """Read an exemplar's depth tier from its keywords (default 'surface' — legacy/untagged
    exemplars stay fully visible, so gating is opt-in via re-deepen)."""
    for kw in (getattr(entry, "keywords", None) or []):
        if isinstance(kw, str) and kw.startswith("tier:"):
            t = kw[5:].strip().lower()
            if t in FACET_TIERS:
                return t
    return "surface"


def entry_when(entry) -> str:
    """The SETTING STAGE an exemplar is tied to (a Condition id), or '' for always-on. Only
    eligible in play while that stage is active — how a character changes flood-season vs festival."""
    for kw in (getattr(entry, "keywords", None) or []):
        if isinstance(kw, str) and kw.startswith("when:"):
            return kw[5:].strip()
    return ""


def select_exemplars(pool, *, allowed_tiers, active_conds, n, situ_cap=2):
    """Salience-based exemplar selection (the contextual-dialog shape: hard criteria filter
    over the FULL rule set → salience order → slotted budget). `pool` arrives ranked
    most-relevant-first (retrieval against the current beat, else priority order) and must
    NEVER be pre-truncated — a pre-cut pool is how the stage gate starved (situational
    entries sit at priority 0). Gates: tier (what the observer is cleared to read) and
    setting stage (`when:` content only while its stage holds). GUARDS (active deflections)
    are always kept — they're few and behaviourally load-bearing (a character must protect
    their secret every turn its stage holds). Then active-stage content AUGMENTS the core —
    up to `situ_cap` lines ON TOP of `n` baseline slots — so a long arc can't flatten someone
    into their stage reaction."""
    elig = [e for e in pool if entry_tier(e) in allowed_tiers
            and (not entry_when(e) or entry_when(e) in active_conds)]
    guards = [e for e in elig if (getattr(e, "facet", "") or "") == "guard"]
    rest = [e for e in elig if (getattr(e, "facet", "") or "") != "guard"]
    situ = [e for e in rest if entry_when(e)][:situ_cap]
    base = [e for e in rest if not entry_when(e)][:n]
    return guards + situ + base

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


def facet_to_entry(facet: dict, source: str = "interview") -> LoreEntry | None:
    """Map a generated facet dict → a LoreEntry in the character's lorebook. `facet` groups
    by type so the cards UI can section them and retrieval can cap one-per-type per turn.
    A `tier` (surface/known/secret) rides along as a `tier:<t>` keyword for play-time gating."""
    ftype = (facet.get("type") or "").strip().lower()
    if ftype not in FACET_TYPES:
        return None
    title = (facet.get("title") or "").strip()
    content = (facet.get("content") or "").strip()
    if not content:
        return None
    kws = [str(k).strip() for k in (facet.get("keywords") or []) if str(k).strip()]
    tier = (facet.get("tier") or "").strip().lower()
    if tier in FACET_TIERS:
        kws.append(f"tier:{tier}")
    when = (facet.get("when") or "").strip()
    if when:
        kws.append(f"when:{when}")
    return LoreEntry(
        id=f"{ftype}-{_slug(title or content)}",
        title=title or ftype.capitalize(),
        keywords=kws,
        content=content,
        facet=ftype,
        source=source,
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


# ── Deepen: harness → portrait (REASONED) → exemplar bank ───────────────────────────
# Two phases, because a fixed slot-contract produces slot-shaped content. Phase 1 is a
# psychologist actually THINKING the person through (reasoning enabled, free prose — mechanism,
# not labels). Phase 2 writes the exemplar bank FROM that portrait, and the model decides which
# moments this particular person needs captured. See [[bond-depth-weave]].

PORTRAIT_SYSTEM = (
    "You are thinking one fictional person through as a real psychological case — a clinician's "
    "working portrait, not a literary character sheet. Work from MECHANISM, not labels: given "
    "what happened to them, how exactly did it produce who they are now? Trace the causal chain. "
    "Attend to what theory-driven writing misses:\n"
    "- Real people are INCONSISTENT: they violate their own patterns, and the violations have "
    "their own logic. Find where this person contradicts themselves.\n"
    "- The defense has a DAILY COST — what it makes them bad at, what it ruins slowly.\n"
    "- The gap between how they feel from inside and how they read from outside.\n"
    "- What they're like ALONE, with no one to perform for.\n"
    "- What the person closest to them would say about them — and which part of it they'd deny.\n"
    "- What they genuinely enjoy, unconnected to any of it — pleasure that isn't symptom.\n"
    "- How they'd describe their own past, versus what actually happened.\n"
    "THE SECRET must be genuinely BURIED and layered — reason through it in depth: what they let "
    "the world see, what they admit only to themselves at 3am, and the thing underneath that they'd "
    "die before saying aloud — and WHY it's that deep (what it would cost them if it surfaced, who "
    "it would hurt). A shallow secret is the biggest failure here. "
    "Write plainly and concretely, era-appropriate, no clinical jargon in the final portrait. "
    "600-900 words of prose. This portrait is working material for a writer — make it the "
    "truest version of this person you can reason your way to."
)


def portrait_prompt(name: str, persona: str, harness: dict, world: str = "",
                    philosophy: str = "") -> str:
    h = {k: (harness.get(k) or "").strip() for k in
         ("role", "temperament", "want", "lie", "wound", "secret", "good_memory")}
    hl = "\n".join(f"  {k}: {v}" for k, v in h.items() if v) or "(thin)"
    return "\n\n".join(filter(None, [
        f"CHARACTER: {name}",
        f"PERSONA:\n{(persona or '')[:600]}",
        f"KNOWN ANCHORS (the given facts — reason from these, don't contradict them):\n{hl}",
        f"WORLD:\n{world}" if world else "",
        f"THE STORY'S CENTRAL QUESTION (their false belief is a stance on it):\n{philosophy}"
        if philosophy else "",
        f"Think {name} through and write the portrait.",
    ]))


DEEPEN_SYSTEM = (
    "You are given a psychological PORTRAIT of one person. Write the exemplar bank a roleplay "
    "model will imitate to BE them, scene after scene, consistently. YOU decide which moments "
    "matter — capture THIS person, not a checklist: the scenes that made them, the lines only "
    "they would say, the reactions that give them away. Include their inconsistencies and what "
    "they're like alone, not just their patterns. 12-20 exemplars, a mix of: life (a lived "
    "2-4 sentence scene from their past, specific enough to have happened once), saying (a line "
    "in their exact voice), reaction ('When <situation> → they <observable behavior>').\n"
    "TIER each exemplar by how DEEPLY it's held. This is the ENGINE of the character, so get it "
    "right:\n"
    "  surface — their daylight face; anyone who meets them reads this.\n"
    "  known   — what someone who actually KNOWS them has learned: softer habits, tells, history "
    "they share with people they trust.\n"
    "  secret  — the hidden truth itself, stated plainly (who they really are, what they did, what "
    "they know). This is the ANSWER KEY, used only when the story reveals it — it is NEVER shown to "
    "the narrator during ordinary play. A few of these.\n\n"
    "THE CRITICAL CRAFT: the narrator only ever sees SURFACE and KNOWN. So those exemplars must "
    "ALREADY be the OBSERVABLE SHADOW of the secret — the character must be fully playable and "
    "behave consistently with their hidden life from surface+known ALONE. For each secret, write "
    "the surface/known tells it would cast: the topics they steer around, the question that makes "
    "them go still, the thing they do too carefully, the over-correction, the flat deflection they "
    "give when pushed, the small lie they keep smooth. Someone reading only the surface should "
    "sense something is off and could even guess — WITHOUT the secret ever being stated. Do NOT "
    "rely on the narrator knowing the secret; it won't. Bake the consequence into the behavior.\n\n"
    "Texture: ordinary and lived, never twee — real rooms, chores, weather; no invented signature "
    "quirks. All sayings are ONE voice — same vocabulary, rhythm, education, era. 3-6 retrieval "
    "keywords each.\n\n"
    "SITUATIONAL CONTENT: you may be given the SETTING STAGES this world moves through (flood "
    "season, a siege, the dungeon opening). For each stage that would genuinely change THIS "
    "person, write 1-3 exemplars with that stage's id in `when` — how they act, what surfaces, "
    "what they hide or reveal WHILE IT HOLDS (a stage can bring out a new reaction, or lift the "
    "lid on a secret). Most exemplars are `when:''` (always). Situational ones are the exception "
    "that make each stage feel like a different person; only ever use an id from the list, never "
    "invent one.\n\n"
    "GUARDS — for every secret, write the DEFLECTIONS that keep it (type `guard`). A guard is a "
    "concrete, observable steer AWAY from the subject that would expose the secret: the topic they "
    "change, the place they'll never suggest going, the question they answer with a question, how "
    "they redirect and to what. State ONLY the behaviour, in the moment it triggers — 'When the "
    "forest comes up, she points the way to the mill and talks past it' — and NEVER the reason. "
    "tier `surface` or `known` (the narrator must be able to read it); `when` the stage where "
    "exposure is most dangerous, or '' if the guard is always up. Write a guard per secret per "
    "stage that threatens it. This is the mechanism: the character protects their own secret while "
    "the model playing them is never told what it is. Output structured JSON only."
)


def deepen_facets_schema(condition_ids=None) -> dict:
    """The deepen bank schema. `when` is constrained to the story's real Condition ids (+ '' for
    always-on) so situational exemplars can only key to stages that exist."""
    ids = [""] + [c for c in (condition_ids or []) if c]
    item = {
        "type": "object", "additionalProperties": False,
        "required": ["type", "tier", "when", "title", "keywords", "content"],
        "properties": {
            "type": {"type": "string", "enum": list(FACET_TYPES)},
            "tier": {"type": "string", "enum": list(FACET_TIERS)},
            "when": {"type": "string", "enum": ids,
                     "description": "a setting-stage id this exemplar is tied to (active only while "
                                    "that stage holds); '' = always true"},
            "title": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "content": {"type": "string"},
        },
    }
    return {"type": "object", "additionalProperties": False, "required": ["facets"],
            "properties": {"facets": {"type": "array", "items": item}}}


def deepen_prompt(name: str, portrait: str, existing: list[LoreEntry], conditions=None) -> str:
    parts = [
        f"CHARACTER: {name}",
        f"PORTRAIT (the person to capture):\n{portrait}",
    ]
    if conditions:
        stages = "\n".join(
            f"- {c.get('id')} — {c.get('name')}: {(c.get('effect') or c.get('description') or '')[:180]}"
            for c in conditions if c.get("id"))
        parts.append("SETTING STAGES (tag situational exemplars with these ids in `when`; '' otherwise):\n"
                     + stages)
    parts.append(f"ALREADY ESTABLISHED (don't duplicate):\n{_facet_digest(existing)}")
    parts.append(f"Write the exemplar bank for {name}.")
    return "\n\n".join(parts)


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
