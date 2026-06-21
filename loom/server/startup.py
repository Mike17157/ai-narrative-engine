"""Server startup hooks — called once from create_app() after routers are registered.

Each hook is isolated: a failure in one never blocks the others or the server boot.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Scan cache warm-up
# ---------------------------------------------------------------------------

def warm_scan_cache(ctx) -> None:
    """Pre-run the ComfyUI model scan so the first /api/comfy/models request
    is instant instead of hitting a cold filesystem walk."""
    try:
        from ..comfy.scan import cached_scan
        bd = ctx.comfy_base_dir()
        if not bd:
            return
        models_dir = bd / "models"
        if models_dir.is_dir():
            cached_scan(models_dir)
            log.info("startup: model scan cache warmed")
    except Exception:  # noqa: BLE001
        log.exception("startup: scan warm-up failed")


# ---------------------------------------------------------------------------
# 2. Stale job reaping
# ---------------------------------------------------------------------------

def reap_stale_jobs() -> None:
    """Mark any jobs stuck in 'running' state as 'error'.

    The REGISTRY is in-memory so it's always empty on a fresh boot; this is a
    safety net for if job state is ever persisted across restarts."""
    try:
        from .jobhub import REGISTRY
        reaped = 0
        for job in REGISTRY.all():
            if job.status == "running":
                job.status = "error"
                job._emit({"type": "done", "status": "error",
                           "message": "interrupted by server restart"})
                reaped += 1
        if reaped:
            log.warning("startup: reaped %d stale job(s)", reaped)
    except Exception:  # noqa: BLE001
        log.exception("startup: job reaping failed")


# ---------------------------------------------------------------------------
# 3. models_manifest.json regeneration
# ---------------------------------------------------------------------------

# Mirrors LOADER_INPUTS from runpod/worker/gen_worker.py — which folders each
# (class_type, input_key) pair belongs under on the volume.
_LOADER_INPUTS: dict[tuple[str, str], str] = {
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints",
    ("CheckpointLoader", "ckpt_name"): "checkpoints",
    ("UNETLoader", "unet_name"): "diffusion_models",
    ("UNet loader with Name (Image Saver)", "unet_name"): "diffusion_models",
    ("LoraLoader", "lora_name"): "loras",
    ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("VAELoader", "vae_name"): "vae",
    ("CLIPLoader", "clip_name"): "text_encoders",
    ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("DualCLIPLoader", "clip_name2"): "text_encoders",
    ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("ControlNetLoader", "control_net_name"): "controlnet",
    ("UpscaleModelLoader", "model_name"): "upscale_models",
    ("IPAdapterModelLoader", "ipadapter_file"): "ipadapter",
    ("SAMLoader", "model_name"): "sams",
    ("UltralyticsDetectorProvider", "model_name"): "ultralytics",
}
_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf", ".sft")
_PLACEHOLDER = "REPLACE_WITH"
_SUBDIR_TO_VOLUME = {
    "checkpoints": "models/checkpoints",
    "diffusion_models": "models/diffusion_models",
    "loras": "models/loras",
    "vae": "models/vae",
    "text_encoders": "models/text_encoders",
    "clip_vision": "models/clip_vision",
    "embeddings": "models/embeddings",
    "ipadapter": "models/ipadapter",
    "controlnet": "models/controlnet",
    "upscale_models": "models/upscale_models",
    "sams": "models/sams",
}


def _volume_key(subdir: str, name: str) -> str:
    prefix = _SUBDIR_TO_VOLUME.get(subdir, f"models/{subdir}")
    return f"{prefix}/{name}"


def regenerate_manifest(ctx) -> None:
    """Scan all workflow JSONs and rewrite runpod/worker/models_manifest.json
    with every model that is both referenced by a workflow AND present locally.

    This keeps the manifest accurate as workflows and local files evolve, so
    ``upload_models.py --check`` always reflects the current state."""
    try:
        workflows_dir = ctx.root / "workflows"
        manifest_path = ctx.root / "runpod" / "worker" / "models_manifest.json"
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None

        if not workflows_dir.is_dir() or not manifest_path.parent.is_dir():
            return
        if not models_dir or not models_dir.is_dir():
            return

        model_refs: dict[str, str] = {}  # forward-slash name -> subdir
        for wf in sorted(workflows_dir.glob("*.json")):
            try:
                graph = json.loads(wf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for node in graph.values():
                if not isinstance(node, dict):
                    continue
                ct = node.get("class_type") or ""
                for k, v in (node.get("inputs") or {}).items():
                    if not (isinstance(v, str) and v.lower().endswith(_MODEL_EXTS)):
                        continue
                    if _PLACEHOLDER in v:
                        continue
                    subdir = _LOADER_INPUTS.get((ct, k))
                    if subdir:
                        model_refs[v.replace("\\", "/")] = subdir

        manifest = []
        for name, subdir in sorted(model_refs.items()):
            local = models_dir / subdir / Path(*name.split("/"))
            if local.is_file():
                manifest.append({
                    "local": f"{subdir}/{name}",
                    "key": _volume_key(subdir, name),
                    "bytes": local.stat().st_size,
                })

        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("startup: manifest regenerated — %d model(s)", len(manifest))
    except Exception:  # noqa: BLE001
        log.exception("startup: manifest regeneration failed")


# ---------------------------------------------------------------------------
# 4. LoRA stack validation
# ---------------------------------------------------------------------------

def validate_lora_stacks(ctx) -> None:
    """Warn about any LoRA library entries whose model file doesn't exist locally.

    Catches breakage early (e.g. after a librarian reorganize or a file delete)
    so the user knows which stacks will fail before they try to render."""
    try:
        bd = ctx.comfy_base_dir()
        loras_dir = (bd / "models" / "loras") if bd else None
        if not loras_dir or not loras_dir.is_dir():
            return

        library = getattr(ctx.base_settings.loras, "library", []) or []
        missing = []
        for entry in library:
            name = getattr(entry, "name", None) or ""
            if not name:
                continue
            local = loras_dir / Path(*name.replace("\\", "/").split("/"))
            if not local.exists():
                missing.append(name)

        if missing:
            log.warning(
                "startup: %d LoRA(s) in library not found on disk:\n%s",
                len(missing),
                "\n".join(f"  - {m}" for m in missing),
            )
        else:
            log.info("startup: all %d LoRA library entries resolved", len(library))
    except Exception:  # noqa: BLE001
        log.exception("startup: LoRA stack validation failed")
