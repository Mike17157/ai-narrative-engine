"""Story-world creation.

This module owns world framing, cast and relationship design, premise and substrate
material, geography, and the graph workflows that sequence those capabilities.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from dataclasses import asdict, dataclass, field, fields as _dc_fields
from typing import Any, Callable

from pydantic_graph import GraphBuilder, StepContext, reduce_null

"""Relationship-first story genesis — harnesses → web → derived stories.

See loom/stories/GENESIS.md for the full design. The arrow is INVERTED vs simulation.py:
instead of premise→cast, we design UNNAMED character harnesses, weave the tension web
between them, and DERIVE candidate stories — premise becomes an OUTPUT. Naming happens at
commit time (the router). Three structured-output passes + one deterministic graph query:

  • design_harnesses(provider, seed, n)        -> [{id, role, want, lie, wound, secret}]
  • weave_relationships(provider, harnesses)   -> [{id, source, target, nature, dynamic, stance, note}]
  • derive_stories(provider, harnesses, edges) -> [{title, dramatic_question, premise, protagonist, anchors, …}]
  • scene_cast(edges, focal, n)                -> [harness_id]   (no LLM; tension-weighted greedy walk)
  • name_cast(provider, harnesses)             -> {id: {name, appearance}}   (commit-time naming)

Every step references the prior by STABLE harness id (h0..hn), assigned HERE by position so
referential integrity is ours, not the model's. Edges/candidates that reference an unknown
id are DROPPED — a candidate pointing at a re-rolled-away harness is the one bug that
silently corrupts a story (see test_genesis.py).
"""

from concurrent.futures import ThreadPoolExecutor


def _data(res) -> dict:
    return getattr(res, "data", None) or {}


def _short(s, n: int = 6) -> str:
    """Relationship dynamics are 2-3 words — backstop a model that writes a sentence."""
    return " ".join(str(s or "").split()[:n])


_STANCES = ("devoted", "warm", "neutral", "strained", "hostile")


# ── 1) Harness design — unnamed stances on a central value ──────────────────────

HARNESS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["harnesses"],
    "properties": {"harnesses": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["role", "temperament", "want", "lie", "contradiction", "wound", "secret", "good_memory"],
        "properties": {
            "role": {"type": "string"},
            "temperament": {"type": "string"},
            "want": {"type": "string"},
            "lie": {"type": "string"},
            "contradiction": {"type": "string"},
            "wound": {"type": "string"},
            "secret": {"type": "string"},
            "good_memory": {"type": "string"},
        }}}},
}

# ── Stereotype catalog — the SHARED-CULTURE character types generation starts from. ──
# A stereotype is legible because everyone already knows it; stating it plainly is the point.
# Depth NEVER comes from decorating the label with invented quirks (whimsy) — it comes from the
# psych interior underneath + the one hidden dimension. See [[restraint-over-whimsy]].
STEREOTYPES = [
    # school
    "the class clown", "the student council president", "the delinquent who skips class",
    "the transfer student", "the teacher's pet", "the quiet bookworm", "the ace of the team",
    "the childhood friend next door", "the sickly kid who misses school", "the gossip queen",
    "the scholarship kid working nights", "the art-room loner", "the class rep who takes it all too seriously",
    "the popular girl everyone assumes is shallow",
    # town / village
    "the strict landlady", "the retired soldier turned shopkeeper", "the village gossip",
    "the overworked single parent", "the priest everyone confides in", "the town drunk",
    "the ambitious apprentice", "the spoiled rich kid", "the grizzled foreman",
    "the outsider who just moved in", "the healer who has seen too much",
    "the fallen noble keeping up appearances", "the innkeeper who hears everything",
    "the golden child who never left", "the black sheep who came back",
    # work / crew
    "the by-the-book supervisor", "the veteran nobody questions", "the new hire trying too hard",
    "the fixer who knows a guy", "the burnout coasting on old glory",
]


def _stereotype_menu(k: int = 14) -> str:
    """A sampled menu of familiar types offered to the designer — start FROM one, adapt its
    wording to the world. Sampling keeps repeated generations from converging on the same picks."""
    import random
    picks = random.sample(STEREOTYPES, min(k, len(STEREOTYPES)))
    return "\n".join(f"- {s}" for s in picks)


DESIGN_SYS = (
    "You design a CAST of psychologically REAL people, before anyone is named — grounded in actual "
    "personality science, not random quirks. Anchor each character in: a coherent Big Five profile "
    "(where they sit on openness, conscientiousness, extraversion, agreeableness, neuroticism), an "
    "attachment style (secure / anxious / avoidant), and the DEFENSE they reach for under stress "
    "(intellectualizing, withdrawing, deflecting with humor, controlling, idealizing, people-pleasing). "
    "Each character STARTS from a familiar stereotype — one of the offered types (or the nearest that "
    "truly fits this world), stated PLAINLY. The stereotype is the whole daylight surface: do NOT "
    "decorate it with an invented signature quirk, habit, or twee detail — that reads as artificial. "
    "The surface stays MUNDANE; whatever distinguishes them comes from ordinary life (their work, "
    "family, money troubles, obligations) — the way real people differ. Underneath, make them a REAL "
    "HUMAN the stereotype merely simplifies: the psych interior does the work. The `secret` is a real "
    "thing they keep hidden — and USUALLY ORDINARY (a debt, a shame, a quiet longing, something done or "
    "lost), grounded in this world. It is NOT an ability, a power, or a hidden specialness, and it need "
    "not re-read the whole person — most people's secrets are small and human. Do NOT make every character "
    "secretly remarkable; that is whimsy. Make `want`, `lie`, `wound`, `secret` FLOW from the makeup: the wound shapes "
    "the defense, the defense hardens into the lie, the lie bends the want. A `contradiction` is made ONLY "
    "WHEN the world's GREATER STRUGGLE earns it — never forced onto everyone. When the world's PRESSURE and "
    "FORCES are well-defined AND this person is genuinely caught in them, the strongest characters carry "
    "one: a SELF-DEFEAT that relates that greater struggle back to this one person — their deepest want "
    "expressing as its OPPOSITE in what they DO (the act that pushes away the very thing they most want), "
    "for a reason that reads as love or duty, not fear (she pushes him away BECAUSE the force hunting her "
    "would use him — nearness is what endangers what she loves). Surface behaviour and true want point "
    "OPPOSITE ways and both are real: a sympathetic, self-inflicted knot, not indecision. But do NOT invent "
    "one when the struggle is thin or the person isn't caught in it — a clear, simple motive beats a forced "
    "contradiction; leave `contradiction` empty. For each: a `role` "
    "(the plain stereotype, adapted to this world's words; not a name, no added quirks); a `temperament` (ONE plain line on how they "
    "COME ACROSS in everyday life — manner, how they treat people, the way you'd describe a "
    "neighbour. NO psychology terms: never 'Big Five', 'openness', 'high/low ___', 'attachment', "
    "'defense' — show the person, never the profile); a `want` (concrete external goal); a `lie` (the "
    "false self-belief the story will test — their personal DISTORTION of the story's central question); a "
    "`contradiction` (the self-defeat that ties the world's greater struggle to this person — how the want "
    "turns into its OPPOSITE in action, reading as love/duty not fear; ONLY when the struggle earns it, "
    "else empty); a `wound` (a CONCRETE past event that hurt them — the trauma "
    "the defense guards, a real scene, not an abstraction; ordinary human material — a divorce, a debt, "
    "a death, a failure — beats exotic trauma); a `secret` (a real hidden thing, usually ordinary — not an ability or specialness); and a `good_memory` (a CONCRETE "
    "cherished moment from their past — the warmth they quietly hold onto). The wound and the good memory "
    "are the biographical anchors their VOICE will later be drawn from, so make them specific and real. "
    "Make them contrast as real people do, and keep every one human and grounded. No names, no appearances. "
    "JSON only."
)


def design_harnesses(provider, seed: str = "", n: int = 4, grounding: str = "", world="") -> list[dict]:
    """Generate `n` unnamed character harnesses around a `seed`, who BELONG to `world` (the authored
    stage — genre/setting/situation, dict or string). `grounding` (retrieved `_psyche` behavioural
    markers) anchors them in real psychology instead of random traits."""
    if provider is None:
        return []
    n = max(2, min(int(n or 4), 8))
    seed = (seed or "").strip()
    grounding = (grounding or "").strip()
    wb = world_brief(world)
    prompt = ((f"WORLD (the shared stage every character lives in — their backgrounds and situations MUST "
               f"belong to it):\n{wb}\n\n" if wb else "")
              + (f"SEED (the story / pairing / vibe to build from):\n{seed}\n\n" if seed else "")
              + (f"PSYCHOLOGY NOTES (real behavioural markers — ground the cast in these):\n{grounding}\n\n"
                 if grounding else "")
              + f"FAMILIAR TYPES (start each character from one of these, or the nearest that fits this "
                f"world — adapt the wording, keep it plain):\n{_stereotype_menu()}\n\n"
              + f"Design exactly {n} psychologically real, contrasting characters who genuinely inhabit this world.")
    res = provider.generate_text(system=DESIGN_SYS, prompt=prompt, emits=HARNESS_SCHEMA)
    out = []
    for i, h in enumerate(_data(res).get("harnesses", [])[:n]):
        out.append({
            "id": f"h{i}",
            "role": (h.get("role") or "").strip() or f"character {i + 1}",
            "temperament": (h.get("temperament") or "").strip(),
            "want": (h.get("want") or "").strip(),
            "lie": (h.get("lie") or "").strip(),
            "contradiction": (h.get("contradiction") or "").strip(),
            "wound": (h.get("wound") or "").strip(),
            "secret": (h.get("secret") or "").strip(),
            "good_memory": (h.get("good_memory") or "").strip(),
        })
    return out


# ── Function-first cast (Truby's character web) ──────────────────────────────
# A cast is a set of POSITIONS in the value contest, not a bag of people (GENESIS.md §6). Each role is
# a load-bearing dramatic FUNCTION defined RELATIVE to the protagonist — the opponent attacks the lie,
# the ally aids the need, the false-ally shares the goal and betrays, the mirror drew the opposite lie
# from the same wound. We generate ONE focused character per role (one model run each) so each gets the
# full budget AND sees the cast-so-far, filling its slot deliberately instead of colliding in a batch.
# `steer` = the character's OWN relational ORBIT, pre-assigned so the four supporting roles (generated in
# parallel, blind to each other) land in DIFFERENT social niches instead of colliding on one (e.g. two
# "the popstar's fixer"). It nudges territory, not archetype — the interior stays the model's to invent.
FUNCTION_ROLES = [
    {"key": "protagonist", "label": "Protagonist", "steer": "",
     "brief": "the POV lead the story puts on trial — often the unremarkable one hiding behind their lie. "
              "Everyone else is defined in relation to THEM. If the SEED names or clearly implies a specific "
              "lead (e.g. 'a shy boy who mends things and the popstar…' → the shy boy), THAT person IS the "
              "protagonist — build exactly them; do NOT invent a different lead or promote a side character."},
    {"key": "ally", "label": "Ally",
     "steer": "Comes from the protagonist's OWN everyday world — a peer beside them in their daily life "
              "(their work, class, home), NOT from a rival's camp or an authority over them.",
     "brief": "aids the protagonist's deeper NEED — but is NOT a yes-man. They carry a competing want that "
              "rubs, and they're the one willing to tell the protagonist the truth they don't want to hear."},
    {"key": "opponent", "label": "Opponent",
     "steer": "Occupies the ARENA the protagonist wants into — a direct rival competing for the very same "
              "place/prize, met on that contested ground, not in the protagonist's private life.",
     "brief": "attacks the protagonist's central WEAKNESS/lie and competes for a version of the SAME goal. "
              "Not a cartoon villain — a person with their own justified want; they can be perfectly WARM "
              "on the surface, which makes them more dangerous."},
    {"key": "false_ally", "label": "False ally",
     "steer": "Is embedded ON the protagonist's SIDE — inside their camp, crew, or cause, trusted as one of "
              "them — which is exactly why the betrayal will land. A DIFFERENT niche from the arena-rival.",
     "brief": "looks like an ally and shares the protagonist's goal, but for a corrupt or self-serving "
              "reason, and will BETRAY when it counts — the richest edge, dramatic irony built in."},
    {"key": "mirror", "label": "Mirror / foil",
     "steer": "Stands at a REMOVE from the protagonist's immediate circle — a parallel figure from a "
              "DIFFERENT corner of this world, NOT a member of the protagonist's crew or the rival's "
              "entourage; they simply happen to share the wound.",
     "brief": "carries the SAME core wound as the protagonist but drew the OPPOSITE lie from it — the road "
              "not taken. Shows the reader who the protagonist could have become, for better or worse."},
]

ONE_HARNESS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["role", "temperament", "want", "lie", "contradiction", "wound", "secret", "good_memory"],
    "properties": {
        "role": {"type": "string"},
        "temperament": {"type": "string"},
        "want": {"type": "string"},
        "lie": {"type": "string"},
        "contradiction": {"type": "string"},
        "wound": {"type": "string"},
        "secret": {"type": "string"},
        "good_memory": {"type": "string"},
    },
}

DESIGN_ONE_SYS = (
    "You design ONE REAL PERSON who happens to serve a dramatic function — a PERSON FIRST, a function "
    "second. The single biggest failure to avoid: a character who is only their theme/role, a vessel with "
    "no life outside the story's point. A real person is NOT thematically unified — most of what they want "
    "and do has NOTHING to do with the plot or the world's magic. So build them from the OUTSIDE IN: give "
    "them a concrete, ordinary daily LIFE (their work, a habit, a small rivalry, a thing they're saving "
    "for, a person they can't stand, what they do on a day off) and SEVERAL wants, most of them mundane "
    "and a couple that CONTRADICT each other — the way real people want incompatible things. They should "
    "feel like they existed before this story found them and would go on if it left. ONLY THEN wire in "
    "their dramatic function underneath. In DAYLIGHT they read as a recognizable stereotype — the label "
    "their world casually files them under, legible and even funny; underneath, in the `secret`, is a real "
    "thing they keep hidden — USUALLY ORDINARY (a debt, a shame, a longing, something done or lost), "
    "grounded in this world, NOT an ability, a power, or a hidden specialness. Most people's secrets are "
    "small and human; do NOT make every character secretly remarkable — that is whimsy. "
    "Ground the interior in real psychology (a coherent Big Five lean, "
    "an attachment style, the DEFENSE they hit under stress) but SHOW it as behavior, never diagnose it. "
    "Make `want`, `lie`, `wound`, `secret` FLOW from the makeup. The `lie` is their particular "
    "DISTORTION of the story's central value — but it is ONE thread of them, not the whole cloth; do NOT "
    "let two characters have the same shape (e.g. all 'someone who doesn't know if their debt is paid'). "
    "A `contradiction` is made ONLY WHEN the world's GREATER STRUGGLE earns it — never forced. When the "
    "world's PRESSURE and FORCES are well-defined AND this person is genuinely caught in them, give one: a "
    "SELF-DEFEAT that relates that struggle back to them — their deepest want turning into its OPPOSITE in "
    "what they DO, sabotaging the very thing they most want, for a reason that reads as love or duty, not "
    "fear (she pushes him away BECAUSE the force hunting her would use him). Surface and true want point "
    "OPPOSITE ways, both real — a sympathetic knot, not indecision. But if the struggle is thin or they "
    "aren't caught in it, do NOT invent one; a clear, simple motive is better — leave `contradiction` empty. "
    "Fields: a `role` (the PLAIN familiar type, adapted to this world's words — 'the class clown', "
    "'the strict landlady'; NOT a name, and NO invented quirk or twee detail bolted onto the label — "
    "what distinguishes them lives in their ordinary life and interior, not the tag); "
    "`temperament` (ONE plain line on how they COME ACROSS in everyday life — a "
    "concrete manner, how they treat people — the way you'd describe a real neighbour. ABSOLUTELY "
    "NO psychology terms or trait names: never write 'adventurousness', 'dutifulness', 'intellect', "
    "'openness', 'high/low ___', 'attachment', 'defense'. Show the person, never the profile); "
    "`want` (concrete external goal); `lie`; `contradiction` (the self-defeat tying the world's struggle "
    "to them — want turning into its OPPOSITE in action, love/duty not fear; ONLY when earned, else empty); "
    "`wound` (a CONCRETE past "
    "event, a real scene); `secret` (a real hidden thing, usually ordinary — not an ability or specialness); `good_memory` (a CONCRETE "
    "cherished moment). Make them DISTINCT from "
    "any characters already in the cast. Give this person their OWN independent standing and stakes in the "
    "world — do NOT default them to being another lead's manager, fixer, handler, assistant, agent, "
    "secretary, publicist, or bodyguard: that support-staff niche is a crutch and makes the whole cast "
    "collapse into one person's entourage. No name, no appearance. JSON only."
)


def design_by_role(provider, seed: str = "", grounding: str = "", world="", roles=None) -> list[dict]:
    """Generate ONE focused harness per dramatic FUNCTION (Truby's web) — a separate model run each, so
    every character gets the full budget and fills its slot. The ONLY dependency is the protagonist
    (everyone else is defined AGAINST them), so we generate the protagonist first, then the remaining
    roles CONCURRENTLY (their distinct functions keep them apart — no sequential chain needed). Returns
    harnesses tagged with `function` (the role key). See GENESIS.md §6."""
    if provider is None:
        return []
    roles = list(roles or FUNCTION_ROLES)
    if not roles:
        return []
    seed = (seed or "").strip()
    grounding = (grounding or "").strip()
    wb = world_brief(world)

    def _gen(role: dict, proto_brief: str) -> dict | None:
        prompt = (
            (f"WORLD (the shared stage — this person's background MUST belong to it):\n{wb}\n\n" if wb else "")
            + (f"SEED (the story / pairing / vibe):\n{seed}\n\n" if seed else "")
            + (f"PSYCHOLOGY NOTES (real behavioural markers — ground them in these):\n{grounding}\n\n"
               if grounding else "")
            + (f"THE PROTAGONIST this character is defined AGAINST — make them a distinct person and wire "
               f"their function to THIS specific protagonist's lie/wound:\n{proto_brief}\n\n" if proto_brief else "")
            + f"FAMILIAR TYPES (start from one of these, or the nearest that fits this world — plain "
              f"wording, no added quirks):\n{_stereotype_menu(10)}\n\n"
            + f"Design ONE character whose DRAMATIC FUNCTION is the {role['label'].upper()}: {role['brief']}"
            + (f"\n\nTHEIR PLACE IN THE WORLD (occupy this niche, distinct from the rest of the cast): "
               f"{role['steer']}" if role.get("steer") else "")
        )
        try:
            res = provider.generate_text(system=DESIGN_ONE_SYS, prompt=prompt, emits=ONE_HARNESS_SCHEMA)
        except Exception:  # noqa: BLE001 — one role failing shouldn't sink the whole cast
            return None
        h = _data(res)
        return h if (isinstance(h, dict) and (h.get("role") or "").strip()) else None

    # Protagonist first (the shared anchor), then the rest in parallel against it.
    results: dict[int, dict | None] = {0: _gen(roles[0], "")}
    others = roles[1:]
    if results[0] is not None and others:
        proto_brief = _harness_brief([{**results[0], "id": "h0"}])
        with ThreadPoolExecutor(max_workers=min(4, len(others))) as ex:
            futs = {ex.submit(_gen, r, proto_brief): i for i, r in enumerate(others, start=1)}
            for fut in futs:
                results[futs[fut]] = fut.result()

    out: list[dict] = []
    for i, r in enumerate(roles):
        h = results.get(i)
        if not h:
            continue
        out.append({
            "id": f"h{i}", "function": r["key"],
            "role": (h.get("role") or "").strip() or r["label"],
            "temperament": (h.get("temperament") or "").strip(),
            "want": (h.get("want") or "").strip(),
            "lie": (h.get("lie") or "").strip(),
            "contradiction": (h.get("contradiction") or "").strip(),
            "wound": (h.get("wound") or "").strip(),
            "secret": (h.get("secret") or "").strip(),
            "good_memory": (h.get("good_memory") or "").strip(),
        })
    return out


# ── 1a) World frame — author the STAGE first (genre/setting), derive the play later ──
# A character's background is meaningless without a world: "where they're from / their situation now"
# only resolves once you know the genre, era, and shared reality. So we author a lean WORLD frame up
# front and thread it into BOTH design_harnesses AND formalize_harness — the cast then belongs to one
# coherent place. The PREMISE stays emergent (relationship-first); this is only the stage, not the play.

WORLD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["genre", "tone", "setting", "situation", "pressure", "forces"],
    "properties": {
        "genre": {"type": "string"},
        "tone": {"type": "string"},
        "setting": {"type": "string"},
        "situation": {"type": "string"},
        "pressure": {"type": "string"},   # the ROOT — the one standing world-level force everything grows from
        "forces": {"type": "array", "items": {   # the proto-creeds: powers/factions contending over the pressure
            "type": "object", "additionalProperties": False, "required": ["name", "stance"],
            "properties": {"name": {"type": "string"},      # ONE coined word (never two words)
                           "stance": {"type": "string"}}}},  # their answer to the pressure, and why it's defensible
    },
}

WORLD_SYS = (
    "From a one-line story idea, establish the WORLD the story lives in — the shared STAGE every "
    "character stands on, NOT the plot. Give:\n"
    "• `genre` — a couple words (contemporary realist drama, low fantasy, near-future sci-fi, cosy "
    "mystery…);\n"
    "• `tone` — the emotional register, a few words (tender and melancholic; wry and tense);\n"
    "• `setting` — 2-3 SPECIFIC sentences: place, era, and the social/economic texture of this world — "
    "the reality every character shares and whose logic their backgrounds must obey. Specific the way a "
    "REAL place is specific (a particular industry, a particular kind of town and how it's changing), NOT "
    "with invented gimmicks;\n"
    "• `situation` — 1-2 sentences: the circumstance that gathers THIS cast and keeps them in each "
    "other's orbit (the same school, the touring band, the dying town).\n"
    "• `pressure` — the ROOT: the ONE standing world-level force everything grows from — a CONDITION, "
    "not an event (an ecology at its limit, a faith in decline, a resource running out, a power that "
    "must be fed a life). This is the engine the story's central conflict AND its people's inner "
    "contradictions will grow out of. State it as a standing pressure the world already lives under, "
    "never as a plot that has started. (Rewrite's is a planet that can no longer afford humanity.)\n"
    "• `forces` — 2-4 competing FORCES that have organized around the pressure: the factions, faiths, "
    "orders or powers that each ANSWER it differently and each believe they are RIGHT. This is where the "
    "central conflict is SEEDED — none is a villain, each is defensible. Each: a `name` (ONE coined word "
    "— never two words, never 'The Adjective Noun': Crownsworn, Unbound, Gaia — NOT 'Harvest Binding') "
    "and a `stance` (their answer to the pressure and why a good person would hold it).\n"
    "This WORLD is the foundation of the story, so write it PLAINLY and CONCRETELY: a specific place, era, "
    "and the social/economic texture, the real feel of it rendered in ordinary detail. Do NOT reach for "
    "EVOCATIVE or LYRICAL prose — specificity carries a world; lyricism is what tips it into WHIMSY, the "
    "twee, precious, fanciful register you must avoid (invented cutesy mechanics, magical-realist gimmicks, "
    "metaphors treated as literal facts). FANTASY AND SCI-FI ARE WELCOME: if the world has magic or its own "
    "tech, give it REAL, grounded rules (what's possible, normal, forbidden, who controls it) — 'unlicensed "
    "transmutation is a crime', NOT 'essences that sing to the worthy'. So: specific and grounded, yes; "
    "fanciful, lyrical, or gimmicky, no. The bait shop is described precisely — it still just sells bait.\n"
    "Do NOT decide the plot or the ending — those EMERGE from the characters. But DO set the `pressure` "
    "and the `forces` contending over it: that is the engine the premise and the cast's contradictions "
    "grow from — a stage with a standing pressure, not an inert backdrop. JSON only."
)


def design_world(provider, seed: str = "") -> dict:
    """A one-line idea → a lean WORLD frame (genre/tone/setting/situation) — the shared stage the cast
    is generated to fit. The premise stays emergent; this only fixes the reality their backgrounds obey."""
    if provider is None:
        return {}
    seed = (seed or "").strip()
    prompt = (f"STORY IDEA:\n{seed}\n\n" if seed else "") + "Establish the world (the stage, not the plot)."
    res = provider.generate_text(system=WORLD_SYS, prompt=prompt, emits=WORLD_SCHEMA)
    d = _data(res)
    forces = [{"name": (f.get("name") or "").strip(), "stance": (f.get("stance") or "").strip()}
              for f in (d.get("forces") or []) if isinstance(f, dict) and (f.get("name") or "").strip()]
    return {"genre": (d.get("genre") or "").strip(), "tone": (d.get("tone") or "").strip(),
            "setting": (d.get("setting") or "").strip(), "situation": (d.get("situation") or "").strip(),
            "pressure": (d.get("pressure") or "").strip(), "forces": forces}


_WORLD_FIELDS = ("genre", "tone", "setting", "situation", "pressure")
_FIELD_GUIDE = {
    "genre": "a couple words (e.g. contemporary realist drama, low fantasy, near-future sci-fi)",
    "tone": "the emotional register, a few words",
    "setting": "2-3 SPECIFIC sentences — place, era, social texture, and any non-realist rules of this world",
    "situation": "1-2 sentences — the circumstance that gathers THIS cast and keeps them in each other's orbit",
    "pressure": "the ROOT — the ONE standing world-level force everything grows from (a condition, not an "
                "event): an ecology at its limit, a faith in decline, a power that must be fed a life",
}


def regen_world_field(provider, world, field: str, seed: str = "") -> str:
    """Re-roll ONE field of the world frame, kept consistent with the others — for an inline ↻ button."""
    if provider is None or field not in _WORLD_FIELDS:
        return ""
    rest = world_brief({k: v for k, v in (world or {}).items() if k != field})
    system = (f"You revise ONE field of a story's WORLD frame: the `{field}` — {_FIELD_GUIDE[field]}. Keep it "
              f"consistent with the rest of the world, but give a FRESH, genuinely DIFFERENT take (not a "
              f"paraphrase of what's there). Write it PLAINLY and CONCRETELY — specificity carries a world; "
              f"do NOT reach for evocative or lyrical prose, which tips into WHIMSY: no twee/precious conceits, "
              f"no invented cutesy mechanics, no metaphors-as-fact. Fantasy/sci-fi rules are welcome but "
              f"GROUNDED and real ('unlicensed transmutation is a crime', not 'essences that sing'). Specific and "
              f"grounded, yes; fanciful or lyrical, no. Output ONLY the new {field} text — no label, no quotes, nothing else.")
    prompt = ((f"STORY IDEA: {seed.strip()}\n\n" if (seed or '').strip() else "")
              + (f"THE REST OF THE WORLD:\n{rest}\n\n" if rest else "")
              + f"Write a fresh {field}.")
    res = provider.generate_text(system=system, prompt=prompt)
    txt = (getattr(res, "text", "") or "").strip()
    for lbl in (field, field.capitalize()):           # strip a leaked "Field:" prefix
        if txt.lower().startswith(lbl.lower() + ":"):
            txt = txt[len(lbl) + 1:].strip()
    return txt.strip('"').strip()


def compose_world(frame: dict | None = None, substrate: dict | None = None,
                  particulars: list | None = None) -> dict:
    """Fold the genesis draft (the design_world FRAME + the SUBSTRATE soul + the lived PARTICULARS)
    into the ONE persisted `Story.world` — the permanent foundation premise & theme distils from.
    The ache/preoccupation IS the pressure; forces come from either layer. Empty keys are dropped so
    a thin world stays thin. Pure — self-checks in __main__."""
    frame = frame if isinstance(frame, dict) else {}
    sub = substrate if isinstance(substrate, dict) else {}
    parts = particulars if isinstance(particulars, list) else []
    forces = frame.get("forces") or sub.get("forces") or []
    out = {
        "genre": (frame.get("genre") or "").strip(), "tone": (frame.get("tone") or "").strip(),
        "setting": (frame.get("setting") or "").strip(), "situation": (frame.get("situation") or "").strip(),
        "place": (sub.get("place") or "").strip(),
        "pressure": (frame.get("pressure") or sub.get("preoccupation") or "").strip(),
        "forces": [{"name": (f.get("name") or "").strip(), "stance": (f.get("stance") or "").strip()}
                   for f in forces if isinstance(f, dict) and (f.get("name") or "").strip()],
        "traditions": [{"name": (t.get("name") or "").strip(), "logic": (t.get("logic") or "").strip()}
                       for t in (sub.get("traditions") or []) if isinstance(t, dict) and (t.get("name") or "").strip()],
        "people": [{"name": (p.get("name") or "").strip(), "life": (p.get("life") or "").strip()}
                   for p in (sub.get("people") or []) if isinstance(p, dict) and (p.get("name") or "").strip()],
        "fragments": [{"kind": (q.get("kind") or "").strip(), "text": (q.get("text") or "").strip()}
                      for q in parts if isinstance(q, dict) and (q.get("text") or "").strip()],
    }
    return {k: v for k, v in out.items() if v}


def world_full_brief(world: dict | None) -> str:
    """The FULL persisted world as a foundation block — pressure/ache, forces, traditions, people, and
    lived fragments — for the premise & theme DISTILLATION (which must read the whole world, not a thin
    logline). '' when the world is empty (then premise-gen has nothing to distil from). See compose_world."""
    if not isinstance(world, dict) or not world:
        return ""
    lines = []
    for k in ("genre", "tone", "setting", "situation", "place"):
        if (world.get(k) or "").strip():
            lines.append(f"{k.capitalize()}: {world[k].strip()}")
    if (world.get("pressure") or "").strip():
        lines.append(f"PRESSURE (the ache — the one standing force everything grows from): {world['pressure'].strip()}")
    for f in (world.get("forces") or []):
        if (f.get("name") or "").strip():
            lines.append(f"FORCE — {f['name']}: {(f.get('stance') or '').strip()}")
    for t in (world.get("traditions") or []):
        if (t.get("name") or "").strip():
            lines.append(f"TRADITION — {t['name']}: {(t.get('logic') or '').strip()}")
    for p in (world.get("people") or []):
        if (p.get("name") or "").strip():
            lines.append(f"PERSON — {p['name']}: {(p.get('life') or '').strip()}")
    frags = [q for q in (world.get("fragments") or []) if (q.get("text") or "").strip()]
    if frags:
        lines.append("LIVED FRAGMENTS (the world shown; the rule withheld):")
        lines += [f"  · [{(q.get('kind') or '').strip()}] {q['text'].strip()}" for q in frags]
    return "\n".join(lines)


def world_brief(world) -> str:
    """Compose a WORLD frame (dict of genre/tone/setting/situation, or a plain string) into a grounding
    block for the cast-generation prompts. Empty when there's no world."""
    if isinstance(world, str):
        return world.strip()
    if not isinstance(world, dict):
        return ""
    lines = [f"{k.capitalize()}: {world[k].strip()}" for k in ("genre", "tone", "setting", "situation")
             if (world.get(k) or "").strip()]
    if (world.get("pressure") or "").strip():
        lines.append(f"Pressure (the standing force everything grows from): {world['pressure'].strip()}")
    forces = [f for f in (world.get("forces") or []) if isinstance(f, dict) and (f.get("name") or "").strip()]
    if forces:
        lines.append("Forces contending over it (each defensible, none a villain): "
                     + "; ".join(f"{f['name']} — {(f.get('stance') or '').strip()}" for f in forces))
    return "\n".join(lines)


# ── 2) Relationship weave — forces between stances ──────────────────────────────

WEAVE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["relationships"],
    "properties": {"relationships": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["source", "target", "nature", "dynamic", "stance", "note"],
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "nature": {"type": "string"},
            "dynamic": {"type": "string"},
            "stance": {"type": "string", "enum": list(_STANCES)},
            "note": {"type": "string"},
        }}}},
}

