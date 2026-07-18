"""The character engine — compile a character as a clinical DOSSIER, in STAGED passes.

The character is a real person built on one wound: a vivid PERSONA (a chuuni, a mother-hen, a hardboiled
act) that compensates for an insecurity, expressed through unique personality quirks; the reveal is the
point where one quirk is re-read as the wound it was covering. Standard nakige construction (Clannad,
Little Busters) — recorded concretely and alive, but never as purple prose.

The wound's real primitive is a FAMILY hole — an adult removed from the child's life (dead, gone,
absent). That convention does the genre's heavy lifting: it pre-installs the grief, strips the safety
net so a teenager can carry stakes, and clears space for the FOUND family (the club, the friend, the
protagonist) to become the emotional centre.

A character's CONNECTION to the war is a per-character dial (`mode`), and a cast SPANS it, as Rewrite's
does — not everyone is plot-central:
  "mundane" — a private tragedy unrelated to the war (a car accident, an illness). The war is backdrop.
  "touched" — an ordinary family caught in ONE war event; bystanders the war reached, not fighters.
  "heir"    — Chihaya: parents were combatants (a faction, a role, a secret), killed/disappeared for
              it; the child is the orphaned heir. A heir backstory, told plainly, ESTABLISHES the story.

World history is authored ONCE as a WAR of diverse events (generate_history); touched/heir characters
each instance a DISTINCT episode, so the cast's backstories reconstruct one shared past instead of
reinventing a template. Mundane characters stay off the timeline.

FUNDAMENTAL DIRECTIVE — no poetry. Every field is clinical case-file data: plain declarative facts, as
a psychologist's assessment would state them. Metaphor, imagery, symbolism, mood, scene-writing and
all 'literary' phrasing are BANNED (see `_CLINICAL`, prepended to every pass). This is deliberate:
asked for prose at high effort a reasoning model performs literature — purple, melodramatic,
contrived. Constrained to facts, it spends its reasoning on the character's actual logic instead.

Why STAGED (four focused calls): each field-group is a different analysis (the event, the psychology,
the observable behaviour, the re-read) and goes deeper alone than mixed into one blob. Each is labeled
text (no json_schema) so the reasoning model never chokes on the grammar. Passes chain so the record
stays coherent. Self-check: python -m loom.stories.characters.engine
"""
from __future__ import annotations

import concurrent.futures as cf

from .labeled import parse_labeled, parse_records, run_labeled_records, run_text

# The fundamental directive, prepended to every pass: character data is clinical fact, never prose.
_CLINICAL = (
    "You are writing a character DOSSIER: concrete, specific, plain facts. Banned: metaphor, simile, "
    "imagery-for-mood, melodrama, poetic or 'literary' phrasing, scene-writing — state things plainly. "
    "BUT this is a living person, not a police report: capture their personality, their voice, what they "
    "want, and what is vivid and particular about them. Specific and alive, never affectless. "
    "Be TERSE: one to three sentences per field, never a paragraph; a genealogy or a sentence piling "
    "clause on clause is wrong. Say the essential thing and stop."
)

# ── Four focused passes, one analysis each; each asks for labeled text, never JSON ─────────────────
# The wound grows from a FAMILY situation — an adult removed from the child's life. That hole is the
# genre's real primitive: it pre-installs the grief and clears space for a found family to heal it.
# A character's CONNECTION to the war is a per-character dial, and the cast SPANS it (like Rewrite):
#   "mundane" — an ordinary private tragedy (a car accident, an illness); the war is only backdrop.
#   "touched" — an ordinary family caught in ONE war event; bystanders the war reached, not fighters.
#   "heir"    — Chihaya: the parents were combatants; the child is the orphaned heir of the war.
_CAUSE_PLAIN = ("an ordinary source with NO tie to the war and NOT centred on parents or siblings — a "
                "friendship, a place, a first love, a teacher, a failure, a hope that broke, an ordinary "
                "loss. Family may be present, but it is NOT the cause; the hurt comes from somewhere else "
                "the world touched them")
_CAUSE_TOUCHED = ("one specific event of the war that caught an ordinary family — a raid that hit the "
                  "wrong house, a stray casualty, a displacement, a rescue that failed; they were "
                  "bystanders the war reached, never fighters")
_CAUSE_HEIR = ("the war itself — the parents were PLAYERS in it, holding a side, a role, or a dangerous "
               "secret, and their fate (killed by the opposing faction, disappeared, forced into hiding, "
               "or defected) is the direct consequence of that partisanship. The child is the orphaned "
               "HEIR of a combatant — human-scale, never the world's chosen one — who inherits the "
               "allegiance, the danger, and the knowledge, and is often taken in by an agent of a faction")

_MUNDANE_RULE = ("This character's history is UNRELATED to the war: an ordinary private story. The "
                 "conflict is only the backdrop of their town, NOT the cause of the trauma. Do NOT tie it "
                 "to any faction or war event; ignore any assigned-event machinery.\n")
