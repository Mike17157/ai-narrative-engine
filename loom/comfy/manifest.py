"""Extract the model files a ComfyUI workflow depends on.

Given an API-format workflow, this finds every weight it references (checkpoint,
LoRAs, VAE, upscaler, text encoder, detectors, SAM, ...), maps each to the
ComfyUI `models/` subfolder it lives in, and resolves it against a real ComfyUI
models root to record size + presence.

The result is a manifest: the authoritative "what does this workflow need"
list. It drives the models/ folder — whether you copy, symlink, or just keep a
record of what ComfyUI must have installed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MODEL_EXTS = (".safetensors", ".pt", ".pth", ".ckpt", ".bin", ".onnx", ".gguf", ".sft")

# class_type -> (input key holding the filename, ComfyUI models subfolder).
# Covers the loaders used across common SDXL/Illustrious/Flux graphs.
CLASS_LOADERS: dict[str, tuple[str, str]] = {
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoints"),
    "CheckpointLoader": ("ckpt_name", "checkpoints"),
    "unCLIPCheckpointLoader": ("ckpt_name", "checkpoints"),
    "LoraLoader": ("lora_name", "loras"),
    "LoraLoaderModelOnly": ("lora_name", "loras"),
    "VAELoader": ("vae_name", "vae"),
    "UpscaleModelLoader": ("model_name", "upscale_models"),
    "CLIPLoader": ("clip_name", "text_encoders"),
    "UNETLoader": ("unet_name", "diffusion_models"),
    # Image Saver's UNet variant also emits the filename; reads the same input.
    "UNet loader with Name (Image Saver)": ("unet_name", "diffusion_models"),
    "ControlNetLoader": ("control_net_name", "controlnet"),
    "UltralyticsDetectorProvider": ("model_name", "ultralytics"),
    "SAMLoader": ("model_name", "sams"),
    "StyleModelLoader": ("style_model_name", "style_models"),
    "GLIGENLoader": ("gligen_name", "gligen"),
}

# Some loaders read from more than one folder depending on the install layout.
FOLDER_ALTERNATES: dict[str, tuple[str, ...]] = {
    "text_encoders": ("text_encoders", "clip"),
    "clip": ("clip", "text_encoders"),
    "diffusion_models": ("diffusion_models", "unet"),
}


@dataclass
class ModelRef:
    category: str          # ComfyUI models subfolder, e.g. "loras"
    name: str              # value as written in the workflow (may include subdirs)
    comfy_relative: str    # posix path under models/, e.g. "loras/illustriousXL/x.safetensors"
    class_type: str
    node: str
    source: str | None = None       # resolved absolute path in the ComfyUI models root
    exists: bool = False
    size_bytes: int = 0


def _norm(value: str) -> str:
    """Workflow paths use Windows separators; normalise to posix for the manifest."""
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def scan_workflow(workflow: dict[str, Any]) -> list[ModelRef]:
    """Return the model references in an API-format workflow, de-duplicated."""
    found: dict[tuple[str, str], ModelRef] = {}

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}

        captured_keys: set[str] = set()
        loader = CLASS_LOADERS.get(class_type)
        if loader:
            key, folder = loader
            value = inputs.get(key)
            if isinstance(value, str) and value:
                rel = f"{folder}/{_norm(value)}"
                found.setdefault((folder, rel), ModelRef(folder, _norm(value), rel, class_type, node_id))
                captured_keys.add(key)

        # Generic fallback: any string input that looks like a weight file but
        # wasn't captured by a known loader. Category is unknown ("?") — surfaced
        # so nothing silently slips through when a new custom node appears.
        for key, value in inputs.items():
            if key in captured_keys:
                continue
            if isinstance(value, str) and value.lower().endswith(MODEL_EXTS):
                rel = _norm(value)
                found.setdefault(("?", rel), ModelRef("?", rel, rel, class_type, node_id))

    return list(found.values())


def _resolve(ref: ModelRef, models_root: Path) -> None:
    """Fill in source/exists/size by locating the file under the ComfyUI root."""
    candidates: list[Path] = []
    folders = FOLDER_ALTERNATES.get(ref.category, (ref.category,)) if ref.category != "?" else ()
    for folder in folders:
        # ref.comfy_relative starts with the primary category; swap it for alts.
        tail = ref.comfy_relative.split("/", 1)[1] if "/" in ref.comfy_relative else ref.comfy_relative
        candidates.append(models_root / folder / tail)
    if ref.category == "?":
        candidates.append(models_root / ref.comfy_relative)

    for path in candidates:
        if path.is_file():
            ref.source = str(path)
            ref.exists = True
            ref.size_bytes = path.stat().st_size
            return


def build_manifest(workflow: dict[str, Any], models_root: str | Path) -> list[ModelRef]:
    """Scan a workflow and resolve every reference against a ComfyUI models root."""
    models_root = Path(models_root)
    refs = scan_workflow(workflow)
    for ref in refs:
        _resolve(ref, models_root)
    refs.sort(key=lambda r: (r.category, r.comfy_relative))
    return refs


def manifest_to_dict(refs: list[ModelRef]) -> dict[str, Any]:
    total = sum(r.size_bytes for r in refs)
    return {
        "models": [asdict(r) for r in refs],
        "summary": {
            "count": len(refs),
            "present": sum(1 for r in refs if r.exists),
            "missing": sum(1 for r in refs if not r.exists),
            "total_bytes": total,
            "total_gb": round(total / 1024**3, 2),
        },
    }
