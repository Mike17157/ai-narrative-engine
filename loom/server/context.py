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
IMAGE_ROLES = ("base", "style", "sprite", "scene", "chat")
_ROLE_PINNED = {"style": "illustrious_style", "sprite": "sprite", "scene": "scene"}
_ROLE_LABELS = {
    "base": "Character base image",
    "style": "Styled base (style transfer)",
    "sprite": "Outfit / emotion sprites",
    "scene": "Story location backgrounds",
    "chat": "Chat pictures",
}

_IMG_URL_RE = re.compile(r"https?://[^\s\"'<>)]+?\.(?:png|jpe?g|webp|gif)", re.IGNORECASE)


class AppContext:
    """Holds app state and the state-bound helpers the web layer needs."""

    def __init__(self, root: Path, store, base_settings, user, comfy_url: str):
        self.root = root
        self.store = store
        self.base_settings = base_settings
        self.user = user
        self.comfy_url = comfy_url

    def reload_settings(self) -> None:
        self.base_settings = load_settings(self.root)

    # -- config-loader wrappers ---------------------------------------------
    def load_image_roles(self) -> dict:
        return config_files.load_image_roles(self.root)

    def load_story_builder(self) -> dict:
        return config_files.load_story_builder(self.root)

    # -- resolvers ----------------------------------------------------------
    def effective_settings(self) -> Settings:
        """Base models plus a text model for each saved text/image-prompt connection."""
        models = dict(self.base_settings.models)
        for conn in [*self.store.list("text"), *self.store.list("image_prompt")]:
            models[conn.id] = ModelDef(provider=conn.provider, kind="text", options=conn.to_model_options())
        return Settings(models=models, characters=self.base_settings.characters, pipelines=self.base_settings.pipelines)

    def image_model_items(self) -> list[dict]:
        return [{"id": k, "name": k} for k, m in self.base_settings.models.items() if m.kind == "image"]

    def text_provider_for(self, model_sel: str | None):
        """Build a text provider for a model selection: a registered model key,
        or an OpenRouter (etc.) model id run through the active text connection."""
        from ..providers.registry import build_provider

        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            return build_provider(s.models[model_sel])
        conn = self.store.active("text")
        if conn:
            return build_provider(ModelDef(
                provider=conn.provider, kind="text",
                options={"model": model_sel or conn.model, "api_key": conn.api_key, "base_url": conn.base_url},
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

    def image_provider(self, model_id: str | None = None):
        """A ComfyUIProvider for an image model — an explicit workflow id if given,
        else the active image connection (honouring its base_url override), plus
        the resolved model id. (provider, id) or (None, error_message)."""
        from ..providers.comfyui_provider import ComfyUIProvider

        conn = self.store.active("image")
        model_id = model_id or (conn.model if conn else None) or self.user.defaults.get("image_model")
        md = self.base_settings.models.get(model_id) if model_id else None
        if md is None or md.kind != "image":
            return None, "no image model selected — pick one in ⚙ Models → Image model"
        opts = dict(md.options)
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        return ComfyUIProvider(opts), model_id

    def role_default(self, role: str) -> str | None:
        """What a role resolves to with NO override: the role-pinned workflow if it exists,
        else the active image connection's model, else the user.yaml default."""
        if role in _ROLE_PINNED and _ROLE_PINNED[role] in self.base_settings.models:
            return _ROLE_PINNED[role]
        conn = self.store.active("image")
        return (conn.model if conn else None) or self.user.defaults.get("image_model")

    def role_model(self, role: str, override: str | None = None) -> str | None:
        """Resolve the workflow for a generation role: explicit request override > configured
        role override (configs/image_roles.json) > role default."""
        if override:
            return override
        v = (self.load_image_roles().get(role) or "").strip()
        return v if (v and v in self.base_settings.models) else self.role_default(role)

    def author_provider(self, model_sel: str | None):
        """Text provider for the Story Builder — an explicit (selectable) author
        model if given, else the active chat connection. Always raises the token
        ceiling (the default 1024 truncates a stage's structured JSON)."""
        from ..providers.registry import build_provider

        s = self.effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel].model_copy()
            md.options = {**md.options, "max_tokens": 4096}
            return build_provider(md)
        conn = self.store.active("text")
        if conn is None:
            return None
        opts = {**conn.to_model_options(), "max_tokens": 4096}
        if model_sel:
            opts["model"] = model_sel
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=opts))

    def builder_ctx(self, body: dict, stage: str | None = None):
        """(provider, invention, systems) for a builder step, or (None, err, _).
        Each stage carries its own model + invention config."""
        cfg = self.load_story_builder()
        provider = self.author_provider(config_files._stage_model(cfg, stage, (body or {}).get("model")))
        if provider is None or not hasattr(provider, "generate_text"):
            return None, "no chat connection — connect a chat model first", None
        return provider, config_files._stage_invention(cfg, stage), (cfg.get("systems") or {})

    def card_extras(self, ch, char_key: str) -> dict:
        scen = self.base_settings.scenarios.get(char_key)
        fields = ch.fields or {}
        return {"scenario_text": fields.get("scenario"),
                "first_mes": (scen.openings[0] if scen and scen.openings else None),
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

    def scenario_dir(self) -> Path:
        return self.root / "configs" / "scenarios"

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
        """The manifest enriched with served image URLs for the frontend."""
        m = self.portrait_manifest(key)
        base = f"/api/characters/{key}/portraits/img"
        glob = m.get("expression_prompts") or {}   # legacy global set (studio fallback only)
        outfits = []
        for o in m.get("outfits", []):
            oid = o["id"]
            files = o.get("expressions") or {}
            exprs = {emo: f"{base}/{oid}/{fn}" for emo, fn in files.items()}   # legacy {emo:url}
            # PER-OUTFIT expression set — each outfit owns its own range (NO DEFAULT_EMOTIONS
            # fallback: with no plan there are no expression cards). emotion -> {prompt, url}.
            prompts = o.get("expression_prompts") or {}
            emos = list(prompts.keys()) + [e for e in files if e not in prompts]
            expression_set = [{"emotion": e, "prompt": prompts.get(e, ""),
                               "url": f"{base}/{oid}/{files[e]}" if files.get(e) else None}
                              for e in emos]
            outfits.append({
                "id": oid, "name": o.get("name") or oid,
                "instruction": o.get("instruction", ""), "prompt": o.get("prompt", ""),
                "attire_prompt": o.get("attire_prompt", o.get("prompt", "")),
                "base": f"{base}/{oid}/base.png" if (o.get("base")) else None,
                "expressions": exprs,                    # legacy map (standalone studio)
                "expression_set": expression_set,        # per-outfit (cast wardrobe Sprites)
            })
        # `emotions` retained for the standalone studio (keeps DEFAULT_EMOTIONS); the cast wardrobe
        # uses per-outfit `expression_set` instead.
        return {"appearance": m.get("appearance", ""),
                "emotions": list(glob.keys()) or _prompts.DEFAULT_EMOTIONS,
                "expression_prompts": glob, "outfits": outfits}

    def scenario_cast(self, scn) -> list[dict]:
        """Cast enriched with each member's display name + avatar URL."""
        char_dir = self.char_dir()
        out = []
        for m in scn.cast:
            ch = self.base_settings.characters.get(m.character)
            out.append({
                "character": m.character, "primary": m.primary, "outfit": m.outfit,
                "name": ch.name if ch else m.character,
                "avatar": f"/api/characters/{m.character}/avatar"
                          if (char_dir / f"{m.character}.png").is_file() else None,
                "missing": ch is None,
            })
        return out

    def scenario_summary(self, key: str, scn) -> dict:
        return {
            "key": key, "name": scn.name, "setting": scn.setting,
            "openings": scn.openings, "background": scn.background,
            "cast": self.scenario_cast(scn),
            "lorebook_entries": len((scn.lorebook or {}).get("entries", []) or []),
        }

    def save_scenario(self, key: str, data: dict):
        from ..config.schema import Scenario
        Scenario(**data)  # validate shape (cast refs checked on full reload)
        self.scenario_dir().mkdir(parents=True, exist_ok=True)
        (self.scenario_dir() / f"{key}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.reload_settings()

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
        # Split the freshly-imported card into a clean character + default scenario.
        from ..config.migrate import split_cards_to_scenarios
        split_cards_to_scenarios(self.root)
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

    def compose_base_prompt(self, name: str, persona: str, appearance_notes: str = "",
                            role: str = "", model: str | None = None) -> dict:
        """THE single appearance authority: FEATURES_SCHEMA draft → co-occurrence enrichment →
        _assemble_base_prompt. Synchronous (call it inside a threadpool). Used by BOTH the ✨
        button AND cast generation, so a regenerated cast produces the SAME rich base prompt as
        the manual button. Returns {prompt, features, companions} or {error}."""
        from ..scenario.builder import DEFAULT_SYSTEMS
        cfg = self.load_story_builder()
        provider = self.author_provider(config_files._stage_model(cfg, "base_image", model))
        if provider is None:
            return {"error": "no author model configured"}
        context = "\n\n".join(p for p in [
            f"NAME: {name}",
            f"PERSONA:\n{persona}" if persona else "",
            f"APPEARANCE NOTES: {appearance_notes}" if appearance_notes else "",
            f"ROLE: {role}" if role else "",
        ] if p)
        system = (cfg.get("systems") or {}).get("base_image") or DEFAULT_SYSTEMS["base_image"]
        context = ("Fill the feature schema from this character's WRITTEN DESCRIPTION below "
                   "(persona + appearance).\n\n" + context)
        feats = (provider.generate_text(system=system, prompt=context, emits=_prompts.FEATURES_SCHEMA).data) or {}
        if not feats:
            return {"error": "model returned no structured features "
                             "(author model may not support structured output)"}
        # 2nd pass — the STANDARD image-prompt method (same as outfits): fetch ~50 RAW REFERENCE
        # LINES (each one real character's appearance tag set) — an unorganized 'soup' of real
        # characters — and CONSTRUCT the final appearance by drawing real tags from it. Grounds the
        # result in intact real bundles rather than a flat ranked list.
        draft = [str(t) for t in (feats.get("appearance") or [])]
        lines: list[str] = []
        try:
            from ..tags import get_cooccur
            ix = get_cooccur()
            lines = ix.sample_appearance_lines(draft, n=50) if (draft and ix.ready) else []
        except Exception:  # noqa: BLE001
            lines = []
        if lines:
            refine = (context + "\n\nYOUR DRAFT appearance tags:\n" + ", ".join(draft)
                      + "\n\nREFERENCE SOUP — ~50 REAL Danbooru characters' appearance tags (each line "
                        "is ONE real character; unorganized raw reference):\n"
                      + "\n".join(f"- {ln}" for ln in lines)
                      + "\n\nCONSTRUCT the FINAL appearance by DRAWING real tags from this soup that fit "
                        "THIS character — make it richer and more specific. Keep ~22-30 persistent "
                        "PHYSICAL tags (hair, eyes, skin, body, marks); do NOT add clothing, expression, "
                        "pose or background.")
            try:
                d2 = provider.generate_text(system=system, prompt=refine, emits=_prompts.FEATURES_SCHEMA).data
                if d2 and d2.get("appearance"):
                    feats = d2
            except Exception:  # noqa: BLE001 — keep the 1st-pass result
                pass
        return {"prompt": _prompts._assemble_base_prompt(feats), "features": feats, "companions": lines}

    def compose_outfit_prompt(self, persona: str, base_appearance: str, outfit_name: str,
                              attire_draft: str, model: str | None = None) -> dict:
        """The OUTFIT counterpart of `_compose_base_prompt` — a UNIQUE 2-step call PER outfit (so the
        model is never overwhelmed generating a whole wardrobe at once). Pass 1 fills OUTFIT_SCHEMA
        (detailed best-guess garments+colours+accessories+makeup+piercings, PLUS a range of emotions
        that fit this outfit); ~50 RAW reference outfit lines are retrieved from danbooru_character.csv;
        pass 2 CONSTRUCTS the final outfit from that soup. Returns {attire: snapped tag string,
        emotions: [{emotion, prompt}]} — the emotions drive THIS outfit's expression sprites. Empty
        attire on failure (caller keeps the draft)."""
        from ..scenario.builder import DEFAULT_SYSTEMS
        cfg = self.load_story_builder()
        provider = self.author_provider(config_files._stage_model(cfg, "wardrobe", model))
        if provider is None:
            return {"attire": "", "emotions": []}

        def _emos(d):
            out = []
            for e in (d.get("emotions") or []):
                emo = re.sub(r"[^\w\-]+", "-", str(e.get("emotion", "")).lower()).strip("-")
                if emo:
                    out.append({"emotion": emo, "prompt": str(e.get("prompt", "")).strip()})
            return out

        system = ((cfg.get("systems") or {}).get("wardrobe") or DEFAULT_SYSTEMS["wardrobe"]) + (
            "\n\nNOW compose the SINGLE outfit below: a COMPLETE, DETAILED `outfit` (every garment "
            "coloured, plus accessories, piercings and makeup that fit) AND a range of `emotions` that "
            "this outfit/scene would call for. Be generous and specific, never minimal.")
        context = "\n\n".join(p for p in [
            f"CHARACTER PERSONA:\n{persona}" if persona else "",
            (f"CHARACTER BASE APPEARANCE (body + persistent worn jewelry/piercings — pick a palette "
             f"that suits it; do NOT restate body/hair/face):\n{base_appearance}") if base_appearance else "",
            f"OUTFIT: {outfit_name}" if outfit_name else "",
            f"DRAFT / CONCEPT: {attire_draft}" if attire_draft else "",
        ] if p)
        feats = (provider.generate_text(system=system, prompt=context, emits=_prompts.OUTFIT_SCHEMA).data) or {}
        draft = [str(t) for t in (feats.get("outfit") or [])]
        emotions = _emos(feats)
        if not draft:
            return {"attire": "", "emotions": emotions}
        # 2nd pass — fetch ~50 RAW REFERENCE LINES from danbooru_character.csv, each line a whole real
        # outfit (one matching character's clothing tags). This is an unorganized 'soup' of real
        # outfits; this pass CONSTRUCTS the final outfit by drawing real tags from it. Colours come
        # from the real tags, so they stay Illustrious-valid; _snap_prompt + _dedupe clean the result.
        lines: list[str] = []
        try:
            from ..tags import get_cooccur
            ix = get_cooccur()
            lines = ix.sample_clothing_lines(draft, n=50) if ix.ready else []
        except Exception:  # noqa: BLE001
            lines = []
        if lines:
            refine = (context + "\n\nYOUR DRAFT outfit tags:\n" + ", ".join(draft)
                      + "\n\nREFERENCE SOUP — ~50 REAL outfits worn by similar Danbooru characters "
                        "(each line is ONE real character's clothing/accessory tags; unorganized, just "
                        "raw reference, many tags already carry COLOURS):\n"
                      + "\n".join(f"- {ln}" for ln in lines)
                      + "\n\nCONSTRUCT the final outfit for THIS character by DRAWING real tags from "
                        "this soup (mix and match the pieces that fit the concept). Requirements: a "
                        "RICH, complete look — top, bottom or dress, layers, LEGWEAR, FOOTWEAR — plus "
                        "fitting ACCESSORIES, PIERCINGS and MAKEUP. EVERY garment carries a COLOUR "
                        "(prefer the real coloured tags from the soup; a colour must form a real booru "
                        "tag — 'navy blue skirt', 'white blouse'). NEVER include both a bare garment "
                        "and its coloured version. ONE coherent palette.")
            try:
                d2 = provider.generate_text(system=system, prompt=refine, emits=_prompts.OUTFIT_SCHEMA).data
                if d2 and d2.get("outfit"):
                    draft = [str(t) for t in d2["outfit"]]
                if d2 and not emotions:
                    emotions = _emos(d2)
            except Exception:  # noqa: BLE001 — keep the 1st-pass result
                pass
        snapped = _prompts._snap_prompt(_prompts._safe_image_tags(", ".join(draft)))
        attire = ", ".join(_prompts._dedupe_outfit_tags([t.strip() for t in snapped.split(",") if t.strip()]))
        return {"attire": attire, "emotions": emotions}

    def refine_outfits(self, outfits: list, persona: str, base_appearance: str,
                       model: str | None = None, emit=None) -> list:
        """Run `_compose_outfit_prompt` over a wardrobe plan's outfits IN PARALLEL (the per-outfit
        refine pass): each gets careful 2-step booru `attire_prompt` AND its own range of
        `expression_prompts` (emotions correlated to the outfit). A failed outfit keeps its draft."""
        from concurrent.futures import ThreadPoolExecutor
        outfits = [dict(o) for o in (outfits or [])]
        if not outfits:
            return outfits

        def _one(o):
            try:
                r = self.compose_outfit_prompt(persona, base_appearance, o.get("name", ""),
                                               o.get("attire_prompt") or o.get("prompt") or "", model)
                if r.get("attire"):
                    o["attire_prompt"] = r["attire"]
                if r.get("emotions"):
                    o["expression_prompts"] = {e["emotion"]: e["prompt"] for e in r["emotions"]}
            except Exception:  # noqa: BLE001
                pass
            if emit:
                emit({"type": "item", "name": o.get("name", "outfit"), "text": o.get("attire_prompt", "")})
            return o

        with ThreadPoolExecutor(max_workers=min(len(outfits), 6)) as ex:
            return list(ex.map(_one, outfits))
