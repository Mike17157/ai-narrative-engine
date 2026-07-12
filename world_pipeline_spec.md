# World-generation pipeline — spec (for formalizing into the real system later)

All stages run on **`deepseek/deepseek-v4-pro`, `reasoning_effort: none`**, via
`ctx.text_provider_for(...).generate_text(system=SYS, prompt=BODY, emits=SCHEMA)`.
Each stage = a **system prompt** + the **previous stage's full output as context** + a **JSON schema**
that forces structured output (naming the fields is what makes the model actually assign them).

**Order (cause-first — each stage grows from the one before):**
`existence → factions (+ history & a local-traditions/folk-lineage faction) → locations → characters → arcs`
- **History + traditions:** the town's serious multi-century history WITH the existence (each faction an
  *inheritor* of it) + a LOCAL-TRADITIONS faction — an old folk lineage / boundary-keeper who continued a
  long line (grounded real folk practice, a normal-named retired townsperson, NOT a fantasy shaman). Adds
  the old-vs-new / town-vs-gown axis. Validated: world_arcs_sim.md ("The Hindley Line", Mary Hindley).
- **Arcs (Key/Rewrite tonal descent):** Arc 1 = the **comedy/school arc** (warm, funny; factions hidden
  IN the comedy — build attachment) → Arc 2 = **where it gets real** (the shadow factions come to DIRECT
  hostilities; secret allegiances collide & are exposed). The `turn`/seam = the comedy's warmth is what
  makes the escalation devastating (daylight-over-depth at ARC scale). Validated & excellent.

