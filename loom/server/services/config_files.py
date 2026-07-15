"""Config-file loaders and pure path/dict helpers — free functions taking `root: Path`.

Each loader reads/writes a JSON under `root/configs`. The *_DEFAULT consts live here
with their loaders. Bodies copied verbatim from app.py, with the create_app-closed
`root` made an explicit first parameter.
"""

from __future__ import annotations

import json
from pathlib import Path


# -- app-wide flags ----------------------------------------------------------
# Small global switches (not per-config), stored in configs/app.json:
#   allow_nsfw    — content gate: when False, nsfw-rated lorebooks are excluded from retrieval.
#   img_detailer  — run the ADetailer (face/detail) pass on renders (the usual need).
#   img_upscale   — run the heavy 4K upscale chain (USDU + hi-res). Off by default — most
#                   renders don't need it.
APP_FLAGS_DEFAULT = {"allow_nsfw": True, "img_detailer": True, "img_upscale": False}
_BOOL_FLAGS = tuple(APP_FLAGS_DEFAULT)

# Local text inference is no longer a special endpoint here — it's a first-class Ollama
# CONNECTION (auto-seeded as `ollama-local`, see connections.py). A preset picks it like any
# other connection. The old configs/app.json → local_model block is dropped on next save.


def load_app_flags(root: Path) -> dict:
    path = root / "configs" / "app.json"
    cfg = dict(APP_FLAGS_DEFAULT)
    if path.is_file():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")) or {})
        except (ValueError, OSError):
            pass
    for k in _BOOL_FLAGS:
        cfg[k] = bool(cfg.get(k, APP_FLAGS_DEFAULT[k]))
    cfg.pop("local_model", None)        # legacy field — retired in favour of the Ollama connection
    return cfg


def save_app_flags(root: Path, data: dict) -> dict:
    cfg = load_app_flags(root)
    for k in _BOOL_FLAGS:
        if (data or {}).get(k) is not None:
            cfg[k] = bool(data[k])
    path = root / "configs" / "app.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


# -- story builder -----------------------------------------------------------
STORY_BUILDER_DEFAULT = {"model": "", "models": {}, "systems": {}, "inventions": {}}


def _story_builder_raw(root: Path) -> dict:
    path = root / "configs" / "story_builder.json"
    cfg = dict(STORY_BUILDER_DEFAULT)
    if path.is_file():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return cfg


def load_story_builder(root: Path) -> dict:
    """The story-builder config (per-stage model/system/invention), with the unified
    script-bound chat configs overlaid on top — so editing a stage's config in the ⚙
    modal drives the pipeline, while configs/story_builder.json remains the fallback.
    A stage config's attached lorebooks are folded into that stage's system prompt."""
    return _story_builder_raw(root)


def _stage_model(cfg: dict, stage: str | None, override: str | None = None) -> str:
    """Resolve which text model drives a builder STAGE: an explicit override wins,
    then a per-stage choice (cfg.models[stage]), then the global builder model, then
    '' (= the active chat model)."""
    if override:
        return override
    per = (cfg.get("models") or {}).get(stage or "") if stage else ""
    return per or cfg.get("model") or ""


# -- per-role image-workflow overrides ---------------------------------------
def _image_roles_raw(root: Path) -> dict:
    path = root / "configs" / "image_roles.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def load_image_roles(root: Path) -> dict:
    """Per-role image workflow overrides (configs/image_roles.json). Roles are pipeline
    internals (base/sprite/scene); chat-surface renders take the active preset's
    image_workflow instead (see context.role_image_provider)."""
    return _image_roles_raw(root)


# -- text-model roles --------------------------------------------------------
# Which connection/model id plays each text role, and the refusal fallback. Empty
# string = "use the active text connection". Powers the world-state engine's
# primary→fallback escalation (e.g. local MeroMero → DeepSeek V3.2).
TEXT_ROLES_DEFAULT = {"narrator": "", "scribe": "", "fallback": ""}


def load_text_roles(root: Path) -> dict:
    path = root / "configs" / "text_roles.json"
    if path.is_file():
        try:
            return {**TEXT_ROLES_DEFAULT, **(json.loads(path.read_text(encoding="utf-8")) or {})}
        except (ValueError, OSError):
            return dict(TEXT_ROLES_DEFAULT)
    return dict(TEXT_ROLES_DEFAULT)


def save_text_roles(root: Path, data: dict) -> dict:
    path = root / "configs" / "text_roles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**TEXT_ROLES_DEFAULT, **(data or {})}
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


# -- model/LoRA family overrides ---------------------------------------------
# Manual `{ "<lora-or-checkpoint-rel-or-workflow-key>": "<family-id>" }` overrides layered on top of
# the folder/arch family resolution (and civitai enrichment). See loom/comfy/family.py.
def load_families(root: Path) -> dict:
    path = root / "configs" / "families.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def save_families(root: Path, data: dict) -> None:
    path = root / "configs" / "families.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {}, indent=2, ensure_ascii=False), encoding="utf-8")


# -- emotion shot-geometry overrides -----------------------------------------
# `{ emotion_key: {framing?, aspect?} }` overrides merged on top of poses.geometry_default(). Body
# language is per-character (manifest pose_prompts), so this file is geometry only. See poses.py.
def load_poses(root: Path) -> dict:
    path = root / "configs" / "poses.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def save_poses(root: Path, data: dict) -> None:
    path = root / "configs" / "poses.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {}, indent=2, ensure_ascii=False), encoding="utf-8")