WEAVE_SYS = (
    "You wire the RELATIONSHIP WEB between a cast of unnamed harnesses — the forces that will "
    "generate scenes. A relationship is a FORCE between two stances: opposition (incompatible wants), "
    "false ally (shared goal, corrupt reason, will betray), mirror (same wound, opposite lie), "
    "dependency (power / knowledge / debt / love imbalance), or shadow (what the lead could become). "
    "EVERY edge must carry an ASYMMETRY or SECRET — one knows, owes, or loves more — stated in `note`. "
    "Build for TRIANGLES (three who all relate) and deliberately leave a few pairs UNCONNECTED as "
    "latent tension for later. Reference characters by their harness `id`. `dynamic` is 2-3 words for "
    "how source feels now ('wary respect', 'old grudge'). Do NOT connect everyone to everyone. JSON only."
)


def _harness_brief(harnesses: list[dict]) -> str:
    return "\n".join(
        f"- {h.get('id')} [{h.get('role', '')}] {h.get('temperament', '')} | "
        f"want: {h.get('want', '')}; lie: {h.get('lie', '')}; "
        f"contradiction: {h.get('contradiction', '')}; secret: {h.get('secret', '')}"
        for h in harnesses)


def weave_relationships(provider, harnesses: list[dict]) -> list[dict]:
    """Generate the tension web. Edges referencing an unknown / self id are DROPPED."""
    if provider is None or len(harnesses) < 2:
        return []
    ids = {h.get("id") for h in harnesses}
    res = provider.generate_text(
        system=WEAVE_SYS,
        prompt=f"CAST (by harness id):\n{_harness_brief(harnesses)}\n\nWire the web.",
        emits=WEAVE_SCHEMA,
    )
    out, seen = [], set()
    for i, r in enumerate(_data(res).get("relationships", [])):
        s, t = r.get("source"), r.get("target")
        if s not in ids or t not in ids or s == t or (s, t) in seen:
            continue
        seen.add((s, t))
        stance = r.get("stance") if r.get("stance") in _STANCES else "neutral"
        out.append({
            "id": f"r{i}", "source": s, "target": t,
            "nature": (r.get("nature") or "").strip(),
            "dynamic": _short(r.get("dynamic")),
            "stance": stance,
            "note": (r.get("note") or "").strip(),
        })
    return out


