# Story-authoring kernel consolidation (2026-07-15)

## Why

A design conversation proposed reorganizing story authoring around five compact
"kernels" — **Premise core**, **Character core**, **Arc tension**, **Scene seed**,
**Protected logic** — each holding just enough truth to be coherent and leaving
enough gap for the narrator LLM to discover moments instead of reciting a script.
It also asked for per-scene themes and per-participant scene roles (e.g. a
lunch-sabotage scene where the best friend misreads the MC's deflection as
bravado), and for the MC to be a defined-but-not-puppeted Character core.

A codebase audit found that **most of this was already built**, just buried
under dead code and an unfinished migration. This document records what
changed to surface it, in five phases, sequenced from lowest risk to highest.

## Key discovery

`frontend/src/lib/story-atlas.js` + `StoryAtlas.svelte` already projected a
story card into World / Opening / Cast / Arcs / Scenes / Director(protected)
kernels. `loom/stories/authoring/dramatic_kernel.py` already validated capped
character-core strings and scene theme/tone/roles. `loom/stories/authoring/arc_design.py`
+ `runtime/arc_guard.py` already did tiered arc-tension visibility with hard
resistance-lock enforcement — better than what the original conversation was
asking for. The actual problems were: the destination UI was a small header
strip sitting above a duplicate 200-line scroll-list; an entire orphaned
genesis-wizard subsystem that nothing called anymore; the rich scene-theme
machinery was wired only into the VN/compiled path, not free play; and the
"protected logic" kernel only reflected one of five sealing mechanisms.

## Phase 1 — Delete dead code

Removed ~2000 lines of unreachable frontend and a 2568-line orphaned backend
subsystem, all confirmed via zero-importer greps re-verified immediately
before each deletion.

- Frontend: `StoryTabs.svelte`, `AgentChat.svelte`, `StoryWorkspace.svelte` +
  its orphaned children `StoryOverview.svelte`/`StoryCard.svelte`,
  `StoryCardNavigator.svelte`, `BaseStudio.svelte`,
  `components/graph/StoryControlNode.svelte`, `RelationshipGraph.svelte`,
  `RelationshipFlow.svelte`. Two of these (`StoryCardNavigator.svelte`,
  `StoryControlNode.svelte`) were never committed to git — deleting them was
  irreversible, so this was confirmed explicitly with the user first.
- Backend: `loom/stories/api/creation.py` (the entire genesis wizard —
  `/genesis/*`, `/story-builder`, `/simulate/*`, `/improv*`, `/arcs/weave`,
  `/extract-scenes`, etc.), deregistered from `loom/stories/router.py`. The
  dead `POST /api/stories/graph-ops` endpoint in `loom/stories/api/runtime.py`
  (its only caller was the also-dead `AgentChat.svelte`).
- Root docs: `story_pipeline_spec.md`, `world_pipeline_spec.md` — described a
  pre-refactor module layout (`storymaster.py`, `story_seed.py`,
  `play_graph.py`) that no longer existed anywhere in the tree.
- **Not deleted**: `loom/stories/world/creation.py` and
  `loom/stories/characters/engine.py` — their generation logic is used
  elsewhere or independently valuable (see Phase 5).

Verified: backend imports clean, `npm run build` clean, no remaining
references to any deleted path.

## Phase 2 — Promote the Story Atlas to the primary card view

`StoryAtlas.svelte` was already mounted in `InterviewWorkspace.svelte` above a
duplicate ~200-line "Live story card" scroll list (world/opening/cast/arcs/
scenes/entity-schedule buttons repeating what the Atlas already showed).

- Deleted the duplicate section/leaf list.
- Relocated the toolbar (Edit/Director tools/Build/Review/Organize), the
  "Build a starting set" panel, and the readiness/"Start play" banner to sit
  directly beside the Atlas — unchanged behavior, new position.
- Caught and preserved two blocks that looked like part of the duplicate but
  had **no Atlas equivalent** — `open_questions` and `author_notes` — which
  the plan had originally scoped for deletion. Relocated instead of deleted.
- Pruned ~250 lines of helper code that only the deleted list consumed
  (`worldCardGroups`, `castNames`, `arcOutline`, `stableCardId`,
  `navigateCard`, etc.), including a `cardSections` derived value that turned
  out to already be fully dead code before this session touched it.
- No changes needed to `story-atlas.js`, `StoryAtlas.svelte`,
  `StoryAtlasCard.svelte`, or `EditorialModal.svelte` — the kernel-click →
  scoped-chat drill-in already worked correctly.

