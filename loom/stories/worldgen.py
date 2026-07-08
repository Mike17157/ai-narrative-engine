"""World generation — BOTTOM-UP by SYSTEMS, each GROUNDED IN A ROBUST TRADITION. Depth is combinatorial,
not hierarchical: you ACCRETE several magical systems and let them COLLIDE — but crucially, NOTHING is
invented from absolute zero. Each system draws on a real, ESTABLISHED foundation (classical elemental
magic, magical physics, magitech, a real mythology/folklore, alchemy, astrology, name-magic, a
death-tradition…) that already carries centuries of internal logic and cultural resonance, and
specializes it rigorously. That's why faerie magic feels deep — it draws on REAL fae folklore (true
names, cold iron, oaths, the courts), not a novel gimmick. Invented-from-nothing systems came out hollow
('Echo Weaving'); grounded ones inherit soul. The world = several robust traditions coexisting +
interfering; a concrete scenario EMERGES from the sharpest collision. Pure functions over a provider.
"""
from __future__ import annotations

from .genesis import _data

SYSTEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "foundation", "premise", "rules", "cost", "access", "tells", "interactions"],
    "properties": {
        "name": {"type": "string"},
        "foundation": {"type": "string"},  # the ESTABLISHED tradition it draws on (elemental/physics/magitech/mythology/…)
        "premise": {"type": "string"},   # how this world SPECIALIZES that foundation
        "rules": {"type": "array", "items": {"type": "string"}},   # HARD procedural rules, TRUE to the foundation's logic
        "cost": {"type": "string"},      # what wielding it demands / its hard constraint
        "access": {"type": "string"},    # who can use it and how (innate / learned / bound to a place, species, object)
        "tells": {"type": "string"},     # the concrete OBSERVABLE signature — how you'd notice it in a scene
        "interactions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["with", "effect"],
            "properties": {
                "with": {"type": "string"},     # the name of an already-invented system it collides with
                "effect": {"type": "string"},   # the EMERGENT consequence when the two meet (neither has it alone)
            }}},
    },
}

SYSTEM_SYS = (
    "You author ONE magical SYSTEM for a world, by taking a ROBUST, ESTABLISHED foundation and SPECIALIZING "
    "it rigorously for this world. Do NOT invent from absolute zero — a made-up gimmick comes out hollow. "
    "Depth and soul come from building on a tradition that ALREADY carries centuries of internal logic and "
    "resonance (the way faerie magic draws on REAL fae folklore — true names, cold iron, sworn oaths, the "
    "seelie/unseelie courts, changelings — not from a novel invention).\n"
    "Draw this system from ONE established `foundation` (state which):\n"
    "• ELEMENTAL — a real elemental tradition, taken seriously: Greek fire/water/air/earth/aether, or "
    "Chinese wuxing (wood-fire-earth-metal-water) with its generative & destructive cycles.\n"
    "• MAGICAL PHYSICS — magic that OBEYS or extends real physics: conservation, thermodynamics/entropy, "
    "fields & forces, resonance, optics, pressure, phase change. Rigorous, predictable.\n"
    "• MAGITECH — magic INDUSTRIALIZED: enchantment as manufacturing, standardized parts, power grids, "
    "guild-industry, supply chains, the economics and failure-modes of magical machines.\n"
    "• MYTHOLOGY / FOLKLORE — a real tradition: faerie lore, a proper pantheon (gods with real domains & "
    "demands), ancestor spirits, animism, demonology & pacts, psychopomps, the evil eye.\n"
    "• ALCHEMY — transmutation, the four humours, correspondences, the philosopher's stone, the great work.\n"
    "• ASTROLOGY / celestial influence · DIVINATION & fate · NECROMANCY / a death-tradition & underworld "
    "geography · NAME & WORD magic (true names, runes, sigils, binding contracts) · BLOODLINE / sanguine law.\n"
    "SPECIALIZE the foundation into THIS world's version — hard, procedural `rules` (3-6; 'doing X costs Y "
    "and triggers Z') that a reader could PREDICT and that stay TRUE to the foundation's inherited logic "
    "(fire obeys real combustion & heat; a pantheon's gods have real domains and demand real observance; "
    "magitech has standards and supply chains). Include one sharp CONSTRAINT and one exploitable EDGE CASE. "
    "Then `cost` (never free), `access` (who wields it / how), `tells` (its concrete observable signature).\n"
    "Draw from a DIFFERENT foundation than the systems already invented, so the world becomes a COMPOSITE "
    "of several robust traditions coexisting (an elemental art AND a magitech industry AND a folklore of "
    "spirits — the way a deep world layers them). The freshness is in the SPECIALIZATION and the "
    "COMBINATION, never in inventing new physics from nothing.\n"
    "COMPOSABLE — depth comes from systems COLLIDING, not from any one being deep. For every system already "
    "invented (listed below), declare an `interactions` entry: `with` = its name, `effect` = the EMERGENT "
    "consequence when the two meet that NEITHER has alone (a new possibility, a lethal combination, a "
    "forbidden loophole, a mutual cancellation). Make them genuinely interlock. Be DISTINCT from every "
    "prior system — different domain, different logic, different cost. JSON only."
)