_HEIR_RULE = ("The backstory must NAME which side the parents were on, the role or secret they held, the "
              "specific fate their partisanship cost them, and who took the child in (often a faction's "
              "agent — their Sakuya). A reader should learn the war's sides and mechanism from this "
              "backstory ALONE. Still human-scale: the orphaned heir of a combatant, not the chosen one.\n")
_INSTANCE_RULE = ("The backstory must be nested INSIDE the ASSIGNED EVENT given below — the intimate, "
                  "ground-level view of that historical moment as ONE family lived it, consistent with "
                  "the history (dates, factions, people). A lived scene, not a plugged-in fact.\n")

_MODES = {
    "mundane": (_CAUSE_PLAIN, _MUNDANE_RULE),
    "touched": (_CAUSE_TOUCHED, _INSTANCE_RULE),
    "heir": (_CAUSE_HEIR, _HEIR_RULE + _INSTANCE_RULE),
}


def _backstory_sys(mode: str) -> str:
    cause, extra = _MODES[mode]
    return (
        "Write the character's BACKSTORY: the emotional formation that made them this archetype. The input "
        "gives the ARCHETYPE and its CHARACTERISTIC WOUND — the kind of hurt that turns someone INTO this "
        "type. INSTANTIATE that wound in THIS world: render the archetype's wound-shape through the world's "
        "own pressure (a kuudere in a town where everyone leaves went cold because caring here is grief you "
        "start early). Do NOT invent a family melodrama — parents and siblings may exist but must NOT be "
        "the throne of the trauma; the wound is about how they became this WAY, and can come from anywhere "
        "the world hurt them (a friendship, a place, a hope, a failure). Keep it ONE quiet layer, not the "
        "whole person.\n"
        "One continuous story, Chihaya-style: the same story that is their trauma is also where their real "
        "life BEGAN — the hurt is where they met the person or found the thing that made them who they are. "
        "5-8 sentences.\n"
        f"How the world touched them ({cause}).\n"
        + extra +
        "DEPTH: the trauma implicates THEM — a guilt, a shame, a thing they did or can't admit, a truth "
        "they hide even from themselves — never a clean thing merely done to them. Quiet and true, never "
        "dramatic.\n"
        "If the input lists 'ALREADY IN THIS CAST', use a DIFFERENT name and a DIFFERENT shape of story.\n"
        "Output exactly these lines, nothing else:\n"
        "NAME: <name>\n"
        "BACKSTORY: <the continuous story, 5-8 sentences>\n"
        "TRAUMA: <the exact moment it struck, inside the story — one or two sentences>\n"
        "BOND: <the founding relationship born from it — the person or place that became their family or "
        "their self in its wake>\n"
        "SECRET: <what they quietly hide about it>"
    )
_PSYCH_SYS = (
    "From the BACKSTORY and TRAUMA, record the character's psychology as clinical fields. LIE: the "
    "maladaptive core belief the trauma installed, stated first person. NEED: the corrective experience "
    "that would resolve "
    "it. GOAL: the conscious daily objective they pursue instead — a safe substitute for the need. "
    "ENGINE: their coping mechanism. Use the ASSIGNED COPING in the input and build the LIE/GOAL/ENGINE "
    "around IT — do NOT default to control or record-keeping; a cast must not all converge on the same "
    "coping.\n"
    "Output exactly these labelled lines, nothing else:\n"
    "LIE: <...>\nNEED: <...>\nGOAL: <...>\nENGINE: <...>"
)
# ARCHETYPE-FIRST. A canonical archetype (childhood friend, kuudere, chuuni…) IS a characteristic wound
# wearing a way-of-being. So each archetype carries three things: its `being` (the defining, socially-
# visible CONDITION you meet first — Shizuru's empty room, Lucia's can't-touch), its `wound` (the kind of
# hurt that turns someone INTO this type — an emotional formation, never a fixed family melodrama), and
# its `coping`. Generation: assign the archetype → present its way-of-being → reverse-derive its wound
# through THIS world. The wound explains the TYPE, not every quirk; most quirks just ARE.
_ARCHETYPES = {
    "childhood friend": {
        "being": "warm, familiar, always around — known everyone forever, treated a little like furniture",
        "wound": "the ache of being the ever-present, taken-for-granted one — always there, never chosen; closeness that reads as invisibility",
        "coping": "keeping everyone close and needed, so at least no one can leave unnoticed"},
    "kuudere": {
        "being": "quiet, spare, walled-off — few words, an almost-empty room, gives nothing away",
        "wound": "decided not to want, because wanting cost too much — went cold to stop being hurt",
        "coping": "withdrawal, keeping everyone at arm's length"},
    "chuuni": {
        "being": "narrates their ordinary life as a grand secret destiny, with total conviction",
        "wound": "couldn't bear being small and unseen, so built a larger self to live inside",
        "coping": "escape into a grandiose invented role"},
    "tsundere": {
        "being": "prickly and sharp on the surface, helplessly soft underneath",
        "wound": "terrified the soft part will be used or laughed at, so pushes first",
        "coping": "push people away to test whether they stay"},
    "dandere": {
        "being": "silent and shrinking, opens up only in tiny increments to a trusted few",
        "wound": "being seen once went badly, so silence became the safer country",
        "coping": "going unnoticed, hiding"},
    "genki": {
        "being": "loud, relentlessly bright, befriends everyone at full volume",
        "wound": "brightness is a job — stop performing cheer and the sadness they hold off arrives",
        "coping": "relentless cheer and busyness"},
    "mother-hen": {
        "being": "fusses, feeds, and organizes everyone whether they want it or not",
        "wound": "a loss they couldn't stop, so now they hold people by taking care of them",
        "coping": "control through relentless caretaking"},
    "delinquent with a soft heart": {
        "being": "tough, rule-breaking, scowling — secretly the gentlest one in the room",
        "wound": "a tenderness that got punished, so they armored it in toughness",
        "coping": "toughness and rule-breaking to hide the softness"},
    "deadpan loner": {
        "being": "flat-voiced, dry, half-out-the-door, treats everything as beneath comment",
        "wound": "grief they must hide, so they numbed the voice that would show it",
        "coping": "withdrawal and dry humour to deflect"},
}

