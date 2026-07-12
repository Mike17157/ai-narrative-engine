# Story Engine — what we're building

## The goal

Turn a thin, imported **character card** (Janitor AI / Chub / SillyTavern) into an ongoing, playable
story — and then let the player **convert a playthrough into a finished, illustrated story**.

The whole product is one pipeline:

```
  import a card
      │
      ▼
  1. CARD  →  MATURE SEED        build a story foundation from the card
      │                          (deep character + world + central question + opening)
      ▼
  2. ROLEPLAY                    the player plays it out (the existing Player runtime)
      │
      ▼
  3. CONSOLIDATION               every ~50 turns: deepen the cards + story from the play,
      │                          segment into arcs — cheap per-turn, depth in batches
      ▼
  4. CONVERT  →  ILLUSTRATED     bake the transcript into prose + generate scene images
     STORY                       (the remaining big gap)
```

Most of the plumbing already exists in the repo; the work is the **seed builder**, the **consolidation
loop**, and the **transcript → story converter**.

---

## The heart: what makes a character (and a question) good

Most of the effort has gone into *how a character is built*, because that's what makes the difference
between "AI slop" and something that reads like Rewrite / The Wandering Inn / Shadow Slave / Key. The
hard-won principles, in order of how much they mattered:

1. **Archetype-first.** Start from a canonical archetype (childhood friend, kuudere, chuuni, tsundere,
   dandere, genki, mother-hen, delinquent-with-a-soft-heart, deadpan loner) and work *backwards*. Each
   archetype **is a characteristic wound wearing a way-of-being** — it carries its own `being` (the
   defining, socially-visible condition), its `wound` (the emotional formation that turns someone into
   this type), and its `coping`.

2. **The archetype defines the trauma — with relation to the world.** Don't invent a fresh wound; take
   the archetype's characteristic wound and **instantiate it through *this* world's pressure.** A
   kuudere in a town where everyone leaves went cold because caring here is grief you start early.

3. **Family off the throne.** Parents and siblings may exist, but they must **not** be the source of
   the trauma. The hurt comes from a friendship, a place, a hope, a first patient, a failure — anywhere
   the world touched them. (The old "family-hole" primitive made every character an orphan; killed.)

4. **The central question is a quiet, lived feeling — not a dramatic abstraction.** The register that
   works is *"How much of yourself do you give to something that is already ending?"*, not *"is it a
   betrayal to be happy in a world of suffering?"* A good question names a feeling the reader has
   actually had, plainly enough to recognise themselves; the story answers it with **people, not a
   thesis**. Its weight is recognition, not stakes. BANNED register: "betrayal / deserve / worth
   existing / defiance / the dark."

5. **Story question vs protagonist question.** The protagonist's private ache can be selfish ("can I be
   happy?"). The *story's* question turns outward — realistic, universal, about the human condition.
   The scale comes from the question's **universality**, not from cosmic fantasy.

6. **The defining condition (Shizuru / Lucia).** The thing you meet first is a singular, socially-
   visible **condition or way of being** — Shizuru is quiet and her room is empty; Lucia can't touch
   anything and everyone thinks she's weird. Relatable not because we share it, but because everyone
   knows what it is to be set apart by their own particular thing. It carries quiet depth **without a
   receipt** — most quirks just *are*; do not decode every one into a wound.

7. **Trauma has depth = self-implication.** Not a clean thing done *to* them — a guilt, a shame, a
   thing they did or can't admit (a relief they hate themselves for, a cruelty they can't take back).
   Quiet, never dramatic. And **not everything is trauma** — a person has textures that aren't scars.

8. **The backstory is one continuous story where the trauma is also the origin of the life** (Chihaya
   meeting Sakuya): the worst thing that happened is also where they met the person / found the thing
   that made them who they are. Before → rupture → the self-and-bond born from it.

### Register and model discipline

- **No poetry, no melodrama.** Character data is written concretely and plainly (`_CLINICAL` banner) —
  but *alive*, not a police report. The engine's recurring failure mode is overshooting into drama and
  abstraction; the fix is almost always quieter and more human than first reach.