def _systems_brief(systems: list[dict]) -> str:
    lines = []
    for s in systems:
        rules = "; ".join((s.get("rules") or [])[:3])
        lines.append(f"- {s.get('name', '?')} [foundation: {s.get('foundation', '?')}]: {s.get('premise', '')} "
                     f"(rules: {rules}; cost: {s.get('cost', '')})")
    return "\n".join(lines)


def accrete_systems(provider, seed: str = "", n: int = 4) -> list[dict]:
    """Build a world BOTTOM-UP: invent `n` radically distinct, procedurally-ruled systems one at a time,
    each declaring how it interacts with those already invented. Sequential BY DESIGN — a new system must
    see the prior ones to be distinct from them and to interlock. Returns the systems (interaction web
    embedded per system)."""
    if provider is None:
        return []
    n = max(2, min(int(n or 4), 7))
    seed = (seed or "").strip()
    out: list[dict] = []
    for i in range(n):
        prior = _systems_brief(out) if out else ""
        prompt = (
            (f"SEED (the world's vibe):\n{seed}\n\n" if seed else "")
            + (f"SYSTEMS ALREADY IN THIS WORLD — draw from a DIFFERENT established foundation than these, "
               f"and declare how your system INTERLOCKS with each (real traditions interfere: cold iron "
               f"disrupts both fae AND magitech; a forge-god's fire feeds an elemental art):\n{prior}\n\n" if prior
               else "This is the FIRST system — ground it in a strong established tradition the rest will "
                    "coexist with.\n\n")
            + "Author ONE system: grounded in a real foundation, specialized rigorously, hard-ruled, composable."
        )
        res = provider.generate_text(system=SYSTEM_SYS, prompt=prompt, emits=SYSTEM_SCHEMA)
        s = _data(res)
        if isinstance(s, dict) and (s.get("name") or "").strip() and (s.get("rules") or []):
            # keep one interaction per real prior system (drop dangling refs + duplicates the model repeats)
            names, seen, kept = {x.get("name") for x in out}, set(), []
            for it in (s.get("interactions") or []):
                w = it.get("with") if isinstance(it, dict) else None
                if w in names and w not in seen:
                    seen.add(w); kept.append(it)
            s["interactions"] = kept
            out.append(s)
    return out


# ── Where the systems bite: the sharpest COLLISION is where a story wants to start (bottom-up). ──
COLLISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["systems", "friction", "who_is_caught", "concrete_scene"],
    "properties": {
        "systems": {"type": "array", "items": {"type": "string"}},   # the 2-3 systems whose interaction bites here
        "friction": {"type": "string"},        # the emergent tension/danger/opportunity their collision creates
        "who_is_caught": {"type": "string"},   # an ordinary, specific person stuck in that friction
        "concrete_scene": {"type": "string"},  # a hyper-concrete opening: one place, one moment, real sensory detail
    },
}

COLLISION_SYS = (
    "You are given several procedurally-ruled systems and their interactions. Find the SHARPEST COLLISION "
    "— the pair (or trio) of systems whose interaction creates the most story-fertile FRICTION (a lethal "
    "combination, a forbidden loophole, an impossible bind) — and ground a story's OPENING there, bottom-up. "
    "Name the `systems` in play; state the `friction` (the emergent tension their rules create together); "
    "`who_is_caught` = one ORDINARY, specific person the friction lands on (not a hero — a smith, a "
    "ferrywoman, a debtor); `concrete_scene` = a hyper-concrete opening moment in ONE place, rendered in "
    "real sensory detail (light, smell, a single object), where the collision is about to matter. Small and "
    "real, with the systems' weight behind it. JSON only."
)