# Ensembles are archetype NAMES chosen for KNOWN chemistry (cast like a band).
_ENSEMBLE_TEMPLATES = [
    ["chuuni", "mother-hen", "deadpan loner"],
    ["childhood friend", "kuudere", "tsundere"],
    ["genki", "delinquent with a soft heart", "dandere"],
]

_QUIRK_CAUSES = ("a secret role, a secret skill or training, a shame or hidden past, a vow or debt, or a "
                 "hidden tie to someone")


def _persona_sys(fantastical: bool) -> str:
    hint = _QUIRK_CAUSES + (", or (this world allows it) a curse or condition" if fantastical else "")
    return (
        "Give the character their defining WAY OF BEING, grown from the ASSIGNED ARCHETYPE in the input "
        "(its name and its way-of-being). Make it a SINGULAR, SOCIALLY-VISIBLE condition — the way "
        "Rewrite's Shizuru is quiet and her room is empty, or Lucia can't touch anything and everyone "
        "thinks she's weird for it. A specific way of being that sets them apart, that other people NOTICE "
        "and read as odd, and that is relatable not because we share it but because everyone knows what it "
        "is to be set apart by their own particular thing. Not a bland type; a person you'd recognise.\n"
        "PERSONA: the specific instance of the archetype's way-of-being, in one vivid sentence.\n"
        "READS_AS: how others see it — what they assume, why they find them a little weird or hard to "
        "read.\n"
        "QUIRKS: 2-3 of these singular conditions / ways of being — concrete, relatable, a touch odd. Most "
        "just ARE who this person is; do NOT decode every one into a wound. AT MOST ONE may quietly hide "
        f"something ({hint}); leave the rest unexplained. Format each as '<the condition / way of being> // "
        "<blank, OR one quiet note only if it genuinely hides something>'.\n"
        "PLAYS_OFF: whom this person clicks or clashes with and the running dynamic it makes.\n"
        "If the input lists 'ALREADY IN THIS CAST', keep to your own assigned archetype.\n"
        "Output exactly these labelled lines, nothing else:\n"
        "PERSONA: <...>\nREADS_AS: <...>\nPLAYS_OFF: <...>\nQUIRKS:\n<condition> // <blank or one note>\n"
        "<condition> // <blank or one note>"
    )
_REVEAL_SYS = (
    "Identify the REVEAL: the single point at which one previously-comic quirk is re-read as an "
    "expression of the trauma. State plainly which quirk and what it actually indicates, in one or two "
    "factual sentences. No dramatisation.\n"
    "Output one labelled line:\nREVEAL: <...>"
)


# ── World history: authored ONCE, then each character's backstory is a distinct instance of it ────────
_HISTORY_SYS = (
    "Compile the WORLD HISTORY of this setting's central conflict as the timeline of a WAR — a series of "
    "DIVERSE, dated events of DIFFERENT KINDS, NOT one mechanism repeated. Across the timeline draw from a "
    "RANGE such as: how the war began, a decisive battle or operation, a betrayal or defection, the death "
    "or assassination of a key figure, a secret weapon or experiment, a massacre or raid, a treaty or "
    "ceasefire, a failed gambit, a turning point that shifted the balance. Use the world's own factions "
    "and powers (its agents, its abilities). Name people and places. Every event must be DISTINCT IN KIND "
    "and year — no two the same type of occurrence — so together they read like the real history of a war "
    "a novel could be set in, and each can root a different character's backstory."
)
_HISTORY_FIELDS = [("WHEN", "how many years ago, e.g. '9 years ago'"),
                   ("EVENT", "a short name for the episode"),
                   ("WHAT", "what concretely happened, factual, 1-2 sentences")]


