"""AppContext — the former state-bound create_app closures gathered onto one object.

The constructor holds the five pieces of app state (root, store, base_settings, user,
comfy_url) the web layer closed over. Each method below is a verbatim copy of a former
closure, with the mechanical substitutions defined in REFACTOR_CONTRACT.md:
  root -> self.root, store -> self.store, base_settings -> self.base_settings,
  user -> self.user, comfy_url -> self.comfy_url; sibling helper calls -> self.x(...);
  pure helper calls -> the imported service free function;
  `nonlocal base_settings; base_settings = load_settings(root)` -> self.reload_settings().
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from fastapi import UploadFile, File  # noqa: F401 — kept for parity; methods don't define routes
from fastapi.responses import JSONResponse

from ..config import load_settings
from ..config.schema import Character, ModelDef, Settings
from ..connections import ConnectionStore  # noqa: F401 — type reference for callers

from .services import config_files
from .services import prompts as _prompts


# Per-role image-workflow overrides. configs/image_roles.json maps each generation role
# (base/style/sprite/scene/chat) to a workflow key; unset roles fall back to today's
# behavior. Read fresh per request (no restart). See GET/POST /api/image-roles.
# The image pipeline is Anima-only, so every role resolves to an anima* workflow
# (see configs/image_roles.json). No role is hard-pinned to a workflow anymore —
# image_roles.json is the single source of truth for role → workflow mapping.
IMAGE_ROLES = ("base", "sprite", "scene", "chat")
_ROLE_PINNED: dict[str, str] = {}


def _parse_role_entry(v) -> tuple[str, dict]:
    """Unpack a roles-config value (plain string OR {model, output_variant, …} object).
    Returns (model_key, extra_opts) — extra_opts feeds image_provider as overrides."""
    if isinstance(v, dict):
        return (v.get("model") or "").strip(), {k: vv for k, vv in v.items() if k != "model"}
    return (v or "").strip(), {}
_ROLE_LABELS = {
    "base": "Character base image",
    "sprite": "Outfit / emotion sprites",
    "scene": "Story location backgrounds",
    "chat": "Chat pictures",
}

_IMG_URL_RE = re.compile(r"https?://[^\s\"'<>)]+?\.(?:png|jpe?g|webp|gif)", re.IGNORECASE)


def _retrieve_palette(draft: list, kind: str) -> tuple[dict, list]:
    """The composer's pass-2 retrieval: a faceted palette of real, compatible tags + a few intact
    real bundles for coherence. Primary source is the tag similarity GRAPH (navigated from the
    draft); falls back to the one-hop PMI palette, then just the soup sample. `kind` in
    {"appearance","clothing"}."""
    palette: dict = {}
    lines: list = []
    if not draft:
        return palette, lines
    try:
        from ..tags import get_graph
        g = get_graph()
        if g.ready:
            palette = g.palette(draft, kind, per_facet=24)
    except Exception:  # noqa: BLE001 — graph unavailable: fall back below
        palette = {}
    try:
        from ..tags import get_cooccur
        ix = get_cooccur()
        if ix.ready:
            if not palette:
                palette = ix.faceted_palette(draft, kind, per_facet=24)
            sampler = ix.sample_appearance_lines if kind == "appearance" else ix.sample_clothing_lines
            lines = sampler(draft, n=8)
    except Exception:  # noqa: BLE001
        pass
    return palette, lines


def _palette_block(palette: dict, sample_lines: list) -> str:
    """Render a faceted PMI palette (+ a few coherent real examples) as the grounding block for a
    composer's 2nd pass — an organized menu of real booru tags the model constructs from."""
    facet_lines = "\n".join(f"  {f.upper()}: {', '.join(tags)}"
                            for f, tags in palette.items() if tags)
    block = ("PALETTE — real Danbooru tags that co-occur with your draft, grouped by facet (these "
             "are VALID tags; draw RICHLY from them where they fit this character):\n" + facet_lines)
    if sample_lines:
        block += ("\n\nA FEW REAL EXAMPLES (whole real characters, for coherent combinations):\n"
                  + "\n".join(f"- {ln}" for ln in sample_lines))
    return block