# ── Particularity mode — the anti-slop. Generate the world as lived, voiced FRAGMENTS; the system stays
# implicit. Encyclopedia specs are slop by construction; a specific person doing a specific thing, with
# the rule withheld, is how a real world reads (and how TWI reads). ──
PARTICULARS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["particulars"],
    "properties": {"particulars": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["kind", "text", "implies"],
        "properties": {
            "kind": {"type": "string", "enum": ["practice", "object", "saying", "moment"]},
            "text": {"type": "string"},       # the voiced, concrete fragment shown to the reader
            "implies": {"type": "string"},    # the latent rule it hints at — for the pipeline, NEVER shown/stated
        }}}},
}

PARTICULARS_SYS = (
    "Write concrete, LIVED fragments of a world — the way a real novel reveals its world, never the way a "
    "wiki catalogues it. Each `text` is ONE specific, voiced fragment: a `practice` (one kind of person "
    "doing one specific thing, with a rule-of-thumb or superstition they follow), an `object` (a specific "
    "thing with a history and a use), a `saying` (something people actually say, that carries a rule), or a "
    "`moment` (a small specific scene where the world shows itself).\n"
    "IRON RULES (this is the whole point — break them and it becomes AI slop):\n"
    "• SHOW, never explain. IMPLY how the world works; NEVER state a rule as a rule. The reader should "
    "infer there's a system and never be handed one. Withhold the mechanism — the gap is the magic.\n"
    "• PLAIN AND EXACT: a reader knows precisely what happened in the fragment — who did what, and to "
    "what — even though the rule behind it stays unstated. Withholding the SYSTEM is NOT the same as "
    "vague writing: no ornate, flowery, or riddling phrasing; state the concrete fact cleanly, in plain "
    "words.\n"
    "• A real VOICE and specific PARTICULARITY: one concrete person/thing, one weird exact detail, plain "
    "words. Not neutral-omniscient. Not exhaustive — one telling detail stands for the whole, never a "
    "balanced survey.\n"
    "• Ground it in a real tradition's LOGIC (alchemy, fae-lore, astrology, folk custom, a death-rite…) but "
    "NEVER name the tradition and NEVER coin a magic-system name. FORBIDDEN: adjective-noun-magic names "
    "('Verdant Rootbind Sorcery'), capitalized System Names, spec-sheet rules, costs/edge-cases stated as "
    "such, the words 'practitioner', 'harness', 'attune', 'channel', 'imbue'. If it sounds like a fantasy "
    "wiki, delete it and write what a person actually did.\n"
    "• STRANGENESS BUDGET: AT MOST 2 of the fragments may touch anything uncanny — the rest are purely "
    "mundane texture (work, tools, money, food, weather, neighbors), because the ordinary fragments are "
    "what make the strange one land. A world where every noun is enchanted reads as twee, not deep. Match "
    "the substrate's stated era/technology exactly — no mixing cart-and-mule with vans.\n"
    "In `implies`, privately note the latent rule the fragment hints at (for our records — this is NEVER "
    "part of the shown text; for a mundane fragment it is simply 'none'). Make the fragments belong to ONE "
    "coherent world. JSON only."
)


def gen_particulars(provider, seed: str = "", n: int = 6, substrate: dict | None = None) -> list[dict]:
    """Anti-slop world gen: emit `n` lived, voiced fragments (systems implicit). When a `substrate` is
    given, the fragments draw quietly on its traditions/ache/people so they cohere into ONE world."""
    if provider is None:
        return []
    n = max(3, min(int(n or 6), 10))
    seed = (seed or "").strip()
    sub = f"THE INVISIBLE SUBSTRATE (draw on its tradition-logic, its ache, its people — NEVER name a tradition or explain a rule):\n{_substrate_brief(substrate)}\n\n" if substrate else ""
    prompt = ((f"THE WORLD'S VIBE:\n{seed}\n\n" if seed else "") + sub
              + f"Write {n} lived fragments of this world — practices, objects, sayings, small moments. Show, "
                "withhold, voice. The systems stay implicit.")
    res = provider.generate_text(system=PARTICULARS_SYS, prompt=prompt, emits=PARTICULARS_SCHEMA)
    return [p for p in _data(res).get("particulars", []) if isinstance(p, dict) and (p.get("text") or "").strip()]


# ── The invisible SUBSTRATE — the skeleton the author knows but never shows: a few real traditions, ONE
# shared human ache the whole world circles, the place, and recurring people the fragments orbit. ──
SUBSTRATE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["preoccupation", "place", "traditions", "people", "forces"],
    "properties": {
        "preoccupation": {"type": "string"},   # the ONE ache the whole world circles (its soul) = the ROOT
        "place": {"type": "string"},
        "traditions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "logic"],
            "properties": {"name": {"type": "string"}, "logic": {"type": "string"}}}},
        "people": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "life"],
            "properties": {"name": {"type": "string"}, "life": {"type": "string"}}}},
        "forces": {"type": "array", "items": {   # camps that have formed around the ache (the proto-creeds); [] if none
            "type": "object", "additionalProperties": False, "required": ["name", "stance"],
            "properties": {"name": {"type": "string"}, "stance": {"type": "string"}}}},
    },
}