_SETTING_SYS = (
    "Find the CENTRAL QUESTION this world exists to ask — and it must be a QUIET, LIVED human feeling, the "
    "kind a reader recognises because they have felt it themselves: being alone and afraid to need anyone; "
    "wanting to belong somewhere; watching other people have the ordinary happy life and quietly doubting "
    "it will ever be yours; loving people you know you will lose. Simple. True. The kind of thing a story "
    "ANSWERS with people, not with an argument.\n"
    "BANNED, absolutely: the dramatic or philosophical register — no 'betrayal,' 'deserve,' 'worth "
    "existing,' 'defiance,' 'the dark,' no trade or cost-benefit, no grand moral verdict. If it sounds "
    "like a debate prompt or a thesis, it is WRONG. It is a plain feeling stated plainly; the weight is in "
    "how TRUE and universal it is, not in the words. Understate it.\n"
    "Then name the TWO quiet stances people here take toward that feeling — each a SINGLE plain word (not "
    "a warring order — a way of living: reach vs guard, stay vs go, hope vs settle). Two named stances "
    "plus at most ONE named thing are the ONLY proper nouns; everything else stays a role.\n"
    "Output exactly, in this order:\n"
    "QUESTION: <the quiet lived feeling, one or two plain sentences>\n"
    "---\nNAME: <one plain word>\nSTANCE: <how they live it, one line>\n"
    "---\nNAME: <one plain word>\nSTANCE: <how they live it, one line>"
)


def generate_setting(provider, *, world: str) -> dict:
    """Extract the world's spine: the weighty CENTRAL QUESTION it argues, and the two factions as its two
    one-word-stance ANSWERS (Gaia/Guardian-legible). Returns {"question": str, "factions": [{NAME,STANCE}]}
    — feed into generate_history + generate_cast so the whole cast argues one question."""
    txt = run_text(provider, _CLINICAL + "\n\n" + _SETTING_SYS, f"WORLD: {world}\n")
    head, _, rest = txt.partition("---")                 # QUESTION lives before the first record separator
    question = parse_labeled(head, ["QUESTION"]).get("QUESTION", "").strip()
    facs = [f for f in parse_records(rest or txt, ["NAME", "STANCE"]) if f.get("NAME")][:2]
    return {"question": question, "factions": facs}


def _factions_note(factions: list[dict] | None) -> str:
    if not factions:
        return ""
    names = " vs ".join(f.get("NAME", "") for f in factions if f.get("NAME"))
    return (f"\nThe two factions are {names}. Use ONLY these faction names. Name at most TWO individual "
            "people total; everyone else is a role (the Keeper, a gardener) — no other proper nouns.")


def generate_history(provider, *, world: str, factions: list[dict] | None = None, question: str = "",
                     n: int = 6) -> list[dict]:
    """Author the world's conflict as a timeline of n concrete, dated episodes — the shared pool every
    character's backstory instantiates. `factions` (from generate_setting) pins the names + a hard name
    budget so it stays legible; `question` keeps every episode a move in the one argument. Clinical facts,
    no poetry. Returns [{WHEN, EVENT, WHAT}, ...]."""
    q = f"\nThe world's central question: {question}. Every episode is a move in THAT argument." if question else ""
    sysp = _CLINICAL + "\n\n" + _HISTORY_SYS + f"\nProduce {n} distinct episodes." + _factions_note(factions) + q
    return run_labeled_records(provider, sysp, f"WORLD: {world}\n", _HISTORY_FIELDS)


def _fmt_history(events: list[dict]) -> str:
    return "\n".join(f"- {e.get('WHEN','')}: {e.get('EVENT','')} — {e.get('WHAT','')}" for e in events)


def _avoid_block(label: str, items: list[str]) -> str:
    """A compact 'ALREADY IN THIS CAST' block the passes read to steer away from siblings' choices."""
    return f"ALREADY IN THIS CAST ({label}):\n" + "\n".join(f"- {x}" for x in items) + "\n" if items else ""


def _pass(provider, system: str, prompt: str, keys: list[str], required: list[str]) -> dict:
    """Run one labeled pass, retrying ONCE if a required field comes back empty — reasoning models
    occasionally return a single empty/misformatted turn, and a silent drop loses a whole character."""
    d = parse_labeled(run_text(provider, system, prompt), keys)
    if all(d.get(k) for k in required):
        return d
    return parse_labeled(run_text(provider, system, prompt), keys)


