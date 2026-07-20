# image-workflow-extras

Everything stripped out of the main app's image system when it was cut down to
just the krea2 pipeline + generation-time LoRA-stack presets. Kept here (not
deleted) in case any of it is wanted again later — RunPod serverless, the
complex-workflow graph editor/importer, GPU-placement auto-routing, and the
non-krea2 model families (anima, flux, wan, illustrious).

Paths mirror their original location in the main repo so a piece can be moved
back with a straight `git mv` (adjust import paths back to `loom.*`/`$lib/*`
since they currently live outside the package).

## Contents

- `loom/providers/runpod_serverless_provider.py`, `loom/runpod/`,
  `loom/server/routers/runpod.py`, `loom/server/services/render_stream.py`,
  `runpod_worker/` (the serverless worker Docker image), `configs/runpod_models.json`,
  `test_runpod.py`, `test_serverless.py`, `sync_check.py` — RunPod serverless
  inference: per-workflow cloud routing, network-volume sync, the worker image.
- `loom/comfy/hardware.py` — GPU probe + local/cloud placement heuristic
  (`/api/hardware`, dead in the UI even before this cut).
- `loom/comfy/workflow_check.py` — workflow classify/import/convert/missing-model-check
  machinery (`classify_workflow`, `ui_to_api`, `suggest_io`, `check_workflow`).
- `loom/server/routers/workflow_extras.py` — the endpoints removed from
  `loom/server/routers/workflow.py`: `/api/translate`, `/api/runpod-models`,
  `/api/wan/render`, `/api/workflow` (save), `/api/workflow/duplicate`,
  `/api/workflow/convert`, `/api/workflow/import`, `/api/workflow/check`.
  Not wired into any app — paste `register()`'s body back into `workflow.py`
  and re-add the router if restoring.
- `frontend/routes_images_graph/` (was `frontend/src/routes/images/graph/`) —
  the SvelteFlow node-graph workflow editor page.
- `frontend/routes_images_workflows/` (was `frontend/src/routes/images/workflows/`) —
  the import + test workflow tab.
- `frontend/lib/workflow_graph.js`, `frontend/lib/node_docs.js`,
  `frontend/lib/workflow/*.svelte`, `frontend/lib/components/image/WorkflowImport.svelte`,
  `WorkflowTester.svelte`, `ModelLibraryModal.svelte` — the graph editor's supporting
  components.
- `scripts/build_anima_workflows.py`, `scripts/flatten_anima_allinone.py`,
  `scripts/reconcile_workflow.py` — the anima workflow authoring toolchain
  (flatten a LiteGraph UI export → API format → reconcile against a live
  ComfyUI's `/object_info`).
- `workflows/anima_*`, `workflows/flux2_klein_api.json`, `workflows/wan_*_api.json`,
  `workflows/illustrious_v3_api.json` — the non-krea2 workflow graphs.
- `configs/image_presets_anima_backup.json` — the 8 baked "Style N" LoRA-stack
  presets that were auto-extracted from `anima_master_api.json` (the live
  `configs/image_presets.json` was trimmed to just the `none` floor).

## Not moved (kept in the main app)

- `loom/comfy/family.py`, `scan.py` — general model-file taxonomy/scan, still
  used for LoRA↔checkpoint compatibility matching. (`librarian.py` was later
  removed along with the LoRA-library UI; it is recoverable from git history.)
- `loom/comfy/stack.py`, `configs/image_presets.json`,
  `loom/server/services/image_presets.py`, `loom/server/routers/image_presets.py`,
  the `ImagePresetPicker` component — LoRA-stack image presets applied at
  generation time (portraits, story images), which never depended on any of the
  above. The standalone `library/image-presets` editor page was later removed
  with the Library section; the active preset is now picked from the ⚙ config
  modal.