# ── 2a) Relationship POTENTIAL — the story seed between two characters ────────────
# The relationship's POTENTIAL is the real seed of the story (the characters are anchors). It has
# three parts: the hidden COMMON CORE they share beneath surface-different backgrounds (the point
# they can plausibly build on — for love OR enmity), the TRAJECTORY it travels (a from→to with a
# tone), and the bond it grows into. `suggest_potentials` proposes a few for a pair; the writer picks.

POTENTIAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["potentials"],
    "properties": {"potentials": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["common", "stance", "read"],
        "properties": {
            "common": {"type": "string"},
            "stance": {"type": "string", "enum": list(_STANCES)},
            "read": {"type": "string"},
        }}}},
}

POTENTIAL_SYS = (
    "Character A is the PROTAGONIST — the role the player will EMBODY. You never script A's feelings; "
    "those are the player's. Your job is the POTENTIAL FOR CLOSENESS that character B (a real, separate "
    "person) holds toward A: the latent ground a real bond could grow from, and where B starts. Do NOT "
    "define an outcome, a trajectory, or where it ENDS — that would rush it and rob the player of the "
    "story. Just the potential and B's starting disposition; the rest emerges in play. For each option:\n"
    "• `common` — the COMMON CORE B and A secretly SHARE beneath surface-different backgrounds — the "
    "hidden point a real closeness could plausibly build on (both perform a self to be loved; both are "
    "lonely in a crowd; both guard one soft thing). Specific to THESE two, never generic. This is the "
    "POTENTIAL — the ground, not the destination.\n"
    "• `stance` / `read` — how B is disposed toward A RIGHT NOW (the starting point, not the end): a "
    "categorical stance (devoted/warm/neutral/strained/hostile) and a short read in B's own terms "
    "('quietly fascinated, watchful', 'wary, keeps distance', 'needs them but won't admit it').\n"
    "Offer DISTINCT options resting on DIFFERENT cores — different grounds give different stories. Closeness "
    "spans the whole range (tender OR adversarial — a rival can be intensely close). JSON only."
)


