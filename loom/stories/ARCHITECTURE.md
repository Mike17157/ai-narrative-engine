# Story feature architecture

The `stories` package is organized by responsibility, not by when code was added.

```text
stories/
  api/          HTTP adapters: validate requests, select a service, shape responses
  records/      card projections, raw-turn residuals, and safe graph updates
  runtime/      live play, scene context, state, and direction
  creation.py   world-design subsystem: design, material, geography, and orchestration
  pipeline/     multi-step story creation pipeline
  *.py          domain behavior and persistence-facing collaborators
  router.py     composition root only
```

## API ownership

| Module | Owns |
| --- | --- |
| `api/builder.py` | story creation, character development, premise, and simulation endpoints |
| `api/genesis.py` | genesis-world generation and commit endpoints |
| `api/drafting.py` | chapter drafting, scene evaluation, and extraction endpoints |
| `api/authoring.py` | workshop, agent, stage-tool, session, role, and lorebook endpoints |
| `api/arcs.py` | arc design, expansion, timelines, and chapter regeneration endpoints |
| `api/library.py` | persisted story records, cards, conditions, cast, and deletion endpoints |
| `api/play.py` | live play, prologue, arc, and day-cycle endpoints |
| `api/manuscript.py` | playthrough manuscript read, edit, baking, and illustration endpoints |
| `api/play_state.py` | derived state-card and mutable live-card read models |
| `api/assets.py` | dreams, geography, wardrobe, character regeneration, and story backgrounds |

## Conventions

- A route module exposes `register(app, ctx)` and owns a coherent public API family.
- Public URLs are compatibility contracts; move code without changing paths or payloads.
- Route modules should stay thin: orchestration belongs in a domain module or a server service.
- New code uses a descriptive noun for the owned capability (`manuscript`, `assets`, `arcs`), not a catch-all name such as `utils`, `helpers`, or `misc`.
- `router.py` only composes route families. It must not contain endpoints or business logic.

The next consolidation target is the live-play runtime: `play_context`, `play_graph`, `state_engine`, and `storymaster` currently overlap and should become one explicit runtime subsystem.

## Runtime story model

The authored surface is deliberately only two card types: a Story card and portable Character
cards. Locations and arcs are sections of the Story card, not independent cards. Live play appends
immutable raw turns, then periodically consolidates them into small evidence-citing residual claims.
See `CARD_NETWORK.md` for the complete card, activation, and map contract.

The Storymaster uses `minimax/minimax-m3` for the reflective residual pass. It reasons over
plain-text card sections and raw turns, using narrow tools, rather than consuming or emitting one
large JSON document.

## Agent workflow contract

The existing HTTP Story workflows use `pydantic-graph`; their established
boundary remains:

```text
deterministic context → model candidate → parse/normalize → validate → MutationPlan → one commit
```

- Models propose prose or structured candidates; they never directly mutate canonical Story,
  Character, or runtime state.
- Deterministic services (scene eligibility, knowledge projection, card validation, and persistence)
  stay ordinary code rather than fake graph nodes.
- Graphs own dependent calls, branching, retries/checkpoints, human approval points, and events.
- Each graph-backed workflow emits common `workflow`, `node`, and `model` trace envelopes through
  `stories.workflows`. Existing UI-specific events remain compatible during migration.

Current graph-backed workflows are the interview, card review/organization, authoring workshop and
draft pipeline, world genesis/systems, story-structure agent, live play, and autonomous simulation.
One-off generation helpers remain leaf tasks and may be called by a graph when they participate in a
larger workflow.

The desktop Story Host migration is the deliberately narrow exception: it embeds
Oh My Pi only for the sealed public Architect proposal turn, while keeping Story
prepare/review/persistence deterministic. It is not a second generic agent
runtime for the existing HTTP workflows. See `../../STORY_HOST_MIGRATION.md`.

Story-structure chat accepts an explicit `workflow` choice: `single` for one bounded tool call,
`structure` for the plan/act/observe/reflect/commit graph, or `auto` as a compatibility fallback.
Routing is deterministic; it is never delegated to an LLM.

## Creation subsystem

`creation.py` is the canonical home for everything that makes a story world before play begins: world framing, cast harnesses, relationship weaving, premise and substrate material, geography, and pydantic-graph orchestration. New code imports from `stories.creation`.
