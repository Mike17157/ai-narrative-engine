# Relationship-first story genesis — design note

Status: **theory, unbuilt.** This is the contract we build to. Supersedes the
six-box premise linter (`premise_coverage` + `_PREMISE_COMPONENTS` in router.py),
which it deletes.

## 1. The inversion

Today the arrow runs `premise → design_cast → relationships` (premise is an input
you type and we lint). We flip it:

```
harnesses  →  relationship web  →  derived stories
```

**Premise becomes an output, not an input.** You seed a thematic shape (or nothing),
we generate unnamed character harnesses, wire the tension web between them, and
*derive* candidate premises from the web's unstable configurations. You pick one.

## 2. The unit: a character harness, generated before naming

Unify the two half-models already in the tree — the spine (`wound/lie/truth`,
graph_pipeline.py) and the sim dossier (`goals/secret`, simulation.py) — into one:

```
harness = { id, role, want, lie, wound, secret }
```

Generated **role-only** (no name, no face): psychology precedes identity. Naming is a
cheap commit-time step. `id` is stable (`h0…hn`) so every later reference survives a
re-roll.

## 3. Two structures — one graph, one tree (don't conflate)

There are exactly two data structures, and only one is a graph:

- **The relationship web — a graph.** Nodes = characters, edges = relationships. The
  *tension lives on the edge*: the rivalry/debt/grudge is a property of the A↔B
  connection, not of A or B alone. Pairwise, non-hierarchical → genuinely a graph (the
  "character map" writers use). Built at genesis, drifts at runtime.
- **The narrative — a hierarchy (tree), NOT a graph.** story ▸ arc ▸ scene ▸ beat.
  Containment + order. An arc is a *folder of scenes*; a scene is a *tree node*. Best as
  nested JSON / folders — there are no arbitrary cross-links, so a graph is the wrong tool.

A **scene is not an edge.** A scene (tree node) *dramatizes* a charged edge — it puts two
characters in a room over the relationship that's under strain. Tension belongs to the
edge; the scene inherits drama by *selecting* a charged edge to pressure. The only links
from the tree into the graph are `scene.cast` (who's present) and `scene.pressures` (which
relationship(s) this scene strains).

An edge is story-bearing when, across it: two `want`s collide, a `secret` threatens the
other's `want`, or `nature` fights `dynamic` (lover/estranged). Triangles are richest. A
character with no charged edges = dead weight, cut or re-wire (the web flags it).

## 4. The level model — two orthogonal axes

A **scene is not a chapter.** They live on different axes:

- **Delivery axis:** beat ⊂ scene ⊂ chapter. A *scene* is the dramatic atom — one
  continuous action, one location/time, **one turn** (a value flips). A *chapter* is a
  reader-pacing container holding 1–N scenes. Never bind scene↔chapter 1:1.
- **Transformation axis:** scene advances arc(s); arc spans many scenes; story = the
  interwoven arcs reaching convergence.

A scene sits at the crossing: it occupies a chapter slot **and** turns an arc.

### lie ↔ arc duality (the hinge)

An **arc is a lie plotted over time** (`wound→lie→ tested → crisis → broken/reaffirmed`);
a relationship arc is the `dynamic` turning. Arc and harness are one substrate at two
timescales — you can't author a contentful arc without the lie it tests. So "arc-first"
and "character-first" seed the *same object*; they are co-determined, not sequenced.

## 5. Generative primitives — seed at any level, fill both ways

The harness is a schema with holes at every level. Four primitives; same query at
different zoom:

1. `arc-shape → harness` — top-down: a theme + desired ending generates the lie/wound that yields it.
2. `harness + web → arcs` — bottom-up: a charged edge *seeds* an arc (the latent transformation it implies). (= `derive_stories`, zoomed out.)
3. `arc + web → scenes` — fill the arc's scene-folder by pressuring its edges in tension-ascending order; each scene dramatizes one edge-turn. (uses `scene_cast` to pick each scene's cast.)
4. `scenes → chapters` — pack for pacing. Lowest value, do last, never 1:1.