def _char_brief(c: dict) -> str:
    nm = c.get("name") or c.get("role") or "character"
    body = (c.get("persona") or c.get("background") or c.get("temperament") or "").strip()
    tail = "; ".join(f"{k}: {c[k]}" for k in ("want", "lie", "wound") if c.get(k))
    return f"{nm} — {body}" + (f" ({tail})" if tail else "")


def suggest_potentials(provider, a: dict, b: dict, n: int = 3) -> list[dict]:
    """Propose `n` POTENTIALS FOR CLOSENESS that character `b` holds toward the PROTAGONIST `a` (the
    role the player embodies — A's own feelings are never authored). Each = the hidden `common` core
    (the ground a real bond could build on) + B's starting disposition (`stance`/`read`). No outcome,
    no trajectory: the player drives where it goes."""
    if provider is None or not a or not b:
        return []
    n = max(1, min(int(n or 3), 5))
    prompt = (f"PROTAGONIST A (the player's role) — {_char_brief(a)}\n\n"
              f"CHARACTER B — {_char_brief(b)}\n\n"
              f"Propose {n} distinct potentials for closeness B could hold toward A.")
    res = provider.generate_text(system=POTENTIAL_SYS, prompt=prompt, emits=POTENTIAL_SCHEMA)
    st = lambda v: v if v in _STANCES else "neutral"
    out = []
    for p in _data(res).get("potentials", [])[:n]:
        if not (p.get("common") or "").strip():
            continue
        out.append({"common": (p.get("common") or "").strip(),
                    "stance": st(p.get("stance")), "read": (p.get("read") or "").strip()})
    return out


# ── 2b) Formalize — a ratified PROSE sketch → its structure + relationships ───────
# The draft cast queue proposes characters as PROSE (one card each, relationships woven in); the
# writer ratifies, THEN we formalize. This extracts the structure already implied by the sketch +
# this character's relationships to the others named in it — so the approval flow stays human-readable
# and the structured harness is built on commit-to-keep, not up front. See [[conversational-edit-loop]].

_STR_LIST = {"type": "array", "items": {"type": "string"}}

FORMALIZE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "temperament", "want", "lie", "contradiction", "wound", "secret",
                 "good_memory", "background", "hobbies", "sayings", "relationships"],
    "properties": {
        "name": {"type": "string"},
        "temperament": {"type": "string"},
        "want": {"type": "string"},
        "lie": {"type": "string"},
        "contradiction": {"type": "string"},
        "wound": {"type": "string"},
        "secret": {"type": "string"},
        "good_memory": {"type": "string"},
        "background": {"type": "string"},
        "hobbies": _STR_LIST,
        "sayings": _STR_LIST,
        "relationships": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["target", "nature", "dynamic", "stance", "note"],
            "properties": {
                "target": {"type": "string"},
                "nature": {"type": "string"},
                "dynamic": {"type": "string"},
                "stance": {"type": "string", "enum": list(_STANCES)},
                "note": {"type": "string"},
            }}},
    },
}

FORMALIZE_SYS = (
    "You are given a character's PSYCHOLOGY and biography — a Big Five-grounded `temperament`, their "
    "`want` / `lie` / `contradiction` / `wound` (a real past trauma) / `secret`, and a `good_memory` (a "
    "cherished moment) — plus the roles of the OTHERS in the cast. Turn it into a legible person whose "
    "VOICE is GROUNDED in that psychology. Invent nothing the harness contradicts. The `contradiction` is "
    "the character's engine — the self-defeat where their want expresses as its OPPOSITE in action (they "
    "push away the thing they most want, and it reads as love/duty not fear); their VOICE must carry it.\n"
    "VOICE (the point — this is what makes them real): `sayings`, 6-8 lines of REAL speech that reveal "
    "this person IN MOTION. Do NOT write one tidy line per trait — a line-per-label is a worksheet, not a "
    "person. Each line is a SITUATED moment — someone pushed a button, asked a question, got too close, "
    "offered something — so the line carries the situation that pulled it out of them, never a free-floating "
    "aphorism. But the situation must show up ONLY in the WORDS — what they're refusing, reacting to, "
    "groping for. Each `sayings` item is PLAIN SPOKEN TEXT and NOTHING ELSE: no quotation marks (the app "
    "adds those), no dialogue tag ('she says', 'he blurts'), no parenthetical stage direction, no trailing "
    "narrator explanation of what the line means. If you catch yourself writing '(doing something)' or a "
    "clause after an em dash that explains the line instead of continuing to speak it, cut it — that's "
    "narration leaking into a quote field, and it renders broken. Let the want, lie, wound and defense "
    "TANGLE within and across lines, the way they do in a real person; a single line can be three of those "
    "at once.\n"
    "Across the set, make sure these land — braided, not isolated:\n"
    "  • the WOUND when something touches it — AND, crucially, the DEFENSE firing in the SAME breath. A "
    "defense is only legible against what it dodges: show the feeling press in and the swerve away from it "
    "in one line (he doesn't recite facts in a vacuum — he grabs for facts the instant an emotion threatens, "
    "so write the trigger AND the dodge, not just the dodge);\n"
    "  • the warmth of the GOOD MEMORY; the WANT pulling at them; the LIE they don't know they're saying;\n"
    "  • the CONTRADICTION made audible — at least one line where they push away the very thing they want "
    "(a refusal that is really a plea, warmth delivered as a shove, 'go' meaning 'stay'); this one must land.\n"
    "Each must sound like THIS specific person. FIGHT the smooth, witty, emotionally-articulate default "
    "voice — let them be flat, blunt, awkward, repetitive, or guarded if that's who the harness says they "
    "are.\n"
    "PERSON: a fitting `name`; a `background` (2-3 sentences — where they come from, their situation now); "
    "`hobbies` (2-4 concrete things they actually do). Echo back `temperament`, `want`, `lie`, "
    "`contradiction`, `wound`, `secret`, `good_memory` consistent with the input.\n"
    "Then `relationships` to the others — `target` (their role/name EXACTLY as listed), `nature`, "
    "`dynamic` (2-3 words for how they feel now), `stance` (devoted/warm/neutral/strained/hostile), `note` "
    "(the asymmetry or secret). Only to characters in the cast list. Keep every list ITEM short. JSON only."
)


def _strs(v, n: int = 4) -> list[str]:
    return [str(x).strip() for x in (v or []) if str(x).strip()][:n]


def formalize_harness(provider, blurb: str, role: str = "", others=None, world="") -> dict:
    """A designed harness (Big Five temperament + want/lie/wound/secret + good_memory, passed as `blurb`)
    → the full person: name/background/hobbies + a VOICE of 5 quotes each ANCHORED to a part of the
    psychology (the wound, the good memory, the want, the lie, the defense) — so the lines are coherent
    AND specific, not free-floating. `world` (the authored stage) anchors the background to a real place
    so it isn't improvised in a vacuum. No strengths/flaws (generic filler). Relationship targets are the
    other characters' roles/names (the caller resolves them to ids). See GENESIS.md."""
    if provider is None or not (blurb or "").strip():
        return {}
    others = [str(o).strip() for o in (others or []) if str(o).strip()]
    others_block = ("OTHER CHARACTERS IN THE CAST:\n" + "\n".join(f"- {o}" for o in others) + "\n\n"
                    if others else "")
    wb = world_brief(world)
    world_block = (f"WORLD (the shared stage — their background, references and situation MUST belong to "
                   f"it, not an invented elsewhere):\n{wb}\n\n" if wb else "")
    prompt = (world_block
              + "CHARACTER HARNESS (their psychology + biography)" + (f" (role: {role})" if role else "")
              + f":\n{blurb.strip()}\n\n" + others_block
              + "Write this person — their voice grounded in the harness, their life grounded in the world, "
              + "each saying anchored as instructed.")
    res = provider.generate_text(system=FORMALIZE_SYS, prompt=prompt, emits=FORMALIZE_SCHEMA)
    d = _data(res)
    rels = []
    for r in d.get("relationships", []):
        tgt = (r.get("target") or "").strip()
        if not tgt:
            continue
        stance = r.get("stance") if r.get("stance") in _STANCES else "neutral"
        rels.append({"target": tgt, "nature": (r.get("nature") or "").strip(),
                     "dynamic": _short(r.get("dynamic")), "stance": stance,
                     "note": (r.get("note") or "").strip()})
    return {"name": (d.get("name") or "").strip(),
            "temperament": (d.get("temperament") or "").strip(), "want": (d.get("want") or "").strip(),
            "lie": (d.get("lie") or "").strip(), "contradiction": (d.get("contradiction") or "").strip(),
            "wound": (d.get("wound") or "").strip(),
            "secret": (d.get("secret") or "").strip(), "good_memory": (d.get("good_memory") or "").strip(),
            "background": (d.get("background") or "").strip(),
            "hobbies": _strs(d.get("hobbies")), "sayings": _strs(d.get("sayings"), 8),
            "relationships": rels}


