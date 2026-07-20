# Lean Story Application

This is the parallel replacement surface for the product that remains after
retiring the general chat, workflow lab, LoRA/training, audio, tags, and admin
tools. It is intentionally a **small full-stack Story application**, not a
browser-only app pretending secrets, persistence, and local rendering do not
exist.

## Architecture boundary

The translation keeps the useful hierarchy of the Architect system without
copying an unrelated runtime wholesale:

1. The Svelte workspace owns interaction state, proposal review, and what the
   writer sees. `frontend/src/lib/story-architect-controller.js` is the
   browser-side coordinator for the public Architect contract.
2. A lean FastAPI composition root exposes only the Story capabilities the
   browser needs: Architect, authoring, play, a narrow cast read/card surface,
   and Story-scoped images.
3. Server-side capability adapters own the things that must not live in a
   browser: provider credentials, the Story Agent model route, atomic Story
   commits, local files, and the optional Comfy process.
4. Each image belongs to one Story at
   `configs/stories/<story-key>/images/`; there is no global image job/catalog
   service in the lean app.

That is the important split: the backend is no longer an application-within-an-
application. It is a small trust and persistence boundary for a frontend-owned
Story product.

## Run it

From the project root:

```powershell
uv run python -m loom.cli story-serve --root . --port 8001
```

This starts the lean API on port `8001`, launches the existing Svelte Story
routes through Vite on port `5173`, and points Vite's `/api` proxy at the lean
API. It does **not** start ComfyUI at boot. A Story image render is the first
operation that will connect to or start the configured runner.

To work with text/Story only:

```powershell
uv run python -m loom.cli story-serve --root . --port 8001 --no-comfy
```

To run the frontend yourself instead of letting the command launch it:

```powershell
$env:LOOM_API_ORIGIN = 'http://127.0.0.1:8001'
$env:VITE_LEAN_STORY = '1'
Set-Location frontend
npm run dev
```

The ordinary `loom serve` command remains unchanged and continues to run the
full application on its usual port (now converged on Stories, Lorebooks,
Presets, and Settings — the standalone chat/images/library/training surfaces
have been retired).

## What the lean API contains today

- Story library, Story Architect, interview/card lifecycle, and the public
  Architect `context`, `model`, and atomic `commit` contract.
- Story play/runtime routes under `/api/stories/...`.
- Story-scoped cast/portrait routes under `/api/stories/{key}/cast/...`, plus
  card edits required by Story authoring and play. A Story cannot enumerate or
  edit the global character library through the lean API. Cast-card data and
  legacy portrait files are resolved with the explicit Story key, never by a
  global character lookup.
- One deterministic play session at `/api/stories/{key}/session`. The legacy
  arbitrary-session endpoints are not exposed; a supplied `sid` must be that
  Story's `play-<key>` session.
- `GET /api/lean/images/status`, Story image gallery/file routes, and
  `POST /api/stories/{key}/images/render`.
- Only configured local image workflows whose model key starts with `krea2` and
  whose provider is `comfyui` are admitted by the lean image capability.

The new Story Images route is `/stories/{key}/images`.

`GET /api/lean/health` includes an `integrity` report. Resolve any reported
`duplicate_character_keys` before treating the lean surface as a full cutover:
some mature runtime internals still predate Story-scoped character maps. New
Story-generated character keys are now kept globally unique, and the lean API
does not expose the old global-cast import shortcut, so new lean work does not
create that ambiguity.

## Image-provider decision

At present, OpenRouter is used here for **text** providers, including the Story
Agent. This repository does not yet have an OpenRouter image-provider adapter,
so the lean image path intentionally does not falsely present it as one.

The supported optional image route is local Krea2 through the existing ComfyUI
workflow definitions. It is deliberately small: choose an allow-listed Krea2
workflow, render on demand, persist the resulting PNG beneath the Story.

## Deliberately absent

The lean application does not register the general chat, jobs, Comfy catalog,
workflow editor, LoRA/training, tags, audio, global model-admin, agent-mode,
tool-editor, text-role, lorebook, global-character import, or old pending-work
queue API families. It also does not mount the legacy SPA as a production
application surface.

The source code for those features has since been retired as well: the legacy
chat/images/library/training routers, services, and packages were removed once
nothing called them. What remains in the main app is the Stories, Lorebooks,
Presets, and Settings surface described in the README.

## Cutover plan

1. **Parallel validation — current phase.** Use `story-serve`, create/edit a
   Story through Architect, activate it, play it, and render a Story-owned
   image. Verify the old app remains unaffected.
2. **Lean frontend shell — current phase.** `story-serve` sets
   `VITE_LEAN_STORY=1`, which renders a Story-only shell and does not boot the
   global legacy health, jobs, persona, or settings polling.
3. **Integrity preflight.** Resolve any duplicate cast keys reported by the
   lean health endpoint before moving an existing library to the lean runtime.
4. **Extract rather than merely filter.** The cast/session/image boundaries are
   already lean-owned; the mature Story runtime is filtered at registration
   time. Move the surviving runtime handlers into lean-owned modules once their
   behavior has been exercised in parallel.
5. **Production handoff.** Serve the reduced Svelte build with the lean app or
   a static host, configure the same API origin, and run the Story smoke suite.
6. **Retire only with evidence.** Remove legacy route registration and then
   unused code after a release has run solely against the lean surface.

This ordering preserves the weeks of debugging embodied in the Story system
while making the target product genuinely smaller at every verified step.