**Key unification to apply when formalizing (idea #1):** this pipeline IS the premise/theme engine at a
larger scale — `existence → Story.world.pressure (root)`, `conflict.question → premise question`,
`factions → premise creeds`, `every faction's folly → the tragedy`. Populate the premise engine from the
pipeline instead of maintaining two systems.

**Open constraints to add before formalizing:**
- **Location names must be universally legible** — plain, self-explanatory types (the church, the
  community garden, the town square, the main street). NO regional/obscure jargon (no "vestry",
  "allotments", "high street"). If a general reader wouldn't instantly picture it, don't use it.
- **Tonal-range guard** (validated): dread is a *gradient*, concentrated at the front line; interior/
  neutral zones stay ordinary, warm, even comedic. A cosmic center must not grey everything out.
- **Protagonist anchor** (open): one character needs a special relationship to the existence, or the
  cast is equally-small bystanders with no lens.
- **Character faction-stance must fall out of the wound** (cause-first), never be assigned.
- **Character-homes** are placed in the *character* stage, into the residential zones — not before.

---

## Stage 1 — EXISTENCE (the neutral, terrible, near-omnipotent center)

**Input:** a seed/theme (as-run: it re-scaled an existing small phenomenon; formalized it takes a
seed + a resonant theme, e.g. "global warming / an indifferent process remaking the world").

**System prompt (verbatim):**
```
You are given a WORLD. Its central phenomenon is currently too SMALL — a resource two factions
squabble over. RE-GENERATE THE CENTER at the scale of Key's Rewrite, where the driver (the Key) was a
NEUTRAL, TERRIBLE, ALMOST ALL-POWERFUL existence that would DECIDE the fate of humanity regardless of
anyone's wishes. Elevate this world's forest / Green-Wood into an existence of that kind.
- existence: what it truly is (the forest is only its visible EDGE) — a vast, ancient, patient thing/
  process/will; its `scale` (far beyond the town — regional, civilisational, geological); its
  `neutrality` (it is INDIFFERENT — not cruel, not kind; it does not hate humanity, it simply does not
  care — and THAT is the terror); its `power` (near-total: what it can and will do); its `process`
  (the judgment / reclamation / transformation it is unstoppably enacting).
- conflict: the central existential `question` about it (genuinely open, at the scale of 'does humanity
  have any place in what comes?'); the `stakes` (region / world / the continuance of human life, not
  town memory); `why_town` (why this ordinary town is merely where it surfaces first).
- org_stances: for each existing organization, its `stance` toward the existence (exploit / appease /
  endure) and its `folly` — why that stance is small and doomed against a thing this vast and neutral.
CRITICAL BALANCE: the EXISTENCE is cosmic and terrible, but the humans stay SMALL and grounded before
it — this is cosmic dread / the sublime, the OPPOSITE of power fantasy (no one gets powers; they are
ants before an indifferent immensity). Daily life stays ordinary; the terror is its SHADOW. JSON only.
```

**Output schema:**
```
existence: { what, scale, neutrality, power, process }
conflict:  { question, stakes, why_town }
org_stances: [{ org, stance, folly }]
```

---

> **REVISED (world_factions_ideological_sim.md):** factions are a FEW (3-4) DISTINCT IDEOLOGICAL LEANINGS,
> not organizations — non-overlapping poles on the one question (pro-humanity / pro-nature / pro-tradition /
> pro-transcendence). Method variety lives in an `internal_range` (moderate→radical) WITHIN each pole, so a
> small set still yields many kinds of people — and consolidation reveals cleaner structure (e.g. a
> character's public-vs-secret split becomes an *intra-faction* moderate/radical schism). This IS the
> premise engine's `creeds`. The prompt below (doomed-responses framing) is superseded by this.

## Stage 2 — FACTIONS (distinct ideological leanings / creeds — see revision above)

**Input:** the existence (Stage 1 output).

**System prompt (verbatim):**
```
You are given THE EXISTENCE — a neutral, terrible, near-all-powerful thing at the centre of a world: a
vast, indifferent process remaking the world regardless of humanity, which cannot be fought or
bargained with. Author the FACTIONS — the human organisations that form as RESPONSES to it.
Humanity cannot control the existence, so each faction is a DOOMED STANCE toward it — the way a society
fractures over any vast indifferent process it can't stop (this deliberately resonates with real dread:
climate collapse, extinction). Cover a spread of stances: EXPLOIT (harness it — greed, immortality),
APPEASE / WORSHIP (make it sacred, submit), DENY / ENDURE (normalcy, business as usual), RESIST / FIGHT
(futile war on it), and optionally SURRENDER / SYMBIOSIS (give in, merge, transform).
Give 4-5 factions. Each: `name` (a grounded, plausible institution — never a coined gimmick); `kind`;
`response` (their stance); `belief` (how they READ the existence — a cure? a god? a hoax? the
inevitable?); `goal`; `method`; `politics` (their power in the town, alliances, rivalries, how they
maneuver); `who_joins` (the kind of frightened, ordinary person drawn to them, and the PERSONAL /
emotional pull — what the faction gives them to feel).
Make them genuinely COLLIDE, and make every stance humanly understandable — nobody is a cartoon; these
are how frightened people respond to something too big. RESTRAINT: grounded institutions, human-scale,
ordinary motives amplified by dread. JSON only.
```

**Output schema:**
```
factions: [{ name, kind, response, belief, goal, method, politics, who_joins }]
```

---

## Stage 3 — LOCATIONS (zone map with a front-line clock)

**Input:** the existence (Stage 1) + the factions (Stage 2).

**System prompt (verbatim):**
```
You are given THE EXISTENCE (a neutral, terrible, ADVANCING process) and its FACTIONS.
Author the LOCATIONS — the town's map of zones — as pipeline stage 3. Use BASIC location names only.
Each zone has a KIND:
- neutral: ordinary, unowned daily-life/connective space (the high street, the allotments).
- faction: controlled by ONE faction (their base or domain) — name it in `control`.
- contested: claimed or infiltrated by TWO factions — the DRAMATIC zones. Put 'contested: A vs B' in
  `control` and say what they fight over in `here`. Include SEVERAL.
- residential: where people live; specific character-homes get placed here in a LATER stage, so leave
  them generic now.
Each zone also carries: `register` (the genre it is ripe for — spread them); its place on the FRONT
LINE (`front`: front-line = being lost now / advancing = pressure rising / interior = still ordinary /
held = fortified against it) — because the existence is ADVANCING, front-line zones sit near the forest
edge, interior zones near the town centre; and `stage` (how far the existence's process has reached
there: untouched / absorption / translation / closure). This gives the map a gradient of dread and a
clock. `here`: how the existence AND the controlling faction show in this zone, briefly.
Then `crossings`: 2-3 basic thresholds to the parallel dimension.
TONAL GUARD: concentrate the dread at the FRONT; keep the INTERIOR and NEUTRAL zones genuinely
ordinary — even comedic and warm. Do NOT smear grey doom over everything; the terror is a gradient,
and daily life is livable in the town centre. RESTRAINT: basic names, grounded. JSON only.
```
*(FIX TO APPLY: replace the parenthetical examples with universally-legible ones and add the
"no regional jargon / self-explanatory names only" rule from the constraints section above.)*

**Output schema:**
```
zones: [{ name, kind, register, control, front, stage, here }]
crossings: [{ where, what }]
```

---

## Stage 4 — CHARACTERS (RUN — validated in world_populated_sim.md)

Small cast (5). Each **secretly** tied to a *different* faction; the personality **echoes** the secret
life + trauma (daylight over depth); cause-first (trauma → secret allegiance → personality); homes
placed into zones here; one flagged `protagonist` with a special connection to the existence.

**Input:** existence + factions + locations, generated ONE character per call (each sees the cast so far).

**System prompt (verbatim):**
```
You are given THE EXISTENCE, its FACTIONS, and the LOCATIONS. Write ONE member of a SMALL cast. Build
CAUSE-FIRST: a concrete TRAUMA (the cause) drove them to SECRETLY belong to / serve a faction, and
their unique PERSONALITY ECHOES that secret life, its responsibilities, and the trauma. Daylight over
depth: on the surface an ordinary, recognizable person; underneath, a hidden allegiance no one in town
knows about.
- role: their ordinary public role. home_zone: a zone from the map they live in / are tied to.
- personality: their unique day-to-day disposition — trait-first, plain, no gimmicks — which quietly
  ECHOES what they secretly carry (a person's manner reflects their hidden weight; silly is never stupid).
- secret_faction: the faction they SECRETLY serve (hidden from the town). secret_life: their hidden
  responsibility — what they actually do for it, unseen.
- trauma: the CONCRETE past wound that drove them to the faction — ordinary human material, a real scene.
- echo: HOW their surface personality and daily behaviour quietly echo the secret life / responsibility
  / trauma — the seam a reader would only notice in hindsight.
- want / need / lie: the motivation spine, each traced to the trauma.
- protagonist: true for AT MOST ONE (told below). The protagonist is ALSO secretly faction-tied, but
  additionally has a special, personal, costly connection to the EXISTENCE itself — they can perceive its
  advance; they are the lens.
RESTRAINT: ordinary grounded people; the secret is real and human; personality echoes depth WITHOUT
performing it; no whimsy. Make them DISTINCT from the cast already made, and secretly tied to a DIFFERENT
faction than the others. JSON only.
```
Per-character HINT designates the protagonist (char 0) and pushes each to a different faction (+ char 4
gets a dry comedic surface, justified by trauma).

**Output schema (per character):**
```
{ name, role, home_zone, personality, secret_faction, secret_life, trauma, echo, want, need, lie, protagonist }
```

**ENHANCEMENTS (validated, world_university_sim.md):**
- **`stubborn_core`** is now a first-class character field — the TWI-style willful spine (Erin reaches for
  a kinder world / a home; Ryoka rejects the system on cynical wit). It makes characters *protagonists of
  their own lives*, not reactive victims, and it should *cause* the faction choice. This was the missing
  anchor; add it to the character schema permanently.
- **Frame is a parameter:** the whole pipeline re-cast cleanly onto a **university** (same-age undergrad
  cast, campus factions, campus zones) just by passing a SETTING/frame + "all same age" + a named,
  audience-relatable protagonist. So `frame` (small town / university / etc.) + `cast age band` are
  top-level inputs.
- **Archetype = an ENSEMBLE lens, NOT a character identity** (corrected — do not repeat the mistake).
  Never stamp one archetype on a person as *who they are* — that flattens them, the same reduction the
  whole pipeline fights (silly≠stupid, base-traits-not-labels). The individual stays irreducible
  (trauma→stubbornness→personality) and may lean into, play against, or move between archetypes. Archetype
  is used ONLY at the cast level, as a **coverage/contrast check**: does the ensemble span varied dramatic
  functions (the axes hope/cynicism, warmth/control, engagement/deflection), and what's MISSING (the uni
  cast lacked a Leader/Visionary)? A post-cast analysis pass, never a per-character field.
- **NAMES must be normal, common, internationally-legible** (Sam, Maya, Jason, Chloe, David, Priya, Emma)
  — NOT British-specific ("Isla") or uncommon/literary ("Hollis", "Vance", "Marsh"). The protagonist
  especially reads as an everystudent. Apply at the character stage (and to any place-derived surnames).

**FIXES surfaced by the runs:**
- The universal-name rule must apply to **every** stage (fixed for characters — held: no jargon).
- Pass **claimed proper names AND surnames** in the cast-so-far context — two dead husbands both "Tom"
  (town run); "Vance" surname reused (uni run — became a nice sister pair, but was unintended).
- Enforce **one distinct faction per character** at the loop level (uni run put two students in the same
  faction despite the instruction) — track claimed factions and forbid repeats, or assign explicitly.
  (Clean regen fixed this by *assigning* the pole+wing per character — works reliably.)
- **Explicitly BAN "vestry"** (and re-check for other stubborn British jargon) — the universal-name rule
  didn't stop it across three runs; the model reaches for that word specifically. Name the forbidden words.
- **Dedup must cover SURNAMES and MENTIONED-DEAD names**, not just living full names — the clean regen
  reused surnames (Marsh/Voss) and named a dead brother "Daniel" who collides with a living cast Daniel.
  Pass all claimed given-names + surnames + any named dead into each character call.

**Simulation artifacts (verbatim DeepSeek output) on disk:**
`world_center_sim.md` (existence) · `world_factions_sim.md` · `world_locations_sim.md` ·
`world_graph_sim.md` + `world_zones_sim.md` + `world_areas_sim.md` (earlier map explorations) ·
`world_politics_sim.md` (superseded orgs, pre-rescale) · `rewrite_sim_results.md` (cast reference) ·
`comedy_test_results.md` (comedic-register tests).
