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

## Creation subsystem

`creation.py` is the canonical home for everything that makes a story world before play begins: world framing, cast harnesses, relationship weaving, premise and substrate material, geography, and pydantic-graph orchestration. New code imports from `stories.creation`.
