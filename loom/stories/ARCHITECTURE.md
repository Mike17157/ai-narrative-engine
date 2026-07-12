# Story feature architecture

The `stories` package is organized by responsibility, not by when code was added.

```text
stories/
  api/          HTTP adapters: validate requests, select a service, shape responses
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

The next extraction target is application state in `server/context.py`: split provider selection, asset paths, and story persistence into explicit collaborators without changing the `AppContext` public surface in one step.
