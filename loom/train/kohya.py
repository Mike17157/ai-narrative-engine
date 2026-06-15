"""Build kohya sd-scripts training commands + dataset config.

We keep this pure (no process/IO side effects beyond writing the TOML) so the
job runner and the endpoints stay thin and testable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Sensible SDXL LoRA defaults (Illustrious / Pony are SDXL checkpoints). The UI
# surfaces these and lets the user override per run.
DEFAULTS = {
    "sdxl": True,
    "resolution": 1024,
    "network_dim": 32,
    "network_alpha": 16,
    "learning_rate": 1e-4,
    "unet_lr": 1e-4,
    "text_encoder_lr": 5e-5,
    "lr_scheduler": "cosine",
    "train_batch_size": 2,
    "max_train_epochs": 10,
    "save_every_n_epochs": 1,
    "num_repeats": 10,
    "clip_skip": 2,        # SD1.5 only; ignored for SDXL
    "mixed_precision": "fp16",
    "optimizer": "AdamW8bit",
    "seed": 42,
}


def detect_cuda() -> dict:
    """Auto-pick the PyTorch CUDA build from the GPU's compute capability.
    Newer archs need newer CUDA — Blackwell (sm_120, RTX 50-series) has no
    kernels in cu124, which surfaces as 'no kernel image is available'."""
    import shutil
    import subprocess

    out: dict = {"gpu": None, "compute_cap": None, "cuda": "cu124"}
    if not shutil.which("nvidia-smi"):
        out["reason"] = "nvidia-smi not found — defaulting to cu124"
        return out
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        line = (r.stdout or "").strip().splitlines()[0]
        name, _, cap = line.partition(",")
        out["gpu"] = name.strip()
        out["compute_cap"] = float(cap.strip())
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"GPU probe failed ({exc}) — defaulting to cu124"
        return out

    cap = out["compute_cap"]
    out["cuda"] = "cu128" if cap >= 12.0 else "cu124" if cap >= 7.5 else "cu121"
    return out


def _default_python(d: Path) -> str | None:
    """The venv interpreter the setup script creates inside sd-scripts."""
    for c in (d / "venv" / "Scripts" / "python.exe", d / ".venv" / "Scripts" / "python.exe",
              d / "venv" / "bin" / "python", d / ".venv" / "bin" / "python"):
        if c.is_file():
            return str(c)
    return None


# Probe the venv interpreter for the things that actually break training:
# Python version, a CUDA torch, torchvision — and crucially that torch can
# really execute a kernel on this GPU (cu124 on a Blackwell card imports fine and
# reports cuda=True, but the first real op fails with "no kernel image").
_PROBE = """
import json, sys
o = {'py': '%d.%d.%d' % sys.version_info[:3]}
try:
    import torch
    o['torch'] = torch.__version__
    o['cuda'] = bool(torch.cuda.is_available())
    if o['cuda']:
        try:
            (torch.zeros(1, device='cuda') + 1).cpu()
            o['cuda_ok'] = True
        except Exception as e:
            o['cuda_ok'] = False
            o['cuda_run_err'] = str(e)[:200]
except Exception as e:
    o['torch_err'] = str(e)
try:
    import torchvision as tv
    o['torchvision'] = tv.__version__
except Exception as e:
    o['tv_err'] = str(e)