def generate_character(provider, *, world: str, role: str, archetype: dict, mode: str = "mundane",
                       avoid: dict | None = None, event: dict | None = None, history: str = "",
                       factions: list[dict] | None = None, fantastical: bool = False, question: str = "",
                       backstory_provider=None) -> dict:
    """Build one nakige character, PERSONA-FIRST: the vivid costume is generated up front (from an
    assigned `persona_type`), then the wound is derived to root the insecurity it compensates for, then
    psych, then the reveal. `mode` sets war-connection: "mundane" (private tragedy — Kotone), "touched"
    (family caught in a war event) or "heir" (a combatant's orphan — Chihaya). `avoid` =
    {"personas":[], "ident":[], "quirks":[]} steers off siblings' choices. `event` (from generate_history)
    is the war episode a touched/heir backstory instances. Returns the full dict, or {} on empty wound."""
    avoid = avoid or {}
    facnote = ""
    if factions:
        facnote = "FACTIONS: " + "; ".join(f"{f.get('NAME','')} ({f.get('STANCE','')})" for f in factions) + "\n"
    qnote = f"CENTRAL QUESTION (the whole cast argues this): {question}\n" if question else ""
    ctx = f"WORLD: {world}\n{facnote}{qnote}ROLE IN THE STORY: {role}\n"

    def clin(body: str) -> str:
        return _CLINICAL + "\n\n" + body

    # 1. PERSONA FIRST — the defining WAY OF BEING (a Shizuru/Lucia condition), from the assigned archetype
    pctx = ctx + f"ASSIGNED ARCHETYPE: {archetype['name']} — {archetype['being']}\n"
    pctx += _avoid_block("archetypes", avoid.get("personas") or [])
    surf = _pass(provider, clin(_persona_sys(fantastical)), pctx,
                 ["PERSONA", "READS_AS", "PLAYS_OFF", "QUIRKS"], ["PERSONA", "QUIRKS"])
    idios = []
    for line in (surf.get("QUIRKS") or "").splitlines():
        if "//" in line:
            quirk, _, hidden = line.partition("//")
            quirk = quirk.strip(" -*•\t")
            if quirk:
                idios.append({"quirk": quirk, "hidden_source": hidden.strip()})

    # 2. BACKSTORY — reverse-derive the ARCHETYPE's characteristic wound through THIS world, as one
    #    continuous trauma-is-origin story (Chihaya + Sakuya). Via `backstory_provider` (a stronger model).
    bctx = (ctx + f"ARCHETYPE: {archetype['name']}\nCHARACTERISTIC WOUND (instantiate THIS in the world): "
            f"{archetype['wound']}\nHOW THEY PRESENT: {surf['PERSONA']}\n")
    bctx += _avoid_block("name + shape of story", avoid.get("ident") or [])
    if event and mode != "mundane":
        bctx += (f"\nWORLD HISTORY (stay consistent with this):\n{history}\n\n"
                 f"ASSIGNED EVENT — this backstory is nested inside THIS episode:\n"
                 f"- {event.get('WHEN','')}: {event.get('EVENT','')} — {event.get('WHAT','')}\n")
    ident = _pass(backstory_provider or provider, clin(_backstory_sys(mode)), bctx,
                  ["NAME", "BACKSTORY", "TRAUMA", "BOND", "SECRET"], ["NAME", "BACKSTORY", "TRAUMA"])
    if not ident.get("TRAUMA"):
        return {}

    # 3. PSYCH from the backstory/trauma (persona in view for consistency)
    cctx = (ctx + f"PERSONA: {surf['PERSONA']}\nNAME: {ident['NAME']}\nBACKSTORY: {ident['BACKSTORY']}\n"
            f"TRAUMA: {ident['TRAUMA']}\nSECRET: {ident['SECRET']}\n")
    pcctx = cctx + f"ASSIGNED COPING (build ENGINE around this, do NOT default to control): {archetype['coping']}\n"
    psych = parse_labeled(run_text(provider, clin(_PSYCH_SYS), pcctx), ["LIE", "NEED", "GOAL", "ENGINE"])

    # 4. REVEAL — one persona-quirk re-reads as the trauma
    reveal = parse_labeled(
        run_text(provider, clin(_REVEAL_SYS), cctx + "SURFACE BITS:\n" + "\n".join(i["quirk"] for i in idios)),
        ["REVEAL"])

    return {"name": ident["NAME"], "archetype": archetype["name"], "persona": surf["PERSONA"],
            "reads_as": surf["READS_AS"],
            "event": (event or {}).get("EVENT", "") if mode != "mundane" else "", "mode": mode,
            "backstory": ident["BACKSTORY"], "trauma": ident["TRAUMA"],
            "bond": ident["BOND"], "secret": ident["SECRET"], "lie": psych["LIE"], "need": psych["NEED"],
            "goal": psych["GOAL"], "engine": psych["ENGINE"], "idiosyncrasies": idios,
            "plays_off": surf["PLAYS_OFF"], "reveal": reveal["REVEAL"]}