`derive_stories` and `scene_cast` are **the same function at two `n`s and a different
start node** — that collapse is the signal the theory is right.

### Cast selection — tension-weighted distance, in code not an LLM

Dramatic distance ≠ emotional distance: enemies are dramatically *adjacent*.
`scene_cast(web, focal, n)` is a deterministic tension-weighted BFS over the edges:

- **distance-1** (direct charged edge) → core two-handers, write first.
- **distance-2** (shared neighbor, no direct edge) → *latent* scenes; a room manufactures
  new tension through the shared neighbor's secret. The web's non-edges are a feature.
- `n` = room size (2–3; max = the climax convergence).

Selection is graph code; **generation** is structured `generate_text(emits=schema)`;
**no native tool-calls** anywhere in genesis (tools are for mid-conversation doc
mutation, not a batch generative pass). Chatting for new concepts = re-running `derive`
/ `scene_cast` with the web held in the Author agent's context.

### Ordering: authored bookends, emergent spine — DECIDED

`derive` authors the two **bookends**: an *opening* edge and a *target* convergence
(`intended_ending`). The runtime director fills the middle emergently — each turn it reaches
for the highest-charge unresolved edge, steering toward the authored convergence. Authored
ends, emergent middle; the director never improvises the destination, only the path to it.
Seed level: **one notch above character** — the arc-shape or the web, never raw beats
(shallow-beats failure, see story-gen-depth) and never raw characters (meanders, no ending).

## 6. Constructing the social world — archetypes + web

The content of `design_harnesses` + `weave_relationships`. Grounded in standard craft
(Truby's *character web*), not stock types.

**Archetype = a position in a value contest, not a personality type.** Define each
character by their *stance* on the story's central value. Every character is defined in
relation to the protagonist and to each other — each a different moral/strategic answer to
the same question. That's what makes a cast cohere (one theme) AND contrast (different
answers). Two layers:

- **functional role** (load-bearing): opponent · false-ally-opponent · mirror/foil · tempter · ally · shadow.
- **surface archetype** (flavor, swappable): the brooding loner, the warm matriarch. Never let the surface be the character.

**The interior = stance × psyche scaffold.** Reuse the existing McAdams/Big-Five/CB5T
scaffold + the `contrast` tool (character-psyche-scaffold) to instantiate the role as an
idiosyncratic person. The **lie is the hinge**: each character's `lie` = their particular
*distortion of the central value*; `want` = their stance's pursuit of it. One theme shapes
every harness's lie → all about the same story, all different people. Differentiate on the
**axes that matter to the theme**, not random traits (each character a road-not-taken).

**Relationships = forces between stances; asymmetry is the fuel.** Build edges by function:
opposition (incompatible wants), false ally (shared goal, corrupt reason, will betray —
richest), mirror/foil (same wound, opposite lie), dependency (power/knowledge/debt/love
imbalance), shadow (what the protagonist could become). **Every edge carries an asymmetry
or secret** — one knows/owes/loves more. Symmetry is static; asymmetry is stored energy +
dramatic irony.

**Richness is topology, not edge count.** Aim for: every node tied to the protagonist *and*
≥1 other (no hub-and-spoke star); **triangles** (alliance shifts + irony); a **reserve of
latent non-edges** (collide later — distance-2 fuel); asymmetric/secret edges. Avoid: the
star (others are props), the complete graph (no room to escalate), disconnected components
(subplots that never touch). Informal score ≈ thematic coverage × edge asymmetry × triangles
× latent reserve (a usable `weave` scorer if it comes out bland).

**Procedure (value-first):** (1) fix the central value/question from the seed → (2) generate
the stance set covering the spectrum [`design_harnesses` roles] → (3) fill each interior via
the scaffold, `lie` = its distortion of the value → (4) weave relationships as forces with
asymmetry/secret, building triangles + latent reserve [`weave_relationships`] → (5) name last.

## 7. The living web — arcs deepen edges, consolidation rewrites them

The web is not a genesis artifact frozen at t0 — it's the living core of a playthrough.