print(json.dumps(o))
"""


def probe_env(python: str, timeout: float = 120) -> dict:
    """Run the venv python and report its version + torch/torchvision/CUDA state."""
    try:
        r = subprocess.run([python, "-c", _PROBE], capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"probe_error": str(e)}
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {"probe_error": (r.stderr or "no probe output").strip()[:400]}


def trainer_status(sd_scripts_dir: str | None, python: str | None) -> dict:
    """Whether a usable kohya install is present. Drives the UI's setup banner.
    Goes beyond 'files exist' — it actually checks the venv can train (right
    Python, CUDA torch, torchvision), so we never green-light a broken venv."""
    out: dict = {"installed": False, "sd_scripts_dir": sd_scripts_dir or "",
                 "python": python or "", "issues": [], "env": {}}
    if not sd_scripts_dir:
        out["issues"].append("sd-scripts directory not set")
        return out
    d = Path(sd_scripts_dir)
    if not d.is_dir():
        out["issues"].append(f"directory not found: {d}")
        return out
    if not (d / "sdxl_train_network.py").is_file() and not (d / "train_network.py").is_file():
        out["issues"].append("train_network.py not found there — is this the kohya sd-scripts folder?")
        return out
    py = python or _default_python(d)
    out["python"] = py or ""
    if not py or not Path(py).exists():
        out["issues"].append("training venv python not found — run setup")
        return out

    env = probe_env(py)
    out["env"] = env
    pv = env.get("py", "")
    if env.get("probe_error"):
        out["issues"].append("venv python failed to run: " + env["probe_error"])
    else:
        if not pv.startswith(("3.10.", "3.11.")):
            out["issues"].append(f"venv Python is {pv} — kohya needs 3.10 or 3.11")
        if "torch" not in env:
            out["issues"].append("torch not installed in the venv"
                                 + (f" ({env['torch_err']})" if env.get("torch_err") else ""))
        elif not env.get("cuda"):
            out["issues"].append(f"torch {env['torch']} is CPU-only — no CUDA")
        elif env.get("cuda_ok") is False:
            out["issues"].append(
                "torch can't run on this GPU — wrong CUDA build for your card. "
                "Reinstall the trainer (auto-detect picks the right one).")
        if "torchvision" not in env:
            out["issues"].append("torchvision not installed")
    out["installed"] = not out["issues"]
    return out


def write_dataset_toml(path: str | Path, image_dir: str | Path, resolution: int,
                       batch_size: int, num_repeats: int) -> None:
    """kohya dataset config. Points at our flat datasets/<name> folder (NNN.png +
    NNN.txt captions) with a repeat count — no `<n>_concept` folder dance."""
    toml = (
        "[general]\n"
        'caption_extension = ".txt"\n'
        "shuffle_caption = false\n"
        "keep_tokens = 1\n\n"
        "[[datasets]]\n"
        f"resolution = {int(resolution)}\n"
        f"batch_size = {int(batch_size)}\n"
        "enable_bucket = true\n"
        "bucket_no_upscale = true\n\n"
        "  [[datasets.subsets]]\n"
        f'  image_dir = "{Path(image_dir).resolve().as_posix()}"\n'
        f"  num_repeats = {int(num_repeats)}\n"
    )
    Path(path).write_text(toml, encoding="utf-8")


def build_command(python: str, sd_scripts_dir: str | Path, checkpoint: str | Path,
                  dataset_toml: str | Path, output_dir: str | Path, output_name: str,
                  p: dict) -> list[str]:
    script = Path(sd_scripts_dir).resolve() / ("sdxl_train_network.py" if p.get("sdxl", True) else "train_network.py")
    # The trainer runs with cwd=sd-scripts, so every path we hand it must be
    # absolute — a relative dataset_config would resolve under sd-scripts and miss.
    cmd = [
        python, str(script),
        "--pretrained_model_name_or_path", str(Path(checkpoint).resolve()),
        "--dataset_config", str(Path(dataset_toml).resolve()),
        "--output_dir", str(Path(output_dir).resolve()),
        "--output_name", output_name,
        "--network_module", "networks.lora",
        "--network_dim", str(p["network_dim"]),
        "--network_alpha", str(p["network_alpha"]),
        "--learning_rate", str(p["learning_rate"]),
        "--unet_lr", str(p["unet_lr"]),
        "--text_encoder_lr", str(p["text_encoder_lr"]),
        "--lr_scheduler", str(p["lr_scheduler"]),
        "--train_batch_size", str(p["train_batch_size"]),
        "--max_train_epochs", str(p["max_train_epochs"]),
        "--save_every_n_epochs", str(p["save_every_n_epochs"]),
        "--mixed_precision", str(p["mixed_precision"]),
        "--save_precision", str(p["mixed_precision"]),
        "--optimizer_type", str(p["optimizer"]),
        "--save_model_as", "safetensors",
        "--seed", str(p["seed"]),
        "--cache_latents",
        "--gradient_checkpointing",
        "--sdpa",   # PyTorch-native attention — no xformers dependency
        "--max_data_loader_n_workers", "1",
    ]
    if not p.get("sdxl", True):
        cmd += ["--clip_skip", str(p["clip_skip"])]
    return cmd


def merge_params(body: dict) -> dict:
    """Overlay request values onto DEFAULTS, keeping types sane."""
    p = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in body and body[k] not in (None, ""):
            p[k] = body[k]
    return p
