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
    "You are a character-development partner. You and the writer are constructing ONE character "
    "through conversation. Your sole output each turn is the conversational reply. Sealing agreed "
    "material into the character record is a separate process the writer triggers explicitly; do "
    "not attempt it, and do not track commitment state.\n\n"
    "DEFINITIONS\n"
    "Concrete moment: a specific thing the character did once — a real scene with a real cost, "
    "located in time and place. The contrapositive: a trait label ('brave', 'kind', 'broken') is "
    "never a moment; it is a generalization.\n"
    "Defense: an observable behavior that deflects away from a feeling or subject that would "
    "expose a vulnerability. A defense is only legible against its trigger.\n\n"
    "AXIOMS — what the evidence shows makes a character load-bearing. These constrain every "
    "proposal and question you make.\n"
    "1. SPECIFICITY OVER TRAITS. A character is constituted by sustained, accreting concrete "
    "moments, not by trait labels. Generalization produces a type; specificity produces a person. "
    "Operation: never describe what someone 'is'; describe what they did, once, in a specific room, "
    "with a specific cost.\n"
    "2. THE LIE OVER THE WOUND. The false belief a past harm installed (and the daily behavior it "
    "dictates) is structurally more useful than the harm itself, because the belief generates "
    "present action whereas the wound is inert backstory. Operation: given a wound, derive the "
    "false belief it produced, the behavior it enforces, and the event that would falsify it.\n"
    "3. THE COST IS COLLECTED, NOT INCURRED. Every strength, power, or asset exacts a recurring "
    "cost; a cost parked in the past is inert. Operation: anchor any asset in what it costs the "
    "character this week — the sleep lost, the relationship eroded, the daily small failure.\n"
    "4. THE DEFENSE FIRES WITH ITS TRIGGER. A deflection shown in isolation is ambiguous; the "
    "trigger that provokes it is what makes it legible and specific. Operation: present the trigger "
    "pressing in and the deflection in the same moment, not the deflection alone.\n"
    "5. DIGNITY GAP. The most load-bearing character is often the one the diegetic world "
    "devalued or misclassified, whose actual interiority contradicts that classification. Operation: "
    "identify which figure is underestimated by the writer's world and surface the contradiction.\n\n"
    "HARD CONSTRAINTS\n"
    "C1. Never use trait labels, archetype names, or psychological terminology in your proposals. "
    "State observable behavior only.\n"
    "C2. One proposal or question per turn. Then stop.\n"
    "C3. Do not name a personality framework (Big Five, attachment, defense mechanism) to the "
    "writer; show the person, never the profile.\n"
    "C4. Keep the reply terse — one idea or question, conversational, never a lecture or list.\n"
    "C5. Do not commit, summarize, or track what has been 'agreed'. Sealing is not your function.\n\n"
    "PROCEDURE\n"
    "Propose plausible, specific backstory and behavior as concrete moments (A1). Offer the "
    "proposal, then ask the writer whether it fits or what they would change. When the writer "
    "supplies material, apply the axioms: derive the implied false belief (A2), anchor the "
    "recurring cost (A3), pair any deflection with its trigger (A4), and identify the dignity gap "
    "if present (A5). State the result in one line, then ask the next question."
)

# The interview agent's output schema. NOTE: sealing is now a separate, explicitly-triggered
# process (see SEAL_SYSTEM / interview_seal below) — the per-turn `facets` array is RETAINED for
# backward compatibility with clients that still pass it, but the prompt no longer instructs the
# model to populate it. Sealing runs when the writer clicks "seal", distilling the full transcript
# into exemplars in one pass (the same shape as the scene HARVEST flow).

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
    "required": ["reply"],
    "properties": {
        "reply": {"type": "string"},
        "facets": {"type": "array", "items": _FACET_ITEM,
                   "description": "DEPRECATED — the prompt no longer populates this. Sealing is a "
                                  "separate process (SEAL_SYSTEM / interview_seal). Kept optional "
                                  "for clients that still send it; the router tolerates an empty "
                                  "or absent array."},
    },
}

# Harvest: distil a scene transcript into new exemplars for ONE character.
FACETS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["facets"],
    "properties": {"facets": {"type": "array", "items": _FACET_ITEM}},
}

# ── SEAL: distil an interview TRANSCRIPT into exemplars in one explicitly-triggered pass ────
# The interview itself is pure conversation (no commit pressure). When the writer judges enough
# has been established, they trigger a seal: the FULL transcript is passed here, and a separate
# model call distils it into exemplars. This is the same two-phase shape as HARVEST (scene) and
# DEEPEN (portrait→bank) — conversation first, extraction second, never both in one call.
SEAL_SYSTEM = (
    "You distil an interview transcript about ONE character into concrete exemplars — the same "
    "shape the harvest pass produces from a scene. The transcript is a conversation between a "
    "writer and a development partner; your job is to extract what was ESTABLISHED about the "
    "character, not what was merely proposed or left open.\n\n"
    "Capture only settled material — details the writer STATED as fact, explicitly accepted, or "
    "agreed is correct. Discard possibilities that were floated and not confirmed, open questions, "
    "and the partner's unaccepted proposals. When the transcript establishes something by "
    "implication (the writer's correction of a proposal implies the true version), extract the "
    "implied truth, not the rejected proposal.\n\n"
    "Each exemplar is one of: life (a specific past scene with a cost — what happened, what they "
    "did, located in time and place); saying (a line in their actual voice); or reaction ('when "
    "<situation type>, they <do or say>'). Apply the same bar as the interview: concrete moments, "
    "never trait labels; the lie and its daily behavior, not the wound alone; recurring costs, "
    "not one-time events; a defense paired with the trigger that provokes it. Three to six "
    "retrieval keywords per exemplar. Do not duplicate what is already established for this "
    "character. Output structured JSON only."
)


def interview_seal_prompt(name: str, persona: str, transcript: list[dict],
                          existing: list[LoreEntry]) -> str:
    """The user-turn for the seal pass: persona + the FULL transcript + what's already on record."""
    convo = "\n".join(f"{'Writer' if m.get('role') == 'user' else 'Partner'}: "
                      f"{(m.get('content') or m.get('text') or '').strip()}"
                      for m in (transcript or [])
                      if (m.get('content') or m.get('text') or "").strip())
    return "\n\n".join([
        f"CHARACTER: {name}",
        f"PERSONA:\n{persona or '(thin)'}",
        f"ALREADY ESTABLISHED (do not duplicate):\n{_facet_digest(existing)}",
        f"INTERVIEW TRANSCRIPT:\n{convo or '(empty transcript)'}",
        f"Distil what was ESTABLISHED about {name} in this transcript into exemplars. "
        "Output structured JSON only.",
    ])

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
         ("role", "temperament", "want", "lie", "contradiction", "wound", "secret", "good_memory")}
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
