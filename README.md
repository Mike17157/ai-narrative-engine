# Loom

A declarative pipeline engine for **chat + image generation**, built to replace a
SillyTavern-style chat surface and a ComfyUI-style image surface with one
controlled, flexible workflow.

The core idea, and the fix for SillyTavern's biggest interface problem: **the
chat model and the image model are fully decoupled.** They're separate named
resources, configured independently, wired together only by a pipeline. Swapping
the chat brain or the image model is a one-line edit, and neither is ever the
same object as the other.

Flexibility comes from **declarative config** (think ComfyUI's composability, as
files) rather than a node canvas — every model, character, and pipeline is a
validated YAML resource. A visual editor can sit on top of this later; the
engine doesn't need it.

## Architecture

```
configs/models.yaml ─┐
configs/characters/ ─┼─► Runner ─► Providers ─► backends
configs/pipelines/  ─┘            (text | image)
```

- **Providers** (`loom/providers/`) — one adapter per backend. `anthropic`
  (cloud LLM, chat) and `comfyui` (local Stable Diffusion, image) ship today;
  adding OpenAI or Automatic1111 is a single new file + a registry entry.
- **Models** (`configs/models.yaml`) — named provider instances. A pipeline
  references them by key.
- **Characters** (`configs/characters/*.yaml`) — persona only, **no model
  binding** (the decoupling, enforced by the schema).
- **Pipelines** (`configs/pipelines/*.yaml`) — ordered, typed steps with
  `{{ jinja }}` templates and optional `when:` guards. A `chat` step can emit
  structured output (e.g. "should I generate an image, and with what prompt?")
  that a later `image` step — running on a *different* model — consumes.

Every config is validated by Pydantic on load, including cross-checks that each
step targets a real model of the correct kind. Flexible, never silently broken.

## Story runtime

Stories use two authored cards: a Story card (world, locations, arcs, cast references) and
portable Character cards. Roleplay is preserved as raw turns, then consolidated into
evidence-citing residuals rather than rewriting source cards. Locations activate deterministically
from the Story card; `krea2_turbo_map` renders a reference-grounded map asset. See
[`loom/stories/CARD_NETWORK.md`](loom/stories/CARD_NETWORK.md).

## Quick start

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...        # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."

loom validate                              # load + cross-check all configs
loom models                                # list models, grouped by kind
loom run chat_with_optional_image -m "show me the rooftop gardens" -c aria
```

`loom validate` and `loom models` work with no API key and no ComfyUI running.
`loom run` needs an Anthropic key for the chat step; the image step additionally
needs a ComfyUI server (`base_url` in `models.yaml`) and a checkpoint set in
[`configs/comfy/sdxl_basic_api.json`](configs/comfy/sdxl_basic_api.json).

## Running it

- **`Start.bat`** (double-click) — sets up a Python venv, installs Loom, builds the
  Svelte UI, and serves everything at **http://127.0.0.1:8000**.
- **`Dev.bat`** — frontend hot-reload: FastAPI on `:8000` + Vite on **`:5173`**
  (proxies `/api`). Edit `frontend/` and changes reload live.

Architecture: **Python/FastAPI backend** (engine, providers, ComfyUI lifecycle,
connections) exposing `/api/*`, and a **SvelteKit SPA** frontend (`frontend/`).
In production FastAPI serves the built SPA; in dev Vite serves it. If Node isn't
installed, the server falls back to a bundled single-file page.

The existing application above is the current web runtime. A separate,
incremental desktop Story-only migration uses a local Bun/Oh My Pi sidecar and
Tauri IPC rather than a runtime HTTP listener; its scope and release gates are
in [`STORY_HOST_MIGRATION.md`](STORY_HOST_MIGRATION.md).

## Roadmap

- **Phase 1 (done)** — engine core: schemas, providers, pipeline runner, CLI.
- **Phase 2 (done)** — FastAPI server + connections (OpenRouter/OpenAI/Anthropic,
  SillyTavern-style connect flow), managed ComfyUI lifecycle.
- **Phase 3 (in progress)** — SvelteKit UI: tabbed sidebar (Connection / Characters
  / Image), searchable model combobox, **independent chat-model and image-model
  selection**, chat view.
- **Phase 4** — SQLite sessions/history, streamed replies, lorebooks,
  `automatic1111` provider, visual pipeline editor.