Verified live in the browser: Atlas is the only card view, every kernel
drill-in opens the correct scoped `/interview` chat, Director tools still
opens correctly, relocated controls still work.

## Phase 3 — Protected-logic kernel gets a real summary

The Director kernel previously only flagged `time_system`/entity data as
"protected." It now summarizes three of the five sealing mechanisms found in
the original audit (the other two — `visibility.py`'s `[[hidden]]` redaction
and `dramatic_kernel.py`'s own caps — are enforcement code, not something to
additionally surface):

- **Hidden author-only text** — detected client-side by scanning the already-
  received card JSON for `[[hidden]]`-style markers. No backend change
  needed; the UI card was already author-visible with these markers intact
  (only `model_story_card`, used for generic co-author calls, strips them).
- **Sealed relationship undercurrents** — counts `relationships` entries
  carrying `potential`/`trajectory`. Already on the card, no backend change.
- **Sealed character depth** — new `protected_facet_count` field added to
  `public_story_card()` in `loom/stories/authoring/card_payload.py`, counting
  secret-tier character facets across the cast via the same lorebook-scan
  pattern already used by the existing `/conditions/usage` endpoint. Wrapped
  in try/except at both the per-character and whole-block level.
- **Residual evidence** (the fourth mechanism from the audit) was scoped
  **out** — it lives entirely in runtime play-session state
  (`runtime/state.py`), not the authored story card, so an unplayed story has
  no residuals to summarize and reaching for them would be real scope creep.

Verified: backend field confirmed flowing through the live API response;
detection logic tested against synthetic hidden-block and sealed-bond data;
a real story with zero signals renders identically to before (no regression).

## Phase 4 — Generalize scene theme/tone/roles to free play

This is the phase that actually delivers the "lunch sabotage" example. The
VN/compiled path already had per-participant scene roles; free play got only
a thin `{goal, pressure, exit}` with no roles at all — a real architectural
fork, not a stub.

- `loom/stories/runtime/director.py`: `_SCENE_SCHEMA` now also requests
  `theme`, `tone`, and `roles` (a list of `{character, role}` pairs, matching
  the schema shape the VN pipeline already uses) from the same single
  per-scene-boundary LLM call — no added latency. The result is validated
  through the existing `dramatic_kernel.normalize_scene_kernel` before it
  reaches `world["scene_plan"]`, with `known_characters` set to the real cast
  roster so a hallucinated name is rejected. On any validation failure,
  theme/tone/roles fail closed to empty together; the pre-existing
  goal/pressure/exit mechanism is unaffected either way.
