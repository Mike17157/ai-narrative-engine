# Loom

A local **story studio**: architect, author, and play LLM-driven stories, with
character portraits and sprite sets rendered through a local ComfyUI backend.
Built to replace a SillyTavern-style patchwork of tools with one controlled,
flexible workflow.

The core idea, and the fix for SillyTavern's biggest interface problem: **the
text model and the image model are fully decoupled.** They're separate named
resources, configured independently, wired together only by a preset. Swapping
the writing brain or the image model is a one-line edit, and neither is ever
the same object as the other.

Flexibility comes from **declarative config** (think ComfyUI's composability, as
files) rather than a node canvas — every model, character, and pipeline is a
validated YAML resource. A visual editor can sit on top of this later; the
engine doesn't need it.

## Architecture

```
configs/models.yaml ─┐
configs/characters/ ─┼─► Providers ─► backends
configs/pipelines/  ─┘   (text | image)
```

- **Providers** (`loom/providers/`) — one adapter per backend. `anthropic`
  (cloud LLM, text) and `comfyui` (local Stable Diffusion, image) ship today;
  adding OpenAI or Automatic1111 is a single new file + a registry entry.
- **Models** (`configs/models.yaml`) — named provider instances. Presets and
  pipelines reference them by key.
- **Characters** (`configs/characters/*.yaml`) — persona only, **no model
  binding** (the decoupling, enforced by the schema).
- **Pipelines** (`configs/pipelines/*.yaml`) — ordered, typed steps with
  `{{ jinja }}` templates and optional `when:` guards, validated on load.

Every config is validated by Pydantic on load, including cross-checks that each
step targets a real model of the correct kind. Flexible, never silently broken.

## The app

The Svelte UI has four top-level sections:

- **Stories** — the heart: a Story library, the Architect (interview-driven
  world/cast authoring), structure/cast/studio workspaces, and ▶ Play (the
  runtime). The studio renders character portraits and emotion sprite sets via
  ComfyUI; cast pages plan wardrobes and render outfit sprites per story.
- **Lorebooks** — world-info books with keyword-triggered entries, semantic
  retrieval, and AI augment.
- **Presets** — the model side: connection + model + mode + params, grouped by
  function (story stages resolve to stage presets).
- **Settings** — System (environment, ComfyUI lifecycle, trainer environment
  setup) and Personas (the portable "you" cards).

## Story runtime

Stories use two authored cards: a Story card (world, locations, arcs, cast references) and
portable Character cards. Roleplay is preserved as raw turns, then consolidated into
evidence-citing residuals rather than rewriting source cards. Locations activate deterministically
from the Story card; `krea2_turbo_map` renders a reference-grounded map asset. See
[`loom/stories/CARD_NETWORK.md`](loom/stories/CARD_NETWORK.md).

## Quick start

```bash
pip install -e .

loom validate                              # load + cross-check all configs
loom models                                # list models, grouped by kind
loom serve                                 # run the app at http://127.0.0.1:8000
```

`loom validate` and `loom models` work with no API key and no ComfyUI running.
Story text generation needs a configured text connection (OpenRouter/OpenAI/
Anthropic); portrait and story-image rendering additionally needs a ComfyUI
server (managed automatically — see `comfyui` in `user.yaml`).

## Running it

- **`Start.bat`** (double-click) — sets up a Python venv, installs Loom, builds the
  Svelte UI, and serves everything at **http://127.0.0.1:8000**.
- **`Dev.bat`** — frontend hot-reload: FastAPI on `:8000` + Vite on **`:5173`**
  (proxies `/api`). Edit `frontend/` and changes reload live.

Architecture: **Python/FastAPI backend** (providers, ComfyUI lifecycle,
connections, story runtime) exposing `/api/*`, and a **SvelteKit SPA** frontend
(`frontend/`). In production FastAPI serves the built SPA; in dev Vite serves
it. If Node isn't installed, the server falls back to a bundled single-file
page. A parallel **lean Story app** (`loom story-serve`) runs a Story-only
surface; see [`LEAN_STORY_APP.md`](LEAN_STORY_APP.md).

## Roadmap

- **Phase 1 (done)** — engine core: schemas, providers, CLI.
- **Phase 2 (done)** — FastAPI server + connections (OpenRouter/OpenAI/Anthropic,
  SillyTavern-style connect flow), managed ComfyUI lifecycle.
- **Phase 3 (done)** — SvelteKit UI with independent text-model and image-model
  selection; the app has since converged on Stories, Lorebooks, Presets, and
  Settings (the standalone chat/images/library/training surfaces were retired).
- **Phase 4** — `automatic1111` provider, visual pipeline editor.