# ── 3) Story derivation — premise as an OUTPUT of the web ────────────────────────

_EDGE_REF = {"type": "object", "additionalProperties": False,
             "required": ["source", "target"],
             "properties": {"source": {"type": "string"}, "target": {"type": "string"}}}

DERIVE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "dramatic_question", "logline", "premise", "protagonist",
                     "anchors", "stakes", "intended_ending", "tone", "themes", "opening_edge"],
        "properties": {
            "title": {"type": "string"},
            "dramatic_question": {"type": "string"},
            "logline": {"type": "string"},
            "premise": {"type": "string"},
            "protagonist": {"type": "string"},
            "anchors": {"type": "array", "items": {"type": "string"}},
            "stakes": {"type": "string"},
            "intended_ending": {"type": "string"},
            "tone": {"type": "string"},
            "themes": {"type": "array", "items": {"type": "string"}},
            "opening_edge": _EDGE_REF,
        }}}},
}

DERIVE_SYS = (
    "You read a RELATIONSHIP WEB and DERIVE candidate stories from its most unstable configurations — "
    "an edge where wants collide, a secret that threatens a neighbor's want, a triangle. You do NOT "
    "invent new characters or relationships; you find the story already latent in the web. CRUCIAL: "
    "when an edge carries a 'common core' (↳), that is the GROUND a real closeness could grow from — the "
    "writer's seed. ENGINEER situations that make that closeness REACHABLE; do NOT script where it ends "
    "(the protagonist is the player's role — let it stay open). The `intended_ending` is at most a "
    "PLAUSIBLE convergence the bonds could reach, never a fixed decree. For each candidate give: "
    "a `title`; the central `dramatic_question` every scene will test (a 'Will X…?'); a `logline`; a "
    "one-paragraph `premise`; the `protagonist` (a harness id); the `anchors` (harness ids the story "
    "rides on, including the protagonist); the `stakes`; the `intended_ending` (the authored convergence "
    "— honor the wanted trajectory); `tone` (honor the wanted aesthetic); `themes`; and the `opening_edge` "
    "(the charged relationship the story OPENS on — source/target harness ids). Authored bookends, "
    "emergent middle: name the opening and the ending, not every beat. Reference characters by harness id. "
    "JSON only."
)


def _web_brief(harnesses: list[dict], edges: list[dict]) -> str:
    by = {h.get("id"): h for h in harnesses}
    lines = []
    for e in edges:
        s = by.get(e.get("source"), {}).get("role", e.get("source"))
        t = by.get(e.get("target"), {}).get("role", e.get("target"))
        note = f" · {e['note']}" if e.get("note") else ""
        lines.append(f"- {e.get('source')}({s}) → {e.get('target')}({t}): "
                     f"{e.get('nature', '')} / {e.get('dynamic', '')} [{e.get('stance', '')}]{note}")
        # The POTENTIAL is the GROUND for closeness (a basis, NOT a destination) — derive makes it reachable.
        if e.get("potential"):
            lines.append(f"    ↳ common core (ground for closeness): {e['potential']}")
    return "\n".join(lines) or "(no relationships)"


def derive_stories(provider, harnesses: list[dict], edges: list[dict],
                   steer: str = "", k: int = 3) -> list[dict]:
    """Derive up to `k` candidate stories from the web. Every id reference is validated;
    a candidate's protagonist is forced into its anchors; a bad opening_edge → None."""
    if provider is None or not harnesses:
        return []
    ids = {h.get("id") for h in harnesses}
    steer = (steer or "").strip()
    prompt = (f"CAST (by harness id):\n{_harness_brief(harnesses)}\n\n"
              f"WEB:\n{_web_brief(harnesses, edges)}\n\n"
              + (f"STEER: {steer}\n\n" if steer else "")
              + f"Derive up to {k} candidate stories from the most unstable parts of the web.")
    res = provider.generate_text(system=DERIVE_SYS, prompt=prompt, emits=DERIVE_SCHEMA)
    out = []
    for c in _data(res).get("candidates", [])[:k]:
        anchors = [a for a in (c.get("anchors") or []) if a in ids]
        prot = c.get("protagonist")
        if prot not in ids:
            prot = anchors[0] if anchors else next(iter(ids))
        if prot not in anchors:
            anchors = [prot] + anchors
        oe = c.get("opening_edge") or {}
        opening = ({"source": oe.get("source"), "target": oe.get("target")}
                   if oe.get("source") in ids and oe.get("target") in ids
                   and oe.get("source") != oe.get("target") else None)
        out.append({
            "title": (c.get("title") or "Untitled").strip(),
            "dramatic_question": (c.get("dramatic_question") or "").strip(),
            "logline": (c.get("logline") or "").strip(),
            "premise": (c.get("premise") or "").strip(),
            "protagonist": prot,
            "anchors": anchors,
            "stakes": (c.get("stakes") or "").strip(),
            "intended_ending": (c.get("intended_ending") or "").strip(),
            "tone": (c.get("tone") or "").strip(),
            "themes": [t for t in (c.get("themes") or []) if t],
            "opening_edge": opening,
        })
    return out


# ── 4) Scene cast — tension-weighted greedy walk (no LLM) ────────────────────────
# Dramatic distance ≠ emotional distance: enemies are dramatically ADJACENT. We grow a
# charged cluster around `focal` — each step pulls in the unchosen harness with the highest-
# charge edge to anyone already chosen. Distance-2 falls out naturally (a harness reachable
# only through a chosen member becomes adjacent once that member is in).

_CHARGE = {"hostile": 3, "strained": 2, "devoted": 2, "warm": 1, "neutral": 0}


def _charge(edge: dict) -> int:
    return _CHARGE.get(edge.get("stance", "neutral"), 0)


def scene_cast(edges: list[dict], focal: str, n: int = 2) -> list[str]:
    """The `n` most dramatically-entangled harness ids around `focal` (focal first)."""
    n = max(2, int(n or 2))
    adj: dict[str, dict[str, int]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        w = _charge(e)
        for a, b in ((s, t), (t, s)):
            row = adj.setdefault(a, {})
            row[b] = max(row.get(b, -1), w)
    chosen, chosen_set = [focal], {focal}
    while len(chosen) < n:
        best, best_w = None, -1
        for node in chosen:
            for nb, w in adj.get(node, {}).items():
                if nb not in chosen_set and w > best_w:
                    best, best_w = nb, w
        if best is None:
            break  # component exhausted — focal's cluster is smaller than n
        chosen.append(best)
        chosen_set.add(best)
    return chosen


# ── Runtime projection — the web is read LOCALLY, never copied (see GENESIS.md §8) ──
# At play time we don't dump the whole web; we project the slice touching the characters on
# stage + mentioned, grouped per character (perspectival). Relationships live in structure;
# this is what reaches the LLM.

def project_web(relationships: list[dict], focus_keys) -> dict:
    """Group the web edges touching any key in `focus_keys`, keyed by the in-focus character
    (perspectival — each sees their own bonds, both directions). Returns
    {key: [{other, nature, dynamic, stance, outward}]}. Edges with neither end in focus are
    skipped entirely; an edge with both ends in focus appears under both perspectives."""
    focus = set(focus_keys or [])
    out: dict = {}
    for r in relationships or []:
        s, t = r.get("source"), r.get("target")
        if not s or not t:
            continue
        if s in focus:
            out.setdefault(s, []).append({"other": t, "nature": r.get("nature", ""),
                                          "dynamic": r.get("dynamic", ""),
                                          "stance": r.get("stance", "neutral"), "outward": True})
        if t in focus:
            out.setdefault(t, []).append({"other": s, "nature": r.get("nature", ""),
                                          "dynamic": r.get("dynamic", ""),
                                          "stance": r.get("stance", "neutral"), "outward": False})
    return out


# ── 5) Naming — commit-time only (harnesses stay unnamed until here) ─────────────

NAME_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["names"],
    "properties": {"names": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "name", "appearance"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "appearance": {"type": "string"},
        }}}},
}

NAME_SYS = (
    "Give each unnamed harness a fitting NAME and a short APPEARANCE, consistent with its role, want "
    "and wound. Appearance is comma-separated Danbooru-style tags (hair, eyes, build, age range, "
    "clothing) for an image model — not prose. Reference each by its harness `id`. JSON only."
)


def name_cast(provider, harnesses: list[dict]) -> dict:
    """Map harness id -> {name, appearance}. Empty for ids the model skips (caller falls back)."""
    if provider is None or not harnesses:
        return {}
    ids = {h.get("id") for h in harnesses}
    res = provider.generate_text(
        system=NAME_SYS, prompt=f"HARNESSES:\n{_harness_brief(harnesses)}", emits=NAME_SCHEMA)
    out = {}
    for it in _data(res).get("names", []):
        if it.get("id") in ids and (it.get("name") or "").strip():
            out[it["id"]] = {"name": it["name"].strip(), "appearance": (it.get("appearance") or "").strip()}
    return out


def persona_from_harness(h: dict) -> str:
    """A persona seed from a harness — richer when the draft was fleshed (background / good_memory /
    hobbies / sayings via formalize); the 'flesh out' front door enriches it further."""
    def _join(v):
        return ", ".join(str(x) for x in (v or []) if str(x).strip())
    bits = [h["background"].strip() if h.get("background") else h.get("role", "")]
    if h.get("temperament"):
        bits.append(f"Temperament: {h['temperament']}.")
    if h.get("want"):
        bits.append(f"They want {h['want']}.")
    if h.get("lie"):
        bits.append(f"They believe, falsely, that {h['lie']}.")
    if h.get("contradiction"):
        bits.append(f"Their self-defeat: {h['contradiction']}.")
    if h.get("wound"):
        bits.append(f"Beneath it is an old wound: {h['wound']}.")
    if h.get("good_memory"):
        bits.append(f"A good memory they hold: {h['good_memory']}.")
    if h.get("secret"):
        bits.append(f"They keep a secret: {h['secret']}.")
    if h.get("hobbies"):
        bits.append(f"Hobbies: {_join(h['hobbies'])}.")
    if h.get("sayings"):
        bits.append("They might say: " + "; ".join(f'"{s}"' for s in h["sayings"][:2]) + ".")
    return " ".join(b for b in bits if b).strip()


# --- premise, substrate, systems, and prologue material ---

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
        from ...server.services import lorebook_store as _LS
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
        from ...server.services import lorebook_store as _LS
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


# --- geography and movement ---

"""Geography — where characters CAN be, and how fast they can move.

Fiction doesn't need pathfinding; it needs plausibility + rate limits. Three pieces:

  • hops(a, b) — a coarse travel distance derived from the existing Location TREE
    (`Location.parent`): same spot 0, parent/sibling 1, cousin 2, different area 3.
    No coordinates. Authored overrides via Story.fields["travel"] = [[a, b, hops], …].
  • orbit(char) — the character's habitual spots, read from data that already exists:
    Place scenes anchored to them + their card's home_scenes. An off-screen character
    is findable in their orbit, not wherever the model invents.
  • make_move_validator(...) — the ENFORCEMENT hook for the state engine's `move` op:
    an off-screen character may only move somewhere reachable in the turns elapsed
    since they were last placed (1 hop/turn). On-screen (narrated) movement is canon
    and always allowed. Unresolvable free-text targets ("the corner of the room") are
    treated as sub-spots of wherever they are — allowed.

The compiler (play_context) uses orbits + last-seen for whereabouts lines, so the
narrator is TOLD where people plausibly are; this module makes sure the state can't
drift somewhere implausible even when the prose leaks.
"""

