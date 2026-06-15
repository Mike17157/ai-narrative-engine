"""Config-file loaders and pure path/dict helpers — free functions taking `root: Path`.

Each loader reads/writes a JSON under `root/configs`. The *_DEFAULT consts live here
with their loaders. Bodies copied verbatim from app.py, with the create_app-closed
`root` made an explicit first parameter.
"""

from __future__ import annotations

import json
from pathlib import Path


# -- story builder -----------------------------------------------------------
STORY_BUILDER_DEFAULT = {"model": "", "models": {}, "inventions": {}, "systems": {}}


def load_story_builder(root: Path) -> dict:
    path = root / "configs" / "story_builder.json"
    cfg = dict(STORY_BUILDER_DEFAULT)
    if path.is_file():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return cfg


def _stage_model(cfg: dict, stage: str | None, override: str | None = None) -> str:
    """Resolve which text model drives a builder STAGE: an explicit override wins,
    then a per-stage choice (cfg.models[stage]), then the global builder model, then
    '' (= the active chat model)."""
    if override:
        return override
    per = (cfg.get("models") or {}).get(stage or "") if stage else ""
    return per or cfg.get("model") or ""


def _stage_invention(cfg: dict, stage: str | None) -> str:
    """The invention level configured for a STAGE (its own setting; default
    'balanced'). 'none' disables the directive for that stage."""
    return ((cfg.get("inventions") or {}).get(stage or "") if stage else "") or "balanced"


# -- per-role image-workflow overrides ---------------------------------------
def load_image_roles(root: Path) -> dict:
    path = root / "configs" / "image_roles.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


# -- promptgen ---------------------------------------------------------------
PROMPTGEN_DEFAULT = {
    "enabled": True,
    "model": "",  # text model key; empty = use the active chat model
    "system": (
        "You are an expert prompt writer for anime illustration models "
        "(Illustrious / SDXL, trained on Danbooru tags). You turn a described "
        "scene into a generation-ready prompt.\n\n"
        "Each prompt is lowercase, comma-separated Danbooru-style tags — never "
        "sentences, explanations, quotes, or markdown. Order tags by weight "
        "(earliest = strongest):\n"
        "1. subject & framing: count + shot type, e.g. \"1girl, solo, upper body\" / \"1boy, full body\"\n"
        "2. character: hair (length, color), eye color, distinctive features, expression, then clothing\n"
        "3. action / pose: what they're doing, gaze (\"looking at viewer\", \"from side\")\n"
        "4. setting: location + a few background elements\n"
        "5. lighting & mood: e.g. \"golden hour\", \"soft lighting\", \"rim light\", \"dramatic shadows\", \"depth of field\"\n"
        "6. quality tags last: \"masterpiece, best quality, highly detailed\"\n\n"
        "Keep each prompt ~15-30 concrete tags. Reflect the scene's emotion and "
        "time of day. Use any character appearance you're given; never substitute "
        "a different named character. Output only the prompt text."
    ),
}


def load_promptgen(root: Path) -> dict:
    path = root / "configs" / "promptgen.json"
    if path.is_file():
        return {**PROMPTGEN_DEFAULT, **json.loads(path.read_text(encoding="utf-8"))}
    return dict(PROMPTGEN_DEFAULT)


# -- chat model system prompt ------------------------------------------------
# A global system prompt for the chat model, layered on top of the selected
# character's own system. Empty by default (character governs entirely).
CHATGEN_DEFAULT = {"system": ""}


def load_chatgen(root: Path) -> dict:
    path = root / "configs" / "chatgen.json"
    if path.is_file():
        return {**CHATGEN_DEFAULT, **json.loads(path.read_text(encoding="utf-8"))}
    return dict(CHATGEN_DEFAULT)


# -- trainer dirs ------------------------------------------------------------
# NOTE: in app.py these two read the managed ComfyUI base dir via the state-bound
# _comfy_base_dir(); to keep this module free of server state, the resolved base
# dir is passed in by the caller (AppContext.comfy_base_dir()).
def _checkpoints_dir(cfg: dict, comfy_base_dir: Path | None) -> Path | None:
    if cfg.get("checkpoints_dir"):
        return Path(cfg["checkpoints_dir"])
    bd = comfy_base_dir
    return (bd / "models" / "checkpoints") if bd else None


def _loras_out_dir(cfg: dict, comfy_base_dir: Path | None, root: Path) -> Path:
    # Default to ComfyUI's loras folder so a trained LoRA is immediately
    # usable (shows up in Chain LoRA). Fall back to the project's loras/.
    if cfg.get("loras_dir"):
        return Path(cfg["loras_dir"])
    bd = comfy_base_dir
    return (bd / "models" / "loras") if bd else (root / "loras")


# -- comfy model folder layout ----------------------------------------------
def _model_kind_folders(kind: str) -> list[str]:
    if kind == "clip":
        return ["text_encoders", "clip"]
    if kind == "sam":
        return ["sams", "sam"]
    return {"checkpoint": ["checkpoints"], "diffusion": ["diffusion_models"], "vae": ["vae"],
            "lora": ["loras"], "controlnet": ["controlnet"], "upscale": ["upscale_models"],
            "ultralytics": ["ultralytics"], "style_models": ["style_models"],
            "ipadapter": ["ipadapter"], "gligen": ["gligen"]}.get(kind, [])