- **Model split.** Structured/reasoning passes (persona, psych, setting, history) → `minimax/minimax-m3`
  (deepseek flakes on structured output). The **prose backstory** pass → `deepseek/deepseek-v4-pro`
  (no-JSON, `reasoning_effort: low`): strong, cheap, large-context, and it weaves the card's own given
  details into the backstory. The "deepseek is bad" finding was structured-output-specific, not prose.

---

## The pieces (files)

- **Depth engine** — `loom/stories/character_engine.py`. Passes: persona (defining condition) → backstory
  (archetype's wound instantiated in the world, on the prose model) → psych → reveal. `_ARCHETYPES`
  library; `generate_cast` casts a chemistry ensemble in parallel; `generate_setting` (quiet central
  question + one-word-stance factions); `generate_history` (war worlds only). Self-test in `__main__`.
- **Card → mature seed** — `loom/stories/story_seed.py` `build_seed(provider, *, card, backstory_provider)`.
  Card-first orchestration of the engine: infer archetype → world + quiet question → deepen → psych →
  opening. Endpoint `POST /api/stories/seed-from-card` in `router.py` (HTTP-verified).
- **Roleplay runtime** — `POST /api/stories/{key}/play` → `play_graph.py` (narrator → scribe → state
  deltas). StoryMaster (`storymaster.py`) mutates the world, fleshes new on-stage people, holds arcs.
  Transcript lives in `world_state["transcript"]`; sessions persist as JSON (`story_sessions.py`).
- **Consolidation** — `stage_tools.py`:
  - `consolidate_on_rest` — existing sleep-only memory-*compression* (sliding window + dream).
  - `consolidate_cast` (new) — periodic **deepening**: self-gates every ~50 turns; updates
    `world_state["story_so_far"]` and each on-stage character's card (`world_state["cards"]`) from the
    recent transcript. Additive, never raises. Wired into `play_graph.py`.
- **Images** — `render_batch` (`services/batch_images.py`), role-routed providers (`context.py`),
  portraits. Primitives exist; per-scene illustration wiring does not.
- **Runners** — `scripts/gen_rewrite_spirit.py` (world → cast, writes `configs/last_cast.html`),
  `scripts/seed_from_card.py` (card → seed), `scripts/dump_payloads.py` (show exact prompts),
  `scripts/test_consolidate.py`.

---

## Runtime architecture (the play → consolidation loop)

- **Encounter = a sketch, not a full card.** When the narrator brings a new person on stage, record a
  *light* sketch — name + appearance + archetype — enough to render a portrait and behave consistently.
  (Currently the encounter still fleshes a full card via `birth_character`; lightening it to a sketch is
  the paired change to `consolidate_cast`.)
- **Consolidation = batched depth.** Every ~50 turns, `consolidate_cast` reads the recent transcript and
  grows the sketches into real cards + updates the story summary. Depth **accretes from the roleplay**
  instead of being paid for per turn.
- **Arcs emerge from play.** Arcs are already first-class (`world["arc"]`: stages, a dramatic question).
  The next step is arc *segmentation* inside consolidation — open/close an arc as the driving question
  shifts, so the playthrough naturally chapters itself (and those chapters feed the bake).

---

## Current state

| Stage | Status |
|---|---|
| Card import + flesh | **exists** (`card_sources.py`, `flesh_character`) |
| Card → mature seed | **built** (`build_seed` + endpoint, verified) |
| Depth engine | **built** (archetype-first, quiet register, model split) |
| Roleplay runtime | **exists** (Player + play_graph + StoryMaster) |
| Consolidation (deepening) | **built** (`consolidate_cast`, self-tested) — pairs with lightening the encounter |
| Arc segmentation from play | **not built** (arc structure exists; segmentation doesn't) |
| Transcript → baked prose | **NOT built** — the biggest gap; the bake path reads authored beat-graphs, not playthroughs |
| Per-scene illustration | **not built** (render primitives exist; per-chapter wiring doesn't) |

---

## Next

1. **Lighten the encounter** to a sketch (appearance + archetype) — completes the cheap-runtime /
   batched-depth split.
2. **Arc segmentation** in consolidation.
3. **Transcript → illustrated story converter** — the remaining magic: take a finished playthrough,
   bake it into prose (per arc/chapter), and generate scene images on top of `render_batch`.
4. **Surface it** — show `story_so_far` and the evolving cards in the UI; a "Build story from card"
   button that calls `seed-from-card` and drops the `opening` into the Player.