def generate_cast(provider, *, world: str, roles: list[tuple[str, str]],
                  history: list[dict] | None = None, ensemble: list[str] | None = None,
                  factions: list[dict] | None = None, fantastical: bool = False, question: str = "",
                  backstory_provider=None, max_workers: int = 8) -> list[dict]:
    """Build a CAST that SPANS two axes, IN PARALLEL. `roles` = [(role, mode), ...] with mode in
    mundane/touched/heir. Archetypes come from one `ensemble` template (default `_ENSEMBLE_TEMPLATES[0]`)
    whose members have KNOWN chemistry — cast like a band, not a bag of distinct types. Each character is
    pre-assigned an archetype + a distinct `history` episode UP FRONT, so they run concurrently (internal
    persona→wound→psych→reveal chain stays sequential). `factions` (from generate_setting) + `fantastical`
    thread through. Order of the returned cast matches `roles`."""
    htext = _fmt_history(history) if history else ""
    ens = ensemble or _ENSEMBLE_TEMPLATES[0]
    plan, ev = [], 0                                    # pre-assign archetype + event (no cross-dependency)
    for i, (role, mode) in enumerate(roles):
        event = history[ev % len(history)] if (history and mode != "mundane") else None
        if event:
            ev += 1
        name = ens[i % len(ens)]
        plan.append((role, mode, {"name": name, **_ARCHETYPES[name]}, event))
    assigned = [arche["name"] for _, _, arche, _ in plan]

    def build(idx: int) -> dict:
        role, mode, arche, event = plan[idx]
        others = [a for j, a in enumerate(assigned) if j != idx]   # keep archetypes distinct without sequencing
        return generate_character(provider, world=world, role=role, archetype=arche, mode=mode,
                                  avoid={"personas": others}, event=event, history=htext,
                                  factions=factions, fantastical=fantastical, question=question,
                                  backstory_provider=backstory_provider)

    with cf.ThreadPoolExecutor(max_workers=min(max_workers, len(plan) or 1)) as ex:
        cast = list(ex.map(build, range(len(plan))))    # map preserves input order; provider I/O runs concurrently
    return [c for c in cast if c]


