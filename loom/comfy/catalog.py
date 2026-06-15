"""Browse + install models from ComfyUI Manager's local catalog.

ComfyUI Manager caches a curated model list (URLs + correct install folders) under
``user/__manager/cache/*model-list*.json``. We read that — no internet search — and
let the user install the auxiliary models workflows need (CLIP, VAE, SAM, the
Ultralytics bbox/segm detectors, upscalers, …) straight into the right folder.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

# Default destination per catalog `type` when an entry's save_path is "default".
# Explicit save_paths (e.g. "ultralytics/bbox", "sams") are used as-is.
_TYPE_DIR = {
    "checkpoint": "checkpoints", "unclip": "checkpoints", "diffusion_model": "diffusion_models",
    "lora": "loras", "motion lora": "loras", "VAE": "vae", "vae": "vae", "TAESD": "vae_approx",
    "clip": "text_encoders", "clip_vision": "clip_vision", "controlnet": "controlnet",
    "T2I-Adapter": "controlnet", "T2I-Style": "style_models", "gligen": "gligen",
    "upscale": "upscale_models", "embedding": "embeddings",
    "sam": "sams", "sam2": "sams", "sam2.1": "sams", "Ultralytics": "ultralytics",
    "IP-Adapter": "ipadapter", "GroundingDINO": "grounding-dino", "insightface": "insightface",
    "GFPGAN": "facerestore_models", "CodeFormer": "facerestore_models", "face_restore": "facerestore_models",
}


def _catalog_file(comfy_base: Path) -> Path | None:
    cache = comfy_base / "user" / "__manager" / "cache"
    hits = sorted(glob.glob(str(cache / "*model-list*.json")), key=os.path.getmtime, reverse=True)
    return Path(hits[0]) if hits else None


def _target_rel(m: dict) -> str:
    """Folder-relative install path (within ComfyUI/models) for a catalog entry."""
    fn = m.get("filename") or os.path.basename(m.get("url", "") or "")
    sp = (m.get("save_path") or "default").strip().strip("/")
    folder = sp if sp and sp != "default" else _TYPE_DIR.get(m.get("type", ""), m.get("type", "") or "misc")
    return f"{folder}/{fn}" if fn else folder


def read_catalog(comfy_base: Path, models_dir: Path) -> dict:
    """Parse the cached catalog → entries tagged with their install path and
    whether the file already exists. Returns {entries, types, count}."""
    cf = _catalog_file(comfy_base)
    if not cf or not cf.is_file():
        return {"entries": [], "types": [], "count": 0, "error": "ComfyUI Manager catalog not found"}
    doc = json.loads(cf.read_text(encoding="utf-8"))
    raw = doc.get("models", doc if isinstance(doc, list) else [])

    entries, types = [], set()
    for m in raw:
        url = m.get("url")
        if not url:
            continue
        rel = _target_rel(m)
        types.add(m.get("type", "?"))
        entries.append({
            "name": m.get("name", os.path.basename(rel)),
            "type": m.get("type", "?"),
            "base": m.get("base", ""),
            "description": (m.get("description", "") or "")[:300],
            "filename": os.path.basename(rel),
            "rel": rel,
            "url": url,
            "size": m.get("size", ""),
            "installed": (models_dir / rel).is_file(),
        })
    return {"entries": entries, "types": sorted(types), "count": len(entries)}


def install_target(models_dir: Path, rel: str) -> Path | None:
    """Resolve + safety-check an install path; None if it escapes models_dir."""
    target = (models_dir / rel.replace("\\", "/")).resolve()
    base = models_dir.resolve()
    if base != target and base not in target.parents:
        return None
    return target
