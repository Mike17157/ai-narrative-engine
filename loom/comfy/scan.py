"""Introspect a ComfyUI model tree into a typed index.

Folder names lie (an Illustrious LoRA in a 'Pony' folder, a DiT in
'diffusion_models'), so we classify each file by its actual tensor *signature* —
read the cheap safetensors header (8-byte length + JSON, no weights) and match
key patterns. Every model gets a `kind` (what it is) and `arch` (which family it
belongs to), which is what lets the rest of the app pair compatible pieces
(this UNet needs that CLIP + that VAE) instead of hoping folder names line up.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

from .family import family_of

# subfolder -> the kind we trust when the header is ambiguous (CLIP/VAE/etc.
# files don't always have decisive keys, but their folder is authoritative).
_FOLDERS = {
    "checkpoints": "checkpoint",
    "diffusion_models": "diffusion",
    "unet": "diffusion",
    "loras": "lora",
    "vae": "vae",
    "clip": "clip",
    "text_encoders": "clip",
    "clip_vision": "clip_vision",
    "controlnet": "controlnet",
    "upscale_models": "upscale",
    "embeddings": "embedding",
}
_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


def _header_keys(path: str) -> list[str] | None:
    """Tensor names from a safetensors header — cheap, no weights read."""
    with open(path, "rb") as f:
        raw = f.read(8)
        if len(raw) < 8:
            return None
        n = struct.unpack("<Q", raw)[0]
        if n <= 0 or n > 80_000_000:
            return None
        hdr = json.loads(f.read(n))
    return [k for k in hdr if k != "__metadata__"]


def _arch_from_diffusion(has) -> str:
    if has("double_blocks") and has("single_blocks"):
        return "flux"
    if has("input_blocks"):  # U-Net (SD family)
        return "sdxl" if (has("add_embedding") or has("label_emb")) else "sd15"
    if has("adaln") or (has(".blocks.") and has("cross_attn")):
        return "dit"  # transformer (PixArt / Lumina / Anima-style)
    return "unknown"


def classify(path: str) -> dict:
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    info = {"name": name, "kind": "unknown", "arch": "", "size": 0}
    try:
        info["size"] = os.path.getsize(path)
    except OSError:
        pass

    if ext == ".gguf":
        info["kind"] = "clip"  # quantised text encoders; refined by folder later
        info["arch"] = "gguf"
        return info
    if ext not in (".safetensors",):
        info["kind"] = "weights"  # .ckpt/.pt/.pth/.bin — can't read header cheaply
        return info

    try:
        keys = _header_keys(path)
    except Exception:  # noqa: BLE001
        return info
    if not keys:
        return info

    def has(sub: str) -> bool:
        return any(sub in k for k in keys)

    # LoRA / LyCORIS
    if has("lora_down") or has("lora_up") or has(".lora_A") or has(".lora_B") \
            or has("lora_unet") or has("lora_te") or has("lokr_") or has("hada_"):
        info["kind"] = "lora"
        info["arch"] = ("flux" if has("double_blocks")
                        else "sdxl" if (has("input_blocks") or has("add_embedding"))
                        else "dit" if has("adaln")
                        else "sd")
        return info

    has_diff = has("input_blocks") or has("double_blocks") or has("diffusion_model") or has("adaln")
    has_clip = has("text_model") or has("cond_stage_model") or has("conditioner.embedders") or has("text_projection")
    is_vae_only = (has("decoder.conv_in") or has("decoder.up")) and \
                  (has("encoder.down") or has("encoder.conv_in")) and not has_diff and not has_clip
    is_clip_only = (has("text_model") or has("encoder.block") or has("model.layers")) and not has_diff and not is_vae_only

    if is_vae_only:
        info["kind"] = "vae"
        return info
    if is_clip_only:
        info["kind"] = "clip"
        return info
    if has_diff:
        info["arch"] = _arch_from_diffusion(has)
        # all-in-one checkpoint (bundles CLIP/VAE) vs diffusion-only weights
        info["kind"] = "checkpoint" if (has_clip or has("first_stage_model")) else "diffusion"
        return info
    return info


_scan_cache: dict | None = None
_scan_cache_root: str | None = None


def cached_scan(models_dir: str | Path) -> dict:
    """Return a cached scan result; re-scans only when models_dir changes or the
    cache has been invalidated (e.g. after a smart-upload or librarian move)."""
    global _scan_cache, _scan_cache_root
    key = str(models_dir)
    if _scan_cache is None or _scan_cache_root != key:
        _scan_cache = scan_models(models_dir)
        _scan_cache_root = key
    return _scan_cache


def invalidate_scan_cache() -> None:
    global _scan_cache
    _scan_cache = None


def scan_models(models_dir: str | Path) -> dict:
    """Walk the ComfyUI models tree → a typed index grouped by kind, plus an
    arch summary for the diffusion models/checkpoints (what you'd build a
    workflow around)."""
    base = Path(models_dir)
    items: list[dict] = []
    for sub, folder_kind in _FOLDERS.items():
        d = base / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _EXTS:
                continue
            info = classify(str(p))
            info["folder"] = sub
            info["rel"] = str(p.relative_to(d)).replace("\\", "/")
            info["arch_dir"] = info["rel"].split("/")[0] if "/" in info["rel"] else "(root)"
            # Folder is authoritative for support-model kinds the header can't pin down.
            if info["kind"] in ("unknown", "weights", "clip") and folder_kind in (
                    "vae", "clip", "clip_vision", "controlnet", "upscale", "embedding"):
                info["kind"] = folder_kind
            # Sub-architecture FAMILY (illustrious/pony/anima/…) layered on arch, for generatable
            # weights + loras. Folder + arch here; overrides/civitai applied at the API boundary.
            if info["kind"] in ("checkpoint", "diffusion", "lora"):
                info["family"] = family_of(info["rel"], arch=info.get("arch") or None)
            items.append(info)

    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(it["kind"], []).append(it)

    # what you generate with: diffusion models + checkpoints, grouped by arch
    bases = [it for it in items if it["kind"] in ("checkpoint", "diffusion")]
    by_arch: dict[str, dict] = {}
    for it in bases:
        a = by_arch.setdefault(it["arch"] or "unknown", {"checkpoint": [], "diffusion": []})
        a[it["kind"]].append(it["rel"])

    # sub-family containers: how many generatable bases + loras fall in each family
    by_family: dict[str, dict] = {}
    for it in items:
        if it.get("family"):
            fam = by_family.setdefault(it["family"], {"base": 0, "lora": 0})
            fam["lora" if it["kind"] == "lora" else "base"] += 1

    return {"items": items, "by_kind": {k: v for k, v in by_kind.items()},
            "by_arch": by_arch, "by_family": by_family,
            "counts": {k: len(v) for k, v in by_kind.items()}}