SUBSTRATE_SYS = (
    "Author the INVISIBLE SUBSTRATE of a world — the skeleton a novel's author knows but NEVER shows the "
    "reader. Give: 1-2 real magical TRADITIONS (each a plain internal `name` + its hard `logic`, drawn from "
    "REAL folk magic / alchemy / astrology / name-magic / death-rites / faerie lore — do NOT invent from "
    "absolute zero, build on established traditions so they carry inherited resonance); ONE shared human "
    "`preoccupation` the whole world secretly circles (an ache — grief, debt, being unseen, owing the "
    "dead…); the `place` (one specific region/town, concrete — and STATE THE ERA/technology plainly in it, "
    "e.g. 'contemporary', '1890s', so every later layer matches); and 2-3 recurring ordinary `people` "
    "(name + their small hard life) whom fragments and scenes will orbit.\n"
    "FORCES — the camps that have quietly formed around the ache: 2-3 factions/faiths/orders that each "
    "answer it differently and each believe they are right (this is where a thematic conflict is SEEDED, so "
    "the story's tensions grow from the world, not from nowhere). Each: a `name` (ONE coined word — never "
    "two words, never 'The Adjective Noun') and a `stance` (their answer to the ache, and why a decent "
    "person would hold it — none is a villain). Keep them grounded and half-buried in ordinary life, NOT "
    "epic armies. Return `forces`: [] only if the ache genuinely divides no one.\n"
    "RESTRAINT: the world is otherwise plainly REAL — work, weather, money, family. The traditions are "
    "quiet, marginal, half-doubted; most people are too busy to think about them. ONE strangeness, rationed, "
    "in an ordinary world beats a world where every noun is enchanted (that reads as twee). This substrate "
    "is NEVER shown to a reader; it exists only so the world's fragments cohere into ONE thing with a soul. "
    "JSON only."
)


def _substrate_brief(s: dict | None) -> str:
    if not s:
        return ""
    lines = [f"ACHE (the world's soul): {s.get('preoccupation', '')}", f"PLACE: {s.get('place', '')}"]
    for t in (s.get("traditions") or []):
        lines.append(f"TRADITION — {t.get('name', '')}: {t.get('logic', '')}")
    for fo in (s.get("forces") or []):
        if (fo.get("name") or "").strip():
            lines.append(f"FORCE — {fo.get('name', '')}: {fo.get('stance', '')}")
    for p in (s.get("people") or []):
        lines.append(f"PERSON — {p.get('name', '')}: {p.get('life', '')}")
    return "\n".join(lines)


# ── PREMISE — a targeted generator for the thing the batch never made: a concrete dramatic SITUATION
# (a person who wants something, an obstacle, stakes, a spark), NOT a theme or a mood. ──
PREMISE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["logline", "protagonist", "want", "obstacle", "stakes", "spark"],
    "properties": {
        "logline": {"type": "string"},       # the one-line situation
        "protagonist": {"type": "string"},   # a specific ORDINARY person (name + what they do)
        "want": {"type": "string"},          # the concrete, mundane external thing they're after RIGHT NOW
        "obstacle": {"type": "string"},      # the specific person/thing in the way
        "stakes": {"type": "string"},        # the concrete thing they LOSE if they fail
        "spark": {"type": "string"},         # the event that kicks it off NOW
    },
}