**Two arc kinds** (extends the `lie ↔ arc` duality to the graph):
- **character arc** = one node's `lie` over time.
- **relationship arc** = one *edge's* `dynamic` over time. An arc "with another character"
  doesn't use the edge — it **is** the edge transforming.

**Authored baseline → runtime deltas.** Never mutate `Story.relationships` (the authored
t0). The evolving web lives as deltas in the thread's **State doc** (a level over baseline,
thread-`thread-<sid>` scope). genesis writes the baseline · each scene close drifts it
(built — `consolidate` / sim CLOSE apply perspectival `relationship_changes`) · consolidation
restructures it. Each playthrough evolves its own web; the authored story stays clean.

**Three ops on an edge** (the third is the new work):
1. **drift** — rewrite `dynamic`/`stance`. *Built.*
2. **deepen** — accrete history/secret into `note` as scenes reveal it. *Mostly built.*
3. **restructure** — create a *new* edge (two who just met), flip `nature` (ally→rival), or
   retire a dead one. **BUILT** — `stage_tools.apply_rel_changes` (pure, co-presence-gated) +
   the `consolidate` storymaster schema (`nature`/`retire`); test_consolidate.py.

**The invariant that keeps a living web coherent:**

> An edge can only be created or modified by a scene **both parties were present for**.

You can't bond with someone you never met. This pins every edge-mutation to a real
`scene.cast`, keeps changes evidence-based (not hallucinated), and preserves perspectival
drift. One rule, grounded web. **BUILT**: consolidation derives `present` from the events
text and drops any change whose two ends aren't both present (returned as `blocked`).

**Same loop at authoring time:** when `derive` proposes an arc needing an A↔C confrontation,
it can *request* a new/latent edge — `weave` isn't frozen after genesis. Authoring (planned)
and runtime (emergent) are the same operation: arc transforms edge.

## 8. The data contract — what lives where

**Representation = plain JSON, both halves.** Authored story is pydantic→JSON; the State
doc is leveled JSON. A cast is a handful of nodes, so the web is a flat JSON **edge-list**,
not a graph DB — a graph store would be over-engineering below ~thousands of nodes.
Structured, nested, schema-validated, human-diffable, and already the substrate.
<!-- ponytail: JSON edge-list; reach for a graph store only past ~1000s of nodes -->

**Characters are a separate reusable registry; the harness references them by key.**
Embedding character bodies into the story would give N copies across N stories + drift.
The boundary test: *if I reuse this character in another story, what comes with them?*

| Lives on the **character card** (registry, portable) | Lives in the **story harness JSON** (per-plot) |
|---|---|
| identity: persona, greeting, image | the relationship **web** — the flat graph (edges by char key) |
| the **core harness**: `want / lie / wound / secret` | the **narrative tree**: `arcs[]` (folders) ▸ `scenes[]` (nodes) |
| portable home scenes, playable flag | tree→graph links: `scene.cast` (char keys), `scene.pressures` (rel ids) |
| | arc-shape/theme, `intended_ending`, tone, themes |

The story harness = one flat graph (`relationships`) + one tree (`arcs ▸ scenes`); the
only tree→graph links are `scene.cast` / `scene.pressures`. Shape:

```jsonc
{ "relationships": [ {source: charKey, target: charKey, nature, dynamic, stance} ],   // graph
  "arcs": [ { id, owner: charKey, lie, shape,                                          // folder
              scenes: [ { id, cast: [charKey], pressures: [relId], turn } ] } ] }       // tree nodes
```

### Three levels — and what is NOT a level

Two registries + the story doc, that's all:

1. **Character** — its own yaml, one unit, **outfits/portraits nested inside it**. Reusable
   across stories (the only irreducible separation; it earns its keep). Carries the portable
   core harness `want/lie/wound/secret`. Does **NOT** carry the web.
2. **Structure** — the relationship web BETWEEN characters (`relationships`). This is where you
   reason over the WHOLE social field (triangles, A-to-C-through-B, shifting alliances).
3. **Narrative** — the arc/scene tree.