- `loom/stories/runtime/engine.py`: new `_freeplay_live_beat()` mirrors
  `_compiled_live_beat` — projects `scene_plan`'s theme/tone/roles plus who's
  currently on stage into the same `select_live_beat` selector the VN path
  uses. Deliberately never touches the plan's private goal/pressure/exit
  (that stays `scene_block`'s job) — purely additive, gated on the director
  actually assigning a theme.
- Wired into the same two-tier pattern already used for `scene_block`:
  `step_compile` surfaces the *standing* beat for every mid-scene turn at
  zero extra cost; `step_scene` overwrites it with the *fresh* beat on the
  turn a new scene opens.

Verified: new tests in `tests/stories/test_live_beat.py`
(`test_freeplay_adapter_uses_current_members_and_never_leaks_private_plan`,
`test_freeplay_adapter_is_empty_without_a_scene_plan_or_theme`) and a new
`tests/stories/test_freeplay_scene_kernel.py` (valid theme/tone/roles stored
correctly; an unknown-character role fails closed; no-provider path
unaffected). Full pre-existing suite re-run with no regressions. One
pre-existing failure (`test_play_lifecycle.py`, calling a since-removed
`/api/stories/new` route) is unrelated and predates this session.

## Phase 5 — Character-core / arc-tension schema convergence

Scoped as the highest-risk phase: converge `world/creation.py`'s harness
generator, `characters/engine.py`'s nakige dossier engine, and
`runtime/simulation.py`'s `DESIGN_SCHEMA` onto the already-good
`fields.character_cores`/`fields.arc_design` shape.

**It turned out to already be mostly done.** Phase 1's deletion of
`api/creation.py` also deleted the *only* code that ever converted a
generated harness or nakige dossier into a persisted `Character` object —
neither `world/creation.py` nor `characters/engine.py` contains a
`Character(...)` construction anywhere. They're now pure text-generation
functions with no persistence path to converge. Separately, the live
`/interview` authoring flow already targets `fields.character_cores`
directly (`loom/stories/api/library.py:1148`'s prompt instructs the model to
add entries there), so the convergence this phase wanted had already
happened for the one path that's actually reachable today.

What did happen:

- Confirmed `loom/stories/runtime/simulation.py` had zero importers anywhere
  in the codebase (a same-named import in a stray root-level
  `test_story_state.py` was already broken pre-existing, importing a
  different, nonexistent module path). Deleted it — same dead-code standard
  as Phase 1.
- Left `world/creation.py`'s harness generator and `characters/engine.py`'s
  nakige dossier engine untouched: the latter still backs
  `scripts/gen_rewrite_spirit.py`, a standalone script, and both would need a
  net-new "generate cast" UI entry point plus a rebuilt harness→Character
  conversion (recreating what Phase 1 deleted) to have any observable effect
  — a materially bigger, different task than "fix redundant schemas," and one
  the user explicitly chose not to build in this pass.
- Deliberately skipped promoting `want`/`lie`/`wound`/`secret` to validated
  `Character` fields (the plan's original sub-step 1). The only live
  consumer, `generate_dream`'s `_scaf()` closure in
  `loom/stories/authoring/stages.py`, already reads them with safe
  `.get()` fallbacks; `portrait_prompt` in `pipeline/character_scaffold.py`,
  the other consumer the original audit named, turned out to already have
  zero callers. Promoting them would have added schema surface for a
  typo-safety benefit with no active bug behind it.

## What was not touched (by design)

- `loom/stories/authoring/arc_design.py` / `loom/stories/runtime/arc_guard.py`
  — reference-quality tiered visibility + resistance-lock enforcement.
- Relationship `potential`/`trajectory` gating in `runtime/context.py`.
- The enforcement code behind all five protected-logic mechanisms
  (`visibility.py`, `character_scaffold.py`'s `FACET_TIERS`,
  `records/residuals.py`, the relationship undercurrent gate) — Phase 3 only
  added a read-only summary over these signals, never touched their logic.
- `loom/stories/ARCHITECTURE.md` — confirmed stale during this work (still
  lists `api/builder.py`, `api/genesis.py`, `api/play.py`, etc., none of
  which exist in the current `api/` layout), but updating it was out of
  scope for this pass.

## Incidental fixes

Two local dev-server issues were found and fixed while verifying phases in
the browser, unrelated to the story system itself but worth recording:

- Vite's dev server got stuck serving a stale module transform after several
  file deletions/edits landed while it was running; fixed by clearing
  `frontend/node_modules/.vite` and restarting.
- The backend's `uvicorn --reload` supervisor process had died as an orphan
  at some point before this session, leaving its worker child alive and
  serving requests but with nothing watching for file changes — edits were
  silently not taking effect. Found via the worker's own
  `multiprocessing.spawn(parent_pid=...)` command line pointing at a PID that
  no longer existed; fixed by killing the orphaned worker and restarting
  through the managed launch config.

## Net effect

One card view instead of two competing ones. The five-kernel model from the
original design conversation — Premise / Character core / Arc tension /
Scene seed / Protected logic — is now the thing actually rendered in the UI,
not a concept sitting next to a wall of buttons. Free-play scenes carry the
same per-participant role richness VN scenes always had. ~2400 lines of dead
frontend/backend code and one fully orphaned runtime module are gone.

## Frontend follow-up (2026-07-15)

Live verification after the consolidation found two migration seams that a
successful Svelte build did not expose:

- The Atlas and every adjacent control were separate grid children. The Atlas
  filled the left column, while the readiness panel and authoring tools flowed
  below the fixed-height grid and were clipped. The surviving controls now live
  in one scrollable authoring rail beside the Atlas, with a single-column mobile
  fallback.
- The conversation-first `/stories/new` page still depended on the small empty-
  card creation endpoint that had been removed with the much larger genesis
  subsystem. That route is now a library lifecycle operation: it only persists
  an empty `interviewing` card and does not restore any wizard, draft graph, or
  model generation path. The same narrow lifecycle treatment preserves the
  active character-import → first story-card shortcut without restoring its
  removed model-seeding machinery. The page now also exposes a retryable error
  instead of hanging forever on creation failure.

The frontend story store was reduced to its active data/lifecycle surface; dead
arc-expansion, graph-expansion, and legacy edit-clone methods that called removed
endpoints were deleted. Verification: production frontend build succeeds, live
desktop geometry checked at 1280×720, new-story navigation reaches the new card,
and the full story suite passes (209 tests).