_MAX_HOPS = 3          # "different area entirely" — a real journey
_HOPS_PER_TURN = 1     # off-screen drift rate


# ── Resolution: free text → a known location id ─────────────────────────────────────

def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def resolve_loc(st, text: str) -> str | None:
    """Match free text against location ids/names (exact or contained). None = unknown
    (a micro-spot like 'the corner' — not a mappable place)."""
    t = _norm(text)
    if not t:
        return None
    for l in st.locations:
        if t == _norm(l.id) or t == _norm(l.name):
            return l.id
    for l in st.locations:
        n = _norm(l.name)
        if n and (n in t or t in n):
            return l.id
    return None


# ── Distance: the Location tree as a coarse travel graph ────────────────────────────

def _path_to_root(by_id: dict, lid: str) -> list[str]:
    path, seen = [lid], {lid}
    while True:
        parent = getattr(by_id.get(path[-1]), "parent", "") or ""
        if not parent or parent in seen or parent not in by_id:
            return path
        path.append(parent)
        seen.add(parent)


def hops(st, a: str, b: str) -> int:
    """Coarse travel distance between two location ids. Authored Story.fields['travel']
    entries ([[a, b, hops], …], by id or name) override the tree-derived default."""
    if a == b:
        return 0
    for e in (getattr(st, "fields", None) or {}).get("travel") or []:
        try:
            ea, eb, eh = _norm(e[0]), _norm(e[1]), int(e[2])
        except (TypeError, ValueError, IndexError):
            continue
        if {_norm(a), _norm(b)} == {ea, eb}:
            return eh
    by_id = {l.id: l for l in st.locations}
    if a not in by_id or b not in by_id:
        return _MAX_HOPS
    pa, pb = _path_to_root(by_id, a), _path_to_root(by_id, b)
    common = next((x for x in pa if x in set(pb)), None)
    if common is None:
        return _MAX_HOPS
    return min(_MAX_HOPS + 1, pa.index(common) + pb.index(common))


# ── Orbits: the spots a character habitually occupies (data that already exists) ────

def orbit(ctx, st, char_key: str) -> list[str]:
    """Habitual spot names for a cast character: location scenes anchored to them, plus
    their card's portable home scenes. Display strings ('Mara's bench — The Grove')."""
    spots: list[str] = []
    for l in st.locations:
        for s in (l.scenes or []):
            anchors = ([s.character] if s.character else []) + list(s.characters or [])
            if char_key in anchors:
                spots.append(f"{s.name or s.id} ({l.name})" if l.name else (s.name or s.id))
    c = ctx.base_settings.characters.get(char_key)
    for h in (getattr(c, "home_scenes", []) or []):
        spots.append(h.name or h.id)
    seen: set[str] = set()
    return [s for s in spots if not (s.lower() in seen or seen.add(s.lower()))][:4]


# ── Enforcement: the `move` delta validator ─────────────────────────────────────────

def make_move_validator(st, on_screen_names: set[str]):
    """Build the hook runtime_state's `move` op calls: (ws, name, target) → allow?
    On-screen characters: always allowed (narrated movement is canon). Off-screen:
    allowed only if the target is reachable in the turns elapsed since they were last
    placed. Unresolvable targets (micro-spots) are allowed."""
    onscreen = {n.lower() for n in on_screen_names}

    def validate(ws: dict, name: str, target: str) -> bool:
        if (name or "").lower() in onscreen:
            return True
        dest = resolve_loc(st, target)
        if dest is None:
            return True                        # micro-spot / unknown → not mappable
        ent = (ws.get("entities") or {}).get(name) or {}
        src = resolve_loc(st, ent.get("location") or "")
        if src is None:
            return True                        # never placed → anywhere is plausible
        elapsed = max(1, int(ws.get("step") or 0) - int(ent.get("loc_step") or 0))
        return hops(st, src, dest) <= elapsed * _HOPS_PER_TURN

    return validate


# ── Generation: the genesis geography node (one targeted call) ──────────────────────
# Grounded in the cast's daily lives (per the particularity lessons): spots are the places
# these specific people already occupy, named the way locals would name them.

GEOGRAPHY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["areas", "travel"],
    "properties": {
        "areas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "spots"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "spots": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["name", "description", "inhabitants"],
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string", "description":
                                        "the spot, objectively — no events, no people"},
                        "inhabitants": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["character", "doing"],
                            "properties": {
                                "character": {"type": "string",
                                              "description": "exact cast name"},
                                "doing": {"type": "string",
                                          "description": "what they habitually do here"},
                            }}},
                    }}},
            }}},
        "travel": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["a", "b", "hops"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string"},
                           "hops": {"type": "integer", "description":
                                    "1 short walk · 2 across the settlement · 3 a real journey"}}}},
    },
}

GEOGRAPHY_SYS = (
    "You are mapping the LIVED GEOGRAPHY of a story: the places its specific cast actually "
    "occupies day to day — not a fantasy atlas.\n"
    "Give 2-3 AREAS (districts/regions), each with 2-4 SPOTS (a home, a workplace, a haunt). "
    "Name places the way LOCALS would: functional, worn, particular ('the fish sheds', "
    "'Marta's bakery', 'the north jetty') — NEVER adjective-noun-mystique names, never "
    "renfaire twee. Spot descriptions are objective and concrete (what's physically there), "
    "one or two sentences, no events, no mood-writing.\n"
    "Anchor the CAST: every cast member gets 1-3 habitual spots (where they live, where they "
    "work, where they linger) with `doing` = the habitual activity, concrete and mundane.\n"
    "If EXISTING LOCATIONS are given, keep their names verbatim as spots (or areas) and place "
    "them in your map — do not rename or duplicate them.\n"
    "`travel`: ONLY the non-obvious distances (a far cave, an island, a shortcut) — pairs of "
    "place names with hops (1 short walk, 2 across the settlement, 3 a real journey). Spots "
    "inside one area are already near each other; don't list those. JSON only."
)


def gen_geography(provider, *, premise: str = "", tone: str = "", world_note: str = "",
                  cast: list[dict] | None = None, existing: list[str] | None = None,
                  root=None) -> dict:
    """ONE focused generation: areas → spots → who habitually occupies them + travel notes.
    `cast` items: {name, about}. With `root`, real-work place studies are retrieved as register
    references. Returns the raw {areas, travel} dict ({} on failure)."""
    if provider is None:
        return {}
    parts = []
    if root is not None:
        ex = exemplar_notes(root, f"{premise} {world_note}", 2, "_location_exemplars")
        if ex:
            parts.append("REFERENCE — how real serials make places feel worked-in. Match the "
                         "REGISTER; never copy names or specifics:\n" + ex)
    if premise:
        parts.append(f"PREMISE: {premise}")
    if tone:
        parts.append(f"TONE: {tone}")
    if world_note:
        parts.append(f"WORLD: {world_note}")
    for c in (cast or []):
        parts.append(f"CAST — {c.get('name', '?')}: {c.get('about', '')}")
    if existing:
        parts.append("EXISTING LOCATIONS (keep verbatim, place them in the map): "
                     + "; ".join(existing))
    parts.append("Map the lived geography.")
    res = provider.generate_text(system=GEOGRAPHY_SYS, prompt="\n".join(parts),
                                 emits=GEOGRAPHY_SCHEMA)
    d = getattr(res, "data", None) or {}
    return d if d.get("areas") else {}


def _slug(name: str, taken: set[str]) -> str:
    import re
    base = re.sub(r"[^\w]+", "_", _norm(name)).strip("_") or "loc"
    s, i = base, 2
    while s in taken:
        s, i = f"{base}_{i}", i + 1
    taken.add(s)
    return s


def apply_geography(geo: dict, st, name_to_key: dict[str, str]) -> dict:
    """Convert a generated {areas, travel} into story-shaped data: `locations` (a tree —
    existing locations KEPT, matched by name and re-parented; inhabited spots gain
    character-anchored `scenes` = the orbits), and `travel` overrides. Pure."""
    existing = {_norm(l.name): l for l in st.locations}
    locations = [l.model_dump() for l in st.locations]
    by_norm = {_norm(l["name"]): l for l in locations}
    taken = {l["id"] for l in locations}

    def _ensure(name: str, description: str, parent: str) -> dict:
        loc = by_norm.get(_norm(name))
        if loc is None:
            loc = {"id": _slug(name, taken), "name": name,
                   "description": description, "parent": parent}
            locations.append(loc)
            by_norm[_norm(name)] = loc
        else:
            # never self-parent (the model may echo an existing name as both area AND spot)
            if not loc.get("parent") and parent and parent != loc["id"]:
                loc["parent"] = parent
            if description and not loc.get("description"):
                loc["description"] = description
        return loc

    def _char_key(nm: str) -> str | None:
        t = _norm(nm)
        for full, key in name_to_key.items():
            fn = _norm(full)
            if t == fn or t == fn.split()[0] or fn.split()[0] == t.split()[0]:
                return key
        return None

    for area in geo.get("areas") or []:
        a = _ensure(area.get("name", ""), area.get("description", ""), "")
        for spot in area.get("spots") or []:
            s = _ensure(spot.get("name", ""), spot.get("description", ""), a["id"])
            scenes = []
            for inh in spot.get("inhabitants") or []:
                ck = _char_key(inh.get("character", ""))
                if ck:
                    scenes.append({"id": f"{s['id']}_{ck}", "name": spot.get("name", ""),
                                   "character": ck, "backstory": inh.get("doing", "")})
            if scenes:
                s["scenes"] = scenes       # orbits live directly on the location now

    travel = []
    for e in geo.get("travel") or []:
        la, lb = by_norm.get(_norm(e.get("a", ""))), by_norm.get(_norm(e.get("b", "")))
        if la is not None and lb is not None and la is not lb:
            travel.append([la["id"], lb["id"], max(1, min(4, int(e.get("hops") or 1)))])

    _ = existing  # (kept for clarity: existing locations were merged above, never dropped)
    return {"locations": locations, "travel": travel}


