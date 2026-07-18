# Story Host migration plan

## Status

The first desktop-native Architect vertical slice is implemented. It is not yet
a full replacement for the existing FastAPI Story application.

What is real now:

- a pinned, MIT-licensed Oh My Pi v15.10.5 SDK integration in `story-host/`;
- an app-contained Bun sidecar with JSONL stdin/stdout only;
- a real OMP Architect turn with one sealed `story.propose` tool;
- deterministic prepare/review around that turn, plus an atomic existing-SQLite
  commit behind a single-use, ten-minute approval token;
- a desktop Tauri sidecar seam and an explicit review/confirm path for the four
  bounded public Architect scopes;
- local Story library, minimal card creation, author-card, readiness and
  navigation-graph reads;
- locked local direct edits for the explicit Story-card prose leaves and six
  Story-local cast fields, with optimistic author revisions;
- optional, Story-owned Krea2/Comfy rendering, disabled by default, plus a
  descriptor-checked Tauri PNG reader for the local Story gallery.
- release first-run seeding of an empty Story data root: a minimal
  `configs/models.yaml` and directories are created only when absent, never by
  copying or overwriting an existing Story database.

The remaining work is deliberately staged. The legacy web UI still has many
`/api` reads and authoring actions, so FastAPI cannot truthfully be removed
from runtime until those capabilities have local IPC equivalents.

## Target runtime

```text
Static Svelte UI
      │ Tauri invoke/events (no loopback HTTP)
      ▼
Persistent Bun Story Host
      │ local JSON Lines (stdin/stdout)
      ▼
Temporary Python Story bridge
      │ existing aggregate validation + SQLite/config data
      ▼
Story database and Story-owned assets
```

Neither the Bun host nor the Python bridge opens a listening port. This keeps
the desktop product to one app process tree rather than several application
servers. The optional Comfy runner is different: it may use its normal local
HTTP protocol only while an author has explicitly enabled and requested an
image render. It remains disabled by default and is not part of normal Story
startup.

## Development desktop mode

The current goal is a dependable developer checkout, not a standalone
installer. Keep the existing Python aggregate validator while the Bun port is
developed behind parity tests; do not package an embedded Python runtime yet.

From the repository root, prepare the local Python and JavaScript dependencies.
The Windows Tauri target also needs Rust/Cargo and the Visual Studio 2022 Build
Tools C++ workload; the desktop launcher finds the normal Rustup and Build
Tools locations itself, so a fresh terminal does not need to have them on its
inherited `PATH`.

```powershell
uv sync
npm --prefix story-host ci
npm --prefix frontend ci
npm --prefix frontend run desktop:preflight
npm --prefix frontend run desktop:dev
```

`desktop:dev` deliberately sets `LOOM_ROOT` to this checkout (unless you
explicitly set another Story root) and `LOOM_PYTHON` to this checkout's
`.venv`. It verifies `import loom`, compiles/stages the Bun host, then starts
Tauri in an MSVC environment. It does not start FastAPI or a loopback
application server. Use this entrypoint rather than calling `tauri dev`
directly; that makes the temporary Python boundary explicit and prevents a
global Python installation from silently masking a broken checkout.
`desktop:preflight` performs the same runtime/toolchain check without building
or opening a window. `desktop:smoke` rebuilds the staged sidecar and proves
`host.health` plus `story.list` work after removing `LOOM_PYTHON` and global
Python/WindowsApps PATH entries from the test process. It also compares the
normal Python `story.list` result with the first feature-flagged Bun-native
reader. To try that bounded read path in the actual desktop UI, use
`npm --prefix frontend run desktop:dev:bun-list`. It is intentionally off by
default: it activates only for an already-normalized local SQLite store and
falls back to Python for bootstrap, pending JSON migration, legacy schema, or
any malformed projection.

## What was lifted from Oh My Pi

The host uses `@oh-my-pi/pi-coding-agent` and `@oh-my-pi/pi-ai` at exact
version `15.10.5`. Its local patch is small and reviewable:
`story-host/patches/@oh-my-pi+pi-coding-agent+15.10.5.patch`. The notice and
upstream MIT attribution are in `story-host/THIRD_PARTY_NOTICES.md`.

Retained OMP mechanics:

- the real session loop, event stream, retry and compaction behavior;
- custom-tool execution and structured tool submission;
- the dependency-wave rhythm of an Architect workflow;
- cancellation and per-turn session lifecycle.

Deliberately excluded from this product boundary:

- coding/file/shell/Git tools, MCP, LSP, extension and custom-tool discovery;
- OMP browser, web search, TTS and provider-driven image tools;
- agent spawning, workspace scanning, ambient OMP auth/config/model state;
- raw database, raw filesystem, raw Comfy workflow and arbitrary provider
  routing capabilities.

The current swarm is intentionally small and truthful:

```text
deterministic explore/prepare → one OMP Architect → deterministic reviewer → author approval → atomic commit
```

`explore` and `reviewer` are deterministic Story services today, not separate
independent OMP agents. That retains the useful OMP execution hierarchy without
inventing autonomous roles before the Story domain needs them.

## Capability contract

Only `world`, `premise`, `cast`, and `first_day` are currently public desktop
Architect scopes. For one request the host:

1. loads a model-safe Story projection and optimistic revision;
2. prepares the prompt/schema using existing Story rules;
3. gives OMP only `story.propose`;
4. validates the structured proposal deterministically;
5. returns a reviewed patch and opaque approval token, without writing canon;
6. accepts that token once for the atomic aggregate commit.

Approval capabilities are in memory. They expire after ten minutes and vanish
when the sidecar restarts. A raw proposal cannot be submitted to the commit
operation.

OMP runs in an app-local state directory with an explicit isolated AuthStorage
and ModelRegistry. It receives only the configured OpenRouter credential and
always uses OpenRouter's official endpoint. The baseline model is deliberately
ordinary tool-capable completion (`reasoning: false`) until a Story model is
registered with explicit provider metadata.

## Current desktop UI increment

`InterviewWorkspace.svelte` keeps the existing browser controller for web
operation. In Tauri, a bounded direct edit instead uses
`desktop-story-architect.ts`:

1. the desktop host produces and reviews a proposal;
2. the existing plan surface shows the reviewed patch;
3. **Confirm plan & execute** sends only the approval token;
4. the full author card reloads through `story.read`, while readiness and the
   control map reload through their matching local capabilities.

The desktop library/card bootstrap now uses `story.list`, `story.create`, `story.read`,
`story.readiness`, `story.control_graph`, and a read-only `architect.context`.
It does not issue the legacy empty Architect HTTP turn. The global character and
model catalogues remain deliberately deferred rather than silently falling back
to HTTP; Story-card cast data is already part of the author-card projection.

Ordinary author card edits now use two narrow local operations:

- `story.inline_text` accepts only the existing allowlisted prose paths (title,
  premise, selected world/entity/location fields, existing Day One text,
  character cores, and entity-period text);
- `story.cast_text` accepts only `name`, `role`, `personality`, `appearance`,
  `background`, and `connection` for a current Story cast member.

Each operation carries an opaque `author_revision`, acquires the SQLite
aggregate write lock, re-checks that revision inside the transaction, validates
the live aggregate, and returns a refreshed author card. A prose/cast change
demotes an active Story and drops its stale compiled runtime snapshot. The
revision covers only locally editable author-card surfaces; it is not a change
oracle for unrelated private/runtime documents.

Author-card and OMP projections are scoped to the current Story's embedded
character records over the reusable YAML library. This prevents two Stories
that share a character key from displaying or editing each other's local copy.

Desktop guards now keep unported legacy routes from silently using HTTP:
protected Architect targets, focused interview modals, Director tools, live
play, and legacy child views remain explicitly unavailable in the local shell
until their own narrow capability exists. That is intentional—not a claim that
the full FastAPI application has been replaced.

Tauri builds the frontend in explicit `VITE_LEAN_STORY=1` mode. That prevents
the root refresh/activity/persona fetches and voice TTS/STT probes from ever
trying the legacy `/api` server; the browser speech APIs remain the local voice
fallback. A desktop-mode app that cannot reach Tauri IPC fails closed instead
of retrying HTTP.

The Story image gallery is the one specialised local child route now enabled.
It uses `image.status`, `image.list`, and `image.request` over the sidecar.
The host returns opaque asset descriptors; a Tauri command independently
restricts each read to the owning Story's `images/*.png` directory, checks
canonical containment, maximum size, and the PNG signature, then returns a
`data:` URL. It exposes neither a filesystem path nor a legacy image URL.