PREMISE_SYS = (
    "Generate a real PREMISE — a concrete dramatic SITUATION, NOT a theme, mood, or 'a world where…'. A "
    "specific ORDINARY person WANTS something concrete and mundane and immediate; a specific person or "
    "thing is IN THE WAY; there are real, specific STAKES (a thing they will lose — a person, a place, a "
    "living, their standing — not 'the fate of the world'); and a SPARK, the event that forces it to start "
    "NOW. Ground it in the seed's world, but any magic/atmosphere is BACKGROUND PRESSURE, never the "
    "subject — the story is a person trying to get a concrete thing while the world squeezes them.\n"
    "RESTRAINT (contrivance is the failure mode):\n"
    "• ONE pressure, not a stack. Do NOT synchronize a deadline + a checkpoint + a discovery + a debt onto "
    "the same day — that reads as a mousetrap, not a life. Stakes may be small and personal; quiet is fine.\n"
    "• A SKEPTIC must buy every non-strange element: the economics (who buys what, why), the distances, why "
    "each person is where they are, what neighbors/officials would actually do. If a reader would ask "
    "'wait, why would he—?', fix it before writing it.\n"
    "• HONOR the seed's shape: if the seed asks for 'a day in the life' or a quiet register, the premise is "
    "a small situation inside an ordinary working day — the day stays a working day; the strange thing "
    "INTRUDES on it. No manufactured countdowns.\n"
    "NEVER state a theme; NEVER write 'a town haunted by…'. A situation with a want, an obstacle, stakes, "
    "and a spark. JSON only."
)


def build_premise(provider, seed: str = "") -> dict:
    """Targeted: a concrete dramatic situation (want/obstacle/stakes/spark) from the `seed`. Not a theme."""
    if provider is None:
        return {}
    seed = (seed or "").strip()
    prompt = (f"SEED (the world / vibe):\n{seed}\n\n" if seed else "") + \
             "Give the concrete PREMISE — one ordinary person, a want, an obstacle, real stakes, a spark."
    res = provider.generate_text(system=PREMISE_SYS, prompt=prompt, emits=PREMISE_SCHEMA)
    d = _data(res)
    return d if isinstance(d, dict) and d.get("logline") else {}


def build_substrate(provider, seed: str = "") -> dict:
    """Author the invisible substrate for a `seed` — the world's ache + traditions + place + people."""
    if provider is None:
        return {}
    seed = (seed or "").strip()
    prompt = (f"SEED (the idea / vibe to grow the world from):\n{seed}\n\n" if seed else "") + \
             "Author the invisible substrate — the traditions, the one ache they all circle, the place, the people."
    res = provider.generate_text(system=SUBSTRATE_SYS, prompt=prompt, emits=SUBSTRATE_SCHEMA)
    d = _data(res)
    return d if isinstance(d, dict) and d.get("preoccupation") else {}


# ── SEED-START — the whole story-start in ONE call (see memory: minimal-prompt-facts). The
# fact sheet is the product: a person with a simple profound want, a real place, one pressure.
# NO psych rig (no lie/wound/secret/traits) — character depth is EARNED in play and captured by
# the scribe, not pre-installed. Everyone beyond 1-2 named people is born on stage, in play. ──

SEED_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "protagonist", "place", "pressure", "people", "facts", "strange"],
    "properties": {
        "title": {"type": "string"},
        "protagonist": {"type": "object", "additionalProperties": False,
                        "required": ["name", "life", "want"],
                        "properties": {"name": {"type": "string"},
                                       "life": {"type": "string",
                                                "description": "what they do and their circumstances, 1-2 plain sentences"},
                                       "want": {"type": "string",
                                                "description": "ONE simple, profound motivation, stated plainly"}}},
        "place": {"type": "object", "additionalProperties": False,
                  "required": ["name", "era", "description"],
                  "properties": {"name": {"type": "string"},
                                 "era": {"type": "string", "description": "period + technology, stated plainly"},
                                 "description": {"type": "string"}}},
        "pressure": {"type": "string",
                     "description": "the ONE concrete situation squeezing the protagonist right now"},
        "people": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "life", "want"],
            "properties": {"name": {"type": "string"}, "life": {"type": "string"},
                           "want": {"type": "string"}}}},
        "facts": {"type": "array", "maxItems": 4,
                  "items": {"type": "string"},
                  "description": "2-4 short mundane world facts (work, money, weather, how things are done here)"},
        "strange": {"type": "string",
                    "description": "the ONE strange thing at the edge of this story ('' if none)"},
    },
}

SEED_SYS = (
    "Set up the START of a story from the seed — the bare minimum; the world grows in play.\n"
    "People have SIMPLE, PROFOUND motivations — a better place for their kids, being square with "
    "what they owe, justice for something specific, getting home. ONE want each, stated plainly. "
    "NO personality profiles, no assigned traits, no pre-installed secrets or wounds — character "
    "emerges from what people DO once play begins.\n"
    "ONE pressure, not a stack; a skeptic must buy every non-strange element (economics, distances, "
    "why people are where they are). At most ONE strange thing, at the edge. Name at most two "
    "people besides the protagonist — everyone else is born on stage later. JSON only."
)