def demo() -> None:
    """Self-check: tree distance, overrides, validator rate-limiting.
    Run: python -m loom.stories.geography"""
    from types import SimpleNamespace as NS
    locs = [NS(id="town", name="Town", parent=""),
            NS(id="market", name="The Market", parent="town"),
            NS(id="inn", name="The Wandering Inn", parent="town"),
            NS(id="wilds", name="The Wilds", parent=""),
            NS(id="cave", name="Shield Cave", parent="wilds")]
    st = NS(locations=locs, fields={"travel": [["inn", "cave", 3]]})
    assert hops(st, "inn", "inn") == 0
    assert hops(st, "market", "inn") == 2      # sibling spots via town
    assert hops(st, "market", "town") == 1     # child → parent
    assert hops(st, "market", "cave") == _MAX_HOPS   # different roots
    assert hops(st, "inn", "cave") == 3        # authored override
    assert resolve_loc(st, "the wandering inn") == "inn"
    assert resolve_loc(st, "over by the market stalls") == "market"
    assert resolve_loc(st, "the corner of the room") is None
    v = make_move_validator(st, on_screen_names={"Erin"})
    ws = {"step": 5, "entities": {"Pisces": {"location": "The Market", "loc_step": 4},
                                  "Erin": {"location": "The Wandering Inn", "loc_step": 4}}}
    assert v(ws, "Erin", "Shield Cave")        # on-screen: narrated movement is canon
    assert not v(ws, "Pisces", "Shield Cave")  # off-screen: 3 hops in 1 turn → blocked
    assert v(ws, "Pisces", "Town")             # 1 hop in 1 turn → fine
    ws["entities"]["Pisces"]["loc_step"] = 1   # 4 turns elapsed
    assert v(ws, "Pisces", "Shield Cave")      # had time to travel → allowed
    assert v(ws, "Pisces", "the corner of the room")   # micro-spot → allowed

    # apply_geography: existing location kept + re-parented; orbits → location.scenes; travel → ids.
    class _L:                                           # minimal Location stand-in
        def __init__(self, **kw): self.__dict__.update(kw)
        def model_dump(self): return dict(self.__dict__)
    st2 = NS(locations=[_L(id="n1", name="The Whispering Grove", description="", parent="")],
             fields={})
    geo = {"areas": [{"name": "The Village", "description": "a fishing village", "spots": [
        {"name": "The Whispering Grove", "description": "old trees", "inhabitants":
            [{"character": "Mara", "doing": "reads on the bench"}]},
        {"name": "the north jetty", "description": "weathered planks", "inhabitants": []},
    ]}], "travel": [{"a": "the north jetty", "b": "The Whispering Grove", "hops": 2}]}
    out = apply_geography(geo, st2, {"Mara": "mara"})
    by = {l["name"]: l for l in out["locations"]}
    assert by["The Whispering Grove"]["id"] == "n1"            # existing kept, not duplicated
    assert by["The Whispering Grove"]["parent"] == by["The Village"]["id"]   # re-parented
    # self-parent guard: an existing name echoed as area AND spot must not parent itself
    geo_self = {"areas": [{"name": "The Whispering Grove", "description": "", "spots": [
        {"name": "The Whispering Grove", "description": "", "inhabitants": []}]}], "travel": []}
    st3 = NS(locations=[_L(id="g1", name="The Whispering Grove", description="", parent="")],
             fields={})
    o2 = apply_geography(geo_self, st3, {})
    assert o2["locations"][0]["parent"] != "g1", o2["locations"][0]
    # orbit anchored: the Grove's location now carries a scene for Mara
    grove = next(l for l in out["locations"] if l["id"] == "n1")
    assert grove["scenes"][0]["character"] == "mara"
    assert out["travel"][0][:2] == [by["the_north_jetty".replace('_', ' ')]["id"], "n1"] or \
           out["travel"][0][2] == 2                             # ids resolved, hops clamped
    print("geography demo ok")


if __name__ == "__main__":
    demo()


# --- creation workflow orchestration ---

"""Genesis as a pydantic-graph pipeline — the full 'grow a story' run as focused, resumable,
streamed operations.

Same substrate as loom/stories/graph_pipeline.py (GraphBuilder + a typed State/Deps + thin steps
that call the existing providers with `emits=`), applied to world/character/scene generation:

    start ─┬─ premise ──┐
           └─ substrate ┴─ (join) → particulars → cast → weave → scene → assemble → end
                    (premise ‖ substrate run in parallel — the two slow calls overlap)

Each node is ONE focused generation (its own prompt + schema), reusing the tested functions in
worldgen.py / genesis.py verbatim — the graph replaces the ad-hoc wiring, not the generation
logic. Steps stream progress via injected `GenesisDeps` callbacks (framework-agnostic → SSE), and
the graph is resumable via pydantic_graph persistence (see run_genesis). This is the template the
other generation pipelines (worldgen systems / scaffold / wardrobe / research) get migrated onto.
"""


def _noop_event(_: dict) -> None: ...
def _never_cancel() -> bool: return False


@dataclass
class GenesisDeps:
    """I/O the steps need, injected by the endpoint — keeps the graph framework-agnostic."""
    provider: Any
    root: Any = None                                # project root — exemplar-deck retrieval
    critic_provider: Any = None                     # scene refine critic (defaults to provider)
    on_event: Callable[[dict], None] = _noop_event  # structured progress → SSE
    cancel: Callable[[], bool] = _never_cancel
    # RESUME: called with the full state dict after each node completes, so an interrupted run can
    # be reloaded and re-run — completed nodes short-circuit (see the `if s.<field>:` guards), so a
    # resumed run replays instantly through what's done and only re-runs what's missing.
    on_checkpoint: Callable[[dict], None] = _noop_event


@dataclass
class GenesisState:
    """Everything that flows across the pipeline (and persists for resume)."""
    seed: str = ""
    world: str = ""            # optional world-frame string for cast grounding (else derived)
    grounding: str = ""        # psyche notes, precomputed by the endpoint (ctx-dependent)
    rounds: int = 2            # scene refine rounds
    premise: dict = field(default_factory=dict)
    substrate: dict = field(default_factory=dict)
    particulars: list = field(default_factory=list)
    cast: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    geography: dict = field(default_factory=dict)   # raw {areas, travel} — applied at commit
    scene: dict = field(default_factory=dict)


async def _thread(fn, *a, **kw):
    """Run a sync provider-call function off the event loop."""
    return await asyncio.to_thread(lambda: fn(*a, **kw))


def _emit(ctx, node: str, status: str, **extra) -> None:
    ctx.deps.on_event({"type": "node", "node": node, "status": status, **extra})


def _checkpoint(ctx) -> None:
    """Persist the accumulated state so an interrupted run can resume from here."""
    ctx.deps.on_checkpoint(asdict(ctx.state))


def _world_str(s: GenesisState) -> str:
    """A short world frame for cast grounding — the endpoint's `world`, else derived from what's made."""
    if (s.world or "").strip():
        return s.world.strip()
    sub, prem = s.substrate or {}, s.premise or {}
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    bits = [b for b in (prem.get("logline", ""), place, sub.get("preoccupation", "")) if b]
    return " — ".join(bits)


def _scene_brief(s: GenesisState) -> str:
    """Compose the scene brief from premise + substrate ache/place + a few lived fragments + cast."""
    p, sub = s.premise or {}, s.substrate or {}
    parts: list[str] = []
    if p.get("logline"):
        parts.append(f"PREMISE: {p['logline']}")
    for k in ("protagonist", "want", "obstacle", "stakes", "spark"):
        if p.get(k):
            parts.append(f"{k.upper()}: {p[k]}")
    if sub.get("preoccupation"):
        parts.append(f"THE ACHE (implicit — never named): {sub['preoccupation']}")
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    if place:
        parts.append(f"PLACE: {place}")
    frags = [f.get("text", "") for f in (s.particulars or [])[:3] if isinstance(f, dict) and f.get("text")]
    if frags:
        parts.append("WORLD TEXTURE (draw on, do NOT explain):\n" + "\n".join(f"- {t}" for t in frags))
    names = [(h.get("name") or h.get("role") or "") for h in (s.cast or [])[:4] if isinstance(h, dict)]
    names = [n for n in names if n]
    if names:
        parts.append("CAST who may appear: " + ", ".join(names))
    return "\n".join(parts)


# ── Steps (each ONE focused generation, reusing the tested functions) ──────────────

async def step_premise(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.premise:                       # resume: already generated → skip the model call
        _emit(ctx, "premise", "cached"); return s.premise
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "premise", "start")
    s.premise = await _thread(build_premise, ctx.deps.provider, s.seed) or {}
    _emit(ctx, "premise", "done", data=s.premise)
    _checkpoint(ctx)
    return s.premise


async def step_substrate(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.substrate:
        _emit(ctx, "substrate", "cached"); return s.substrate
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "substrate", "start")
    s.substrate = await _thread(build_substrate, ctx.deps.provider, s.seed) or {}
    _emit(ctx, "substrate", "done", data=s.substrate)
    _checkpoint(ctx)
    return s.substrate