Structure and narrative are **two sections of one `story.yaml`**, NOT two files — they're
laced by id (`scene.pressures→relId`, `arc.owner→charKey`), and cross-*file* id refs are the
one integrity hazard worth avoiding (one file = one atomic save/validate). Splitting into
separate files later is a thin serialization change; don't pay for it now.

**Format:** YAML for all authored data (character / structure / narrative) — human-readable,
comment-able, git-diffable, the project convention. The runtime query "relationships for the
present + mentioned cast" is a 2-line filter over a handful of nodes, NOT a reason to adopt a
DB. Keep libSQL for the runtime State doc + lorebook vectors, where querying/scale pay off.

### Runtime projection — the web is read locally, never copied

Relationships live in **structure**, never duplicated into character files. At each turn,
play **projects** the relevant slice onto the LLM: for every character on stage *and* every
one mentioned in recent conversation, pull their own edges from the web and compose them into
that character's context — **perspectival** (each sees their own bonds), **scoped** (present ∪
mentioned), delivered through the existing lorebook **trigger-keyword** path (a name fires its
card; its edges ride along). Global reasoning lives in structure; the character card only ever
holds its **local projection**. (Surfaced in the Structure pane: selecting a node shows exactly
that character's edges — the same slice play injects.)

Their core harness travels; the arc, edges, and scenes stay behind. This is why
`Story.relationships` already sits on the story (and drifts at runtime), not on the
character — correct as-is.

### The genesis draft is the one place inlining is right

Pre-commit, harnesses are unnamed with no registry key, so they live **inline** in a
transient, client-held draft (the sim_state pattern — "client holds the state"):

```jsonc
// genesis draft (scratch, never persisted as-is)
{ "seed": "...", "harnesses": [ {id, role, want, lie, wound, secret}, ... ],
  "relationships": [ {source: hId, target: hId, nature, dynamic, stance, note}, ... ],
  "candidates": [ {title, dramatic_question, logline, premise, protagonist: hId,
                   anchors: [hId], stakes, intended_ending, tone, themes} ] }
```

`commit` promotes anchor harnesses to registry characters, rewrites every `hId →
character key`, writes the Story, and discards the draft. The persisted story holds
**character keys + harness structure**, never harness bodies.

## 9. Build map — wire levels, don't add entities

The four levels already exist in schema (`arcs[]`, `chapters[]`, `scenes[]`/`SceneHarness`,
`relationships[]`). What's missing is the **cross-references**, not new types.

**New — `loom/stories/genesis.py`** (~120 lines, mirrors simulation.py; pure functions,
provider-None guards, schema-validated, capped parsing):
- `design_harnesses(provider, seed, n)` — `design_cast` minus premise/names, plus harness fields + ids.
- `weave_relationships(provider, harnesses)` — edges by harness id, engineered for tension.
- `derive_stories(provider, harnesses, edges)` — candidates referencing harness ids.
- `scene_cast(web, focal, n)` — deterministic tension-weighted BFS (no LLM).

**New endpoints** (router.py, next to the sim endpoints, through `ctx.builder_ctx(body,
"premise")`): `POST /api/stories/genesis/{harnesses,weave,derive,commit}`.

**Integrity boundary (do NOT be lazy here):** every step references the prior by stable
id; `derive` and `commit` validate referenced ids exist and drop dangling refs — a
candidate pointing at a re-rolled-away harness is the one bug that corrupts a story
silently. `test_genesis.py` with a stub provider asserts: weave edges + candidate anchors
only reference live harness ids, and each candidate's protagonist ∈ its anchors.

**Frontend:** genesis studio = new story front-door (`/stories/new/genesis`), three steps
(seed → web → derived stories), re-openable from the overview. Web view reuses
`StoryCanvas`; candidates are a card grid; commit hands off to the existing
Outfits→Scenes wizard.

## 10. Deliberately skipped (with upgrade paths)

- numeric instability-scoring — LLM judges tension in `derive`; add a score only if candidates come out bland.
- streaming — synchronous POSTs first; wrap in GenStream if latency annoys.
- a typed `Harness` pydantic model — reuse the dict shape + `Relationship`.
- chapter packing (primitive 4) — last; cosmetic until the lower levels are real.