def exemplar_notes(root, query: str, k: int = 3, book: str = "_char_exemplars") -> str:
    """Reference STUDIES from an exemplar deck (real casts/places/premises/arcs of serials +
    anime), fetched by similarity — exemplar-conditioning shifts the register prior where
    instructions get performed instead of obeyed. Decks: _char_exemplars, _location_exemplars,
    _premise_exemplars, _arc_exemplars (scripts/seed_*_exemplars.py). '' when absent."""
    try:
        from ..server.services import lorebook_store as _LS
        hits = _LS.retrieve(root, query or "story", [book], top_k=k)
        return "\n\n".join(h.content for h in hits if h.content)
    except Exception:  # noqa: BLE001 — reference is best-effort, never block generation
        return ""


# ── CHARACTER BIRTH — transpose a real character (template) into a named story role. The model
# is bad at inventing a person from a rule, good at moving a real person into a new situation:
# the substance is borrowed, the model only does the analogical transfer. Output is a PROSE card
# the narrator reads directly; prominence sets the length (a walk-on stays a walk-on). ──

PROMINENCE_WORDS = {"background": 35, "supporting": 90, "major": 160, "protagonist": 200}

BIRTH_SYS = (
    "You write ONE character as a short PROSE study a novelist would keep — flowing prose, no "
    "field labels, no headings. Cover how they talk and carry themselves, what they want, and "
    "what they would never do, all through concrete particulars (objects, habits, a phrase or "
    "two) native to the story's setting. Plain, grounded prose. About {n} words — sized to how "
    "much the story leans on them; a minor character stays a sketch.\n"
    "When given a TEMPLATE (a real character as a MOLD), transpose their SHAPE — their angle on "
    "the world, how they deal with people — onto this new individual; give them their own name, "
    "setting, and details, and NEVER reuse the template's name or specifics. Transpose, don't copy."
)


def _template_source(root, query: str, rank: int = 0) -> str:
    """The rank-th most similar real character from `_char_sources` (raw mirrored substance)."""
    try:
        from ..server.services import lorebook_store as _LS
        hits = _LS.retrieve(root, query or "person", ["_char_sources"], top_k=max(3, rank + 1))
        return hits[rank].content if rank < len(hits) else (hits[-1].content if hits else "")
    except Exception:  # noqa: BLE001
        return ""


def _lead_name(card: str) -> str:
    """The leading proper-noun run of a card (the model is told to open with the name)."""
    m = re.match(r"([A-Z][\w'’]+(?:\s+[A-Z][\w'’]+){0,2})\b", (card or "").strip())
    return m.group(1) if m else ""


def birth_character(provider, *, role: str, story: str, prominence: str = "supporting",
                    root=None, name: str = "", template=None, rank: int = 0, known: str = "") -> dict:
    """Create ONE character. `template=None` → retrieve the best real match from `_char_sources`;
    `template=""` → no template (flesh the given facts, e.g. a protagonist the seed already
    defines); a string → use it verbatim. `known` = a brief of the cast already made, so this
    character coheres with them (shared household, no contradictions). Returns
    {name, prominence, role, card} ({} on failure)."""
    if provider is None:
        return {}
    n = PROMINENCE_WORDS.get(prominence, 90)
    tmpl = template if template is not None else _template_source(root, f"{role} {story}", rank)
    parts = [f"STORY:\n{story}", f"ROLE: {role}", f"IMPORTANCE: {prominence}"]
    if known:
        parts.append("PEOPLE ALREADY IN THIS STORY (stay consistent with them — shared household, "
                     f"ties, and facts; contradict nothing, invent no new named people):\n{known}")
    if name:
        parts.append(f"The character's name is {name} (keep it).")
    else:
        parts.append("Begin the study with the character's name.")
    if tmpl:
        parts.append(f"TEMPLATE (the mold — a real character; transpose their shape, not their "
                     f"details):\n{tmpl[:2000]}")
    parts.append("Write the character.")
    res = provider.generate_text(system=BIRTH_SYS.replace("{n}", str(n)), prompt="\n\n".join(parts))
    card = (getattr(res, "text", "") or "").strip()
    if not card:
        return {}
    return {"name": name or _lead_name(card), "prominence": prominence, "role": role, "card": card}


# ── PROLOGUE — the opening of the NOVEL: the protagonist living their ordinary life, slow, in
# close third, so the reader has real context before anything happens. The world/people/pressure
# are established through lived detail; the strange thing stays at the edge until the last beat
# (the inciting intrusion). A SEQUENCE of short sections, each aware of the ones before it. ──

