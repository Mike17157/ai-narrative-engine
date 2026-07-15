"""Provider-selection responsibilities for :class:`AppContext`."""

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



class ProviderContextMixin:
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

    def text_provider_for(self, model_sel: str | None, params: dict | None = None,
                          connection: str | None = None):
        """Build a text provider for a model selection: a registered model key, or a model id
        run through a connection. `connection` (a preset's bound connection id) is used when
        given — so different presets can target different providers (including the local Ollama
        connection); otherwise the active text connection. `params` (temperature/top_p/…) are
        merged into the provider options. Local inference is just an Ollama connection now (its
        keyless + no-think quirks ride along via Connection.to_model_options)."""
        from ..providers.registry import build_provider

        params = params or {}
        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel]
            if params:
                md = md.model_copy(); md.options = {**md.options, **params}
            return build_provider(md)
        conn = (self.store.get(connection) if connection else None) or self.store.active("text")
        if conn:
            opts = conn.to_model_options()          # carries provider quirks (e.g. Ollama keyless + no-think)
            if model_sel:
                opts["model"] = model_sel
            opts.update(params)                     # a config's params win (incl. reasoning_effort)
            return build_provider(ModelDef(provider=conn.provider, kind="text", options=opts))
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
        """A provider for an image WORKFLOW — an explicit workflow id if given, else the active
        image connection, plus the resolved model id. (provider, id) or (None, error_message).
        output_variant ('full'|'cutout') overrides whatever the model definition says."""
        from ..providers.comfyui_provider import ComfyUIProvider

        conn = self.store.active("image")
        model_id = model_id or (conn.model if conn else None) or self.user.defaults.get("image_model")
        md = self.base_settings.models.get(model_id) if model_id else None
        if md is None or md.kind != "image":
            return None, "no image model selected — pick one in ⚙ Models → Image model"
        opts = dict(md.options)
        if output_variant:
            opts["output_variant"] = output_variant

        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        opts["flags"] = {**opts.get("flags", {}), **self.image_flags()}
        return ComfyUIProvider(opts), model_id

    def image_flags(self) -> dict:
        """Global pipeline toggles for renders (configs/app.json) → the workflow's switch
        gates. `img_upscale` drives BOTH the USDU and hi-res upscale branches (the "4K flow")."""
        from .services import config_files as _cf
        f = _cf.load_app_flags(self.root)
        up = bool(f.get("img_upscale", False))
        return {"detailer": bool(f.get("img_detailer", True)), "upscale": up, "highrez": up}

    def role_default(self, role: str) -> str | None:
        """What a role resolves to with NO override: the active image connection's model,
        else the user.yaml default."""
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

    def role_image_provider(self, role: str, override: str | None = None,
                            image_preset: str | None = None):
        """Convenience: resolve role → (model_key + output_variant) → image provider.
        When override is set the caller chose a model explicitly — skip role extra opts.
        The active (or per-render `image_preset`) LoRA stack is injected before return — the
        single chokepoint every normal render path funnels through.

        Chat-surface roles ('chat'/'scene') take the ACTIVE preset's image WORKFLOW + look —
        the unified preset bundles them. Pipeline roles (base/sprite/style) keep using
        image_roles.json. Backward-compatible: a preset with no image_workflow falls
        straight through to the legacy role resolution."""
        if not override and role in ("chat", "scene"):
            from .services import presets as _P
            ap = _P.active_preset(self.root)
            if (ap.get("image_workflow") or "") in self.base_settings.models:
                override = ap["image_workflow"]
                if image_preset is None and ap.get("image_preset"):
                    image_preset = ap["image_preset"]
        model_id = self.role_model(role, override)
        variant = None if override else self.role_extra_opts(role).get("output_variant")
        provider, mid = self.image_provider(model_id, output_variant=variant)
        if provider is not None:
            self._apply_image_preset(provider, image_preset)
        return provider, mid

    def preset_image_provider(self, preset: dict | None, override: str | None = None,
                              output_variant: str | None = None):
        """The unified image entry: a PRESET's image workflow + look. `preset.image_workflow`
        is the workflow (override wins); `preset.image_preset` is the LoRA stack. Falls back
        to the active image connection when the preset names no workflow. The single
        chokepoint, same as role_image_provider, so the LoRA look is always injected."""
        preset = preset or {}
        workflow = override or (preset.get("image_workflow") or None)
        provider, mid = self.image_provider(workflow, output_variant=output_variant)
        if provider is not None:
            self._apply_image_preset(provider, preset.get("image_preset") or None)
        return provider, mid

    def _apply_image_preset(self, provider, preset_id: str | None) -> None:
        """Neutralize the workflow's baked LoRA styles, inject the chosen image preset's stack, and
        gather LoRA trigger words → provider.prompt_suffix (appended to the positive conditioning at
        render). Triggers come from BOTH the preset's LoRAs (explicit `trigger` field) AND any LoRAs
        sitting in the workflow's own `easy loraStack` node (resolved from metadata). `preset_id`
        None/"" → the global-default active preset; "none" → vanilla base."""
        from ..comfy.stack import inject_models, neutralize_baked_stack, resolve_lora_names
        from .services import image_presets as IP

        neutralize_baked_stack(provider.workflow)
        pid = preset_id or IP.load_image_presets(self.root).get("active")
        preset = IP.get_image_preset(self.root, pid)

        triggers: list[str] = []
        if preset and preset["id"] != "none" and preset.get("loras"):
            # Presets store LoraManager bare names; map them to real ComfyUI lora filenames.
            loras = resolve_lora_names(list(preset["loras"]), self.available_loras())
            provider.workflow = inject_models(
                provider.workflow, preset.get("base_checkpoint") or None, loras)
            triggers += [t for lr in preset["loras"] if (t := str(lr.get("trigger") or "").strip())]
        # LoRAs added straight to the workflow's easy loraStack node have no trigger field —
        # resolve theirs from each file's metadata so they fire too.
        triggers += self._workflow_lora_triggers(provider.workflow)
        # Trigger words must appear in the prompt to fire — the provider appends them to the
        # positive conditioning (a string suffix, so it works for single-stage AND Gemma-optimized
        # krea2 workflows alike). De-duped, order preserved.
        provider.prompt_suffix = ", ".join(dict.fromkeys(t for t in triggers if t))

    def _workflow_lora_triggers(self, workflow: dict) -> list[str]:
        """Trigger words for LoRAs sitting in the workflow's `easy loraStack` node(s), resolved
        from each LoRA's metadata (Civitai trained words, else the file's own trigger phrase;
        cached by lora_metadata). Skips stacks toggled off and empty slots; never raises."""
        from ..comfy.civitai import lora_metadata, local_trigger_words
        bd = self.comfy_base_dir()
        loras_dir = (bd / "models" / "loras") if bd else None
        out: list[str] = []
        for node in (workflow or {}).values():
            if not isinstance(node, dict) or node.get("class_type") != "easy loraStack":
                continue
            ins = node.get("inputs") or {}
            if ins.get("toggle") is False:          # stack disabled → its LoRAs don't apply
                continue
            try:
                num = min(max(int(ins.get("num_loras") or 10), 0), 10)
            except (TypeError, ValueError):
                num = 10
            for k in range(1, num + 1):
                name = str(ins.get(f"lora_{k}_name") or "").strip()
                if not name or name == "None":
                    continue
                try:
                    tw = (lora_metadata(self.root, name, loras_dir).get("trained_words")
                          or local_trigger_words(loras_dir, name))
                except Exception:                    # noqa: BLE001 — never break a render on lookup
                    tw = None
                if tw:
                    out.append(", ".join(tw) if isinstance(tw, list) else str(tw))
        return out

    _loras_cache: list | None = None

    def available_loras(self) -> list[str]:
        """The ComfyUI LoraLoader filename list (cached per process). Empty if unreachable."""
        if self._loras_cache is not None:
            return self._loras_cache
        loras: list[str] = []
        try:
            import httpx
            r = httpx.get(self.active_comfy_url().rstrip("/") + "/object_info/LoraLoader", timeout=10)
            r.raise_for_status()
            v = (r.json().get("LoraLoader", {}).get("input", {}).get("required", {}).get("lora_name"))
            if isinstance(v, list) and v and isinstance(v[0], list):
                loras = list(v[0])
        except Exception:  # noqa: BLE001
            pass
        self._loras_cache = loras
        return loras

    def allow_nsfw(self) -> bool:
        """The global content gate (configs/app.json). When False, nsfw-rated lorebooks are
        excluded from retrieval everywhere."""
        from .services import config_files as _cf
        return bool(_cf.load_app_flags(self.root).get("allow_nsfw", True))

    def author_provider(self, model_sel: str | None, params: dict | None = None):
        """Text provider for the Story Builder — an explicit (selectable) author
        model if given, else the active chat connection. Always raises the token
        ceiling (a small default truncates a stage's structured JSON). A config's
        `params` (temperature/…) override, so an explicit max_tokens wins over the default."""
        from ..providers.registry import build_provider

        params = params or {}
        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel].model_copy()
            md.options = {**md.options, "max_tokens": 40000, **params}
            return build_provider(md)
        conn = self.store.active("text")
        if conn is None:
            return None
        opts = {**conn.to_model_options(), "max_tokens": 40000, **params}
        if model_sel:
            opts["model"] = model_sel
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=opts))

    def stage_provider(self, stage: str | None, model_override: str | None = None,
                       max_tokens: int = 40000):
        """The ONE model-resolution chokepoint for a pipeline stage. A STAGE LOREBOOK bound
        to a PRESET supplies the stage's model + connection + params (so any stage can run
        local Ollama, etc., like every chat surface). Falls back to the legacy
        configs/story_builder.json per-stage model. An explicit `model_override` wins.
        Returns a text provider (or None)."""
        from .services import presets as _presets
        if stage and not model_override:
            preset = _presets.stage_preset(self.root, stage)
            if preset:
                params = {"max_tokens": max_tokens, **(preset.get("params") or {})}
                prov = self.text_provider_for(
                    preset.get("model") or None, params,
                    connection=preset.get("connection") or None)
                if prov is not None and hasattr(prov, "generate_text"):
                    return prov
                # preset present but unbuildable → fall through to the legacy config
        cfg = self.load_story_builder()
        return self.author_provider(
            config_files._stage_model(cfg, stage, model_override),
            config_files.stage_params(cfg, stage))

    def stage_system(self, stage: str | None) -> str | None:
        """The stage's SYSTEM prompt from its bound preset (stage lorebook → preset), or None
        to defer to the caller's default / story_builder.json systems."""
        if not stage:
            return None
        from .services import presets as _presets
        preset = _presets.stage_preset(self.root, stage)
        return (preset or {}).get("system") or None

    def builder_ctx(self, body: dict, stage: str | None = None):
        """(provider, systems) for a builder step, or (None, err). The stage's model +
        connection + params + system resolve through the unified stage chokepoint (a stage
        lorebook bound to a preset), falling back to configs/story_builder.json. An explicit
        per-request `body.model` override still wins."""
        cfg = self.load_story_builder()
        systems = dict(cfg.get("systems") or {})
        model_override = (body or {}).get("model")
        if not model_override:
            sys_override = self.stage_system(stage)
            if sys_override:
                systems[stage] = sys_override
        provider = self.stage_provider(stage, model_override)
        if provider is None or not hasattr(provider, "generate_text"):
            return None, "no chat connection — connect a chat model first"
        return provider, systems

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
        data = self._read_character_data(key) or {}
        data["name"] = revised.get("name") or data.get("name") or ch.name
        data["system"] = revised.get("persona") or ch.system or ""
        data["fields"] = {**(data.get("fields") or {}), "role": revised.get("role") or fields.get("role", ""),
                          "appearance": revised.get("appearance") or fields.get("appearance", "")}
        self._write_character_data(key, data)   # owning story DB (embedded) or global YAML
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