def demo() -> None:
    # Stub returns the right labeled block per pass (dispatched on a distinctive phrase in the system).
    class _Stub:
        def __init__(self): self.wound_prompts = []; self.persona_prompts = []
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            assert "DOSSIER" in system                               # clinical directive on every pass
            if "CENTRAL QUESTION" in system:                         # the setting pass (question + stances)
                r.text = ("QUESTION: is a paradise worth the lives it eats?\n---\n"
                          "NAME: Gaia\nSTANCE: the world over us\n---\nNAME: Guardian\nSTANCE: us over the world")
                return r
            if "timeline of a WAR" in system:                        # the world-history pass
                r.text = ("WHEN: 12 years ago\nEVENT: The Ledger Purge\nWHAT: the order took nine "
                          "defectors\n---\nWHEN: 6 years ago\nEVENT: The Long Green Year\nWHAT: a surge "
                          "tripled the takings")
                return r
            if "emotional formation that made them" in system:
                self.wound_prompts.append(prompt)
                r.text = ("NAME: Mira\nBACKSTORY: her mother crewed the last boat; the winter it didn't "
                          "come back, eleven-year-old Mira had been the one who untied the last line, "
                          "waving them off; she was taken in by the old lighthouse keeper, who taught her "
                          "the lamp, and she has kept it since.\n"
                          "TRAUMA: age 11, she cast off the line that sent the boat out, and it never came back\n"
                          "BOND: the lighthouse keeper who took her in and taught her the lamp\n"
                          "SECRET: she keeps the harbour light lit a little longer than the rota requires")
            elif "maladaptive core belief" in system:
                r.text = "LIE: nobody stays\nNEED: to be kept without earning it\nGOAL: be indispensable\nENGINE: over-helping"
            elif "defining WAY OF BEING" in system:
                self.persona_prompts.append(prompt)
                r.text = ("PERSONA: a chuuni who claims a sealed demon sleeps in his arm\n"
                          "READS_AS: the class weirdo who won't drop the act\nPLAYS_OFF: the deadpan\nQUIRKS:\n"
                          "wears a single black glove to 'contain' the demon // to feel powerful\n"
                          "gives grand names to ordinary chores //")
            else:
                r.text = "REVEAL: the black glove is the power he never had at home"
            return r

    arche = {"name": "chuuni", **_ARCHETYPES["chuuni"]}
    c = generate_character(_Stub(), world="a coast", role="a girl at the harbour", archetype=arche)
    assert c["name"] == "Mira" and c["archetype"] == "chuuni" and c["persona"].startswith("a chuuni")
    assert c["reads_as"].startswith("the class weirdo")
    assert c["backstory"].startswith("her mother crewed") and c["bond"].startswith("the lighthouse keeper")
    assert c["trauma"].startswith("age 11") and c["secret"].startswith("she keeps the harbour light")
    assert len(c["idiosyncrasies"]) == 2                     # a blank // note is fine; the condition still counts
    assert c["idiosyncrasies"][0]["hidden_source"] == "to feel powerful"
    assert c["goal"] == "be indispensable" and c["reveal"].startswith("the black glove")
    assert generate_character(object(), world="w", role="r", archetype=arche) == {}   # no provider → {}, no crash
    # ARCHETYPE-FIRST: each canonical archetype carries a way-of-being + its characteristic wound + coping
    assert "Banned:" in _CLINICAL and "living person" in _CLINICAL
    assert _ENSEMBLE_TEMPLATES[0][0] == "chuuni" and "empty room" in _ARCHETYPES["kuudere"]["being"]
    assert "Chihaya" in _backstory_sys("mundane") and "Sakuya" in _backstory_sys("heir")
    assert "UNRELATED to the war" in _backstory_sys("mundane") and "bystanders the war reached" in _backstory_sys("touched")
    # cast built in PARALLEL: archetypes pre-assigned distinct (no live cross-char dedup) so order is safe
    stub = _Stub()
    cast = generate_cast(stub, world="a coast", roles=[("a first-year", "mundane"), ("a captain", "heir")])
    assert len(cast) == 2
    assert any("ASSIGNED ARCHETYPE" in p for p in stub.persona_prompts)                    # each got an archetype
    assert any("ALREADY IN THIS CAST (archetypes" in p for p in stub.persona_prompts)      # told the others'
    # world history authored once; touched/heir instance distinct episodes, mundane stays off the timeline
    hist = generate_history(_Stub(), world="a coast", n=2)
    assert len(hist) == 2 and hist[0]["EVENT"] == "The Ledger Purge"
    stub2 = _Stub()
    hcast = generate_cast(stub2, world="a coast",
                          roles=[("m", "mundane"), ("t", "touched"), ("h", "heir")], history=hist)
    assert [c["event"] for c in hcast] == ["", "The Ledger Purge", "The Long Green Year"]  # map preserves order
    assert any("ASSIGNED EVENT" in p and "Ledger Purge" in p for p in stub2.wound_prompts)  # touched instanced it
    # setting = a weighty CENTRAL QUESTION + two one-word STANCE factions (its two answers)
    setting = generate_setting(_Stub(), world="a coast")
    assert setting["question"].startswith("is a paradise")
    assert [f["NAME"] for f in setting["factions"]] == ["Gaia", "Guardian"]
    stub3 = _Stub()
    generate_cast(stub3, world="a coast", roles=[("a first-year", "heir")],
                  factions=setting["factions"], question=setting["question"])
    assert any("Gaia" in p and "Guardian" in p and "paradise" in p for p in stub3.persona_prompts)
    # persona pass = a Shizuru/Lucia defining CONDITION; curse gated by `fantastical`; quirks mostly un-decoded
    assert "Shizuru" in _persona_sys(False) and "AT MOST ONE" in _persona_sys(False)
    assert "this world allows it" in _persona_sys(True) and "this world allows it" not in _persona_sys(False)
    # trauma must have DEPTH (self-implication), and coping is ASSIGNED so the cast doesn't converge on control
    assert "implicates" in _backstory_sys("mundane") and "ASSIGNED COPING" in _PSYCH_SYS
    # _pass retries once on an empty required field, so a flaky turn doesn't silently drop a character
    class _Flaky:
        def __init__(self): self.n = 0
        def generate_text(self, *, system, prompt):
            self.n += 1
            class _R: pass
            r = _R(); r.text = "" if self.n == 1 else "NAME: X\nWOUND: y"
            return r
    assert _pass(_Flaky(), "s", "p", ["NAME", "WOUND"], ["NAME", "WOUND"])["NAME"] == "X"
    print("ok — character_engine: stance-factions + chemistry-ensemble + typed quirk-causes + parallel")


if __name__ == "__main__":
    demo()


# --- ensemble cast dynamics ---

"""CAST DYNAMICS — an ensemble as a SYSTEM OF FRICTIONS, generated in STAGED prose passes.

Entertainment is a property of the cast, not the individual: comedy comes from CONTRASTING recognisable
TYPES colliding (the optimist vs the cynic, the zealot vs the gremlin). So build from types, and build
the collisions on purpose. The anti-cliché guard is per-member: a specific WOUND (why this person is
this type) and a TWIST (where they break it). And the nakige knife: the SAME friction that entertains
early is the shape of the absence late — every dynamic carries `friction` (comedy) and `turns_to` (grief).

Why STAGED instead of one nested blob (the blob was the schema that kept emptying):
  • Pass 1 — a light SKELETON: the members as contrasting types (labeled records, no JSON).
  • Pass 2 — FAN OUT: each clashing pair gets its own tiny `friction`/`turns_to` call.
A dozen small prose calls don't choke where one heavy nested json_schema did. The per-pair calls are
independent, so a caller can run them in parallel. Self-check: python -m loom.stories.cast_dynamics
"""