PROLOGUE_BEATS = [
    ("morning", "Open on {name} in an ordinary moment of their day — waking, working, the plain "
                "shape of their life. Establish their world and what they want through what they "
                "physically DO, not through statement. The strange thing does NOT appear yet. "
                "Ground us fully in this real, particular life."),
    ("the people", "{name}'s day continues and {person} enters it. Show the relationship and more "
                   "of this world through the ordinary encounter — how they speak, what passes "
                   "between them. Still no strangeness."),
    ("the weight", "The thing pressing on {name}'s life surfaces, concrete and mundane: {pressure}. "
                   "Keep it ordinary — money, time, an obligation. Still their world, their eyes."),
    ("the crack", "For the first time, something at the edge of the ordinary intrudes: {strange}. "
                  "This is the first wrongness in an ordinary life. End here — the story is about "
                  "to begin."),
]

PROLOGUE_SYS = (
    "You write the OPENING of a novel: plain, grounded prose in CLOSE THIRD PERSON, past tense, "
    "following ONE character so we live inside their day. Slow and lived — establish before you "
    "disrupt. Concrete particulars over description; no purple prose, no metaphysical narration. "
    "Continue seamlessly from the story so far in the same voice; do not recap or repeat. Write "
    "~200-300 words for this section only — do not race ahead of it."
)


def generate_prologue(provider, sheet: dict) -> dict:
    """Write the novel's opening as a sequence of short sections in the protagonist's POV. Returns
    {sections: [{title, text}]} ({} on failure). Each section is generated in order, seeing the
    prose so far, so the prologue reads as one continuous opening."""
    if provider is None or not sheet:
        return {}
    p = sheet.get("protagonist") or {}
    name = (p.get("name") or "the protagonist").strip()
    people = [q.get("name") for q in (sheet.get("people") or []) if q.get("name")]
    strange = (sheet.get("strange") or "").strip()
    facts = seed_brief(sheet)
    sections, prev = [], ""
    for title, instr in PROLOGUE_BEATS:
        if title == "the crack" and not strange:
            continue                              # no strange thing → the prologue is pure life
        directive = instr.format(name=name, person=(people[0] if people else "someone they know"),
                                 pressure=sheet.get("pressure", ""), strange=strange or "a small wrongness")
        prompt = (f"FACTS (the world; draw on them, don't list them):\n{facts}\n\n"
                  + (f"THE OPENING SO FAR (continue it seamlessly — same voice, no repeating):\n"
                     f"{prev[-1400:]}\n\n" if prev else "")
                  + f"WRITE THE NEXT SECTION — {directive}")
        res = provider.generate_text(system=PROLOGUE_SYS, prompt=prompt)
        text = (getattr(res, "text", "") or "").strip()
        if text:
            sections.append({"title": title, "text": text})
            prev = (prev + "\n\n" + text).strip()
    return {"sections": sections}


def seed_brief(s: dict) -> str:
    """The fact sheet rendered as the writer's prose brief (labeled facts, zero rules)."""
    if not s:
        return ""
    p, pl = s.get("protagonist") or {}, s.get("place") or {}
    out = [f"- {p.get('name', '?')}: {p.get('life', '')} Wants: {p.get('want', '')}"]
    out.append(f"- Place: {pl.get('name', '')} — {pl.get('era', '')}. {pl.get('description', '')}")
    out.append(f"- Now: {s.get('pressure', '')}")
    for q in s.get("people") or []:
        out.append(f"- {q.get('name', '?')}: {q.get('life', '')} Wants: {q.get('want', '')}")
    for f in s.get("facts") or []:
        out.append(f"- {f}")
    if (s.get("strange") or "").strip():
        out.append(f"- The strange thing: {s['strange']}")
    return "FACTS:\n" + "\n".join(out)


# ── The refine LOOP — quality by iteration, not by culling. Model-agnostic: a weak model can drive both
# the writing and (optionally) the critique, and we test whether looping lifts it to a strong bar. ──
# MINIMAL PROSE PROMPTS (see memory: minimal-prompt-facts). A/B-proven: the model's untouched
# register is already plain-grounded-correct; stacked craft rules INSTRUCTED the whimsy they were
# meant to prevent. The writer gets a tiny system + a FACT SHEET brief; rules stay near zero.
# The pipeline's job is making the facts, not lecturing the writer.
_CRAFT = "You write fiction. Plain, grounded prose."
SCENE_GEN_SYS = (_CRAFT + " Write the opening scene of a story (600-900 words) from the facts "
    "given. Start in a concrete moment. Return {title, prose}. JSON only.")