async def step_particulars(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.particulars:
        _emit(ctx, "particulars", "cached", count=len(s.particulars)); return s.particulars
    if ctx.deps.cancel():
        return []
    _emit(ctx, "particulars", "start")
    s.particulars = await _thread(gen_particulars, ctx.deps.provider, s.seed, 6, s.substrate or None) or []
    _emit(ctx, "particulars", "done", count=len(s.particulars), data=s.particulars)
    _checkpoint(ctx)
    return s.particulars


async def step_cast(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.cast:
        _emit(ctx, "cast", "cached", count=len(s.cast)); return s.cast
    if ctx.deps.cancel():
        return []
    _emit(ctx, "cast", "start")
    s.cast = await _thread(design_by_role, ctx.deps.provider, s.seed, s.grounding, _world_str(s)) or []
    _emit(ctx, "cast", "done", count=len(s.cast), data=s.cast)
    _checkpoint(ctx)
    return s.cast


async def step_weave(ctx: StepContext[GenesisState, GenesisDeps, None]) -> list:
    s = ctx.state
    if s.relationships:
        _emit(ctx, "weave", "cached", count=len(s.relationships)); return s.relationships
    if ctx.deps.cancel() or len(s.cast) < 2:
        return s.relationships
    _emit(ctx, "weave", "start")
    s.relationships = await _thread(weave_relationships, ctx.deps.provider, s.cast) or []
    _emit(ctx, "weave", "done", count=len(s.relationships), data=s.relationships)
    _checkpoint(ctx)
    return s.relationships


async def step_geography(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.geography:
        _emit(ctx, "geography", "cached"); return s.geography
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "geography", "start")
    sub = s.substrate or {}
    place = sub.get("place")
    place = place.get("name", "") if isinstance(place, dict) else (place or "")
    # Harnesses are NAMELESS until commit — use the short function tag ('protagonist'…) as the
    # handle, never the whole role paragraph (it pollutes the map prompt as a fake name).
    cast_lines = [{"name": h.get("name") or h.get("function") or "?",
                   "about": (h.get("role") or h.get("want") or "")[:160]}
                  for h in (s.cast or []) if isinstance(h, dict)]
    s.geography = await _thread(gen_geography, ctx.deps.provider,
                                premise=(s.premise or {}).get("logline", ""),
                                world_note=place, cast=cast_lines, root=ctx.deps.root) or {}
    _emit(ctx, "geography", "done",
          areas=len((s.geography or {}).get("areas") or []), data=s.geography)
    _checkpoint(ctx)
    return s.geography


async def step_scene(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    s = ctx.state
    if s.scene:
        _emit(ctx, "scene", "cached"); return s.scene
    if ctx.deps.cancel():
        return {}
    _emit(ctx, "scene", "start")
    critic = ctx.deps.critic_provider or ctx.deps.provider
    out = await _thread(loop_scene, ctx.deps.provider, _scene_brief(s), s.rounds, critic) or {}
    s.scene = out.get("scene") or {}
    _emit(ctx, "scene", "done", data=s.scene, rounds_used=out.get("rounds_used"), trace=out.get("trace"))
    _checkpoint(ctx)
    return s.scene


async def step_assemble(ctx: StepContext[GenesisState, GenesisDeps, None]) -> dict:
    # The graph's return value is delivered to the client as the job's `result` event — no
    # terminal event here (a `done` type would collide with the job wrapper's own `done`).
    s = ctx.state
    return {"premise": s.premise, "substrate": s.substrate, "particulars": s.particulars,
            "cast": s.cast, "relationships": s.relationships, "geography": s.geography,
            "scene": s.scene}


# ── Graph construction ─────────────────────────────────────────────────────────────

def _build_genesis_graph():
    gb = GraphBuilder(state_type=GenesisState, deps_type=GenesisDeps, output_type=dict)
    pr, sub = gb.step(step_premise), gb.step(step_substrate)
    part, cast = gb.step(step_particulars), gb.step(step_cast)
    weave, geo = gb.step(step_weave), gb.step(step_geography)
    scene, asm = gb.step(step_scene), gb.step(step_assemble)
    join = gb.join(reduce_null)
    join2 = gb.join(reduce_null)
    gb.add(gb.edge_from(gb.start_node).to(pr, sub))   # fork: the two slow calls overlap
    gb.add_edge(pr, join)
    gb.add_edge(sub, join)
    gb.add_edge(join, part)                            # then linear (each reads prior state)
    gb.add_edge(part, cast)
    gb.add(gb.edge_from(cast).to(weave, geo))          # fork: web + geography both hang off cast
    gb.add_edge(weave, join2)
    gb.add_edge(geo, join2)
    gb.add_edge(join2, scene)
    gb.add_edge(scene, asm)
    gb.add_edge(asm, gb.end_node)
    return gb.build()


GENESIS_GRAPH = _build_genesis_graph()


def genesis_state_from(d: dict) -> GenesisState:
    """Rebuild GenesisState from a persisted checkpoint dict (ignores unknown/legacy keys)."""
    names = {f.name for f in _dc_fields(GenesisState)}
    return GenesisState(**{k: v for k, v in (d or {}).items() if k in names})


# Run checkpoint persistence used to live here as per-run JSON files (configs/genesis_runs/);
# removed with the move of all data handling to the relational store — the graph checkpoints
# in-process via `deps.on_checkpoint` and nothing called save_run/load_run anymore.


async def run_genesis(state: GenesisState, deps: GenesisDeps) -> dict:
    """Grow a full story: premise → substrate → particulars → cast → weave → scene. Progress
    streams through `deps.on_event`; state is checkpointed after each node via `deps.on_checkpoint`.
    RESUME: pass a `state` rebuilt from the last checkpoint (genesis_state_from) — completed nodes
    short-circuit (their `if s.<field>:` guards) and only the missing ones re-run."""
    return await GENESIS_GRAPH.run(state=state, deps=deps, inputs=None)


# ── Bottom-up WORLD SYSTEMS graph — accrete interlocking systems → let a scenario emerge ───────

@dataclass
class SystemsState:
    seed: str = ""
    n: int = 4
    systems: list = field(default_factory=list)
    scenario: dict = field(default_factory=dict)


async def step_systems(ctx: StepContext[SystemsState, GenesisDeps, None]) -> list:
    if ctx.deps.cancel():
        return []
    _emit(ctx, "systems", "start")
    s = ctx.state
    s.systems = await _thread(accrete_systems, ctx.deps.provider, s.seed, s.n) or []
    _emit(ctx, "systems", "done", count=len(s.systems), data=s.systems)
    return s.systems


async def step_scenario(ctx: StepContext[SystemsState, GenesisDeps, None]) -> dict:
    if ctx.deps.cancel() or not ctx.state.systems:
        return {}
    _emit(ctx, "scenario", "start")
    s = ctx.state
    s.scenario = await _thread(scenario_from_systems, ctx.deps.provider, s.systems, s.seed) or {}
    _emit(ctx, "scenario", "done", data=s.scenario)
    return s.scenario


async def step_systems_assemble(ctx: StepContext[SystemsState, GenesisDeps, None]) -> dict:
    return {"systems": ctx.state.systems, "scenario": ctx.state.scenario}


def _build_systems_graph():
    gb = GraphBuilder(state_type=SystemsState, deps_type=GenesisDeps, output_type=dict)
    sysn, scen, asm = gb.step(step_systems), gb.step(step_scenario), gb.step(step_systems_assemble)
    gb.add_edge(gb.start_node, sysn)
    gb.add_edge(sysn, scen)
    gb.add_edge(scen, asm)
    gb.add_edge(asm, gb.end_node)
    return gb.build()


SYSTEMS_GRAPH = _build_systems_graph()


async def run_systems(state: SystemsState, deps: GenesisDeps) -> dict:
    """Bottom-up world gen: accrete interlocking systems, then let a scenario emerge. Streams
    a `node` event per stage through `deps.on_event`."""
    return await SYSTEMS_GRAPH.run(state=state, deps=deps, inputs=None)


# --- character card to story seed ---

"""Card -> MATURE SEED: turn an imported character card into a story foundation you can roleplay from.

Chains the existing depth engine (loom.stories.characters.engine), CARD-FIRST: read the card -> infer the
one canonical archetype that fits -> derive the grounded WORLD + the quiet CENTRAL QUESTION the character
anchors -> deepen them into a continuous backstory (the archetype's characteristic wound instantiated in
that world, on a strong model) -> psych -> an OPENING scene. The result is enough to start an ongoing
roleplay, and later (the transcript->story converter) to bake into an illustrated story.

The heavy passes (`character_engine`) already exist; this only orchestrates them from a card instead of a
world seed. Self-check: python -m loom.stories.story_seed
"""

from ..characters.engine import _ARCHETYPES, _CLINICAL, _PSYCH_SYS, _backstory_sys, _pass
from ..characters.labeled import parse_labeled, run_text


def _clin(body: str) -> str:
    return _CLINICAL + "\n\n" + body


_ARCHETYPE_SYS = (
    "Read the character card. Choose the ONE canonical archetype that best fits this character from this "
    "list: " + ", ".join(_ARCHETYPES) + ".\nOutput one line, nothing else:\nARCHETYPE: <one of the list>"
)

_SEED_SYS = (
    "From the character card, establish a STORY FOUNDATION to roleplay from. Give a grounded WORLD (the "
    "situation and stage this character lives in, 2-3 plain sentences, true to the card's scenario) and "
    "the CENTRAL QUESTION the story quietly asks — a lived human feeling a reader recognises (loneliness, "
    "wanting to belong, being afraid to be happy, loving something you know you'll lose). It must NOT be a "
    "dramatic abstraction or a debate prompt; a plain feeling, answered by people, understated.\n"
    "Output exactly, nothing else:\nWORLD: <...>\nQUESTION: <...>"
)

_OPENING_SYS = (
    "Write the OPENING scene of an ongoing story to roleplay from — a specific first moment that puts "
    "THIS character in motion in their world, faithful to the card's scenario, in a quiet grounded "
    "register. 3-5 sentences, ending on a beat that invites the other person (the player) to respond. "
    "Output one line:\nOPENING: <...>"
)


def build_seed(provider, *, card: str, backstory_provider=None) -> dict:
    """Turn a character card (name + description + scenario, as text) into a MATURE SEED. `provider` runs
    the light passes; `backstory_provider` (a stronger model, optional) writes the continuous backstory.
    Returns the seed dict, or {} if the card yields no usable trauma."""
    cctx = f"CHARACTER CARD:\n{card.strip()}\n"

    # 1. infer the canonical archetype (fall back to a safe default off-list)
    a = parse_labeled(run_text(provider, _clin(_ARCHETYPE_SYS), cctx), ["ARCHETYPE"]).get("ARCHETYPE", "")
    name = a.strip().lower()
    name = name if name in _ARCHETYPES else "childhood friend"
    arche = _ARCHETYPES[name]

    # 2. world + the quiet central question, seeded by the card
    ws = parse_labeled(run_text(provider, _clin(_SEED_SYS), cctx), ["WORLD", "QUESTION"])
    world, question = ws.get("WORLD", ""), ws.get("QUESTION", "")

    # 3. deepen: reverse-derive the archetype's characteristic wound through THIS world, card as the face
    bctx = (f"WORLD: {world}\nCENTRAL QUESTION: {question}\n{cctx}"
            f"ARCHETYPE: {name}\nCHARACTERISTIC WOUND (instantiate THIS in the world): {arche['wound']}\n"
            "Keep the character's NAME and surface faithful to the card; the backstory is what's underneath.\n")
    ident = _pass(backstory_provider or provider, _clin(_backstory_sys("mundane")), bctx,
                  ["NAME", "BACKSTORY", "TRAUMA", "BOND", "SECRET"], ["NAME", "BACKSTORY", "TRAUMA"])
    if not ident.get("TRAUMA"):
        return {}

    # 4. psych, built around the archetype's coping
    pctx = (f"WORLD: {world}\nNAME: {ident['NAME']}\nBACKSTORY: {ident['BACKSTORY']}\n"
            f"TRAUMA: {ident['TRAUMA']}\nSECRET: {ident['SECRET']}\n"
            f"ASSIGNED COPING (build ENGINE around this, do NOT default to control): {arche['coping']}\n")
    psych = parse_labeled(run_text(provider, _clin(_PSYCH_SYS), pctx), ["LIE", "NEED", "GOAL", "ENGINE"])

    # 5. an opening scene to roleplay from
    octx = f"WORLD: {world}\n{cctx}BACKSTORY: {ident['BACKSTORY']}\n"
    opening = parse_labeled(run_text(provider, _clin(_OPENING_SYS), octx), ["OPENING"]).get("OPENING", "")

    return {"name": ident["NAME"], "archetype": name, "world": world, "question": question,
            "backstory": ident["BACKSTORY"], "trauma": ident["TRAUMA"], "bond": ident["BOND"],
            "secret": ident["SECRET"], "lie": psych["LIE"], "need": psych["NEED"], "goal": psych["GOAL"],
            "engine": psych["ENGINE"], "opening": opening}


def demo() -> None:
    class _Stub:
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            assert "DOSSIER" in system
            if "canonical archetype" in system:
                r.text = "ARCHETYPE: kuudere"
            elif "STORY FOUNDATION" in system:
                r.text = "WORLD: a winding-down hospital in a shrinking town\nQUESTION: is it worth caring for a place that's leaving anyway"
            elif "emotional formation that made them" in system:
                r.text = ("NAME: Rin\nBACKSTORY: she trained here when the ward was full and warm\n"
                          "TRAUMA: the night the last of her mentors was transferred out without a goodbye\n"
                          "BOND: the empty night ward she keeps immaculate\nSECRET: she rehearses handovers to no one")
            elif "maladaptive core belief" in system:
                r.text = "LIE: caring only makes the leaving worse\nNEED: to be stayed with\nGOAL: keep the ward perfect\nENGINE: withdrawal"
            else:
                r.text = "OPENING: The ward is dark except for the nurses' station. She looks up as you come in, already knowing your name."
            return r

    seed = build_seed(_Stub(), card="Name: Rin\nDescription: a cold night-shift nurse")
    assert seed["name"] == "Rin" and seed["archetype"] == "kuudere"
    assert seed["question"].startswith("is it worth") and seed["engine"] == "withdrawal"
    assert seed["backstory"].startswith("she trained") and seed["opening"].startswith("The ward is dark")
    assert build_seed(object(), card="x") == {}                     # no provider -> {}, no crash
    print("ok — story_seed: card -> archetype + world + question + backstory + psych + opening")


if __name__ == "__main__":
    demo()