class AppContext:
    """Holds app state and the state-bound helpers the web layer needs."""

    def __init__(self, root: Path, store, base_settings, user, comfy_url: str):
        self.root = root
        self.store = store
        self.base_settings = base_settings
        self.user = user
        self.comfy_url = comfy_url
        # RunPod config for dynamic GPU scaling
        rp = user.runpod if hasattr(user, 'runpod') else None
        self.runpod_config = {
            # Env var wins so the secret can live in .env (gitignored) rather than user.yaml.
            "enabled": rp.enabled if rp else True,
            "api_key": os.environ.get("RUNPOD_API_KEY", "") or (rp.api_key if rp else ""),
            "serverless_endpoint_id": os.environ.get("RUNPOD_ENDPOINT_ID", "") or (rp.serverless_endpoint_id if rp else ""),
            "images_per_instance": rp.images_per_instance if rp else 10,
            "min_instances": rp.min_instances if rp else 1,
            "max_instances": rp.max_instances if rp else 10,
            "template_id": rp.template_id if rp else None,
        }

    def set_runpod_enabled(self, enabled: bool) -> None:
        """Toggle RunPod routing on/off and persist to user.yaml."""
        self.runpod_config["enabled"] = enabled
        user_path = self.root / "user.yaml"
        raw: dict = yaml.safe_load(user_path.read_text(encoding="utf-8")) if user_path.is_file() else {}
        raw.setdefault("runpod", {})["enabled"] = enabled
        user_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def reload_settings(self) -> None:
        self.base_settings = load_settings(self.root)

    # -- config-loader wrappers ---------------------------------------------
    def load_image_roles(self) -> dict:
        return config_files.load_image_roles(self.root)

    def pose_tags(self, character_key: str | None, emo: str, outfit_id: str | None = None) -> str:
        """Body-language tags for ONE character at an emotion.

        When `outfit_id` is given, checks the outfit's per-outfit `poses` dict first
        (generated by compose_outfit_poses at wardrobe time). Falls back to the character-level
        `pose_prompts` (legacy / manual), then to empty. 'neutral' always returns NEUTRAL_POSE.
        """
        from .services.poses import NEUTRAL_POSE
        if emo == "neutral":
            return NEUTRAL_POSE
        if not character_key:
            return ""
        m = self.portrait_manifest(character_key)
        if outfit_id:
            outfit = next((o for o in (m.get("outfits") or []) if o.get("id") == outfit_id), None)
            if outfit:
                tags = (outfit.get("poses") or {}).get(emo, "")
                if tags:
                    return tags.strip()
        return ((m.get("pose_prompts") or {}).get(emo) or "").strip()

    def pose_framing(self, emo: str) -> str:
        """Camera-crop framing tags (cowboy / full body) for this emotion's shot geometry (global)."""
        from .services.poses import FRAMING_TAGS, resolve_geometry
        return FRAMING_TAGS[resolve_geometry(emo, config_files.load_poses(self.root))["framing"]]

    def pose_latent(self, emo: str) -> tuple[int, int]:
        """The (width, height) latent canvas for this emotion's aspect (global shot geometry)."""
        from .services.poses import ASPECT_DIMS, resolve_geometry
        return ASPECT_DIMS[resolve_geometry(emo, config_files.load_poses(self.root))["aspect"]]

    def load_pose_library(self) -> dict:
        """Curated body-language pose palette (configs/pose_library.json or the built-in default)."""
        return config_files.load_pose_library(self.root)

    def load_story_builder(self) -> dict:
        return config_files.load_story_builder(self.root)

    # -- resolvers ----------------------------------------------------------
    def effective_settings(self) -> Settings:
        """Base models plus a text model for each saved text/image-prompt connection."""
        models = dict(self.base_settings.models)
        for conn in self.store.list("text"):
            models[conn.id] = ModelDef(provider=conn.provider, kind="text", options=conn.to_model_options())
        return Settings(models=models, characters=self.base_settings.characters, pipelines=self.base_settings.pipelines)

    def image_model_items(self) -> list[dict]:
        fams = self.image_families()
        return [{"id": k, "name": k, "family": fams.get(k, "unknown")}
                for k, m in self.base_settings.models.items() if m.kind == "image"]

    def workflow_family(self, model_id: str | None) -> str:
        """The sub-family (illustrious/pony/anima/…) of an image workflow, read from its
        checkpoint/UNet (folder + tensor arch), with a manual override (configs/families.json) keyed
        by the workflow id or the base filename. 'unknown' if unresolved."""
        from ..comfy.family import family_of
        from ..comfy.scan import classify
        md = self.base_settings.models.get(model_id) if model_id else None
        if md is None or md.kind != "image":
            return "unknown"
        base_name, folder = None, None
        wf = md.options.get("workflow")
        try:
            p = self.root / wf if wf else None
            if p and p.is_file():
                for node in json.loads(p.read_text(encoding="utf-8")).values():
                    ct = node.get("class_type") if isinstance(node, dict) else None
                    if ct == "CheckpointLoaderSimple":
                        base_name, folder = node.get("inputs", {}).get("ckpt_name"), "checkpoints"; break
                    if ct in ("UNETLoader", "UnetLoaderGGUF"):
                        base_name, folder = node.get("inputs", {}).get("unet_name"), "diffusion_models"; break
        except Exception:  # noqa: BLE001
            pass
        arch = ""
        bd = self.comfy_base_dir()
        if base_name and bd and folder:
            fp = bd / "models" / folder / base_name.replace("\\", "/")
            if fp.is_file():
                arch = classify(str(fp)).get("arch") or ""
        ov = config_files.load_families(self.root)
        return family_of(base_name, arch=arch or None,
                         override=ov.get(model_id) or (ov.get(base_name) if base_name else None))

    def image_families(self) -> dict:
        """{workflow_id: family} for every image workflow — for grouping the pickers by container."""
        return {k: self.workflow_family(k)
                for k, m in self.base_settings.models.items() if m.kind == "image"}

    def output_prefix_for(self, model_id: str | None, role: str, character: str | None = None) -> str:
        """Organized ComfyUI output path for a render: loom/<family>/<role>/<character|misc>.
        Pass the resolved workflow id the render actually uses."""
        from .services.img_naming import output_prefix
        return output_prefix(self.workflow_family(model_id), role, character)

    def text_provider_for(self, model_sel: str | None, params: dict | None = None):
        """Build a text provider for a model selection: a registered model key,
        or an OpenRouter (etc.) model id run through the active text connection.
        `params` (temperature/top_p/…) from a config are merged into the provider options."""
        from ..providers.registry import build_provider

        params = params or {}
        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel]
            if params:
                md = md.model_copy(); md.options = {**md.options, **params}
            return build_provider(md)
        conn = self.store.active("text")
        if conn:
            return build_provider(ModelDef(
                provider=conn.provider, kind="text",
                options={"model": model_sel or conn.model, "api_key": conn.api_key,
                         "base_url": conn.base_url, **params},
            ))
        return None

    def ip_provider(self):
        """The image-prompt model's text/vision provider (its own connection,
        falling back to the chat connection)."""
        from ..providers.registry import build_provider

        conn = self.store.active("image_prompt") or self.store.active("text")
        if conn is None:
            return None
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=conn.to_model_options()))

    def image_provider(self, model_id: str | None = None, output_variant: str | None = None):
        """A ComfyUIProvider for an image model — an explicit workflow id if given,
        else the active image connection (honouring its base_url override), plus
        the resolved model id. (provider, id) or (None, error_message).
        output_variant ('full'|'cutout') overrides whatever the model definition says."""
        from ..providers.comfyui_provider import ComfyUIProvider

        conn = self.store.active("image")
        model_id = model_id or (conn.model if conn else None) or self.user.defaults.get("image_model")
        md = self.base_settings.models.get(model_id) if model_id else None
        if md is None or md.kind != "image":
            return None, "no image model selected — pick one in ⚙ Models → Image model"
        opts = dict(md.options)
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        if output_variant:
            opts["output_variant"] = output_variant
        return ComfyUIProvider(opts), model_id

    def role_default(self, role: str) -> str | None:
        """What a role resolves to with NO override: the role-pinned workflow if it exists,
        else the active image connection's model, else the user.yaml default."""
        if role in _ROLE_PINNED and _ROLE_PINNED[role] in self.base_settings.models:
            return _ROLE_PINNED[role]
        conn = self.store.active("image")
        return (conn.model if conn else None) or self.user.defaults.get("image_model")

    def role_model(self, role: str, override: str | None = None) -> str | None:
        """Resolve the workflow key for a generation role: explicit override > image_roles.json > default."""
        if override:
            return override
        key, _ = _parse_role_entry(self.load_image_roles().get(role))
        return key if (key and key in self.base_settings.models) else self.role_default(role)

    def role_extra_opts(self, role: str) -> dict:
        """Extra provider options for a role from image_roles.json (e.g. output_variant)."""
        _, opts = _parse_role_entry(self.load_image_roles().get(role))
        return opts

    def role_image_provider(self, role: str, override: str | None = None):
        """Convenience: resolve role → (model_key + output_variant) → ComfyUIProvider.
        When override is set the caller chose a model explicitly — skip role extra opts."""
        model_id = self.role_model(role, override)
        variant = None if override else self.role_extra_opts(role).get("output_variant")
        return self.image_provider(model_id, output_variant=variant)

    def author_provider(self, model_sel: str | None, params: dict | None = None):
        """Text provider for the Story Builder — an explicit (selectable) author
        model if given, else the active chat connection. Always raises the token
        ceiling (the default 1024 truncates a stage's structured JSON). A config's
        `params` (temperature/…) override, so an explicit max_tokens wins over 4096."""
        from ..providers.registry import build_provider

        params = params or {}
        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel].model_copy()
            md.options = {**md.options, "max_tokens": 4096, **params}
            return build_provider(md)
        conn = self.store.active("text")
        if conn is None:
            return None
        opts = {**conn.to_model_options(), "max_tokens": 4096, **params}
        if model_sel:
            opts["model"] = model_sel
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=opts))

    def builder_ctx(self, body: dict, stage: str | None = None):
        """(provider, systems) for a builder step, or (None, err).
        Each stage carries its own model + inference params (from its script config)."""
        cfg = self.load_story_builder()
        provider = self.author_provider(
            config_files._stage_model(cfg, stage, (body or {}).get("model")),
            config_files.stage_params(cfg, stage))
        if provider is None or not hasattr(provider, "generate_text"):
            return None, "no chat connection — connect a chat model first"
        return provider, (cfg.get("systems") or {})

    # A thin seed → a thorough, disciplined-prose character sheet. The single "flesh thin→rich" front
    # door that feeds every downstream parser (appearance / pose / expression → tags).
    _FLESH_INSTRUCTION = (
        "Expand this thin seed into a thorough, vivid character: full physical appearance, personality, "
        "demeanor, and how they physically carry and express themselves — while staying faithful to the "
        "details given.")

    def flesh_character(self, key: str, instruction: str = "") -> dict:
        """Rewrite a (thin) character into a thorough disciplined-prose sheet (persona + appearance +
        role) via the story reviser, persisted IN PLACE. Returns the new fields or {error}."""
        from ..stories import revise_character
        ch = self.base_settings.characters.get(key)
        if ch is None:
            return {"error": "no such character"}
        provider, systems = self.builder_ctx({}, "characters")
        if provider is None:
            return {"error": systems}
        fields = ch.fields or {}
        try:
            revised = revise_character(
                provider, name=ch.name, persona=ch.system or "", role=fields.get("role", ""),
                appearance=fields.get("appearance", ""),
                instruction=instruction.strip() or self._FLESH_INSTRUCTION,
                systems=systems)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"flesh failed: {exc}"}
        safe = re.sub(r"[^\w\-]+", "", key)
        path = self.char_dir() / f"{safe}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}
        data["name"] = revised.get("name") or data.get("name") or ch.name
        data["system"] = revised.get("persona") or ch.system or ""
        data["fields"] = {**(data.get("fields") or {}), "role": revised.get("role") or fields.get("role", ""),
                          "appearance": revised.get("appearance") or fields.get("appearance", "")}
        Character(**data)  # validate
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()
        return {"ok": True, "name": data["name"], "persona": data["system"],
                "appearance": data["fields"]["appearance"], "role": data["fields"]["role"]}

    def ensure_fleshed(self, key: str) -> None:
        """Auto-fallback: if a character's persona is still thin (a bare seed), flesh it into the
        disciplined-prose sheet before generation parses tags from it. No-op on thorough personas."""
        ch = self.base_settings.characters.get(key)
        if ch is not None and len((ch.system or "").strip()) < 320:
            self.flesh_character(key)

    def card_extras(self, ch, char_key: str) -> dict:
        fields = ch.fields or {}
        return {"scenario_text": fields.get("scenario"),
                "first_mes": ch.greeting or fields.get("first_mes"),
                "mes_example": fields.get("mes_example"),
                "appearance": fields.get("appearance")}

    def active_comfy_url(self) -> str:
        conn = self.store.active("image")
        return (conn.base_url if conn and conn.base_url else self.comfy_url)

    def comfy_base_dir(self) -> Path | None:
        """The managed ComfyUI base directory (holds models/checkpoints, models/
        loras). None for connect-only setups where we don't know the layout."""
        from ..comfy.server import get_server
        launch = getattr(get_server(self.comfy_url), "launch", None)
        bd = getattr(launch, "base_directory", None) if launch else None
        return Path(bd) if bd else None

    def workflow_path(self, model_key: str) -> Path | None:
        md = self.base_settings.models.get(model_key)
        if md is None or md.kind != "image":
            return None
        wf = md.options.get("workflow")
        if not wf:
            return None
        path = (self.root / wf).resolve()
        # Guard against path traversal — must stay inside the project root.
        if self.root.resolve() not in path.parents:
            return None
        return path

    # -- paths --------------------------------------------------------------
    def char_dir(self) -> Path:
        return self.root / "configs" / "characters"

    def persona_dir(self, *, create: bool = False) -> Path:
        """configs/personas — one YAML per persona (filename stem = key), with a
        <key>.png avatar alongside. Created on demand."""
        d = self.root / "configs" / "personas"
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def persona_avatar_path(self, key: str) -> Path | None:
        """The persona's avatar PNG (<key>.png), or None if absent."""
        safe = re.sub(r"[^\w\-]+", "", key)
        p = self.persona_dir() / f"{safe}.png"
        return p if p.is_file() else None

    def save_persona_avatar(self, key: str, png: bytes) -> str:
        """Write the chosen portrait candidate's bytes to <key>.png."""
        safe = re.sub(r"[^\w\-]+", "", key)
        self.persona_dir(create=True)
        (self.persona_dir() / f"{safe}.png").write_bytes(png)
        return f"/api/personas/{key}/avatar"

    def write_persona(self, key: str, data: dict) -> dict:
        """Validate + persist a persona YAML (configs/personas/<key>.yaml), then reload.
        `key` is the filename stem; the caller is responsible for slug/dedup."""
        from ..config.schema import Persona
        Persona(**{k: v for k, v in data.items() if k != "key"})  # validate (drops a stray 'key')
        self.persona_dir(create=True)
        payload = {k: v for k, v in data.items() if k in ("name", "description", "summary",
                                                          "appearance", "fields") and v not in (None, "")}
        (self.persona_dir() / f"{key}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()
        return {"ok": True, "key": key}

    def delete_persona(self, key: str) -> bool:
        """Remove a persona's YAML + avatar PNG, then reload. Returns whether anything was removed."""
        safe = re.sub(r"[^\w\-]+", "", key)
        removed = False
        for fn in (f"{safe}.yaml", f"{safe}.png"):
            p = self.persona_dir() / fn
            if p.is_file():
                p.unlink()
                removed = True
        if removed:
            self.reload_settings()
        return removed

    def story_dir(self) -> Path:
        return self.root / "configs" / "stories"

    def story_bg_dir(self, key: str) -> Path:
        return self.story_dir() / re.sub(r"[^\w\-]+", "", key) / "bg"

    def portrait_dir(self, key: str, *, create: bool = False) -> Path:
        safe = re.sub(r"[^\w\-]+", "", key)
        d = self.root / "configs" / "characters" / "portraits" / safe
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def portrait_manifest(self, key: str) -> dict:
        p = self.portrait_dir(key) / "manifest.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — corrupt manifest: start fresh
                pass
        return {"appearance": "", "outfits": []}

    def save_portrait_manifest(self, key: str, data: dict) -> None:
        d = self.portrait_dir(key, create=True)
        (d / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def portrait_outfit(self, manifest: dict, oid: str) -> dict | None:
        return next((o for o in manifest.get("outfits", []) if o.get("id") == oid), None)

    def reference_path(self, key: str) -> Path | None:
        """The character's img2img reference image: a dedicated <key>.ref.png if
        set, else the card avatar <key>.png. None if neither exists."""
        safe = re.sub(r"[^\w\-]+", "", key)
        for fn in (f"{safe}.ref.png", f"{safe}.png"):
            p = self.char_dir() / fn
            if p.is_file():
                return p
        return None

    def sets_dir(self) -> Path:
        return self.root / "configs" / "prompt_sets"

    def dataset_dir(self, name: str) -> Path | None:
        safe = re.sub(r"[^\w\-]+", "_", name or "").strip("_")
        d = (self.root / "datasets" / safe).resolve()
        if not safe or self.root.resolve() not in d.parents or not d.is_dir():
            return None
        return d

    # -- domain ops ---------------------------------------------------------
    def character_images(self, k: str, c) -> list[dict]:
        """All images attached to a character, categorized and checked. Local files
        are verified to exist; URLs found in the card are listed as external."""
        char_dir = self.char_dir()
        out: list[dict] = []
        has_avatar = (char_dir / f"{k}.png").is_file()
        has_ref = (char_dir / f"{k}.ref.png").is_file()
        if has_avatar:
            out.append({"kind": "avatar", "url": f"/api/characters/{k}/avatar", "ok": True, "source": "card"})
        # the reference is the dedicated ref if present, else the avatar
        if has_ref or has_avatar:
            out.append({"kind": "reference", "url": f"/api/characters/{k}/reference",
                        "ok": True, "source": "dedicated" if has_ref else "avatar"})
        # image URLs embedded in the card (description, fields…)
        seen = set()
        for blob in (c.system or "", c.greeting or "", json.dumps(c.fields, ensure_ascii=False)):
            for m in _IMG_URL_RE.findall(blob):
                if m not in seen:
                    seen.add(m)
                    out.append({"kind": "card link", "url": m, "ok": None, "source": "external"})
        return out

    def portrait_payload(self, key: str) -> dict:
        """The manifest enriched with served image URLs for the frontend.

        affect.range in the manifest may be either the old format (list of dicts with
        emotion/valence/arousal) or the new format (list of strings). Both are normalised to
        a plain list of keys here; the API response enriches back to [{emotion, valence, arousal}]
        for the frontend carousel and AffectScatter."""
        from .services.emotions import (EMOTION_KEYS, EMOTION_LABELS, EMOTION_COORDS,
                                        NORMAL_KEYS, range_to_display)
        m = self.portrait_manifest(key)
        base = f"/api/characters/{key}/portraits/img"
        canon = m.get("expression_prompts") or {}
        # Normalise affect.range to a list of string keys regardless of stored format.
        raw_range = (m.get("affect") or {}).get("range") if isinstance(m.get("affect"), dict) else None
        if isinstance(raw_range, list) and raw_range:
            if isinstance(raw_range[0], dict):
                # Old format: [{emotion, valence?, arousal?}] → extract keys
                affect_key_list = [e["emotion"] for e in raw_range if e.get("emotion") in EMOTION_KEYS]
            else:
                # New format: [key1, key2, ...]
                affect_key_list = [k for k in raw_range if k in EMOTION_KEYS]
        else:
            affect_key_list = list(NORMAL_KEYS)
        affect_keys = set(affect_key_list)
        # Build display-ready range (sorted by circumplex angle) for carousel + AffectScatter.
        affect_range_display = range_to_display(affect_key_list)
        outfits = []
        for o in m.get("outfits", []):
            oid = o["id"]
            files = o.get("expressions") or {}
            exprs = {emo: f"{base}/{oid}/{fn}" for emo, fn in files.items()}
            out_prompts = o.get("expression_prompts") or {}
            expression_set = [{
                "emotion": k, "label": EMOTION_LABELS.get(k, k),
                "prompt": out_prompts.get(k) or canon.get(k, ""),
                "url": f"{base}/{oid}/{files[k]}" if files.get(k) else None,
                "valence": EMOTION_COORDS[k][0],
                "arousal": EMOTION_COORDS[k][1],
                "in_range": k in affect_keys,
            } for k in EMOTION_KEYS]
            outfits.append({
                "id": oid, "name": o.get("name") or oid,
                "instruction": o.get("instruction", ""), "prompt": o.get("prompt", ""),
                "attire_prompt": o.get("attire_prompt", o.get("prompt", "")),
                "base": f"{base}/{oid}/base.png" if (o.get("base")) else None,
                "expressions": exprs,
                "expression_set": expression_set,
            })
        return {"appearance": m.get("appearance", ""),
                "emotions": EMOTION_KEYS,
                "affect": {"range": affect_range_display, "keys": affect_key_list,
                           "dimensions": ["valence", "arousal"]},
                "expression_prompts": canon, "outfits": outfits}

    def write_npc(self, npc: dict, story_key: str = "", ref_from: str | None = None,
                  base_prompt: str = "") -> str:
        """Create a story-bound Character file (a generated NPC, or the duplicated
        protagonist); returns its key. Tagged with its owning `story` so it's scoped /
        grouped and hidden from global pickers. If `ref_from` is given, that character's
        reference image is copied across. `base_prompt` (from the shared ✨ composer) is stored
        as fields.base_prompt so the base image renders richly without a manual ✨ pass."""
        char_dir = self.char_dir()
        char_dir.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^a-z0-9]+", "_", (npc.get("name") or "npc").lower()).strip("_") or "npc"
        key, i = base, 2
        while (char_dir / f"{key}.yaml").exists():
            key, i = f"{base}_{i}", i + 1
        fields = {"appearance": npc.get("appearance", ""), "role": npc.get("role", ""),
                  "story": story_key, "_generated": True}
        if base_prompt:
            fields["base_prompt"] = base_prompt
        try:                                              # numeric stature → sprite scaling (not a tag)
            if npc.get("height_cm"):
                fields["height_cm"] = int(npc["height_cm"])
        except (TypeError, ValueError):
            pass
        cdata = {
            "name": npc.get("name") or key,
            "system": npc.get("persona") or "",
            "fields": fields,
            "_migrated_split": True,
        }
        Character(**cdata)  # validate (extra top-level keys ignored)
        (char_dir / f"{key}.yaml").write_text(
            yaml.safe_dump(cdata, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if ref_from:
            ref = self.reference_path(ref_from)
            if ref and ref.is_file():
                import shutil
                try:
                    shutil.copyfile(ref, char_dir / f"{key}.ref.png")
                except Exception:  # noqa: BLE001
                    pass
        return key

    def write_character(self, cdata: dict, avatar_png: bytes | None) -> dict:
        """Validate + persist a normalized character: one configs/characters/
        <key>.yaml, the avatar PNG alongside (loader globs *.yaml, so the .png is
        ignored by config loading), then reload settings so it's selectable."""
        Character(**cdata)  # raises on a malformed card
        char_dir = self.char_dir()
        char_dir.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^a-z0-9]+", "_", cdata["name"].lower()).strip("_") or "character"
        key, i = base, 2
        while (char_dir / f"{key}.yaml").exists():
            key, i = f"{base}_{i}", i + 1
        (char_dir / f"{key}.yaml").write_text(
            yaml.safe_dump(cdata, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if avatar_png and avatar_png.startswith(b"\x89PNG\r\n\x1a\n"):
            (char_dir / f"{key}.png").write_bytes(avatar_png)
        self.reload_settings()
        return {"ok": True, "key": key, "name": cdata["name"],
                "avatar": (char_dir / f"{key}.png").is_file()}

    def prune_orphan_characters(self) -> list[str]:
        """Auto-delete GENERATED characters that no longer belong to any story — their owning
        story was deleted, or they were never cast. Library / imported cards (not `_generated`)
        are NEVER touched; they're independent. Removes the yaml + avatar/ref + portraits, like
        delete_character. Idempotent — safe to call after any story/cast change or at startup.
        Returns the keys removed."""
        import shutil
        live_stories = set(self.base_settings.stories.keys())
        cast_members = {m.character for st in self.base_settings.stories.values() for m in st.cast}
        cdir = self.char_dir()
        removed: list[str] = []
        for k, c in list(self.base_settings.characters.items()):
            fields = c.fields or {}
            if not fields.get("_generated"):
                continue  # a library card — keep it regardless of story membership
            story_key = fields.get("story")
            has_story = (bool(story_key) and story_key in live_stories) or (k in cast_members)
            if has_story:
                continue
            safe = re.sub(r"[^\w\-]+", "", k)
            for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                fp = cdir / fn
                if fp.is_file():
                    fp.unlink()
            shutil.rmtree(self.portrait_dir(k), ignore_errors=True)
            removed.append(k)
        if removed:
            self.reload_settings()
        return removed

    def save_location_bg(self, key: str, loc: str, png: bytes) -> str:
        """Write a chosen background and record it on the story's location."""
        d = self.story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{loc}.png").write_bytes(png)
        safe = re.sub(r"[^\w\-]+", "", key)
        path = self.story_dir() / f"{safe}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background"] = f"/api/stories/{key}/bg/{loc}.png"
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()
        return f"/api/stories/{key}/bg/{loc}.png"