SCENE_REVISE_SYS = (_CRAFT + " Revise the scene to address the editor's notes; keep what works. "
    "Return {title, prose}. JSON only.")
SCENE_CRITIC_SYS = (
    "You are an exacting fiction editor. Score the opening scene 1-5 on: `voice` (a real narrative voice), "
    "`legibility` (a FIRST-TIME reader follows who/what/why and feels the stakes with ZERO metaphor-decoding "
    "— any sentence you must reread to parse fails this), `plainness` (clear/plain like The Wandering Inn, "
    "not dense or overwritten), `withholding` (the magic is implied, never explained — but not at the cost "
    "of legibility), `pulse` (a real human center you feel), `plausibility` (a skeptic buys every "
    "non-magical element — the economics, the distances, why people are where they are, how they behave; "
    "contrived interlocking pressures or 'wait, why would he—?' moments fail this), and `restraint` (the "
    "strange is RATIONED — one uncanny thing, in an otherwise checkable-real world; charm/omen/folk-clutter "
    "in every paragraph fails this). `pass` = true ONLY if every score >= 4. If not pass, `fix` = concrete "
    "instructions naming the EXACT weakest lines/elements and how to fix them. JSON only.")

_SCENE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "prose"],
                 "properties": {"title": {"type": "string"}, "prose": {"type": "string"}}}
_VERDICT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["voice", "legibility", "plainness", "withholding", "pulse", "plausibility", "restraint",
                 "pass", "fix"],
    "properties": {"voice": {"type": "integer"}, "legibility": {"type": "integer"},
        "plainness": {"type": "integer"}, "withholding": {"type": "integer"}, "pulse": {"type": "integer"},
        "plausibility": {"type": "integer"}, "restraint": {"type": "integer"},
        "pass": {"type": "boolean"}, "fix": {"type": "string"}}}


def loop_scene(gen_provider, brief: str, rounds: int = 3, critic_provider=None) -> dict:
    """Generate an opening scene, then LOOP critique→revise up to `rounds` times, converging to the bar
    (no culling). `critic_provider` defaults to the generator itself — pass a stronger one to test
    weak-writer + strong-critic. Returns the final scene + a per-round trace so we can watch it climb."""
    if gen_provider is None:
        return {}
    critic_provider = critic_provider or gen_provider
    res = gen_provider.generate_text(system=SCENE_GEN_SYS, prompt=brief, emits=_SCENE_SCHEMA)
    scene = _data(res)
    trace = [{"round": 0, "scores": None, "prose": scene.get("prose", "")}]
    for r in range(1, max(1, int(rounds)) + 1):
        v = _data(critic_provider.generate_text(system=SCENE_CRITIC_SYS,
                  prompt=f"SCENE:\n{scene.get('prose','')}", emits=_VERDICT_SCHEMA))
        scores = {k: v.get(k) for k in ("voice", "legibility", "plainness", "withholding", "pulse",
                                        "plausibility", "restraint")}
        trace[-1]["scores"] = scores
        trace[-1]["pass"] = bool(v.get("pass"))
        if v.get("pass"):
            break
        rev = _data(gen_provider.generate_text(system=SCENE_REVISE_SYS,
                    prompt=f"{brief}\n\nEDITOR'S NOTES (fix exactly):\n{v.get('fix','')}\n\nCURRENT SCENE:\n{scene.get('prose','')}",
                    emits=_SCENE_SCHEMA))
        if rev.get("prose"):
            scene = rev
        trace.append({"round": r, "scores": None, "prose": scene.get("prose", "")})
    return {"scene": scene, "trace": trace, "rounds_used": len(trace) - 1}


def scenario_from_systems(provider, systems: list[dict], seed: str = "") -> dict:
    """Bottom-up: let a concrete opening EMERGE from the sharpest system collision (not a drilled premise)."""
    if provider is None or not systems:
        return {}
    brief = _systems_brief(systems)
    inter = "\n".join(f"- {s.get('name')} × {it.get('with')}: {it.get('effect')}"
                      for s in systems for it in (s.get("interactions") or []))
    prompt = (f"SYSTEMS:\n{brief}\n\nINTERACTIONS (where they collide):\n{inter}\n\n"
              + (f"SEED:\n{seed}\n\n" if (seed or "").strip() else "")
              + "Find the sharpest collision and ground a concrete opening there.")
    res = provider.generate_text(system=COLLISION_SYS, prompt=prompt, emits=COLLISION_SCHEMA)
    d = _data(res)
    return d if isinstance(d, dict) and d.get("concrete_scene") else {}
