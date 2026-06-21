"""Config-file loaders and pure path/dict helpers — free functions taking `root: Path`.

Each loader reads/writes a JSON under `root/configs`. The *_DEFAULT consts live here
with their loaders. Bodies copied verbatim from app.py, with the create_app-closed
`root` made an explicit first parameter.
"""

from __future__ import annotations

import json
from pathlib import Path


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
    cfg = _story_builder_raw(root)
    _overlay_story_builder(cfg, root)
    return cfg


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
    """Per-role image workflow overrides, with the unified `image:<role>` script configs
    overlaid on top (the ⚙ modal edits those; image_roles.json is the fallback)."""
    cfg = _image_roles_raw(root)
    for role in ("base", "style", "sprite", "scene", "chat"):
        c = script_config(root, f"image:{role}")
        wf = (c or {}).get("workflow") if c else None
        if wf:
            existing = cfg.get(role)
            if isinstance(existing, dict):
                cfg[role] = {**existing, "model": wf}
            else:
                cfg[role] = wf
    return cfg


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
    c = script_config(root, "chatgen")
    if c and c.get("system"):
        cfg["system"] = c["system"]
    return cfg


# -- named chat configs ------------------------------------------------------
# A library of reusable chat "configurations" — each bundles a text model, a
# system prompt, default attached lorebooks, and a creativity/invention level.
# One is active at a time; the main chat (and any chat surface) reads the active
# config as its default. Edited at the point of use via the ⚙ config modal, not
# a settings page. Stored in configs/chat_configs.json.
def _default_chat_config() -> dict:
    # `script` binds a config to a pipeline action ('' = free chat). `workflow` is the
    # ComfyUI workflow for image-role scripts (text scripts/chat use `model` instead).
    # `params` holds inference controls (temperature, top_p, …) — see _PARAM_SPEC.
    return {"id": "default", "name": "Default", "model": "", "system": "",
            "lorebooks": [], "params": {}, "script": "", "workflow": ""}


CHAT_CONFIGS_DEFAULT = {"active": "default", "configs": [_default_chat_config()]}

# The editable fields of a single config (stray UI keys are dropped on save).
_CHAT_CONFIG_FIELDS = ("id", "name", "model", "system", "lorebooks", "params",
                       "script", "workflow")

# Inference controls a config may set (subset; key → coercion). Only the ones a user
# actually sets are stored and forwarded to the provider; the rest use model defaults.
_PARAM_SPEC = {"temperature": float, "top_p": float, "top_k": int, "max_tokens": int,
               "frequency_penalty": float, "presence_penalty": float,
               "repetition_penalty": float, "min_p": float}


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

# Every config is a "chat with rules": a model + a system prompt + lorebooks + a SCRIPT
# (the pipeline action whose rules it encodes). These are the built-in scripts — the old
# settings/story-gen stages, now first-class configs. `kind` decides the editor: 'text'
# scripts (+ free chat) pick a text model; 'image' scripts pick a ComfyUI workflow.
SCRIPTS = [
    {"id": "", "label": "Free chat", "group": "Chat", "kind": "text"},
    {"id": "storyboard", "label": "Storyboard", "group": "Story builder", "kind": "text"},
    {"id": "locations", "label": "Locations", "group": "Story builder", "kind": "text"},
    {"id": "characters", "label": "Characters", "group": "Story builder", "kind": "text"},
    {"id": "wardrobe", "label": "Wardrobe", "group": "Story builder", "kind": "text"},
    {"id": "base_image", "label": "Base image", "group": "Story builder", "kind": "text"},
    {"id": "chatgen", "label": "Chat prompt", "group": "Prompts", "kind": "text"},
    {"id": "promptgen", "label": "Tag prompt", "group": "Prompts", "kind": "text"},
    {"id": "image:base", "label": "Base", "group": "Image roles", "kind": "image"},
    {"id": "image:style", "label": "Style", "group": "Image roles", "kind": "image"},
    {"id": "image:sprite", "label": "Sprite", "group": "Image roles", "kind": "image"},
    {"id": "image:scene", "label": "Scene", "group": "Image roles", "kind": "image"},
    {"id": "image:chat", "label": "Chat", "group": "Image roles", "kind": "image"},
]
# The text builder stages that live under story_builder.json (script id == stage name).
_BUILDER_STAGES = ("storyboard", "locations", "characters", "wardrobe", "base_image")


def _clean_chat_config(raw: dict) -> dict:
    cfg = _default_chat_config()
    cfg.update({k: v for k, v in (raw or {}).items() if k in _CHAT_CONFIG_FIELDS})
    cfg["lorebooks"] = [str(b) for b in (cfg.get("lorebooks") or []) if b]
    cfg["script"] = str(cfg.get("script") or "")
    cfg["params"] = _clean_params(cfg.get("params") or {})
    return cfg


def _seed_script_config(root: Path, spec: dict) -> dict:
    """Build a config for a built-in script, seeded from its legacy config file so the
    migration is loss-free. Uses RAW loaders (no overlay) to avoid recursion."""
    sid = spec["id"]
    cfg = _default_chat_config()
    cfg.update({"id": "script_" + (sid.replace(":", "_") or "chat"),
                "name": spec["label"], "script": sid})
    if sid in _BUILDER_STAGES:
        sb = _story_builder_raw(root)
        cfg["model"] = (sb.get("models") or {}).get(sid, "") or ""
        cfg["system"] = (sb.get("systems") or {}).get(sid, "") or ""
    elif sid == "chatgen":
        path = root / "configs" / "chatgen.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        cfg["system"] = raw.get("system", "") or ""
    elif sid == "promptgen":
        path = root / "configs" / "promptgen.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        cfg["model"] = raw.get("model", "") or ""
        cfg["system"] = raw.get("system", "") or ""
    elif sid.startswith("image:"):
        role = sid.split(":", 1)[1]
        entry = _image_roles_raw(root).get(role)
        cfg["workflow"] = (entry.get("model") if isinstance(entry, dict) else entry) or ""
    return cfg