# -- curated body-language pose library --------------------------------------
# A palette of real Danbooru pose tags grouped by body facet that GENERATED poses pick from
# (see services/pose_library.py). The shipped configs/pose_library.json is the editable source
# of truth; absent it, pose_library.POSE_LIBRARY is the built-in default.
def load_pose_library(root: Path) -> dict:
    from .pose_library import POSE_LIBRARY
    path = root / "configs" / "pose_library.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except (ValueError, OSError):
            pass
    return POSE_LIBRARY


# -- chat model system prompt ------------------------------------------------
# A global system prompt for the chat model, layered on top of the selected
# character's own system. Empty by default (character governs entirely).
CHATGEN_DEFAULT = {"system": ""}


def load_chatgen(root: Path) -> dict:
    path = root / "configs" / "chatgen.json"
    cfg = dict(CHATGEN_DEFAULT)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


# -- address mode + inference params -----------------------------------------
# NOTE: the parallel chat_configs.json / SCRIPTS overlay system was removed in the
# preset-unification overhaul — presets are the single chat primitive, and pipeline
# stage/prompt config lives directly in story_builder.json / chatgen.json /
# promptgen.json / image_roles.json (read raw above). Only these pure helpers remain.
def address_mode(cfg: dict) -> str:
    """Resolve a config's effective address mode. Explicit 'roleplay'/'assist' wins;
    otherwise script-bound configs assist the writer and free chat roleplays."""
    m = (cfg or {}).get("mode") or ""
    if m in ("roleplay", "assist"):
        return m
    return "assist" if (cfg or {}).get("script") else "roleplay"

# Inference controls a config may set (subset; key → coercion). Only the ones a user
# actually sets are stored and forwarded to the provider; the rest use model defaults.
_PARAM_SPEC = {"temperature": float, "top_p": float, "top_k": int, "top_a": float, "min_p": float,
               "max_tokens": int, "frequency_penalty": float, "presence_penalty": float,
               "repetition_penalty": float, "seed": int}


def _clean_params(raw: dict) -> dict:
    out: dict = {}
    for k, cast in _PARAM_SPEC.items():
        v = (raw or {}).get(k)
        if v is None or v == "":
            continue
        try:
            out[k] = cast(v)
        except (TypeError, ValueError):
            pass
    return out

def stage_params(cfg: dict, stage: str | None) -> dict:
    """The inference params for a builder stage (from its overlaid script config)."""
    return (cfg.get("params") or {}).get(stage or "") or {}


# -- image-prompt generator config ------------------------------------------
PROMPTGEN_DEFAULT: dict = {"enabled": True, "model": "", "system": ""}


def load_promptgen(root: Path) -> dict:
    path = root / "configs" / "promptgen.json"
    cfg = dict(PROMPTGEN_DEFAULT)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


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


# -- trainer + captioner config (shared by the comfy / lora / trainer routers) --
TRAINER_DEFAULT = {"sd_scripts_dir": "", "python": "", "checkpoints_dir": "", "loras_dir": ""}


def load_trainer(root: Path) -> dict:
    path = root / "configs" / "trainer.json"
    cfg = dict(TRAINER_DEFAULT)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


CAPTIONER_DEFAULT = {
    "enabled": True,
    "model": "",  # an OpenRouter vision-capable model id
    "system": (
        "You are an expert anime image tagger writing captions to train a LoRA for an "
        "Illustrious-based SDXL model, which understands Danbooru tags. Caption the image "
        "as ONE line of lowercase, comma-separated booru tags. Use spaces inside multi-word "
        "tags (e.g. \"long hair\", \"looking at viewer\").\n\n"
        "Tag only what is clearly visible, roughly in this order:\n"
        "1. count/subject: 1girl, 1boy, 2girls, solo, multiple girls…\n"
        "2. character name as a booru tag ONLY if you clearly recognize them "
        "(e.g. \"hatsune miku\"); otherwise omit the name.\n"
        "3. appearance: hair colour, length and style (e.g. \"aqua hair\", \"long hair\", "
        "\"twintails\"), eye colour, notable body features.\n"
        "4. clothing and accessories (e.g. \"school uniform\", \"detached sleeves\", \"thighhighs\").\n"
        "5. expression, then pose/action (e.g. \"smile\", \"looking at viewer\", \"arms up\", \"sitting\").\n"
        "6. setting/background (e.g. \"classroom\", \"night\", \"cherry blossoms\", \"simple background\").\n"
        "7. framing/camera: portrait, upper body, cowboy shot, full body, and angle tags like "
        "\"from above\", \"from side\", \"dutch angle\" when clear.\n\n"
        "Critical for a STYLE LoRA: tag only the CONTENT. Do NOT tag the art style, shading, "
        "line art, colour palette, level of detail, medium, \"anime\", \"illustration\", or any "
        "quality words (masterpiece, best quality, highres…). Whatever you tag is treated as "
        "already-known and is NOT absorbed into the LoRA — leaving the style untagged is exactly "
        "how the LoRA learns it.\n\n"
        "Be specific and accurate to THIS image; never invent details you can't see. No artist "
        "names, no full sentences, no trailing period. Output only the tags on one line."
    ),
}


def load_captioner(root: Path) -> dict:
    path = root / "configs" / "captioner.json"
    cfg = dict(CAPTIONER_DEFAULT)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


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