_SKELETON_SYS = (
    "Design an ENSEMBLE as a SYSTEM OF FRICTIONS built from CONTRASTING recognisable personality TYPES "
    "(tsundere, genki optimist, deadpan cynic, gremlin, rich-girl, delinquent-with-a-heart, mum-friend, "
    "himbo, try-hard, chuuni, airhead, stoic). Pick types that COLLIDE. For each member give:\n"
    "NAME, ARCHETYPE (the type, one line), TWIST (one way THIS person breaks the type so they're not a "
    "cliché), COMIC_BIT (a funny bit that sparks off the others, with NO visible sadness), WOUND (brief: "
    "why they became this type — the mask over the wound).\n"
    "The type is the on-ramp; the specific wound and twist make it a person. Output ONE RECORD PER MEMBER, "
    "records separated by a line containing only '---'. Each record is labelled lines:\n"
    "NAME: ...\nARCHETYPE: ...\nTWIST: ...\nCOMIC_BIT: ...\nWOUND: ..."
)
_DYNAMIC_SYS = (
    "Two characters collide. FRICTION: the type-vs-type comic clash and the RUNNING BIT it produces "
    "(their double act, rivalry, or odd-couple routine) — the entertainment, opaque comedy. TURNS_TO: how "
    "that SAME bit becomes the shape of grief or absence when one of them is gone — the nakige knife. The "
    "funnier the friction, the harder the fall.\n"
    "Output exactly two labelled lines:\nFRICTION: <...>\nTURNS_TO: <...>"
)


def design_cast(provider, *, world: str, size: int = 4) -> dict:
    """Design an ensemble as a system of frictions: a skeleton pass (contrasting types) topped up until we
    hit `size`, then a fanned pass per clashing pair. Returns {cast:[...], dynamics:[...]} (or {} if empty)."""
    keys = ["NAME", "ARCHETYPE", "TWIST", "COMIC_BIT", "WOUND"]
    records: list[dict] = []
    seen: set[str] = set()
    for _ in range(4):                          # skeleton, then top-up rounds until we reach `size`
        if not records:
            prompt = f"WORLD: {world}\nDesign EXACTLY {size} ensemble members, no more, no fewer.\n"
        else:
            have = "; ".join(f"{m['NAME']} ({m['ARCHETYPE']})" for m in records)
            prompt = (f"WORLD: {world}\nALREADY DESIGNED (do NOT repeat these names or types): {have}\n"
                      f"Add EXACTLY {size - len(records)} MORE contrasting members that collide with them.\n")
        for r in parse_records(run_text(provider, _SKELETON_SYS, prompt), keys):
            nm = (r.get("NAME") or "").strip()
            if nm and nm.lower() not in seen:
                records.append(r)
                seen.add(nm.lower())
        if len(records) >= size:
            break
    records = records[:size]
    if not records:
        return {}
    cast = [{k.lower(): v for k, v in r.items()} for r in records]

    dynamics = []
    for i in range(len(cast)):
        for j in range(i + 1, len(cast)):
            a, b = cast[i], cast[j]
            d = parse_labeled(run_text(
                provider, _DYNAMIC_SYS,
                f"WORLD: {world}\n"
                f"A: {a['name']} — {a['archetype']} — {a['comic_bit']}\n"
                f"B: {b['name']} — {b['archetype']} — {b['comic_bit']}\n"), ["FRICTION", "TURNS_TO"])
            if d.get("FRICTION"):
                dynamics.append({"between": [a["name"], b["name"]],
                                 "friction": d["FRICTION"], "turns_to": d["TURNS_TO"]})
    return {"cast": cast, "dynamics": dynamics}


def demo() -> None:
    class _Stub:
        def __init__(self): self.n = 0
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            if "SYSTEM OF FRICTIONS" in system:
                self.n += 1                                  # skeleton returns 2; top-up fills the 3rd
                if self.n == 1:
                    r.text = ("NAME: Mari\nARCHETYPE: genki\nTWIST: t\nCOMIC_BIT: b\nWOUND: w\n---\n"
                              "NAME: Vann\nARCHETYPE: deadpan\nTWIST: t\nCOMIC_BIT: b\nWOUND: w")
                else:
                    r.text = "NAME: Grem\nARCHETYPE: gremlin\nTWIST: t\nCOMIC_BIT: b\nWOUND: w"
            else:
                r.text = "FRICTION: f\nTURNS_TO: she does the bit alone"
            return r

    stub = _Stub()
    out = design_cast(stub, world="a ship", size=3)
    assert [m["name"] for m in out["cast"]] == ["Mari", "Vann", "Grem"]   # top-up filled 2 -> 3
    assert len(out["dynamics"]) == 3                         # C(3,2) = 3 pairs, fanned
    assert out["dynamics"][-1]["turns_to"] == "she does the bit alone"
    assert design_cast(object(), world="x") == {}           # no provider → {}, no crash
    assert "CONTRASTING recognisable personality TYPES" in _SKELETON_SYS and "nakige knife" in _DYNAMIC_SYS
    print("ok — cast_dynamics: skeleton + top-up-to-size + fanned per-pair dynamics (no JSON)")


if __name__ == "__main__":
    demo()
