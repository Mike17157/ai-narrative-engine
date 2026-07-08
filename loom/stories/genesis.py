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
from __future__ import annotations

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
