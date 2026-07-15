"""Sample the local GPU once and decide where a workflow can run.

The companion to ``runpod/worker/adaptive_start.sh`` (which solves the *torch/CUDA*
axis on RunPod). This solves the *placement* axis locally: detect the GPU's VRAM,
estimate what a workflow needs from the on-disk size of the models it references,
and report whether it can run on this machine or must go to the cloud.

Nothing here is hardcoded per model — VRAM is probed at runtime and the per-workflow
need is derived from the actual model files, so a new workflow self-classifies.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

# VRAM headroom over raw weights: activations, the VAE decode, and CUDA/runtime
# overhead. Coarse on purpose — it only has to be good enough to split local vs cloud.
_HEADROOM_MULT = 1.3
_HEADROOM_BASE_GB = 1.5

# Only the weights resident DURING SAMPLING drive peak VRAM. ComfyUI runs the text
# encoder, frees it, then samples, then decodes with the VAE — so clip/vae/upscale
# files (often the biggest, e.g. an 11 GB umt5-xxl) must NOT be summed into the peak,
# or a 2.7 GB Wan render looks like it needs 13 GB. LoRAs merge into the model (small).
_RESIDENT_KINDS = {"checkpoint", "diffusion", "controlnet"}

_GPU_CACHE: dict[str, Any] = {}


def probe_gpu(refresh: bool = False) -> dict[str, Any]:
    """Sample the primary GPU → {name, vram_gb, compute_cap, source}. Cached after the
    first call (hardware doesn't change mid-run); pass refresh=True to re-sample."""
    if _GPU_CACHE and not refresh:
        return _GPU_CACHE["gpu"]
    info = _probe_smi() or _probe_torch() or {
        "name": None, "vram_gb": None, "compute_cap": None, "source": "none"}
    _GPU_CACHE["gpu"] = info
    return info


def _probe_smi() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        name, _, mib = r.stdout.strip().splitlines()[0].partition(",")
        vram_gb = round(float(mib.strip()) / 1024, 1) if mib.strip() else None
    except Exception:  # noqa: BLE001
        return None
    # compute_cap is a separate query — older nvidia-smi lacks the field, so don't let
    # it sink the whole probe.
    cap = None
    try:
        r2 = subprocess.run(
            [exe, "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        if r2.returncode == 0 and r2.stdout.strip():
            cap = r2.stdout.strip().splitlines()[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return {"name": name.strip(), "vram_gb": vram_gb, "compute_cap": cap, "source": "nvidia-smi"}


def _probe_torch() -> dict[str, Any] | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        p = torch.cuda.get_device_properties(0)
        return {"name": p.name, "vram_gb": round(p.total_memory / 1024 ** 3, 1),
                "compute_cap": f"{p.major}.{p.minor}", "source": "torch"}
    except Exception:  # noqa: BLE001
        return None


def estimate_vram_need(graph: dict, models_dir: Path | None) -> dict[str, Any]:
    """Estimate a workflow's peak VRAM from the on-disk size of the model files it
    references, and list any that aren't installed locally. Returns
    {weights_gb, est_vram_gb, missing}. ``missing`` being non-empty is a HARD signal
    the graph can't run on this machine (no files to load)."""
    from .workflow_check import _KIND_FOLDERS, model_refs

    seen: set[tuple] = set()
    weights_bytes = 0
    missing: list[str] = []
    for r in model_refs(graph or {}):
        key = (r["kind"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        rel = r["name"].replace("\\", "/")
        folders = _KIND_FOLDERS.get(r["kind"], [r["kind"]])
        path = next((models_dir / f / rel for f in folders
                     if models_dir and (models_dir / f / rel).is_file()), None) if models_dir else None
        if path is None:
            missing.append(rel)
            continue
        if r["kind"] in _RESIDENT_KINDS:   # only sampling-resident weights drive peak VRAM
            weights_bytes += path.stat().st_size

    weights_gb = weights_bytes / 1024 ** 3
    est_gb = round(weights_gb * _HEADROOM_MULT + _HEADROOM_BASE_GB, 1) if weights_gb else 0.0
    return {"weights_gb": round(weights_gb, 1), "est_vram_gb": est_gb, "missing": missing}


def classify_placement(graph: dict, models_dir: Path | None, vram_gb: float | None,
                       manual_cloud: bool = False) -> dict[str, Any]:
    """Where should this workflow run? Combines the GPU probe with the per-workflow
    estimate. Returns {placement, local_capable, reason, est_vram_gb, weights_gb, missing}.

      placement: 'cloud'      — user/preset flagged it cloud (manual override)
                 'cloud-only'  — can't run on this machine (missing files / VRAM / no GPU)
                 'either'      — fits locally; cloud is still available as an option
                 'local'       — fits locally and no cloud configured to compare against
    """
    est = estimate_vram_need(graph, models_dir)
    if est["missing"]:
        local_capable, reason = False, f"{len(est['missing'])} model file(s) not installed locally"
    elif not vram_gb:
        local_capable, reason = None, "no local GPU detected"
    elif est["est_vram_gb"] and est["est_vram_gb"] > vram_gb:
        local_capable, reason = False, f"needs ~{est['est_vram_gb']} GB VRAM, GPU has {vram_gb} GB"
    else:
        local_capable, reason = True, f"~{est['est_vram_gb']} GB fits the {vram_gb} GB GPU"

    if manual_cloud:
        placement = "cloud"
    elif local_capable is True:
        placement = "either"
    else:                       # False or None
        placement = "cloud-only"
    return {"placement": placement, "local_capable": local_capable, "reason": reason, **est}