def _ensure_script_configs(root: Path, data: dict) -> bool:
    """Add a config for every built-in script that doesn't have one yet (seeded from the
    legacy files). Returns True if anything was added (caller should persist)."""
    have = {c.get("script") for c in data["configs"] if c.get("script")}
    added = False
    for spec in SCRIPTS:
        if spec["id"] and spec["id"] not in have:
            data["configs"].append(_seed_script_config(root, spec))
            added = True
    return added


def load_chat_configs(root: Path) -> dict:
    """The chat-config library: {active, configs:[...]}. Always returns at least one free
    config plus one per built-in script (lazily migrated in from the legacy config files)."""
    path = root / "configs" / "chat_configs.json"
    data = dict(CHAT_CONFIGS_DEFAULT)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded.get("configs"), list) and loaded["configs"]:
                data = loaded
        except (ValueError, OSError):
            pass
    data["configs"] = [_clean_chat_config(c) for c in data.get("configs") or []]
    if not any(c.get("script", "") == "" for c in data["configs"]):
        data["configs"].insert(0, _default_chat_config())
    if _ensure_script_configs(root, data):
        data = save_chat_configs(root, data)            # persist the one-time migration
    ids = {c["id"] for c in data["configs"]}
    if data.get("active") not in ids:
        # Prefer a free-chat config as the default active selection.
        free = next((c["id"] for c in data["configs"] if c.get("script", "") == ""), None)
        data["active"] = free or data["configs"][0]["id"]
    return data


def script_config(root: Path, script: str) -> dict | None:
    """The config bound to a given script id (e.g. 'storyboard', 'image:base'), or None."""
    if not script:
        return None
    for c in load_chat_configs(root)["configs"]:
        if c.get("script") == script:
            return c
    return None


def _stage_lore_block(root: Path, books: list) -> str:
    """Render the enabled entries of a stage's attached lorebooks as a rules block
    (no transcript to retrieve against — stages apply their books wholesale, capped)."""
    if not books:
        return ""
    try:
        import re as _re

        from .lorebook import format_lore_block
        from . import lorebook_store as _LS
        entries = []
        for b in books:
            scope = _re.sub(r"[^\w\-]+", "_", str(b))
            entries += [e for e in _LS.load_lorebook(root, scope) if e.enabled and e.content]
            if len(entries) >= 8:
                break
        return format_lore_block(entries[:8]) if entries else ""
    except Exception:  # noqa: BLE001 — lore is additive; never break a stage on it
        return ""


def _overlay_story_builder(cfg: dict, root: Path) -> None:
    """Fold each builder stage's script config (model/system/lorebooks/params) onto the
    raw story_builder cfg in place. A stage's lorebooks are appended to its system; its
    inference params land under cfg['params'][stage] for builder_ctx to apply."""
    try:
        from ...stories.pipeline._helpers import DEFAULT_SYSTEMS
    except Exception:  # noqa: BLE001
        DEFAULT_SYSTEMS = {}
    by_script = {c.get("script"): c for c in load_chat_configs(root)["configs"] if c.get("script")}
    models, systems = cfg.setdefault("models", {}), cfg.setdefault("systems", {})
    params = cfg.setdefault("params", {})
    for stage in _BUILDER_STAGES:
        c = by_script.get(stage)
        if not c:
            continue
        if c.get("model"):
            models[stage] = c["model"]
        if c.get("params"):
            params[stage] = c["params"]
        lore = _stage_lore_block(root, c.get("lorebooks") or [])
        if c.get("system") or lore:
            base = c.get("system") or systems.get(stage) or DEFAULT_SYSTEMS.get(stage) or ""
            systems[stage] = (base + ("\n\n" + lore if lore else "")).strip()


def stage_params(cfg: dict, stage: str | None) -> dict:
    """The inference params for a builder stage (from its overlaid script config)."""
    return (cfg.get("params") or {}).get(stage or "") or {}


def save_chat_configs(root: Path, data: dict) -> dict:
    data = data or {}
    out = {
        "active": data.get("active") or "default",
        "configs": [_clean_chat_config(c) for c in data.get("configs") or []],
    }
    if not out["configs"]:
        out["configs"] = [_default_chat_config()]
    ids = {c["id"] for c in out["configs"]}
    if out["active"] not in ids:
        out["active"] = out["configs"][0]["id"]
    path = root / "configs" / "chat_configs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def active_chat_config(root: Path, config_id: str | None = None) -> dict:
    """The active config dict (or the one named by `config_id` if given)."""
    data = load_chat_configs(root)
    want = config_id or data["active"]
    for c in data["configs"]:
        if c["id"] == want:
            return c
    return data["configs"][0]


# -- image-prompt generator config ------------------------------------------
PROMPTGEN_DEFAULT: dict = {"enabled": True, "model": "", "system": ""}


def load_promptgen(root: Path) -> dict:
    path = root / "configs" / "promptgen.json"
    cfg = dict(PROMPTGEN_DEFAULT)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    c = script_config(root, "promptgen")           # `enabled` stays from the json
    if c:
        if c.get("model"):
            cfg["model"] = c["model"]
        if c.get("system"):
            cfg["system"] = c["system"]
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