The host's returned card is model-safe and is never substituted for the full
author card. That distinction prevents a redacted agent projection from
silently becoming the writer's data model.

## Delivery phases and exit gates

| Phase | Scope | Exit gate |
| --- | --- | --- |
| 0 — sealed Architect foundation | Bun host, OMP isolation, Story bridge, approval guard | Passed offline tool-surface and commit-guard tests |
| 1 — desktop review seam | Tauri IPC and public-scope proposal/review/confirm UI | Consented live model proposal, approve, cancel, stale revision, and restart checks |
| 2 — local Story reads/lifecycle | `story.list`, narrow `story.create`, `story.read`, readiness and author-card/control-map projections over IPC | A Tauri Story card opens/reloads with FastAPI stopped |
| 3 — local authoring/play | Direct Story-card prose/cast editing is local; move remaining structural authoring and play session to narrow IPC capabilities | Full Story author/play smoke suite with no app HTTP server |
| 4 — assets | Descriptor-checked Tauri resolver and optional on-demand Comfy | Native Story gallery/render smoke without legacy image URLs |
| 5 — managed research | A cited librarian facade: search/open/extract/store only | Provenance cards, source limits, no login/post/download/browser attachment |
| 6 — persistence end state | Bundle Python/SQLite safely or port the bridge to Bun/SQLite after golden parity | Migration, backup, corruption recovery and concurrent-write tests |
| 7 — retire HTTP path | Remove production FastAPI route registration only after all prior gates | Release runs solely through Tauri IPC; then remove dead web-only code |

## Image decision

OpenRouter is currently the Architect's text route only. This project does not
claim an OpenRouter image adapter. The present optional image capability is
allow-listed local Krea2/Comfy. It accepts only a Story key, prompt, role and
registered model; it returns a Story asset identifier, not an HTTP URL.

The Tauri resolver/viewer is implemented; it still needs a native build and a
real local render smoke before Phase 4 can pass.

## Browser research decision

Inspiration research belongs to a future `librarian` capability, not to the
stock OMP browser tool. Its hard policy is:

- allow: search, open, extract, cite and store a small inspiration card;
- deny: login, posting, messaging, purchases, downloads and attachment to a
  user's browser session;
- never write research directly into canon.

The host currently exposes that policy only. Browser automation has not been
enabled yet.

## Validation evidence and remaining release checks

Passed locally for this slice:

- `npm run check`, `npm test` and compiled sidecar checks in `story-host/`;
- live SDK isolation test showing exactly `story.propose`, even beside MCP,
  custom-tool and `AGENTS.md` sentinel files;
- Python JSONL context/prepare/validate/atomic-commit tests;
- Python JSONL direct prose/cast edit tests, including stale revisions, active
  runtime invalidation, global-character cloning, and duplicate-key isolation;
- image status/disabled/request-boundary tests;
- frontend normal and local-only production bundle builds, plus sidecar staging.
- Rust unit tests, a Windows x64 Tauri MSI bundle build, and a current-user
  NSIS bundle build. The packages include the Bun sidecar, the empty first-run
  Story seed, and the descriptor-checked native asset resolver.
- a silent current-user NSIS install, packaged desktop-to-sidecar launch,
  development-bridge `story.list` smoke, and uninstall. The machine-wide MSI
  correctly requires administrator rights and was not used as the non-admin
  runtime test.

Still required before a desktop release:

- a consented real OpenRouter proposal and approval/cancel/stale-token run;
- a real optional Comfy render and Tauri asset display;
- a standalone installed-app IPC and first-run app-data smoke with no
  development Python/Loom runtime available;
- package the temporary Python bridge and its Loom runtime (the release data
  root now seeds safely, but the current bridge still requires a Python/Loom
  runtime); add an explicit one-time importer for an existing Story database;
- sidecar crash/restart, expired capability, cancellation and back-pressure
  tests;
- review of dependency audit findings rather than an automatic audit fix;
- a no-listening-port check for the normal non-image desktop path.

## Decision rule

Do not delete or disable the current FastAPI Story path merely because the OMP
host exists. Remove it only after Phase 3 has a verified local replacement for
every Story capability the shipped UI invokes. That preserves the debugging
knowledge already encoded in the Story system while making each migration step
small, reversible and testable.
