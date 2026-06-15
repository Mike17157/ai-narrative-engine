"""Loom web server — connection flow + tabbed UI + decoupled chat/image selection.

Serves one self-contained page (no build step). The left pane is tabbed:
  * Connection — SillyTavern-style: pick provider, paste key, Connect (validates +
    lists models), choose a model, Save. Saved connections become chat models.
  * Characters — pick the active persona.
  * Image — ComfyUI status/launch + the image model used for generation.

Chat runs the selected pipeline with the active connection as the chat model and
the selected image model as the image model — independently chosen.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

_SERVER_STARTED = time.time()  # for uptime in Settings → Server

import yaml
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..cards import extract_card_json, to_character
from ..card_sources import fetch_card
from ..comfy.server import ComfyServer, LaunchConfig, detect_desktop_install, get_server, register_server
from ..config import load_settings, load_user
from ..config.schema import Character, ModelDef, Settings
from ..connections import ConnectionStore, Connection, list_providers, ping_comfyui, test_connection
from ..engine import Runner


class WorkflowSaveRequest(BaseModel):
    model: str
    json: dict


class WorkflowTestRequest(BaseModel):
    model: str
    json: dict | None = None       # test the in-editor workflow (unsaved) if given
    prompt: str | None = None      # test positive prompt
    init_image: str | None = None  # base64/data-URL source image for img2img (LoadImage)


class LoraPromptsRequest(BaseModel):
    count: int = 60
    theme: str = "anime characters, diverse scenes, varied lighting"
    model: str | None = None       # text model for prompt generation


class LoraGenRequest(BaseModel):
    model: str                     # image model
    prompts: list[str]
    variations: int = 5


class LoraSaveRequest(BaseModel):
    name: str
    base_model: str | None = None
    images: list[dict]             # [{ src: dataURI, caption?: str }]


class PromptSetRequest(BaseModel):
    name: str
    prompts: list[str]


class CharacterImportRequest(BaseModel):
    data_b64: str            # PNG or JSON card bytes, base64-encoded
    filename: str = ""


class RunRequest(BaseModel):
    pipeline: str
    message: str
    character: str | None = None
    scenario: str | None = None  # the experience; resolves its primary cast character
    chat_model: str | None = None
    image_model: str | None = None
    use_reference: bool = False  # img2img: seed image steps from the character's reference image


class TestRequest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str | None = None
    kind: str = "text"


class SaveConnRequest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    id: str | None = None
    kind: str = "text"


def _load_dotenv(root: Path) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, existing env wins."""
    import os

    path = root / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _register_comfy(user, root: Path) -> str:
    c = user.comfyui
    launch = None
    if c.managed:
        if c.python or c.main_py or c.base_directory:
            launch = LaunchConfig(python=c.python, main_py=c.main_py, base_directory=c.base_directory)
        else:
            launch = detect_desktop_install()
    register_server(
        ComfyServer(
            c.base_url, managed=c.managed, launch=launch,
            startup_timeout=c.startup_timeout_s,
            log_path=Path(root) / "logs" / "comfyui.log", new_console=c.console,
        )
    )
    return c.base_url


def create_app(root: str | Path = ".") -> FastAPI:
    root = Path(root)
    _load_dotenv(root)
    # Split any bundled character cards into clean Character + default Scenario
    # before loading (idempotent — no-op once everything is migrated).
    from ..config.migrate import split_cards_to_scenarios
    split_cards_to_scenarios(root)
    base_settings = load_settings(root)
    user = load_user(root)
    comfy_url = _register_comfy(user, root)
    store = ConnectionStore(root)

    def effective_settings() -> Settings:
        """Base models plus a text model for each saved text/image-prompt connection."""
        models = dict(base_settings.models)
        for conn in [*store.list("text"), *store.list("image_prompt")]:
            models[conn.id] = ModelDef(provider=conn.provider, kind="text", options=conn.to_model_options())
        return Settings(models=models, characters=base_settings.characters, pipelines=base_settings.pipelines)

    def image_model_items() -> list[dict]:
        return [{"id": k, "name": k} for k, m in base_settings.models.items() if m.kind == "image"]

    def text_provider_for(model_sel: str | None):
        """Build a text provider for a model selection: a registered model key,
        or an OpenRouter (etc.) model id run through the active text connection."""
        from ..providers.registry import build_provider

        s = effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            return build_provider(s.models[model_sel])
        conn = store.active("text")
        if conn:
            return build_provider(ModelDef(
                provider=conn.provider, kind="text",
                options={"model": model_sel or conn.model, "api_key": conn.api_key, "base_url": conn.base_url},
            ))
        return None

    app = FastAPI(title="Loom")

    @app.get("/api/health")
    def health() -> dict:
        server = get_server(comfy_url)
        text_conn = store.active("text")
        image_conn = store.active("image")
        ip_conn = store.active("image_prompt") or text_conn
        return {
            "comfyui": {"base_url": server.base_url, "up": server.is_up(), "managed": server.managed},
            "characters": list(base_settings.characters),
            "pipelines": list(base_settings.pipelines),
            "profile": user.profile,
            "defaults": user.defaults,
            "active": store.active_map,
            "active_chat_model": (text_conn.model if text_conn else None),
            "active_image_model": (image_conn.model if image_conn else user.defaults.get("image_model")),
            "promptgen_model": (ip_conn.model if ip_conn else None),
        }

    def _char_dir() -> Path:
        return root / "configs" / "characters"

    _IMG_URL_RE = re.compile(r"https?://[^\s\"'<>)]+?\.(?:png|jpe?g|webp|gif)", re.IGNORECASE)

    def _character_images(k: str, c) -> list[dict]:
        """All images attached to a character, categorized and checked. Local files
        are verified to exist; URLs found in the card are listed as external."""
        char_dir = _char_dir()
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

    @app.get("/api/characters")
    def characters() -> list:
        char_dir = _char_dir()
        # Which story owns each GENERATED character — from the explicit `story` tag,
        # else derived from story cast membership (so pre-tag NPCs still group).
        owner: dict[str, str] = {}
        for sk, st in base_settings.stories.items():
            for m in st.cast:
                owner.setdefault(m.character, sk)

        def story_of(k: str, c) -> str | None:
            f = c.fields or {}
            if f.get("story"):
                return f["story"]
            return owner.get(k) if f.get("_generated") else None

        return [
            {
                "key": k, "name": c.name, "greeting": c.greeting, "system": c.system,
                "fields": c.fields,
                "image": c.image.model_dump(),
                "avatar": f"/api/characters/{k}/avatar" if (char_dir / f"{k}.png").is_file() else None,
                "reference": f"/api/characters/{k}/reference" if _reference_path(k) else None,
                "images": _character_images(k, c),
                # Story this character is attached to (generated NPCs); None = library.
                "story": story_of(k, c),
                "generated": bool((c.fields or {}).get("_generated")),
            }
            for k, c in base_settings.characters.items()
        ]

    @app.post("/api/characters/{key}/image")
    def set_character_image(key: str, body: dict):
        """Persist a character's portrait preset (checkpoint + base LoRAs + an
        optional named routing stack) into its YAML, then reload settings."""
        nonlocal base_settings
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _char_dir() / f"{safe}.yaml"
        if not path.is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            loras = [{"name": l["name"], "weight": float(l.get("weight", 1.0))}
                     for l in (body.get("loras") or []) if l.get("name")]
            data["image"] = {"checkpoint": (body.get("checkpoint") or None),
                             "loras": loras, "stack": (body.get("stack") or None)}
            from ..config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        base_settings = load_settings(root)
        return {"ok": True}

    @app.get("/api/characters/{key}/avatar")
    def character_avatar(key: str):
        """Serve the imported card PNG as the character's avatar (404 if none)."""
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _char_dir() / f"{safe}.png"
        if not path.is_file():
            return JSONResponse({"error": "no avatar"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    def _reference_path(key: str) -> Path | None:
        """The character's img2img reference image: a dedicated <key>.ref.png if
        set, else the card avatar <key>.png. None if neither exists."""
        safe = re.sub(r"[^\w\-]+", "", key)
        for fn in (f"{safe}.ref.png", f"{safe}.png"):
            p = _char_dir() / fn
            if p.is_file():
                return p
        return None

    @app.get("/api/characters/{key}/reference")
    def character_reference(key: str):
        """Serve the character's reference image (dedicated ref, else avatar)."""
        path = _reference_path(key)
        if path is None:
            return JSONResponse({"error": "no reference"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/characters/{key}/reference")
    async def set_character_reference(key: str, file: UploadFile = File(...)):
        """Upload/replace a dedicated reference image (<key>.ref.png) for img2img."""
        safe = re.sub(r"[^\w\-]+", "", key)
        if not (_char_dir() / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        data = await file.read()
        # normalize whatever was uploaded to PNG via Pillow if available; else store raw
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001 — Pillow missing or odd format; store as-is
            pass
        (_char_dir() / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.delete("/api/characters/{key}/reference")
    def clear_character_reference(key: str):
        """Drop the dedicated reference, falling back to the avatar."""
        safe = re.sub(r"[^\w\-]+", "", key)
        p = _char_dir() / f"{safe}.ref.png"
        if p.is_file():
            p.unlink()
        return {"ok": True}

    # -- character portraits: outfits + emotion expressions (miku.gg-style) ----
    #
    # A character gets a small image studio: the image-prompt (vision) model
    # describes the reference into a canonical APPEARANCE prompt; that seeds
    # OUTFITS (clothing/setting variations, rendered through the active image
    # model with the reference for identity); each outfit seeds a set of
    # EXPRESSIONS (persona + emotion -> personalized expression tags, rendered
    # img2img from the outfit's base so only the face changes). Stored under
    # configs/characters/portraits/<key>/ so it never collides with the *.yaml
    # config loader.
    DEFAULT_EMOTIONS = [
        "neutral", "happy", "sad", "angry", "surprised", "embarrassed",
        "scared", "smug", "crying", "laughing", "shy", "confused",
    ]

    _DESCRIBE_SYSTEM = (
        "You are an expert anime character tagger. Given a reference image and the character's "
        "persona, output ONE line of lowercase, comma-separated Danbooru tags describing the "
        "character's CANONICAL APPEARANCE so an Illustrious/SDXL model can redraw them consistently. "
        "Include: 1girl/1boy/solo as appropriate; the character's booru name tag ONLY if they are a "
        "clearly recognizable, well-known character; hair colour/length/style; eye colour; distinctive "
        "body features; and their DEFAULT outfit and accessories. Prefer what you actually see in the "
        "image; use the persona only to disambiguate. Do NOT include expression, pose, background, "
        "camera framing, art-style, medium or quality words. No sentences, no trailing period. Tags only."
    )
    _OUTFIT_SYSTEM = (
        "You compose a Danbooru-tag prompt for an alternate OUTFIT of an established anime character. "
        "You are given the character's canonical appearance tags, their persona, and an outfit "
        "instruction. Output ONE line of lowercase, comma-separated booru tags: KEEP every identity tag "
        "(count, name if any, hair, eyes, face, body), and REPLACE clothing, accessories and (only if "
        "the instruction implies it) the setting to match the instruction. Keep a neutral expression. "
        "Do NOT add art-style, medium or quality words. No sentences, no trailing period."
    )
    _EXPRESSION_SYSTEM = (
        "You choose facial-expression tags for an anime character reacting with a given EMOTION, "
        "personalized to their persona (a stoic character shows subtle expressions; an energetic one is "
        "exaggerated). Output ONE line of 3-7 lowercase, comma-separated Danbooru EXPRESSION tags ONLY "
        "— facial expression, eyes, eyebrows, mouth, and emotion-specific tags (blush, tears, sweatdrop, "
        "nose blush, wavy mouth, etc.). Do NOT restate hair, clothing, body, background, framing or the "
        "character's name. No sentences, no trailing period."
    )
    # Appended to every portrait render so sprites are consistent, chat-friendly busts.
    _PORTRAIT_FRAMING = "upper body, looking at viewer, simple background"
    # Outfit images are FULL BODY (whole-look reference), unlike the face-focused emotion sprites.
    _FULLBODY_FRAMING = ("solo, full body, standing, full body shot, head to toe, feet visible, "
                         "looking at viewer, simple background, grey background")

    def _portrait_dir(key: str, *, create: bool = False) -> Path:
        safe = re.sub(r"[^\w\-]+", "", key)
        d = root / "configs" / "characters" / "portraits" / safe
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def _portrait_manifest(key: str) -> dict:
        p = _portrait_dir(key) / "manifest.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — corrupt manifest: start fresh
                pass
        return {"appearance": "", "outfits": []}

    def _save_portrait_manifest(key: str, data: dict) -> None:
        d = _portrait_dir(key, create=True)
        (d / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _persona_text(c) -> str:
        """A compact persona blob for the image-prompt model: name + system +
        the descriptive card fields most relevant to look and demeanour."""
        parts = [f"Name: {c.name}" if c.name else ""]
        for fld in ("appearance", "description", "personality", "scenario"):
            v = (c.fields or {}).get(fld)
            if v:
                parts.append(f"{fld.capitalize()}: {v}")
        if c.system:
            parts.append(c.system)
        blob = "\n".join(p for p in parts if p).strip()
        return blob[:4000]  # keep the prompt bounded

    def _ip_provider():
        """The image-prompt model's text/vision provider (its own connection,
        falling back to the chat connection)."""
        from ..providers.registry import build_provider

        conn = store.active("image_prompt") or store.active("text")
        if conn is None:
            return None
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=conn.to_model_options()))

    def _image_provider(model_id: str | None = None):
        """A ComfyUIProvider for an image model — an explicit workflow id if given,
        else the active image connection (honouring its base_url override), plus
        the resolved model id. (provider, id) or (None, error_message)."""
        from ..providers.comfyui_provider import ComfyUIProvider

        conn = store.active("image")
        model_id = model_id or (conn.model if conn else None) or user.defaults.get("image_model")
        md = base_settings.models.get(model_id) if model_id else None
        if md is None or md.kind != "image":
            return None, "no image model selected — pick one in ⚙ Models → Image model"
        opts = dict(md.options)
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        return ComfyUIProvider(opts), model_id

    # Per-role image-workflow overrides. configs/image_roles.json maps each generation role
    # (base/style/sprite/scene/chat) to a workflow key; unset roles fall back to today's
    # behavior. Read fresh per request (no restart). See GET/POST /api/image-roles.
    IMAGE_ROLES = ("base", "style", "sprite", "scene", "chat")
    _ROLE_PINNED = {"style": "illustrious_style", "sprite": "sprite", "scene": "scene"}

    def load_image_roles() -> dict:
        path = root / "configs" / "image_roles.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8")) or {}
            except (ValueError, OSError):
                return {}
        return {}

    def _role_default(role: str) -> str | None:
        """What a role resolves to with NO override: the role-pinned workflow if it exists,
        else the active image connection's model, else the user.yaml default."""
        if role in _ROLE_PINNED and _ROLE_PINNED[role] in base_settings.models:
            return _ROLE_PINNED[role]
        conn = store.active("image")
        return (conn.model if conn else None) or user.defaults.get("image_model")

    def _role_model(role: str, override: str | None = None) -> str | None:
        """Resolve the workflow for a generation role: explicit request override > configured
        role override (configs/image_roles.json) > role default."""
        if override:
            return override
        v = (load_image_roles().get(role) or "").strip()
        return v if (v and v in base_settings.models) else _role_default(role)

    def _gen_text(provider, system: str, prompt: str, images: list[str] | None = None) -> str:
        res = provider.generate_text(system=system, prompt=prompt, images=images or [])
        return (res.text or "").strip().replace("\n", " ").strip(" ,.")

    def _portrait_outfit(manifest: dict, oid: str) -> dict | None:
        return next((o for o in manifest.get("outfits", []) if o.get("id") == oid), None)

    def _portrait_payload(key: str) -> dict:
        """The manifest enriched with served image URLs for the frontend."""
        m = _portrait_manifest(key)
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
                "emotions": list(glob.keys()) or DEFAULT_EMOTIONS,
                "expression_prompts": glob, "outfits": outfits}

    @app.get("/api/characters/{key}/portraits")
    def get_portraits(key: str):
        if key not in base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        return _portrait_payload(key)

    @app.get("/api/characters/{key}/portraits/img/{oid}/{file}")
    def portrait_image(key: str, oid: str, file: str):
        if not re.fullmatch(r"[\w\-]+", oid) or not re.fullmatch(r"[\w\-]+\.png", file):
            return JSONResponse({"error": "bad path"}, status_code=404)
        p = (_portrait_dir(key) / oid / file).resolve()
        if _portrait_dir(key).resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    @app.post("/api/characters/{key}/portraits/describe")
    def portrait_describe(key: str, body: dict | None = None):
        """Vision-caption the reference image into a canonical appearance prompt."""
        c = base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider = _ip_provider()
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect an image-prompt model first (⚙ Models)"}, status_code=400)
        ref = _reference_path(key)
        if ref is None:
            return JSONResponse({"error": "no reference image — set one above first"}, status_code=400)
        uri = "data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()
        try:
            appearance = _gen_text(
                provider, _DESCRIBE_SYSTEM,
                f"Persona:\n{_persona_text(c)}\n\nDescribe this character's canonical appearance.",
                images=[uri])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"describe failed: {exc}"}, status_code=500)
        m = _portrait_manifest(key)
        m["appearance"] = appearance
        _save_portrait_manifest(key, m)
        return {"appearance": appearance}

    @app.put("/api/characters/{key}/portraits/appearance")
    def portrait_set_appearance(key: str, body: dict):
        if key not in base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = _portrait_manifest(key)
        m["appearance"] = (body or {}).get("appearance", "")
        _save_portrait_manifest(key, m)
        return {"ok": True}

    @app.post("/api/characters/{key}/portraits/outfit")
    def portrait_add_outfit(key: str, body: dict):
        """Create an outfit: rewrite the appearance prompt for the instruction
        (or use the appearance as-is for a blank/base outfit), render its base
        image through the active image model with the reference for identity."""
        c = base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        name = (body.get("name") or "Outfit").strip()
        instruction = (body.get("instruction") or "").strip()
        m = _portrait_manifest(key)
        appearance = m.get("appearance", "")
        if not appearance:
            return JSONResponse({"error": "describe the appearance first"}, status_code=400)

        # Compose the outfit prompt.
        if instruction:
            ipp = _ip_provider()
            if ipp is None:
                return JSONResponse({"error": "connect an image-prompt model first (⚙ Models)"}, status_code=400)
            try:
                prompt = _gen_text(
                    ipp, _OUTFIT_SYSTEM,
                    f"Persona:\n{_persona_text(c)}\n\nCanonical appearance tags:\n{appearance}\n\n"
                    f"Outfit instruction: {instruction}")
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"outfit prompt failed: {exc}"}, status_code=500)
        else:
            prompt = appearance

        provider, model_id = _image_provider(body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        ref = _reference_path(key)
        # Outfit base is a FULL-BODY whole-look image (not a bust) — see the cast wardrobe flow.
        render_prompt = f"{prompt}, neutral expression, {_FULLBODY_FRAMING}"
        try:
            from ..comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(
                prompt=render_prompt, init_image=ref.read_bytes() if ref else None)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if not result.images:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)

        oid = re.sub(r"[^\w\-]+", "-", name.lower()).strip("-") or "outfit"
        existing = {o["id"] for o in m["outfits"]}
        base_oid, n = oid, 2
        while oid in existing:
            oid, n = f"{base_oid}-{n}", n + 1
        (_portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (_portrait_dir(key) / oid / "base.png").write_bytes(result.images[0])
        outfit = {"id": oid, "name": name, "instruction": instruction, "prompt": prompt,
                  "base": "base.png", "expressions": {}}
        m["outfits"].append(outfit)
        _save_portrait_manifest(key, m)
        return _portrait_payload(key)

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/expression")
    def portrait_render_expression(key: str, oid: str, body: dict):
        """Render ONE emotion expression for an outfit (the frontend loops over
        the set for live progress). Persona + emotion -> expression tags, then
        img2img seeded from the outfit base so only the face changes."""
        c = base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        emotion = ((body or {}).get("emotion") or "").strip()
        if not emotion:
            return JSONResponse({"error": "emotion required"}, status_code=400)
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        base_png = _portrait_dir(key) / oid / "base.png"
        if not base_png.is_file():
            return JSONResponse({"error": "outfit has no base image"}, status_code=400)

        ipp = _ip_provider()
        if ipp is None:
            return JSONResponse({"error": "connect an image-prompt model first (⚙ Models)"}, status_code=400)
        try:
            expr_tags = _gen_text(
                ipp, _EXPRESSION_SYSTEM,
                f"Persona:\n{_persona_text(c)}\n\nEmotion: {emotion}\n\n"
                f"How does THIS character express '{emotion}'?")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"expression tags failed: {exc}"}, status_code=500)

        provider, model_id = _image_provider((body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        render_prompt = f"{outfit.get('prompt','')}, {expr_tags}, {_PORTRAIT_FRAMING}"
        try:
            from ..comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(prompt=render_prompt, init_image=base_png.read_bytes())
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if not result.images:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)

        emo_safe = re.sub(r"[^\w\-]+", "-", emotion.lower()).strip("-") or "emotion"
        (_portrait_dir(key) / oid / f"{emo_safe}.png").write_bytes(result.images[0])
        outfit.setdefault("expressions", {})[emo_safe] = f"{emo_safe}.png"
        _save_portrait_manifest(key, m)
        return {"emotion": emo_safe, "tags": expr_tags,
                "url": f"/api/characters/{key}/portraits/img/{oid}/{emo_safe}.png"}

    @app.delete("/api/characters/{key}/portraits/outfit/{oid}")
    def portrait_delete_outfit(key: str, oid: str):
        m = _portrait_manifest(key)
        if _portrait_outfit(m, oid) is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        m["outfits"] = [o for o in m["outfits"] if o.get("id") != oid]
        _save_portrait_manifest(key, m)
        import shutil
        d = (_portrait_dir(key) / oid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        return _portrait_payload(key)

    @app.delete("/api/characters/{key}/portraits/outfit/{oid}/expression/{emo}")
    def portrait_delete_expression(key: str, oid: str, emo: str):
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        (outfit.get("expressions") or {}).pop(emo, None)         # rendered image
        (outfit.get("expression_prompts") or {}).pop(emo, None)  # the expression definition
        _save_portrait_manifest(key, m)
        f = (_portrait_dir(key) / oid / f"{emo}.png")
        if f.is_file():
            f.unlink()
        return _portrait_payload(key)

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/expression-prompt")
    def portrait_set_expression_prompt(key: str, oid: str, body: dict):
        """Add or edit ONE expression (emotion + optional face prompt) for THIS outfit only — each
        outfit carries its own expression range (the runtime picks the closest by emotion vector
        similarity). Returns the portrait payload."""
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        body = body or {}
        emo = re.sub(r"[^\w\-]+", "-", (body.get("emotion") or "").lower()).strip("-")
        if not emo:
            return JSONResponse({"error": "emotion required"}, status_code=400)
        outfit.setdefault("expression_prompts", {})[emo] = (body.get("prompt") or "").strip()
        _save_portrait_manifest(key, m)
        return _portrait_payload(key)

    @app.post("/api/characters/{key}/card")
    def update_character_card(key: str, body: dict):
        """Edit the character card's text (name / persona / greeting / appearance
        + any extra fields), persist to its YAML, and reload settings."""
        nonlocal base_settings
        if key not in base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _char_dir() / f"{safe}.yaml"
        if not path.is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if "name" in body:
                data["name"] = (body.get("name") or "").strip() or data.get("name") or key
            if "system" in body:
                data["system"] = body.get("system") or ""
            if "greeting" in body:
                data["greeting"] = body.get("greeting") or None
            if isinstance(body.get("fields"), dict):
                data["fields"] = {**(data.get("fields") or {}), **body["fields"]}
            from ..config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        base_settings = load_settings(root)
        return {"ok": True}

    @app.post("/api/characters/{key}/reference/from-url")
    async def set_reference_from_url(key: str, body: dict):
        """Download an image (e.g. a card-link URL) and store it as the character's
        reference / base image (<key>.ref.png)."""
        import httpx

        safe = re.sub(r"[^\w\-]+", "", key)
        if not (_char_dir() / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        url = (body or {}).get("url", "")
        if not re.match(r"^https?://", url):
            return JSONResponse({"error": "bad url"}, status_code=400)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                data = resp.content
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"download failed: {exc}"}, status_code=502)
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001 — Pillow missing or odd format; store as-is
            pass
        (_char_dir() / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    # -- scenarios: the situational half of the character/scenario split --------
    def _scenario_dir() -> Path:
        return root / "configs" / "scenarios"

    def _scenario_cast(scn) -> list[dict]:
        """Cast enriched with each member's display name + avatar URL."""
        char_dir = _char_dir()
        out = []
        for m in scn.cast:
            ch = base_settings.characters.get(m.character)
            out.append({
                "character": m.character, "primary": m.primary, "outfit": m.outfit,
                "name": ch.name if ch else m.character,
                "avatar": f"/api/characters/{m.character}/avatar"
                          if (char_dir / f"{m.character}.png").is_file() else None,
                "missing": ch is None,
            })
        return out

    def _scenario_summary(key: str, scn) -> dict:
        return {
            "key": key, "name": scn.name, "setting": scn.setting,
            "openings": scn.openings, "background": scn.background,
            "cast": _scenario_cast(scn),
            "lorebook_entries": len((scn.lorebook or {}).get("entries", []) or []),
        }

    @app.get("/api/scenarios")
    def list_scenarios() -> list:
        return [_scenario_summary(k, s) for k, s in base_settings.scenarios.items()]

    @app.get("/api/scenarios/{key}")
    def get_scenario(key: str):
        scn = base_settings.scenarios.get(key)
        if scn is None:
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        return {**scn.model_dump(), "key": key, "cast": _scenario_cast(scn)}

    def _save_scenario(key: str, data: dict):
        nonlocal base_settings
        from ..config.schema import Scenario
        Scenario(**data)  # validate shape (cast refs checked on full reload)
        _scenario_dir().mkdir(parents=True, exist_ok=True)
        (_scenario_dir() / f"{key}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        base_settings = load_settings(root)

    @app.post("/api/scenarios")
    def create_scenario(body: dict):
        body = body or {}
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        key = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "scenario"
        base = key
        n = 2
        while (_scenario_dir() / f"{key}.yaml").is_file():
            key, n = f"{base}_{n}", n + 1
        data = {
            "name": name, "setting": body.get("setting", ""),
            "openings": [o for o in (body.get("openings") or []) if o],
            "lorebook": body.get("lorebook") or {},
            "cast": body.get("cast") or [],
            "background": body.get("background"),
        }
        try:
            _save_scenario(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.post("/api/scenarios/{key}")
    def update_scenario(key: str, body: dict):
        scn = base_settings.scenarios.get(key)
        if scn is None:
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        body = body or {}
        data = scn.model_dump()
        for f in ("name", "setting", "openings", "lorebook", "cast", "background", "fields"):
            if f in body:
                data[f] = body[f]
        try:
            _save_scenario(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.delete("/api/scenarios/{key}")
    def delete_scenario(key: str):
        nonlocal base_settings
        p = _scenario_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml"
        if not p.is_file():
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        p.unlink()
        base_settings = load_settings(root)
        return {"ok": True}

    # -- Story Builder: author a story experience (locations + cast) from a card -
    STORY_BUILDER_DEFAULT = {"model": "", "models": {}, "inventions": {}, "systems": {}}

    def _author_provider(model_sel: str | None):
        """Text provider for the Story Builder — an explicit (selectable) author
        model if given, else the active chat connection. Always raises the token
        ceiling (the default 1024 truncates a stage's structured JSON)."""
        from ..providers.registry import build_provider

        s = effective_settings()
        if model_sel and model_sel in s.models and s.models[model_sel].kind == "text":
            md = s.models[model_sel].model_copy()
            md.options = {**md.options, "max_tokens": 4096}
            return build_provider(md)
        conn = store.active("text")
        if conn is None:
            return None
        opts = {**conn.to_model_options(), "max_tokens": 4096}
        if model_sel:
            opts["model"] = model_sel
        return build_provider(ModelDef(provider=conn.provider, kind="text", options=opts))

    def load_story_builder() -> dict:
        path = root / "configs" / "story_builder.json"
        cfg = dict(STORY_BUILDER_DEFAULT)
        if path.is_file():
            try:
                cfg.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
        return cfg

    @app.get("/api/story-builder")
    def get_story_builder() -> dict:
        return load_story_builder()

    @app.post("/api/story-builder")
    def set_story_builder(body: dict):
        path = root / "configs" / "story_builder.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**STORY_BUILDER_DEFAULT, **(body or {})}, indent=2), encoding="utf-8")
        return {"ok": True}

    def _story_dir() -> Path:
        return root / "configs" / "stories"

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

    def _builder_ctx(body: dict, stage: str | None = None):
        """(provider, invention, systems) for a builder step, or (None, err, _).
        Each stage carries its own model + invention config."""
        cfg = load_story_builder()
        provider = _author_provider(_stage_model(cfg, stage, (body or {}).get("model")))
        if provider is None or not hasattr(provider, "generate_text"):
            return None, "no chat connection — connect a chat model first", None
        return provider, _stage_invention(cfg, stage), (cfg.get("systems") or {})

    def _card_extras(ch, char_key: str) -> dict:
        scen = base_settings.scenarios.get(char_key)
        fields = ch.fields or {}
        return {"scenario_text": fields.get("scenario"),
                "first_mes": (scen.openings[0] if scen and scen.openings else None),
                "mes_example": fields.get("mes_example"),
                "appearance": fields.get("appearance")}

    # The build is a controlled, stepped procedure — one LLM call per endpoint,
    # reviewed in the UI before the next. Nothing is written until /api/stories.
    @app.post("/api/stories/storyboard")
    async def story_storyboard(body: dict):
        """Stage 1 — STREAM the storyboard live (LOGLINE/PREMISE/TONE/THEMES/BEATS)
        so it can be watched. Emits `delta` text events + a final `board` event.
        If the client disconnects (cancel), upstream generation is stopped."""
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ..scenario import parse_storyboard, storyboard_inputs

        body = body or {}
        ch = base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, invention, systems = _builder_ctx(body, "storyboard")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        system, prompt = storyboard_inputs(name=ch.name, persona=ch.system,
                                           extras=_card_extras(ch, body["character"]),
                                           invention=invention, systems=systems)

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def on_delta(t: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                res = await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, on_delta=on_delta,
                    cancel=cancel_evt.is_set))
                if not cancel_evt.is_set():
                    board = parse_storyboard(res.text or "")
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "board", "board": board})
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()  # client disconnected / cancelled → stop upstream
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/stories/builder/prompt")
    def builder_prompt(body: dict):
        """Preview/inspect a builder STAGE's prompt before generating. Returns the
        editable `base` system prompt, the `default` (for reset), and the effective
        `system` (base + invention directive). For the storyboard stage it also
        composes the user-message `prompt` for the given character. Stages:
        storyboard · locations · characters · wardrobe."""
        from ..scenario.builder import NEEDS_IMAGE, NO_INVENTION, DEFAULT_SYSTEMS, _sys, storyboard_inputs

        body = body or {}
        stage = body.get("stage", "storyboard")
        if stage not in DEFAULT_SYSTEMS:
            return JSONResponse({"error": f"unknown stage '{stage}'"}, status_code=400)
        cfg = load_story_builder()
        invention = _stage_invention(cfg, stage)
        systems = cfg.get("systems") or {}
        out = {
            "stage": stage,
            "base": systems.get(stage) or DEFAULT_SYSTEMS[stage],
            "default": DEFAULT_SYSTEMS[stage],
            "system": _sys(systems, stage, invention),
            "model": _stage_model(cfg, stage, body.get("model")),  # this stage's model ('' = active chat)
            "invention": invention,                                 # this stage's invention level
            "no_invention": stage in NO_INVENTION,
            "requires_image": stage in NEEDS_IMAGE,                  # needs a vision model
        }
        ch = base_settings.characters.get(body.get("character"))
        if stage == "storyboard" and ch is not None:
            _, out["prompt"] = storyboard_inputs(name=ch.name, persona=ch.system,
                                                 extras=_card_extras(ch, body["character"]),
                                                 invention=invention, systems=systems)
        return out

    @app.post("/api/stories/builder/test")
    async def builder_test(body: dict):
        """Test ONE builder stage end-to-end for a chosen character, using that stage's
        (possibly unsaved) model + invention + system prompt. Prerequisite stages run
        with their SAVED config. Returns a readable {summary, output} to preview before
        committing the config."""
        from fastapi.concurrency import run_in_threadpool

        from ..scenario import builder as B
        from ..scenario import extract_characters, extract_locations, plan_wardrobe

        body = body or {}
        stage = body.get("stage", "storyboard")
        if stage not in B.DEFAULT_SYSTEMS:
            return JSONResponse({"error": f"unknown stage '{stage}'"}, status_code=400)
        ch = base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "pick a character to test with"}, status_code=400)
        cfg = load_story_builder()
        saved = cfg.get("systems") or {}
        systems = dict(saved)
        if body.get("system"):           # the unsaved edit for the target stage
            systems[stage] = body["system"]
        t_inv = body.get("invention") or _stage_invention(cfg, stage)
        t_prov = _author_provider(body.get("model") or _stage_model(cfg, stage))
        if t_prov is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        extras = _card_extras(ch, body.get("character"))
        fields = ch.fields or {}

        def gen_board(target: bool):
            inv = t_inv if (target and stage == "storyboard") else _stage_invention(cfg, "storyboard")
            sysd = systems if (target and stage == "storyboard") else saved
            prov = t_prov if (target and stage == "storyboard") else _author_provider(_stage_model(cfg, "storyboard"))
            s, p = B.storyboard_inputs(name=ch.name, persona=ch.system, extras=extras, invention=inv, systems=sysd)
            return B.parse_storyboard(prov.generate_text(system=s, prompt=p).text or "")

        def run():
            if stage == "storyboard":
                bd = gen_board(True)
                out = "\n".join([f"LOGLINE: {bd['logline']}", f"TONE: {bd['tone']}", "",
                                 f"{len(bd['beats'])} chapters:"] +
                                [f"{i}. {b.get('title') or '(untitled)'}  —  @{b.get('location') or '?'}"
                                 for i, b in enumerate(bd['beats'], 1)])
                return f"{len(bd['beats'])} chapters", out
            if stage == "base_image":
                ctx = "\n\n".join(s for s in [
                    f"NAME: {ch.name}", f"PERSONA:\n{ch.system}" if ch.system else "",
                    f"APPEARANCE NOTES: {fields.get('appearance')}" if fields.get("appearance") else "",
                    f"ROLE: {fields.get('role')}" if fields.get("role") else ""] if s)
                sysb = systems.get("base_image") or B.DEFAULT_SYSTEMS["base_image"]
                imgs = []
                ref = _reference_path(body["character"])
                if ref:
                    imgs = ["data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()]
                    ctx = "Describe the CHARACTER IN THE IMAGE.\n\n" + ctx
                feats = t_prov.generate_text(system=sysb, prompt=ctx, emits=FEATURES_SCHEMA, images=imgs).data or {}
                return "feature schema filled" + (" (from reference image)" if imgs else ""), _assemble_base_prompt(feats)
            bd = gen_board(False)
            if stage == "locations":
                locs = extract_locations(t_prov, board=bd, invention=t_inv, systems=systems).get("locations", [])
                return f"{len(locs)} locations", "\n".join(
                    f"• {l.get('name')} ({l.get('id')})\n  {l.get('background_prompt') or l.get('description','')}" for l in locs)
            if stage == "characters":
                npcs = extract_characters(t_prov, name=ch.name, persona=ch.system, board=bd,
                                          extras=extras, invention=t_inv, systems=systems).get("npcs", [])
                return f"{len(npcs)} characters", "\n\n".join(
                    f"• {n.get('name')} — {n.get('role','')}\n  {n.get('appearance','')}" for n in npcs) or "(no supporting cast in this storyboard)"
            if stage == "wardrobe":
                story = {"premise": bd.get("premise", ""), "tone": bd.get("tone", ""),
                         "storyboard": {"logline": bd.get("logline", ""), "beats": bd.get("beats", [])}}
                plan = plan_wardrobe(t_prov, char_name=ch.name, persona=ch.system,
                                     appearance=fields.get("appearance", ""), story=story,
                                     invention=t_inv, systems=systems)
                outs = "\n".join(f"• {o['name']}: {o['attire_prompt']}" for o in plan.get("outfits", []))
                exprs = "\n".join(f"• {k}: {v}" for k, v in (plan.get("expressions") or {}).items())
                return (f"{len(plan.get('outfits', []))} outfits, {len(plan.get('expressions') or {})} expressions",
                        f"OUTFITS\n{outs}\n\nEXPRESSIONS\n{exprs}")
            return "", ""

        try:
            summary, output = await run_in_threadpool(run)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"test failed: {exc}"}, status_code=500)
        return {"stage": stage, "summary": summary, "output": output}

    @app.post("/api/stories/extract-scenes")
    def story_extract_scenes(body: dict):
        """Stage 2 — extract neutral locations (pure backgrounds) from the beats."""
        from ..scenario import extract_locations

        provider, invention, systems = _builder_ctx(body or {}, "locations")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        board = (body or {}).get("board") or {}
        try:
            return extract_locations(provider, board=board, invention=invention, systems=systems)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/stories/extract-characters")
    def story_extract_characters(body: dict):
        """Stage 3 — build the cast in TWO steps: (1) distill the source card into the main
        character, then (2) flesh the supporting NPCs from the beats in that SAME structure.
        Returns { cast: [...] } — ONE uniform list with the protagonist first, flagged `primary`
        (no separate "main character card")."""
        from ..scenario import extract_characters, extract_protagonist

        body = body or {}
        ch = base_settings.characters.get(body.get("character"))
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, invention, systems = _builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        extras = _card_extras(ch, body["character"])
        try:
            base = extract_protagonist(provider, name=ch.name, persona=ch.system or "",
                                       extras=extras, invention="faithful", systems=systems)
            out = extract_characters(provider, name=base["name"], persona=base["persona"],
                                     board=body.get("board") or {}, extras=extras,
                                     invention=invention, systems=systems,
                                     reference_card=base["persona"])
            npcs = out.get("npcs", [])
            # ONE uniform cast list — protagonist first, flagged `primary`. Compose the SUPERIOR ✨
            # image description (_compose_base_prompt: FEATURES_SCHEMA → Danbooru co-occurrence) for
            # EVERY member IN PARALLEL, stored on `base_prompt` (what the renderer uses), so the
            # wizard shows/saves the rich description, not the basic tags. The basic `appearance` is
            # kept as the seed fed into the composer.
            cast = [{**base, "primary": True}] + [{**n, "primary": False} for n in npcs]
            from concurrent.futures import ThreadPoolExecutor

            def _bp(item):
                idx, p = item
                try:
                    r = _compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                             p.get("appearance", ""), p.get("role", ""))
                    return (idx, r.get("prompt", "") if isinstance(r, dict) else "")
                except Exception:  # noqa: BLE001
                    return (idx, "")

            with ThreadPoolExecutor(max_workers=min(len(cast), 6)) as ex:
                bps = dict(ex.map(_bp, list(enumerate(cast))))
            for idx, member in enumerate(cast):
                if bps.get(idx):
                    member["base_prompt"] = bps[idx]
            return {"cast": cast}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    def _write_npc(npc: dict, story_key: str = "", ref_from: str | None = None,
                   base_prompt: str = "") -> str:
        """Create a story-bound Character file (a generated NPC, or the duplicated
        protagonist); returns its key. Tagged with its owning `story` so it's scoped /
        grouped and hidden from global pickers. If `ref_from` is given, that character's
        reference image is copied across. `base_prompt` (from the shared ✨ composer) is stored
        as fields.base_prompt so the base image renders richly without a manual ✨ pass."""
        char_dir = _char_dir()
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
            ref = _reference_path(ref_from)
            if ref and ref.is_file():
                import shutil
                try:
                    shutil.copyfile(ref, char_dir / f"{key}.ref.png")
                except Exception:  # noqa: BLE001
                    pass
        return key

    @app.post("/api/stories")
    def save_story(body: dict):
        """Materialize an accepted draft: create Character files for proposed NPCs,
        remap cast names→keys, link beats to locations, and write the Story."""
        nonlocal base_settings
        from ..config.schema import Story

        draft = body or {}
        name = (draft.get("name") or "Story").strip()
        primary_key = draft.get("source_character") or draft.get("character")
        if primary_key and primary_key not in base_settings.characters:
            primary_key = None

        # Ensure a UNIQUE display name (not just a unique key) so the story list isn't
        # full of identically-named entries.
        existing_names = {st.name for st in base_settings.stories.values()}
        if name in existing_names:
            base_name, n = name, 2
            while name in existing_names:
                name, n = f"{base_name} ({n})", n + 1

        # Decide the story key first so NPCs can be tagged with it.
        skey_base = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "story"
        skey, i = skey_base, 2
        while (_story_dir() / f"{skey}.yaml").is_file():
            skey, i = f"{skey_base}_{i}", i + 1

        created: list[str] = []
        from ..scenario import extract_protagonist

        # UNIFIED CAST — ONE ordered list of members; the protagonist is simply the member flagged
        # `primary` (no separate "main character card" path). The wizard sends `cast`; older drafts
        # sent `primary_card` + `proposed_npcs`, still accepted for compatibility.
        cast_in = draft.get("cast")
        if cast_in is None:
            cast_in = ([{**draft["primary_card"], "primary": True}] if draft.get("primary_card") else []) \
                + [{**n, "primary": False} for n in (draft.get("proposed_npcs") or [])]
        cast_in = [dict(m) for m in cast_in if isinstance(m, dict)]

        # Always guarantee a protagonist: if a source character is set but no member is flagged
        # primary, distil one faithfully from the source card and prepend it. (The original library
        # card stays free; the protagonist is a fresh story-bound character like everyone else.)
        src = base_settings.characters.get(primary_key) if primary_key else None
        if src is not None and not any(m.get("primary") for m in cast_in):
            base_card = None
            try:
                provider, _inv, systems = _builder_ctx(draft, "characters")
                if provider is not None:
                    base_card = extract_protagonist(
                        provider, name=src.name, persona=src.system or "",
                        extras=_card_extras(src, primary_key), invention="faithful", systems=systems)
            except Exception:  # noqa: BLE001
                base_card = None
            if not base_card:
                base_card = {"name": src.name, "persona": src.system or "",
                             "appearance": (src.fields or {}).get("appearance", ""),
                             "role": (src.fields or {}).get("role") or "protagonist"}
            cast_in.insert(0, {**base_card, "primary": True})

        # Compose the RICH base-image prompt via the shared ✨ pipeline (the single appearance
        # authority) for EVERY member IN PARALLEL — reuse one already on the entry, only compose the
        # missing ones. Same path as the ✨ button and regenerate_cast.
        from concurrent.futures import ThreadPoolExecutor

        def _bp(item):
            idx, p = item
            existing = (p.get("base_prompt") or "").strip()
            if existing:
                return (idx, existing)
            try:
                r = _compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                         p.get("appearance", ""), p.get("role", ""))
                return (idx, r.get("prompt", "") if isinstance(r, dict) else "")
            except Exception:  # noqa: BLE001
                return (idx, "")

        bps: dict[int, str] = {}
        if cast_in:
            with ThreadPoolExecutor(max_workers=min(len(cast_in), 6)) as ex:
                bps = dict(ex.map(_bp, list(enumerate(cast_in))))

        # Write EVERY member through the SAME _write_npc path. The protagonist differs only by
        # carrying the source card's reference image (ref_from) and the `primary` flag — exactly one
        # member is primary.
        cast = []
        seen_primary = False
        for idx, member in enumerate(cast_in):
            is_primary = bool(member.get("primary")) and not seen_primary
            seen_primary = seen_primary or is_primary
            k = _write_npc(member, story_key=skey,
                           ref_from=(primary_key if is_primary else None),
                           base_prompt=bps.get(idx, ""))
            created.append(k)
            cast.append({"character": k, "primary": is_primary})

        # Locations are self-contained neutral places — no cast remapping needed.
        locations = []
        name_to_loc: dict[str, str] = {}
        for l in draft.get("locations", []) or []:
            lid = l.get("id")
            locations.append({
                "id": lid, "name": l.get("name", lid),
                "description": l.get("description", ""),
                "background_prompt": l.get("background_prompt", ""),
                "background": l.get("background"),
            })
            if l.get("name"):
                name_to_loc[l["name"].lower()] = lid

        # Persist the storyboard as the spine; link each beat's place to a location id.
        board = draft.get("storyboard") or {}
        beats = []
        for b in board.get("beats", []) or []:
            loc = (b.get("location") or "")
            beats.append({"title": b.get("title", ""), "summary": b.get("summary", ""),
                          "location": name_to_loc.get(loc.lower(), loc),
                          "characters": b.get("characters", [])})
        storyboard = {"logline": board.get("logline", ""), "beats": beats}

        story = {
            "name": name, "premise": draft.get("premise", ""), "tone": draft.get("tone", ""),
            "themes": draft.get("themes", []), "storyboard": storyboard, "cast": cast,
            "lorebook": draft.get("lorebook") or {}, "locations": locations,
            "start": draft.get("start"), "background": draft.get("background"),
            "fields": {"source_character": primary_key} if primary_key else {},
        }
        try:
            Story(**story)  # validate (start ∈ locations, cast ∈ characters)
            _story_dir().mkdir(parents=True, exist_ok=True)
            (_story_dir() / f"{skey}.yaml").write_text(
                yaml.safe_dump(story, allow_unicode=True, sort_keys=False), encoding="utf-8")
            base_settings = load_settings(root)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save story: {exc}"}, status_code=400)
        return {"ok": True, "key": skey, "created_characters": created}

    @app.get("/api/stories")
    def list_stories() -> list:
        out = []
        for k, st in base_settings.stories.items():
            f = _story_dir() / f"{k}.yaml"
            mtime = f.stat().st_mtime if f.is_file() else 0.0
            out.append({"key": k, "name": st.name, "premise": st.premise, "tone": st.tone,
                        "themes": st.themes, "locations": len(st.locations), "start": st.start,
                        "cast": [m.character for m in st.cast], "_mtime": mtime})
        out.sort(key=lambda s: s["_mtime"], reverse=True)  # newest first
        for s in out:
            s.pop("_mtime", None)
        return out

    @app.get("/api/stories/{key}")
    def get_story(key: str):
        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {**st.model_dump(), "key": key}

    @app.put("/api/stories/{key}")
    def update_story(key: str, body: dict):
        """Edit a saved story in place (iterate). Updates only the fields sent;
        cast members are existing character keys, so no NPCs are re-created."""
        nonlocal base_settings
        from ..config.schema import Story

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        data = st.model_dump()
        for f in ("name", "premise", "tone", "themes", "storyboard", "cast",
                  "lorebook", "locations", "start", "background", "fields"):
            if f in (body or {}):
                data[f] = body[f]
        try:
            Story(**data)
            (_story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml").write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            base_settings = load_settings(root)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.delete("/api/stories/{key}")
    def _prune_orphan_characters() -> list[str]:
        """Auto-delete GENERATED characters that no longer belong to any story — their owning
        story was deleted, or they were never cast. Library / imported cards (not `_generated`)
        are NEVER touched; they're independent. Removes the yaml + avatar/ref + portraits, like
        delete_character. Idempotent — safe to call after any story/cast change or at startup.
        Returns the keys removed."""
        nonlocal base_settings
        import shutil
        live_stories = set(base_settings.stories.keys())
        cast_members = {m.character for st in base_settings.stories.values() for m in st.cast}
        cdir = _char_dir()
        removed: list[str] = []
        for k, c in list(base_settings.characters.items()):
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
            shutil.rmtree(_portrait_dir(k), ignore_errors=True)
            removed.append(k)
        if removed:
            base_settings = load_settings(root)
        return removed

    def delete_story(key: str):
        nonlocal base_settings
        p = _story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml"
        if not p.is_file():
            return JSONResponse({"error": "no such story"}, status_code=404)
        p.unlink()
        base_settings = load_settings(root)
        removed = _prune_orphan_characters()  # cascade: drop the now-storyless generated cast
        return {"ok": True, "removed_characters": removed}

    def _compose_base_prompt(name: str, persona: str, appearance_notes: str = "",
                             role: str = "", model: str | None = None) -> dict:
        """THE single appearance authority: FEATURES_SCHEMA draft → co-occurrence enrichment →
        _assemble_base_prompt. Synchronous (call it inside a threadpool). Used by BOTH the ✨
        button AND cast generation, so a regenerated cast produces the SAME rich base prompt as
        the manual button. Returns {prompt, features, companions} or {error}."""
        from ..scenario.builder import DEFAULT_SYSTEMS
        cfg = load_story_builder()
        provider = _author_provider(_stage_model(cfg, "base_image", model))
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
        feats = (provider.generate_text(system=system, prompt=context, emits=FEATURES_SCHEMA).data) or {}
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
                d2 = provider.generate_text(system=system, prompt=refine, emits=FEATURES_SCHEMA).data
                if d2 and d2.get("appearance"):
                    feats = d2
            except Exception:  # noqa: BLE001 — keep the 1st-pass result
                pass
        return {"prompt": _assemble_base_prompt(feats), "features": feats, "companions": lines}

    # The OUTFIT counterpart of FEATURES_SCHEMA — one complete outfit + the emotions it calls for.
    OUTFIT_SCHEMA = {
        "type": "object", "additionalProperties": False, "required": ["outfit", "emotions"],
        "properties": {
            "emotions": {"type": "array",
                         "items": {"type": "object", "additionalProperties": False,
                                   "required": ["emotion", "prompt"],
                                   "properties": {
                                       "emotion": {"type": "string", "description":
                                                   "one lowercase emotion word (happy, excited, shy, "
                                                   "determined, somber, flirty …)"},
                                       "prompt": {"type": "string", "description":
                                                  "FACE-ONLY booru expression tags for it (eyes, "
                                                  "eyebrows, mouth, + emotion tags like blush, "
                                                  "tears, sweatdrop) — NO clothing/pose/background"}}},
                         "description":
                             "4-8 emotions whose RANGE FITS THIS OUTFIT'S mood + scene (a beach look → "
                             "happy / excited / relaxed / playful; a battle outfit → determined / "
                             "fierce / focused; a gala gown → elegant / shy / flirty). These drive the "
                             "outfit's expression sprites, so pick what this look would actually show."},
            "outfit": {"type": "array", "items": {"type": "string"},
                       "description":
                           "18-30 CANONICAL Danbooru tags fully specifying ONE complete, DETAILED "
                           "outfit — be GENEROUS and specific, never a lazy 5-tag sketch. Slot order: "
                           "count tag (1girl/1boy); MAIN garment(s); LAYERS (jacket / cardigan / coat "
                           "/ vest); LEGWEAR; FOOTWEAR; HEADWEAR; then ACCESSORIES (jewelry, bag, "
                           "gloves, belt, scarf — with placement: 'single bracelet', 'pendant "
                           "necklace', 'single earring'); PIERCINGS ('navel piercing', 'ear piercing') "
                           "and MAKEUP ('red lipstick', 'eyeshadow', 'eyeliner', 'blush') where they "
                           "suit the character + occasion.\n"
                           "EVERY GARMENT TAG MUST INCLUDE A COLOUR WORD — never a bare 'skirt' / "
                           "'shirt' / 'thighhighs', ALWAYS 'red pleated skirt' / 'white blouse' / "
                           "'black thighhighs' / 'brown loafers'. NEVER output BOTH a bare garment AND "
                           "its coloured version ('jeans' AND 'black jeans') — output ONLY the coloured "
                           "one. (Accessories / piercings / makeup may omit colour.) Keep ONE coherent "
                           "palette; pin exact colours so it renders the same every time.\n"
                           "LITERAL canonical tags only — no metaphor, brand or material poetry. "
                           "CLOTHING, ACCESSORIES, PIERCINGS and MAKEUP only — NO body / hair / eye / "
                           "skin tags, NO facial EXPRESSION, NO pose, NO background."},
        },
    }

    def _dedupe_outfit_tags(tags: list) -> list:
        """Drop exact dupes AND a tag whose words are a strict subset of another tag with the SAME
        head noun — collapses 'jeans' vs 'black jeans', 'shirt' vs 'white shirt', 'bracelet' vs
        'single bracelet' (keep the more specific). Order-preserving."""
        cleaned, seen = [], set()
        for t in tags:
            t = " ".join(str(t).split())
            tl = t.lower()
            # drop ABSENCE tags ('no jacket', 'no eyewear', 'without …') — they don't belong in a
            # positive prompt — and exact dupes.
            if not t or tl in seen or tl.startswith("no ") or tl.startswith("without "):
                continue
            cleaned.append(t)
            seen.add(tl)
        words = [(t, set(t.lower().split()), t.lower().split()[-1] if t.split() else "") for t in cleaned]
        keep = []
        for t, tw, head in words:
            subsumed = any(o is not t and ohead == head and tw < ow for o, ow, ohead in words)
            if not subsumed:
                keep.append(t)
        return keep

    def _compose_outfit_prompt(persona: str, base_appearance: str, outfit_name: str,
                               attire_draft: str, model: str | None = None) -> dict:
        """The OUTFIT counterpart of `_compose_base_prompt` — a UNIQUE 2-step call PER outfit (so the
        model is never overwhelmed generating a whole wardrobe at once). Pass 1 fills OUTFIT_SCHEMA
        (detailed best-guess garments+colours+accessories+makeup+piercings, PLUS a range of emotions
        that fit this outfit); ~50 RAW reference outfit lines are retrieved from danbooru_character.csv;
        pass 2 CONSTRUCTS the final outfit from that soup. Returns {attire: snapped tag string,
        emotions: [{emotion, prompt}]} — the emotions drive THIS outfit's expression sprites. Empty
        attire on failure (caller keeps the draft)."""
        from ..scenario.builder import DEFAULT_SYSTEMS
        cfg = load_story_builder()
        provider = _author_provider(_stage_model(cfg, "wardrobe", model))
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
        feats = (provider.generate_text(system=system, prompt=context, emits=OUTFIT_SCHEMA).data) or {}
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
                d2 = provider.generate_text(system=system, prompt=refine, emits=OUTFIT_SCHEMA).data
                if d2 and d2.get("outfit"):
                    draft = [str(t) for t in d2["outfit"]]
                if d2 and not emotions:
                    emotions = _emos(d2)
            except Exception:  # noqa: BLE001 — keep the 1st-pass result
                pass
        snapped = _snap_prompt(_safe_image_tags(", ".join(draft)))
        attire = ", ".join(_dedupe_outfit_tags([t.strip() for t in snapped.split(",") if t.strip()]))
        return {"attire": attire, "emotions": emotions}

    def _refine_outfits(outfits: list, persona: str, base_appearance: str,
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
                r = _compose_outfit_prompt(persona, base_appearance, o.get("name", ""),
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

    def _start_stream_job(category: str, kind: str, label: str, screen: str, work):
        """Run blocking `work(emit, cancelled)` in the background as a streamable BaseJob.
        `emit(ev)` pushes an SSE event (thread-safe); `cancelled()` reflects the job's cancel.
        The job auto-appears in Activity and is consumed via /api/jobs/<id>/stream — ONE frontend
        component (GenStream) renders any of them. Returns the job (the endpoint returns its id)."""
        import asyncio

        from fastapi.concurrency import run_in_threadpool

        from .jobhub import BaseJob
        loop = asyncio.get_running_loop()
        job = BaseJob(category, kind, label=label, screen=screen, log_cap=600)

        def emit(ev: dict) -> None:
            loop.call_soon_threadsafe(job._emit, ev)

        async def run():
            try:
                result = await run_in_threadpool(lambda: work(emit, lambda: job.cancelling))
                if not job.cancelling:
                    loop.call_soon_threadsafe(job._emit, {"type": "result", "result": result})
                job.status = "cancelled" if job.cancelling else "done"
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                loop.call_soon_threadsafe(job._emit, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(job._emit, {"type": "done"})

        job._task = asyncio.create_task(run())
        return job

    @app.post("/api/stories/{key}/regenerate-cast")
    async def regenerate_cast(key: str, body: dict):
        """DESTRUCTIVE: re-derive the whole cast from the story's storyboard, STREAMED live as a
        job (roster pass + each character). Step 1 distils the source card into ONE clean BASE
        CHARACTER CARD; step 2 re-extracts supporting NPCs in that structure. Old story-bound
        characters (+ portraits) are deleted and the cast rewritten; the source card is untouched.
        Returns {job} — consume /api/jobs/<id>/stream to watch + know when it's done."""
        nonlocal base_settings
        import shutil

        from ..scenario import extract_characters, extract_protagonist

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, invention, systems = _builder_ctx(body or {}, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)

        # Resolve the protagonist source: the imported source card if it still exists, else fall
        # back to the story's current primary cast member.
        source = (st.fields or {}).get("source_character")
        prot_key = source if (source and source in base_settings.characters) else None
        if prot_key is None:
            prot_key = next((m.character for m in st.cast if m.primary), None) \
                or (st.cast[0].character if st.cast else None)
        prot = base_settings.characters.get(prot_key) if prot_key else None
        board = {"logline": st.storyboard.logline, "premise": st.premise, "tone": st.tone,
                 "beats": [b.model_dump() for b in st.storyboard.beats]}

        def work(emit, cancelled):
            nonlocal base_settings
            prot_data = None
            if prot is not None:
                prot_data = extract_protagonist(
                    provider, name=prot.name, persona=prot.system or "",
                    extras=_card_extras(prot, prot_key), invention="faithful",
                    systems=systems, on_event=emit)
            out = extract_characters(
                provider, name=(prot_data["name"] if prot_data else st.name),
                persona=(prot_data["persona"] if prot_data else ""),
                board=board, extras=_card_extras(prot, prot_key) if prot else {},
                invention=invention, systems=systems,
                reference_card=(prot_data["persona"] if prot_data else ""), on_event=emit)
            npcs = out.get("npcs", [])
            if cancelled():
                return {"cancelled": True}

            # Compose the RICH base prompt for EVERYONE via the shared ✨ pipeline (single
            # appearance authority) — in parallel — so the regenerated cast matches the manual ✨
            # button (skin-tone/eye-demeanor/cooccur/etc.) with no extra step.
            from concurrent.futures import ThreadPoolExecutor
            people = ([("__prot__", prot_data)] if prot_data else []) \
                + [(str(i), n) for i, n in enumerate(npcs)]

            def _bp(item):
                pid, p = item
                if cancelled():
                    return (pid, "")
                emit({"type": "phase", "label": f"Rendering appearance — {p.get('name', '?')}"})
                r = _compose_base_prompt(p.get("name", ""), p.get("persona", ""),
                                         p.get("appearance", ""), p.get("role", ""))
                bp = r.get("prompt", "") if isinstance(r, dict) else ""
                if bp:
                    emit({"type": "item", "name": p.get("name", "?"), "text": bp})
                return (pid, bp)

            bps = {}
            if people:
                with ThreadPoolExecutor(max_workers=min(len(people), 6)) as ex:
                    bps = dict(ex.map(_bp, people))

            emit({"type": "phase", "label": "Saving the cast…"})
            # Build the whole new cast, commit the story yaml, THEN delete the old members —
            # transactional: a failed write throws before the story is touched.
            created: list[str] = []
            cast = []
            if prot_data is not None:
                pkey = _write_npc(prot_data, story_key=key, ref_from=prot_key,
                                  base_prompt=bps.get("__prot__", ""))
                cast.append({"character": pkey, "primary": True}); created.append(pkey)
            for i, npc in enumerate(npcs):
                nk = _write_npc(npc, story_key=key, base_prompt=bps.get(str(i), ""))
                cast.append({"character": nk, "primary": False}); created.append(nk)
            path = _story_dir() / f"{re.sub(r'[^\w\-]+', '', key)}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["cast"] = cast
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            base_settings = load_settings(root)
            keep = {source, *created}
            cdir = _char_dir()
            for m in st.cast:
                ck = m.character
                if ck in keep:
                    continue
                ch = base_settings.characters.get(ck)
                bound = ch and (((ch.fields or {}).get("story") == key) or (ch.fields or {}).get("_generated"))
                if not bound:
                    continue
                safe = re.sub(r"[^\w\-]+", "", ck)
                for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
                    f = cdir / fn
                    if f.is_file():
                        f.unlink()
                shutil.rmtree(_portrait_dir(ck), ignore_errors=True)
            base_settings = load_settings(root)
            emit({"type": "phase", "label": f"Done — {len(cast)} cast members"})
            return {"ok": True, "cast": [m["character"] for m in cast], "created": created}

        job = _start_stream_job("cast", "Regenerate cast", st.name, f"stories/{key}/cast", work)
        return {"job": job.id}

    PLAY_SCHEMA = {
        "type": "object", "additionalProperties": False,
        "required": ["reply", "location", "present", "emotions", "movement"],
        "properties": {
            "reply": {"type": "string"},
            "location": {"type": "string"},
            "present": {"type": "array", "items": {"type": "string"}},
            "emotions": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["character", "emotion"],
                "properties": {"character": {"type": "string"}, "emotion": {"type": "string"}}}},
            "movement": {"type": "boolean"},
        },
    }

    @app.post("/api/stories/{key}/play")
    def story_play(key: str, body: dict):
        """The runtime DIRECTOR: given the transcript (+ an optional location the
        player moved to), narrate the next turn AND report the scene state — current
        location, who's present, each one's emotion, and whether the moment invites
        moving to another location (so the UI can offer location choices)."""
        from ..providers.registry import build_provider

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        tconn = store.active("text")
        if tconn is None:
            return JSONResponse({"error": "no chat connection"}, status_code=400)
        opts = {**tconn.to_model_options(), "max_tokens": 1500}
        if body.get("chat_model"):
            opts["model"] = body["chat_model"]
        provider = build_provider(ModelDef(provider=tconn.provider, kind="text", options=opts))

        # Compose the director's brief from the story.
        def cast_line(m):
            c = base_settings.characters.get(m.character)
            return f"- {c.name if c else m.character}: {((c.system or '').splitlines()[0] if c else '')[:160]}"
        cast = "\n".join(cast_line(m) for m in st.cast) or "(none)"
        locs = "\n".join(f"- {l.id} | {l.name}: {l.description}" for l in st.locations) or "(none)"
        lore = "; ".join(e.get("comment", "") for e in (st.lorebook or {}).get("entries", []) if e.get("comment"))
        cur = body.get("location") or st.start or (st.locations[0].id if st.locations else "")
        system = (
            f"You are the narrator and director of an interactive visual novel titled \"{st.name}\".\n"
            f"PREMISE: {st.premise}\nTONE: {st.tone}\n"
            + (f"WORLD: {lore}\n" if lore else "")
            + f"CAST (use these names):\n{cast}\n"
            f"LOCATIONS (the scene is in exactly one; use the id):\n{locs}\n\n"
            "Narrate the next moment in-world and in the established tone, responding to the player. "
            "Then report the scene state in your structured output:\n"
            "- reply: the narration (second person to the player, plus character action/dialogue). Vivid but concise.\n"
            "- location: the id of the location the scene is currently in (one of the listed ids).\n"
            "- present: ALWAYS list the names of EVERY cast character physically in the scene right now "
            "(anyone who speaks, acts, or is described as present) — never leave it empty if someone is there.\n"
            "- emotions: each present character's current emotion as ONE lowercase word.\n"
            "- movement: true ONLY when this moment invites the player to move to a different location "
            "(they suggest leaving, a path opens, the beat concludes) — otherwise false."
        )

        history = body.get("history") or []
        lines = []
        for m in history:
            who = "Player" if m.get("role") == "user" else "Narrator"
            lines.append(f"{who}: {m.get('text', '')}")
        transcript = "\n".join(lines) or "(the story is just beginning)"
        moved = body.get("choice")
        directive = ""
        if moved:
            dest = next((l.name for l in st.locations if l.id == moved), moved)
            directive = f"\n\n[The player moves to: {dest}. Narrate the transition and arrival there; set location to '{moved}'.]"
        prompt = (f"CURRENT LOCATION: {cur}\n\nTRANSCRIPT:\n{transcript}{directive}\n\n"
                  f"Narrate the next turn and report the scene state.")
        try:
            res = provider.generate_text(system=system, prompt=prompt, emits=PLAY_SCHEMA)
            data = res.data or {}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not data:
            return JSONResponse({"error": "director returned no structured data (model may not support it)"},
                                status_code=500)
        # map present/emotion names -> character keys for the UI's sprite lookup
        name_to_key = {(base_settings.characters[m.character].name if m.character in base_settings.characters
                        else m.character).lower(): m.character for m in st.cast}
        present_keys = [name_to_key.get((n or "").lower()) for n in data.get("present", [])]
        emotions = {name_to_key.get((e.get("character") or "").lower()): e.get("emotion")
                    for e in data.get("emotions", [])}
        loc = data.get("location") if any(l.id == data.get("location") for l in st.locations) else cur
        return {
            "reply": data.get("reply", ""), "location": loc,
            "present": [k for k in present_keys if k],
            "emotions": {k: v for k, v in emotions.items() if k},
            "movement": bool(data.get("movement")),
        }

    @app.post("/api/stories/{key}/plan-wardrobe")
    async def story_plan_wardrobe(key: str, body: dict):
        """Plan one cast character's wardrobe (outfits) + story-derived expression prompts,
        STREAMED live as a job. Returns {job}; the final `result` event carries the plan for
        review (persist via the portraits wardrobe endpoint). Renders nothing."""
        from ..scenario import plan_wardrobe

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        char_key = (body or {}).get("character")
        ch = base_settings.characters.get(char_key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider, invention, systems = _builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        story = st.model_dump()
        appearance = (ch.fields or {}).get("appearance", "")

        def work(emit, cancelled):
            plan = plan_wardrobe(provider, char_name=ch.name, persona=ch.system,
                                 appearance=appearance, story=story, invention=invention,
                                 systems=systems, on_event=emit)
            # Pass 2 — refine EACH outfit into careful, consistent booru tags, in parallel (same
            # 2-step pipeline the base image gets).
            emit({"type": "phase", "label": "Refining each outfit — booru tags"})
            plan["outfits"] = _refine_outfits(plan.get("outfits"), ch.system, appearance, emit=emit)
            return {"character": char_key, **plan}

        job = _start_stream_job("wardrobe", "Plan wardrobe", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/stories/{key}/plan-wardrobe-all")
    async def story_plan_wardrobe_all(key: str, body: dict):
        """Plan + SAVE (replace) the wardrobe for EVERY cast member, STREAMED as one job. Plans
        only — renders no sprites; the user renders those per-character afterward. Returns {job}."""
        from ..scenario import plan_wardrobe

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        provider, invention, systems = _builder_ctx(body or {}, "wardrobe")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        story = st.model_dump()
        members = [m.character for m in st.cast]

        def work(emit, cancelled):
            planned = 0
            for ckey in members:
                if cancelled():
                    break
                ch = base_settings.characters.get(ckey)
                if ch is None:
                    continue
                emit({"type": "phase", "label": f"Planning {ch.name}'s wardrobe"})
                try:
                    appr = (ch.fields or {}).get("appearance", "")
                    plan = plan_wardrobe(provider, char_name=ch.name, persona=ch.system,
                                         appearance=appr, story=story, invention=invention,
                                         systems=systems, on_event=emit)
                    outfits = _refine_outfits(plan.get("outfits"), ch.system, appr, emit=emit)
                    portraits_apply_wardrobe(ckey, {"outfits": outfits,
                                                    "expressions": plan.get("expressions", {}),
                                                    "replace": True})
                    planned += 1
                    emit({"type": "item", "name": ch.name,
                          "text": f"{len(plan.get('outfits', []))} outfits · "
                                  f"{len(plan.get('expressions', {}))} emotions"})
                except Exception as exc:  # noqa: BLE001 — one character failing must not sink the rest
                    emit({"type": "phase", "label": f"{ch.name} skipped ({exc})"})
            return {"ok": True, "planned": planned}

        job = _start_stream_job("wardrobe", "Plan all wardrobes", st.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    @app.post("/api/characters/{key}/portraits/wardrobe")
    def portraits_apply_wardrobe(key: str, body: dict):
        """Merge a planned wardrobe into the character's portrait studio (additive):
        add outfits (with attire_prompt) and the per-emotion expression prompts. The
        sprites themselves are rendered later by the portrait studio.

        With `replace: true` it's DESTRUCTIVE — the existing outfits and their rendered
        sprites are deleted first (e.g. after the base image changed, so the old sprites
        are stale), then the new plan is written fresh."""
        if key not in base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        m = _portrait_manifest(key)
        if body.get("replace"):
            import shutil
            pdir = _portrait_dir(key)
            for o in m.get("outfits", []) or []:
                od = pdir / (o.get("id") or "")
                if o.get("id") and od.is_dir():
                    shutil.rmtree(od, ignore_errors=True)
            m["outfits"] = []
            m["expression_prompts"] = {}
        plan_exprs = body.get("expressions") or {}   # emotion -> face prompt (seeds each outfit)
        existing = {o.get("name", "").lower() for o in m.get("outfits", [])}
        for o in body.get("outfits", []) or []:
            nm = (o.get("name") or "").strip()
            if not nm or nm.lower() in existing:
                continue
            oid = re.sub(r"[^\w\-]+", "-", nm.lower()).strip("-") or "outfit"
            base_oid, n = oid, 2
            ids = {x.get("id") for x in m["outfits"]}
            while oid in ids:
                oid, n = f"{base_oid}-{n}", n + 1
            # Snap the attire to real Danbooru tags — same care the base image gets.
            attire = _snap_prompt(_safe_image_tags((o.get("attire_prompt") or "").strip()))
            # Each outfit carries its OWN emotion range (from the 2-step outfit generator); fall back
            # to the story-wide plan set only if this outfit didn't bring its own.
            out_exprs = o.get("expression_prompts") if isinstance(o.get("expression_prompts"), dict) else None
            m["outfits"].append({"id": oid, "name": nm, "instruction": "",
                                 "prompt": attire, "attire_prompt": attire,
                                 "expression_prompts": dict(out_exprs or plan_exprs), "expressions": {}})
            existing.add(nm.lower())
        if plan_exprs:   # keep the legacy global set in sync (standalone studio)
            m["expression_prompts"] = {**(m.get("expression_prompts") or {}), **plan_exprs}
        _save_portrait_manifest(key, m)
        return {"ok": True, "outfits": [o["name"] for o in m["outfits"]],
                "emotions": list((m.get("expression_prompts") or {}).keys())}

    @app.post("/api/stories/{key}/regenerate-character")
    async def regenerate_character(key: str, body: dict):
        """DESTRUCTIVE, STREAMED: re-derive ONE cast member end-to-end, steered by an optional
        free-text `instruction`. Rewrites their persona + role + appearance (so the story overview
        updates), recomposes + saves the base-image prompt, re-renders the base image, and rebuilds
        the wardrobe (old outfits + sprites cleared). Returns {job}; GenStream watches it live."""
        nonlocal base_settings
        from ..scenario import plan_wardrobe, revise_character

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        char_key = body.get("character")
        ch = base_settings.characters.get(char_key)
        if ch is None or not any(m.character == char_key for m in st.cast):
            return JSONResponse({"error": "character is not in this story's cast"}, status_code=404)
        provider, invention, systems = _builder_ctx(body, "characters")
        if provider is None:
            return JSONResponse({"error": invention}, status_code=400)
        instruction = (body.get("instruction") or "").strip()
        is_primary = any(m.character == char_key and m.primary for m in st.cast)
        story = st.model_dump()
        board = {"logline": (story.get("storyboard") or {}).get("logline", "")}
        cur_name, cur_persona = ch.name, ch.system or ""
        cur_role = (ch.fields or {}).get("role") or ("protagonist" if is_primary else "supporting")
        cur_appear = (ch.fields or {}).get("appearance", "")

        def work(emit, cancelled):
            nonlocal base_settings
            # 1. Rewrite persona + role + appearance, steered by the instruction.
            revised = revise_character(provider, name=cur_name, persona=cur_persona, role=cur_role,
                                       appearance=cur_appear, instruction=instruction, board=board,
                                       invention=invention, systems=systems, on_event=emit)
            if cancelled():
                return {"cancelled": True}
            # 2. Compose the rich base-image prompt from the rewritten persona + appearance.
            emit({"type": "phase", "label": "Composing the base-image prompt"})
            comp = _compose_base_prompt(revised["name"], revised["persona"],
                                        revised["appearance"], revised["role"])
            base_prompt = comp.get("prompt", "") if isinstance(comp, dict) else ""
            # 3. Persist the rewritten card (name / persona / role / appearance + base_prompt).
            safe = re.sub(r"[^\w\-]+", "", char_key)
            path = _char_dir() / f"{safe}.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["name"] = revised["name"] or data.get("name") or char_key
            data["system"] = revised["persona"]
            data["fields"] = {**(data.get("fields") or {}), "role": revised["role"],
                              "appearance": revised["appearance"], "base_prompt": base_prompt}
            from ..config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            base_settings = load_settings(root)
            emit({"type": "item", "name": revised["name"], "text": base_prompt or revised["appearance"]})
            if cancelled():
                return {"cancelled": True}
            # 4. Re-render the base image from the new prompt and set it as the reference (best-effort).
            emit({"type": "phase", "label": "Rendering the new base image"})
            try:
                iprov, _mid = _image_provider(_role_model("base"))
                if iprov is not None and base_prompt:
                    from ..comfy.server import get_server
                    get_server(iprov.base_url).ensure_up()
                    res = iprov.generate_image(prompt=base_prompt)
                    if res.images:
                        (_char_dir() / f"{safe}.ref.png").write_bytes(_clean_reference_png(res.images[0]))
            except Exception as exc:  # noqa: BLE001 — a render failure must not sink the rewrite
                emit({"type": "phase", "label": f"Base image skipped ({exc})"})
            if cancelled():
                return {"cancelled": True}
            # 5. Rebuild the wardrobe (replace) — old outfits + stale sprites cleared.
            emit({"type": "phase", "label": "Rebuilding the wardrobe"})
            try:
                plan = plan_wardrobe(provider, char_name=revised["name"], persona=revised["persona"],
                                     appearance=revised["appearance"], story=story, invention=invention,
                                     systems=systems, on_event=emit)
                outfits = _refine_outfits(plan.get("outfits"), revised["persona"],
                                          revised["appearance"], emit=emit)
                portraits_apply_wardrobe(char_key, {"outfits": outfits,
                                                    "expressions": plan.get("expressions", {}),
                                                    "replace": True})
            except Exception as exc:  # noqa: BLE001
                emit({"type": "phase", "label": f"Wardrobe rebuild skipped ({exc})"})
            emit({"type": "phase", "label": f"Done — {revised['name']} regenerated"})
            return {"ok": True, "character": char_key, "name": revised["name"], "role": revised["role"]}

        job = _start_stream_job("character", "Regenerate character", ch.name,
                                f"stories/{key}/cast", work)
        return {"job": job.id}

    # -- asset rendering: location backgrounds + character base images ---------
    def _story_bg_dir(key: str) -> Path:
        return _story_dir() / re.sub(r"[^\w\-]+", "", key) / "bg"

    async def _render(provider, prompt: str, init_image: bytes | None = None) -> bytes | None:
        from fastapi.concurrency import run_in_threadpool

        from ..comfy.server import get_server
        await run_in_threadpool(get_server(provider.base_url).ensure_up)
        result = await run_in_threadpool(
            lambda: provider.generate_image(prompt=prompt, init_image=init_image))
        return result.images[0] if result.images else None

    # Colour-NAME words a literal SDXL model paints as the actual colour, not as the trait
    # they describe (the classic: "olive skin" -> green skin). Rewrite the worst offenders to
    # plain booru tags so existing prompts self-heal at render time (newer prompts avoid them
    # via the feature schema). Skin tones only — clothing colours aren't in the base prompt.
    _IMAGE_TAG_FIXES = [
        (r"\bolive(?:[ -](?:skin|complexion|skin tone|toned?|colou?red))\b", "tan"),
        (r"\bolive(?=\s+skin)", "tan"),
        (r"\bporcelain(?:[ -]skin)?\b", "pale skin"),
        (r"\bebony[ -]skin\b", "dark skin"),
        # "young" biases Illustrious childlike — strip it from ADULT subjects. "young adult" /
        # "young woman" / "young man" are grown-ups, so drop the misleading "young". (An actual
        # young girl/boy is written as 1girl/1boy + child/teen, which we leave untouched.)
        (r"\byoung\s+adult\b", "adult"),
        (r"\byoung\s+(woman|man|female|male)\b", r"\1"),
        # plain hair colours — common artistic/metaphor words the model still slips in
        (r"\bbrunette\b", "brown"),
        (r"\braven\s+(hair|black)\b", "black hair"),
        (r"\bauburn\b", "red"),
        # neutral-grey the base backdrop (RMBG-2.0 mattes it). Rewrite any other bg tag to grey.
        (r"\b(?:plain white|plain simple|plain|white|green|magenta)\s+background\b", "grey background"),
        # literal-model traps: figurative/shape-by-analogy phrases render as the literal object.
        # face/chin/nose SHAPE is barely tagged on Danbooru — drop the figurative ones outright
        # (their old "fixes" pointed chin / narrow eyes were ALSO dead tags). Eyes → real shapes.
        (r"\bheart[- ]shaped\s+face\b", ""),
        (r"\balmond[- ]shaped\s+eyes\b", "tsurime"),
        (r"\balmond\s+eyes\b", "tsurime"),
        (r"\bbutton\s+nose\b", ""),
        (r"\bsharp\s+eyes\b", "tsurime"),
        (r"\beyebags?\b", ""),
        (r"\bnarrow\s+eyes\b", "tsurime"),
        (r"\b(?:pointed|v-shaped)\s+(?:chin|jaw)\b", ""),
        (r"\b(?:star|diamond|oval)[- ]shaped\s+face\b", ""),
    ]

    # Tags that just never render attractively on this checkpoint — half-lidded / shut / tired
    # eye looks, etc. Stripped from every image prompt regardless of where it came from. Extend
    # this list as more bad-result tags surface.
    _AESTHETIC_BLOCK = (
        "closed eyes", "half-closed", "half closed", "jitome", "eyebag", "bags under eyes",
        "one eye closed", "rolling eyes", "empty eyes", "drooping eyes",
    )

    def _safe_image_tags(text: str) -> str:
        """Rewrite literal-model-hostile phrases (olive skin, dead-tag eye shapes) to plain tags,
        then drop blank fragments AND aesthetically-bad tags (sleepy/closed eyes …), order-keeping."""
        out = text or ""
        for pat, repl in _IMAGE_TAG_FIXES:
            out = re.sub(pat, repl, out, flags=re.I)
        kept = []
        for part in out.split(","):
            p = part.strip()
            if p and not any(b in p.lower() for b in _AESTHETIC_BLOCK):
                kept.append(p)
        return ", ".join(kept)

    def _snap_prompt(text: str) -> str:
        """Snap a prompt onto the real Danbooru vocabulary (alias/typo/reorder), KEEPING any
        unknown tags verbatim — non-destructive. A missing/unbuilt index is a silent no-op so
        generation never depends on it. The editor surfaces unknowns; this just canonicalizes."""
        try:
            from ..tags import get_index
            ix = get_index()
            return ix.snap(text)["prompt"] if ix.ready else (text or "")
        except Exception:  # noqa: BLE001 — vocabulary is a nicety, never a hard dependency
            return text or ""

    # The base backdrop colour the prompt paints (magenta) — the colour filter keys it out so the
    # enclosed arm/body gap RMBG leaves filled goes transparent. The character's palette avoids it.
    _KEY_COLOR = (255, 0, 255)

    def _strip_key_color(raw: bytes, target=_KEY_COLOR, tol: int = 95) -> bytes:
        """Remove leftover backdrop-colour pixels (the enclosed gap RMBG keeps) by setting their
        alpha to 0. RMBG already cut the silhouette + hair; this only clears the key colour, which
        the character doesn't contain — so it can't hole the character. No-op if numpy is absent."""
        try:
            from io import BytesIO

            import numpy as np
            from PIL import Image
            im = Image.open(BytesIO(raw)).convert("RGBA")
            a = np.array(im)
            rgb = a[..., :3].astype(np.int16)
            dist = np.sqrt(((rgb - np.array(target, dtype=np.int16)) ** 2).sum(-1))
            a[dist < tol, 3] = 0                 # near the key colour → transparent
            out = BytesIO(); Image.fromarray(a, "RGBA").save(out, "PNG"); return out.getvalue()
        except Exception:  # noqa: BLE001 — numpy/Pillow missing or odd image; leave as-is
            return raw

    def _clean_reference_png(raw: bytes) -> bytes:
        """STORE a reference: strip embedded metadata (the ComfyUI prompt chunk) but PRESERVE
        transparency, so a background-removed candidate stays transparent. We NEVER composite the
        cutout back onto a white (or any) background — not on save, and not when feeding it to a
        model — so the removed background is never re-introduced. Re-filling it would defeat the
        removal and break compositing the character onto scenes."""
        from io import BytesIO

        from PIL import Image
        im = Image.open(BytesIO(raw))
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")          # keep alpha
        else:
            im = im.convert("RGB")
        out = BytesIO(); im.save(out, format="PNG"); return out.getvalue()

    def _base_prompt(ch) -> str:
        """The default positive prompt for a character's base image: their own physical
        `appearance` (falls back to the persona/name — never a hard-coded gender) framed as
        a clean FULL-BODY template in plain swimwear. Minimal clothing on purpose — a complex
        outfit corrupts the identity capture; story outfits are layered on later (wardrobe).
        The workflow carries its own quality/style embeddings."""
        appearance = ((ch.fields or {}).get("appearance")
                      or ch.system or ch.name or "solo").strip()
        # Swimwear template by apparent gender (read the count tag in the appearance).
        male = re.search(r"\b1\s*(boy|man|male)\b", appearance.lower()) is not None
        swim = "swim trunks, bare chest" if male else "bikini"
        return _safe_image_tags(
            f"{appearance}, solo, full body, standing, facing viewer, {swim}, "
            "grey background, simple background, full body shot, head to toe, feet visible")

    def _randomize_seeds(graph: dict) -> None:
        """Give every sampler a fresh seed so repeated renders of one prompt vary
        (workflows ship with a fixed seed). Mutates in place."""
        import random
        for node in graph.values():
            ins = node.get("inputs") if isinstance(node, dict) else None
            if isinstance(ins, dict):
                for k in ("seed", "noise_seed"):
                    if isinstance(ins.get(k), (int, float)):
                        ins[k] = random.randint(0, 2_147_483_646)

    def _save_location_bg(key: str, loc: str, png: bytes) -> str:
        """Write a chosen background and record it on the story's location."""
        nonlocal base_settings
        d = _story_bg_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{loc}.png").write_bytes(png)
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _story_dir() / f"{safe}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background"] = f"/api/stories/{key}/bg/{loc}.png"
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        base_settings = load_settings(root)
        return f"/api/stories/{key}/bg/{loc}.png"

    @app.post("/api/stories/{key}/locations/{loc}/regen-prompt")
    def regen_location_prompt(key: str, body: dict):
        """Regenerate ONE location's background_prompt using the locations stage's
        configured model + system prompt (framing rules included), then save it to the
        story. Returns {prompt}."""
        nonlocal base_settings
        from ..scenario.builder import DEFAULT_SYSTEMS

        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        location = next((l for l in st.locations if l.id == loc), None)
        if location is None:
            return JSONResponse({"error": "no such location"}, status_code=404)
        cfg = load_story_builder()
        provider = _author_provider(_stage_model(cfg, "locations"))
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        locsys = (cfg.get("systems") or {}).get("locations") or DEFAULT_SYSTEMS["locations"]
        system = (locsys + "\n\nNOW: output ONLY the `background_prompt` for the SINGLE location "
                  "below — a flat comma-separated Danbooru tag list framing the place itself. No "
                  "id, no name, no description, no commentary, no quotes — just the tags.")
        ctx = f"NAME: {location.name}\nDESCRIPTION: {location.description or location.name}"
        try:
            res = provider.generate_text(system=system, prompt=ctx)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"regenerate failed: {exc}"}, status_code=500)
        prompt = (res.text or "").strip().strip("`").strip().strip('"').strip()
        if not prompt:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        # Save onto the story.
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _story_dir() / f"{safe}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for l in data.get("locations", []):
            if l.get("id") == loc:
                l["background_prompt"] = prompt
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        base_settings = load_settings(root)
        return {"prompt": prompt}

    @app.get("/api/stories/{key}/bg/{file}")
    def story_background(key: str, file: str):
        if not re.fullmatch(r"[\w\-]+\.png", file):
            return JSONResponse({"error": "bad path"}, status_code=404)
        p = (_story_bg_dir(key) / file).resolve()
        if _story_bg_dir(key).resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    @app.post("/api/stories/{key}/locations/{loc}/background/candidate")
    async def background_candidate(key: str, loc: str, body: dict):
        """Render ONE background CANDIDATE for a location (fresh seed each call, so a
        batch varies). Returns a data URI — not saved. The UI shows a few and lets
        you pick the winner via .../background/select. Workflow-driven: only the
        location description is injected into the `scene` workflow's {{image}}."""
        st = base_settings.stories.get(key)
        if st is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        location = next((l for l in st.locations if l.id == loc), None)
        if location is None:
            return JSONResponse({"error": "no such location"}, status_code=404)
        prompt = (location.background_prompt or location.description or "").strip()
        if not prompt:
            return JSONResponse({"error": "location has no background prompt"}, status_code=400)
        model = _role_model("scene", (body or {}).get("image_model"))
        provider, model_id = _image_provider(model)
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/stories/{key}/locations/{loc}/background/select")
    def select_location_background(key: str, loc: str, body: dict):
        """Save a chosen candidate (base64 data URI) as the location's background."""
        st = base_settings.stories.get(key)
        if st is None or not any(l.id == loc for l in st.locations):
            return JSONResponse({"error": "no such story/location"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        url = _save_location_bg(key, loc, png)
        return {"ok": True, "url": url}

    @app.post("/api/characters/{key}/sprite-candidate")
    async def sprite_candidate(key: str, body: dict):
        """Render ONE sprite candidate for (outfit × emotion) from the wardrobe plan:
        prompt = the outfit's attire + the emotion's expression prompt, img2img from
        the character's base reference (IPAdapter identity). Fresh seed per call;
        returns a data URI (not saved)."""
        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        oid, emotion = body.get("outfit_id"), body.get("emotion")
        m = _portrait_manifest(key)
        outfit = next((o for o in m.get("outfits", []) if o.get("id") == oid), None)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        # The sprite workflow is txt2img (illustrious fork) — identity comes from the
        # prompt, so include the character's core appearance alongside attire+expression.
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        # Per-outfit expression prompt (legacy global as fallback for old data).
        expr = ((outfit.get("expression_prompts") or {}).get(emotion)
                or (m.get("expression_prompts") or {}).get(emotion) or emotion or "")
        # Every outfit picture is FULL BODY (the expression sprite shows the whole look + the face).
        prompt = _snap_prompt(_safe_image_tags(
            ", ".join(p for p in (appearance, attire, expr, _FULLBODY_FRAMING) if p)))
        model = _role_model("sprite", body.get("image_model"))
        provider, model_id = _image_provider(model)
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        # txt2img — identity comes from the appearance tags (the model is consistent enough that
        # img2img from the base added little). The workflow removes the background (→ transparent).
        try:
            png = await _render(provider, prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/characters/{key}/sprite/select")
    def sprite_select(key: str, body: dict):
        """Save a chosen sprite candidate into the outfit's expression grid."""
        body = body or {}
        oid, emotion = body.get("outfit_id"), body.get("emotion")
        m = _portrait_manifest(key)
        outfit = next((o for o in m.get("outfits", []) if o.get("id") == oid), None)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        emo = re.sub(r"[^\w\-]+", "-", (emotion or "").lower()).strip("-") or "emotion"
        uri = body.get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        (_portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (_portrait_dir(key) / oid / f"{emo}.png").write_bytes(png)
        outfit.setdefault("expressions", {})[emo] = f"{emo}.png"
        _save_portrait_manifest(key, m)
        return {"ok": True, "url": f"/api/characters/{key}/portraits/img/{oid}/{emo}.png"}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/recompose")
    def portrait_outfit_recompose(key: str, oid: str, body: dict):
        """Re-run the robust 2-step outfit-prompt pipeline (`_compose_outfit_prompt`: best-guess →
        danbooru_character.csv clothing retrieval → choose-from-real refine) and SAVE it as the
        outfit's attire_prompt. Regenerating the outfit IMAGE calls this first, so the render always
        rides a fresh, complete, colour-consistent prompt — never a stale stored one. {attire_prompt}."""
        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        r = _compose_outfit_prompt(
            ch.system or "", (ch.fields or {}).get("appearance", ""), outfit.get("name", ""),
            outfit.get("attire_prompt") or outfit.get("prompt") or "", (body or {}).get("model"))
        attire = r.get("attire") if isinstance(r, dict) else ""
        if not attire:
            return JSONResponse({"error": "could not compose outfit prompt (author model may "
                                          "not support structured output)"}, status_code=500)
        outfit["attire_prompt"] = attire
        outfit["prompt"] = attire
        # Refresh this outfit's emotion range too (correlated to the outfit), unless it already has
        # rendered sprites we'd orphan — only seed emotions that don't exist yet.
        if r.get("emotions"):
            ep = outfit.setdefault("expression_prompts", {})
            for e in r["emotions"]:
                ep.setdefault(e["emotion"], e["prompt"])
        _save_portrait_manifest(key, m)
        return {"ok": True, "attire_prompt": attire}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/candidate")
    async def portrait_outfit_candidate(key: str, oid: str, body: dict):
        """Render ONE FULL-BODY candidate for an outfit — the whole-look reference (character
        appearance + the outfit's attire, FULL-BODY framing, txt2img). Fresh seed each call; returns
        a data URI (not saved). The UI recomposes the prompt first, then generates 3 and lets you
        pick. Distinct from the face-focused emotion sprites."""
        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        prompt = _snap_prompt(_safe_image_tags(", ".join(p for p in (appearance, attire, _FULLBODY_FRAMING) if p)))
        model = _role_model("sprite", (body or {}).get("image_model"))
        provider, model_id = _image_provider(model)
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        # txt2img — identity from the appearance tags (the model is consistent without img2img).
        try:
            png = await _render(provider, prompt)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/base")
    def portrait_outfit_set_base(key: str, oid: str, body: dict):
        """Save a chosen FULL-BODY candidate as the outfit's base image (base.png)."""
        m = _portrait_manifest(key)
        outfit = _portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        (_portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (_portrait_dir(key) / oid / "base.png").write_bytes(png)
        outfit["base"] = "base.png"
        _save_portrait_manifest(key, m)
        return {"ok": True, "url": f"/api/characters/{key}/portraits/img/{oid}/base.png"}

    @app.get("/api/characters/{key}/base-prompt")
    def base_prompt(key: str):
        """The exact positive prompt that base-candidate will send for this character
        (so the UI can show/edit it before generating). Also returns the raw appearance
        field for context."""
        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        default = _base_prompt(ch)
        saved = _safe_image_tags(((ch.fields or {}).get("base_prompt") or "").strip())
        return {"prompt": saved or default, "default": default, "saved": bool(saved),
                "appearance": (ch.fields or {}).get("appearance") or "",
                "name": ch.name}

    # Deterministic feature slots for a base image. The author model FILLS this schema
    # (structured output) — it cannot skip, merge or duplicate slots — and we assemble
    # the tag string IN CODE in a fixed order. Vocab is Illustrious-tested: face age and
    # body are controlled by VISUAL-TRAIT tags (they correspond to pixels, not "age").
    _BODY_VOCAB = ["slim body", "toned body", "curvy body", "mature body", "petite body",
                   "muscular", "wide hips", "narrow waist", "long legs", "broad shoulders"]
    _HAIR_LEN_VOCAB = ["very short hair", "short hair", "medium hair", "long hair", "very long hair"]
    _EYE_SHAPE_VOCAB = ["sharp eyes", "tsurime", "tareme", "round eyes", "narrow eyes", "almond-shaped eyes"]
    _FACE_MATURITY_VOCAB = ["youthful face", "adult face", "mature face"]
    _FACE_DETAIL_VOCAB = ["small eyes", "large eyes", "sharp eyes", "defined jawline",
                          "soft facial features", "defined facial features"]
    _HEIGHT_VOCAB = ["tall", "average height", "short", "petite"]
    # Skin tone is a BRIGHTNESS tag, not a colour name — Illustrious paints colour-names
    # literally ("olive skin" -> green skin). Lock it to safe booru brightness tags.
    _SKIN_VOCAB = ["pale skin", "fair skin", "light skin", "tan", "dark skin", "very dark skin"]
    # features must be PERSISTENT physical traits — reject anything expression/mood/state.
    _FEATURE_BLOCK = ("smil", "grin", "frown", "blush", "sweat", "tear", "cry", "open mouth",
                      "angry", "happy", " sad", "pout", "wink", "laugh", "surpris", "expression",
                      "looking", "pose", "standing", "sitting")
    # …and reject anything that belongs in a DEDICATED slot (the model often dumps hair/eye/skin
    # tags into `features`), so the features slot only carries true marks (freckles, scar, mole…).
    _FEATURE_REJECT = ("hair", "eyes", "skin", "parted", "bangs", "ponytail", "braid", "bob",
                       "bikini", "swim", "body", "tall", "short", "petite")
    # Free-form appearance tags: reject only TRANSIENT emotion, scene/background, clothing and
    # dynamic poses — persistent gaze/mood (tired, eyebags, half-closed eyes, looking at viewer,
    # expressionless) and anatomy are KEPT. The base is rendered neutral, in swimwear, on grey.
    _APPEARANCE_BLOCK = (
        "smil", "grin", "blush", "tear", "cry", "angry", "happy", " sad", "pout", "wink", "laugh",
        "open mouth", "surpris", "scream", "embarrassed",
        "background", "scenery", "indoors", "outdoors",
        "bikini", "swimsuit", "dress", "shirt", "skirt", "jacket", "coat", "uniform", "hoodie",
        "sweater", "blazer", "pants", "shorts", "gloves", "boots", "shoes", "socks",
        "wearing", "clothes", "outfit",
        "sitting", "lying", "kneeling", "jumping", "walking", "running", "from above", "from below",
    )
    FEATURES_SCHEMA = {
        "type": "object", "additionalProperties": False,
        "required": ["count", "apparent_age", "expression", "skin_tone", "pose",
                     "distinguishing_feature", "appearance"],
        "properties": {
            "count": {"type": "string", "enum": ["1girl", "1boy"],
                      "description": "the character's SEX only: 1girl (female) or 1boy (male). "
                                     "Age is separate — see apparent_age."},
            "apparent_age": {"type": "string", "description":
                             "the character's apparent age, read HONESTLY from the persona — a number "
                             "('24 years old', '10 years old') or a band: 'child' / 'teenager' / "
                             "'young adult' / 'adult' / 'middle-aged' / 'elderly'. Do NOT force every "
                             "character to be an adult; capture their REAL age. (Minors are always "
                             "depicted clothed and non-sexualised.)"},
            "expression": {"type": "string", "description":
                           "ONE persistent RESTING expression by PERSONALITY, and DEFAULT TO WARMTH: "
                           "most people look approachable, so use 'light smile' or 'smile' UNLESS the "
                           "persona is genuinely otherwise. Map: warm/kind/gentle/shy/cheerful -> "
                           "'light smile'/'smile'; playful/mischievous -> 'grin'; confident/cocky -> "
                           "'smirk'; stern/disciplined -> 'serious'; grumpy/cold/cruel -> 'scowl'/"
                           "'glaring' (ONLY if the character is truly hard). Do NOT default everyone "
                           "to smug/serious/cold — that reads cruel. NEVER 'neutral expression'/"
                           "'expressionless' (cold resting-bitch-face) or a big transient emotion."},
            "skin_tone": {"type": "string",
                          "enum": ["pale skin", "light skin", "tan", "dark skin", "very dark skin"],
                          "description": "the character's skin BRIGHTNESS/tone — MATCH their heritage "
                                         "(infer from name + persona; don't default non-white characters "
                                         "to pale): e.g. East-Asian-coded -> light skin, Latina/"
                                         "Mediterranean -> tan, South-Asian/African -> dark or very dark "
                                         "skin. TONE only; texture ('shiny skin') and marks ('freckles') "
                                         "go in the appearance list. Never 'olive'/'fair' (not real tags)."},
            "pose": {"type": "string",
                     "enum": ["arms at sides", "hand on hip", "crossed arms", "arms behind back",
                              "hands in pockets", "contrapposto"],
                     "description": "ONE subtle STANDING reference pose that suits the PERSONALITY "
                                    "(the base stays standing, full-body, facing viewer — these are "
                                    "template-safe, NOT dynamic/action poses). confident/assertive -> "
                                    "'hand on hip' or 'crossed arms'; shy/formal/reserved -> 'arms "
                                    "behind back'; casual/relaxed -> 'hands in pockets' or "
                                    "'contrapposto'; neutral default -> 'arms at sides'."},
            "distinguishing_feature": {"type": "array", "items": {"type": "string"},
                "minItems": 1, "maxItems": 3,
                "description":
                    "1-2 DISTINCTIVE facial identity hooks that make THIS face unmistakable and "
                    "DIFFERENT from the model's default pretty-anime face — the single biggest lever "
                    "against a same-y cast, so NEVER leave it empty. Pick HIGH-SIGNAL booru tags that "
                    "actually render, and VARY them character-to-character (don't put the same hook on "
                    "everyone): 'mole under eye', 'mole under mouth', 'tear mole', 'freckles', 'beauty "
                    "mark', 'heterochromia', 'glasses', 'eyeshadow', 'red lipstick', 'eyeliner', 'fang', "
                    "'scar across eye', 'facial mark', 'sharp eyes', 'tsurime', 'tareme'. Choose what "
                    "fits the persona (a tidy character might get glasses + a mole; a striking one "
                    "heterochromia). Marks only — NOT hair/clothing/expression/pose."},
            # Free-form: the model writes as many descriptive booru tags as it needs (no rigid slots).
            "appearance": {"type": "array", "items": {"type": "string"},
                           "description":
                               "~22-30 SPECIFIC, FLATTERING canonical booru tags — capture what makes THIS "
                               "character distinct AND attractive; tight, not a generic 8-tag sketch and not "
                               "padded. LAYER a few tags per facet (slot pattern, fill each with a real tag "
                               "that fits THIS character — vary every value, don't reuse the same defaults): "
                               "HAIR (<colour> + <length> + ONE primary style + 1-2 details — don't stack "
                               "ponytail+bun+twintails; an afro/dreadlocks/cornrows is an all-over COILY "
                               "style, so NEVER pair it with 'bangs' or 'straight/wavy hair'); EYES (<colour> + <shape> + optional lashes; eyebrows "
                               "ONLY if distinctive, don't default to 'thick eyebrows'); SKIN+MARKS (texture "
                               "like 'shiny skin' + marks like 'freckles'/'mole under eye'/'scar across eye'/"
                               "'tattoo'/'glasses'); BODY (ONE "
                               "build that FITS the persona — NOT always 'slim'; medium breasts is the usual "
                               "adult default (small/large to fit), 'flat chest' for males only; + 'collarbone', "
                               "'wide hips', 'thick thighs', 'abs', height) and FACE/CUTE tags ('fang', 'blush "
                               "stickers', 'mole under eye', 'facial mark', 'makeup').\n"
                               "THE IMAGE MODEL IS LITERAL — only real booru tags render; unknown/figurative "
                               "phrases render as nothing or the literal object. NEVER: 'olive skin' (->GREEN; "
                               "use pale/tan/dark), 'almond eyes' (->almonds; use tsurime/tareme), gem/"
                               "metaphor colours (raven/auburn/emerald -> plain black/red/green). Face/chin/"
                               "cheekbone/nose/lip SHAPE is barely tagged — OMIT it; convey mood through eye "
                               "SHAPE ('tsurime' sharp, 'tareme' soft) with eyes OPEN — NEVER half-closed/jitome/"
                               "closed eyes/eyebags (sleepy, ugly). NO transient "
                               "emotion, clothing, pose, background or scene — those are added separately."},
        },
    }

    # Tags that cannot truthfully co-exist — the model sometimes emits several (e.g. 'large eyes'
    # AND 'small eyes'). Keep only the FIRST seen from each group, drop the rest.
    _EXCLUSIVE_GROUPS = [
        {"small eyes", "large eyes"},
        {"youthful face", "adult face", "mature face"},
        {"tall", "short", "very short", "average height"},
        # main body build — keep the FIRST the model picks (wide hips / narrow waist may co-exist)
        {"petite", "slim", "slender", "toned", "athletic", "curvy", "voluptuous", "plump",
         "muscular", "muscular female", "muscular male"},
        # bust size — exactly one
        {"flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts",
         "gigantic breasts"},
        # ONE primary tie/updo hairstyle — stops stacking ponytail + bun + twintails (looks odd)
        {"ponytail", "low ponytail", "high ponytail", "side ponytail", "folded ponytail",
         "twintails", "low twintails", "hair bun", "double bun", "single hair bun", "hime cut",
         "drill hair", "twin drills"},
        # ONE base hair texture, and ONE "disorder" tag — avoid wavy+straight or messy+flyaway piles
        {"straight hair", "wavy hair", "curly hair"},
        {"messy hair", "flyaway hair", "disheveled hair", "disheveled hair"},
    ]

    def _assemble_base_prompt(f: dict) -> str:
        """Assemble the base-image prompt from the model's free-form `appearance` tag list plus the
        fixed neutral / full-body / swimwear / grey framing. The model writes rich descriptors
        freely; this only enforces the guardrails it gets wrong: a strong CANONICAL sex anchor by
        age, transient/scene/clothing leakage filtered, and contradictory tags reduced to one."""
        count = (f.get("count") or "1girl").strip().lower()
        male = re.search(r"\b1\s*(boy|man|male)\b", count) is not None

        # Adult vs minor from the apparent age.
        age = (f.get("apparent_age") or "").lower()
        m = re.search(r"\d+", age)
        if m:
            minor = int(m.group()) < 18
        elif any(w in age for w in ("child", "teen", "kid")):
            minor = True
        else:
            minor = False

        # SEX ANCHOR (canonical). '1man'/'1woman' are NOT real Danbooru tags (≈0 images), so a
        # female-skewed style LoRA happily genderbends them; '1boy'/'1girl' are the real tags for
        # ALL ages. AGE is honest, not forced: ADULTS get 'mature male'/'mature female' (the real
        # grown-up anchors) + the minimal swimwear identity-capture template; MINORS get neither
        # 'mature' nor adult-physique anchors, and a MODEST base outfit (never swimwear) — their
        # real age comes through the appearance tags. 'male focus' resists the genderbend either
        # way. (Sexualisation guards — loli/shota — stay in the workflow negatives regardless.)
        if minor:
            gender = ["1boy", "male focus"] if male else ["1girl"]
            attire = "t-shirt, shorts"
        elif male:
            gender = ["1boy", "male focus", "mature male", "pectorals", "flat chest"]
            attire = "swim trunks, bare chest"
        else:
            gender = ["1girl", "mature female"]
            attire = "bikini"

        # Free-form appearance tags: split any crammed strings, drop transient/scene/clothing/pose
        # leakage AND any person-count tag the model slips in (e.g. '1woman', '1girl') — the sex
        # anchor above is authoritative, so a leaked count would only duplicate/contradict it.
        count_re = re.compile(r"^\d+\s*(boy|girl|man|woman|male|female|other)s?$")

        def _clean_tags(items):
            out = []
            for item in (items or []):
                for atom in str(item).split(","):
                    a = atom.strip()
                    if a and not count_re.match(a.lower()) and not any(b in a.lower() for b in _APPEARANCE_BLOCK):
                        out.append(a)
            return out

        app = _clean_tags(f.get("appearance"))
        # DISTINCTIVE FACE HOOKS (mole/freckles/heterochromia/glasses/makeup/…) — placed EARLY so
        # they carry prompt weight and break Illustrious's "house face" prior that otherwise renders
        # every character with the same default anime face.
        face_hooks = _clean_tags(f.get("distinguishing_feature"))
        # Skin TONE is a guaranteed brightness tag (the model used to give only 'shiny skin', a
        # texture, and never a tone). Validate against the gradient; default to a mid 'light skin'.
        skin = (f.get("skin_tone") or "").strip().lower()
        if skin not in ("pale skin", "light skin", "tan", "dark skin", "very dark skin"):
            skin = "light skin"
        parts = [*gender, skin, *face_hooks, *app]
        # Persistent RESTING expression by personality (NOT 'neutral expression' — a near-dead tag
        # that renders a cold resting-bitch-face). Fall back to a warm 'light smile'; never let a
        # neutral/expressionless value through. Sprites still vary emotion on top of this base.
        expr = (f.get("expression") or "").strip().lower()
        if not expr or "neutral" in expr or "expressionless" in expr:
            expr = "light smile"
        # Subtle STANDING reference pose by personality (template-safe; default 'arms at sides').
        pose = (f.get("pose") or "").strip().lower()
        if pose not in ("arms at sides", "hand on hip", "crossed arms", "arms behind back",
                        "hands in pockets", "contrapposto"):
            pose = "arms at sides"
        # full-body swimwear template framing, on neutral grey (RMBG/Inspyrenet mattes it).
        parts += [expr, "solo", "full body", "standing", pose, "facing viewer", attire,
                  "grey background", "simple background", "full body shot", "head to toe", "feet visible"]
        # normalise underscores → spaces; drop blanks; de-dup; resolve contradictions (keep first).
        seen, used_groups, out = set(), set(), []
        for p in parts:
            p = p.replace("_", " ").strip().strip(",").strip()
            if not p or p.lower() in seen:
                continue
            grp = next((i for i, g in enumerate(_EXCLUSIVE_GROUPS) if p.lower() in g), None)
            if grp is not None:
                if grp in used_groups:
                    continue            # already have a tag from this exclusive group
                used_groups.add(grp)
            seen.add(p.lower()); out.append(p)
        low = {t.lower() for t in out}
        # A bun means the hair is gathered UP — a flowing-length tag alongside it ('long hair' +
        # 'hair bun') reads as two hairstyles at once. Drop the length when an updo is present.
        if low & {"hair bun", "double bun", "single hair bun"}:
            out = [t for t in out if t.lower() not in
                   ("long hair", "very long hair", "absurdly long hair", "medium hair")]
        # All-over COILY styles (afro / dreadlocks / cornrows) have no separate fringe and aren't
        # smooth — so 'parted bangs + afro' or 'straight hair + dreadlocks' is physically impossible.
        # When one is present, drop every bangs tag and any contradicting smooth texture. ('curly
        # hair' is consistent with an afro, so it stays.)
        if low & {"afro", "dreadlocks", "cornrows"}:
            out = [t for t in out if "bangs" not in t.lower()
                   and t.lower() not in ("straight hair", "wavy hair")]
        # Guarantee a bust tag for adult women — the model under-tags it and skews flat by
        # omission; 'medium breasts' is the natural default (it can still pick small/large above).
        _BUST = ("flat chest", "small breasts", "medium breasts", "large breasts",
                 "huge breasts", "gigantic breasts")
        if not male and not minor and not (low & set(_BUST)):
            out.append("medium breasts")
        # literal-tag normalizer (olive->tan, …) then snap to real booru tags — unknowns kept.
        return _snap_prompt(_safe_image_tags(", ".join(out)))

    @app.post("/api/characters/{key}/base-prompt/generate")
    async def generate_base_prompt(key: str, body: dict):
        """Regenerate a character's base-image prompt via the shared appearance authority
        (_compose_base_prompt): FEATURES_SCHEMA draft → co-occurrence enrichment →
        _assemble_base_prompt. Returns {prompt, features, companions}. The SAME function runs at
        cast generation, so a regenerated cast already matches this."""
        from fastapi.concurrency import run_in_threadpool

        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        fields = ch.fields or {}
        out = await run_in_threadpool(lambda: _compose_base_prompt(
            ch.name, ch.system or "", fields.get("appearance", ""), fields.get("role", ""),
            (body or {}).get("model")))
        if "error" in out:
            return JSONResponse({"error": out["error"]}, status_code=500)
        return out

    @app.get("/api/tags/search")
    async def tags_search(q: str = "", limit: int = 20, noisy: bool = False):
        """Autocomplete against the real Danbooru vocabulary — prefix-first, ranked by post
        count. Returns {tags:[{tag, name, count, category}]}. Empty index → empty list."""
        from fastapi.concurrency import run_in_threadpool

        from ..tags import get_index
        ix = await run_in_threadpool(get_index)          # first call parses the CSV (~0.4s)
        n = min(max(int(limit or 20), 1), 50)
        return {"tags": ix.search(q or "", limit=n, include_noisy=bool(noisy))}

    @app.post("/api/tags/snap")
    async def tags_snap(body: dict):
        """Snap a free-text prompt onto real booru tags. Returns the full snap report
        {prompt, tags, items, changed, unknown} so the editor can colour each tag and show
        what changed. Unknown tags are KEPT (never silently dropped) and carry suggestions."""
        from fastapi.concurrency import run_in_threadpool

        from ..tags import get_index
        body = body or {}
        text = body.get("prompt") or body.get("text") or ""
        ix = await run_in_threadpool(get_index)
        if not ix.ready:                                  # no vocabulary available — echo input
            tags = [t.strip() for t in text.split(",") if t.strip()]
            return {"prompt": ", ".join(tags), "tags": tags, "items": [],
                    "changed": [], "unknown": []}
        return ix.snap(text)

    @app.post("/api/tagify")
    async def tagify(body: dict):
        """Convert an existing PROSE image prompt into Danbooru tags in place (for
        stories built before the tag rule). Stateless: takes {text, kind} and returns
        {tags}. kind='scene' formats as a no-humans scenery plate; anything else as a
        character/subject tag list. Uses the active author model."""
        from ..scenario.builder import _APPEARANCE_RULE, _TAG_RULE

        body = body or {}
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "no text"}, status_code=400)
        kind = (body.get("kind") or "character").lower()
        provider = _author_provider(body.get("model"))
        if provider is None:
            return JSONResponse({"error": "no author model configured"}, status_code=400)
        if kind == "scene":
            fmt = ("Format: a flat comma-separated list of short lowercase booru tags for an "
                   "Illustrious/SDXL anime model — NO sentences, NO articles, NO connecting words. "
                   "Start with `no humans, scenery`, then the place, then mood/light/atmosphere tags.")
            system = ("You convert an image-generation prompt from prose into Danbooru tags. Preserve "
                      "EVERY concrete detail — translate, never invent or drop. Output ONLY the tag "
                      "list, nothing else (no preamble, no quotes, no explanation).\n\n" + fmt)
        elif kind == "base":
            # Clean a character's base-image prompt: keep only the persistent physical
            # identity, DROP clothing/pose/expression/scene, and append the full-body
            # swimwear template so outfits layer on a clean capture.
            system = ("You rewrite a character image prompt into a CLEAN BASE-IMAGE prompt. From the "
                      "input, KEEP ONLY the persistent physical identity and DISCARD all clothing, "
                      "accessories, pose, gesture, facial expression, action, background and scene "
                      "(those are added later). " + _APPEARANCE_RULE + "\n\n"
                      "After the identity tags, append EXACTLY this framing: `solo, full body, "
                      "standing, facing viewer, <SWIM>, plain simple background, full body shot, head "
                      "to toe, feet visible` — where <SWIM> is `swim trunks, bare chest` if the subject "
                      "is male else `bikini`. Output ONLY the final tag list, nothing else.")
        else:
            system = ("You convert an image-generation prompt from prose into Danbooru tags. Preserve "
                      "EVERY concrete detail (subject, count, hair, eyes, clothing, pose, setting, mood, "
                      "lighting) — translate, never invent or drop. Output ONLY the tag list, nothing "
                      "else (no preamble, no quotes, no explanation).\n\n" + _TAG_RULE)
        from fastapi.concurrency import run_in_threadpool
        try:
            res = await run_in_threadpool(
                lambda: provider.generate_text(system=system, prompt=f"Convert this:\n{text}"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"tagify failed: {exc}"}, status_code=500)
        tags = (res.text or "").strip().strip('"').strip()
        return {"tags": tags}

    @app.post("/api/characters/{key}/base-candidate")
    async def base_candidate(key: str, body: dict):
        """Generate ONE candidate base image for a character from their description.
        If a style source (another character's reference, e.g. the story's primary)
        is given, render in THAT art style via the IPAdapter style flow; otherwise
        plain txt2img. Returns a data URI (not saved) — the UI lets you pick a winner."""
        ch = base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        # Inject only the subject description — the chosen workflow carries its own
        # quality/style (e.g. illustrious_style's `embedding:lazypos, {{image}}`).
        # The UI may pass an edited `prompt` (see GET .../base-prompt); else use the
        # character's own appearance (NOT a 1girl fallback — that mis-genders e.g. Darek).
        body = body or {}
        prompt = _safe_image_tags((body.get("prompt") or "").strip()) or _base_prompt(ch)

        # Style source: an explicit image URL (e.g. a base-card image link) wins;
        # else another character's reference (`style_from`). The illustrious_style
        # flow translates the LOOK, not the identity.
        style_bytes = None
        style_url = body.get("style_url")
        if style_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as hc:
                    resp = await hc.get(style_url); resp.raise_for_status()
                    style_bytes = resp.content
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"could not fetch style image: {exc}"}, status_code=502)
        elif body.get("style_from"):
            ref = _reference_path(body["style_from"])
            if ref is not None and (not _reference_path(key) or body["style_from"] != key):
                style_bytes = ref.read_bytes()
        # Pick the workflow by role: 'style' when a style image is in play (config-overridable
        # via image_roles), else 'base'. An explicit image_model in the request always wins.
        # The init image only matters to a style/img2img graph; ComfyUIProvider ignores it on a
        # plain txt2img workflow (no LoadImage node), so passing it through is safe.
        model = _role_model("style" if style_bytes else "base", (body or {}).get("image_model"))
        provider, model_id = _image_provider(model)
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)  # fresh seed each call → a batch of 4 varies
        try:
            png = await _render(provider, prompt, init_image=style_bytes)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(),
                "model": model_id, "styled": style_bytes is not None}

    @app.post("/api/characters/{key}/reference/from-data")
    def set_reference_from_data(key: str, body: dict):
        """Set the character's reference/base image from a base64 data URI (used to
        accept a chosen base-image candidate)."""
        safe = re.sub(r"[^\w\-]+", "", key)
        if not (_char_dir() / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            data = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001
            pass
        (_char_dir() / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.post("/api/characters/{key}/expand-background")
    def expand_background(key: str, body: dict):
        """Rewrite a character's persona into a THOROUGH, labelled background
        (Identity / History / Personality / Relationships / Voice). Works for any
        character (incl. imported); uses the character's attached story as context."""
        nonlocal base_settings
        safe = re.sub(r"[^\w\-]+", "", key)
        path = _char_dir() / f"{safe}.yaml"
        ch = base_settings.characters.get(key)
        if ch is None or not path.is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider = _author_provider((body or {}).get("model"))
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect a chat model first"}, status_code=400)
        story_ctx = ""
        sk = (ch.fields or {}).get("story") or (body or {}).get("story")
        st = base_settings.stories.get(sk) if sk else None
        if st is not None:
            story_ctx = f"\n\nThis character belongs to the story \"{st.name}\": {st.premise}"
        system = (
            "You are a character writer. Expand the given character into a THOROUGH background for a "
            "chat character, written as labelled sections: Identity, History, Personality, "
            "Relationships, Voice. Deepen and enrich while staying fully consistent with what is "
            "given and the story — never contradict established facts. Output only the background.")
        prompt = (f"NAME: {ch.name}\nAPPEARANCE: {(ch.fields or {}).get('appearance', '')}\n"
                  f"CURRENT PERSONA:\n{ch.system or '(thin / none)'}{story_ctx}\n\n"
                  f"Write {ch.name}'s thorough background.")
        try:
            res = provider.generate_text(system=system, prompt=prompt)
            bg = (res.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not bg:
            return JSONResponse({"error": "model returned nothing"}, status_code=500)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["system"] = bg
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        base_settings = load_settings(root)
        return {"ok": True, "system": bg}

    @app.delete("/api/characters/{key}")
    def delete_character(key: str):
        """Delete a character (yaml + avatar/ref + portraits). First strips it from
        every scenario/story cast so the config still validates on reload."""
        nonlocal base_settings
        import shutil
        safe = re.sub(r"[^\w\-]+", "", key)
        cdir = _char_dir()
        if not (cdir / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        # remove cast references in scenarios + stories (don't delete those files)
        for d in (_scenario_dir(), _story_dir()):
            if not d.is_dir():
                continue
            for p in d.glob("*.yaml"):
                try:
                    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                except Exception:  # noqa: BLE001
                    continue
                cast = doc.get("cast")
                if isinstance(cast, list) and any(isinstance(m, dict) and m.get("character") == key for m in cast):
                    doc["cast"] = [m for m in cast if not (isinstance(m, dict) and m.get("character") == key)]
                    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
            f = cdir / fn
            if f.is_file():
                f.unlink()
        shutil.rmtree(_portrait_dir(key), ignore_errors=True)
        base_settings = load_settings(root)
        return {"ok": True}

    def _write_character(cdata: dict, avatar_png: bytes | None) -> dict:
        """Validate + persist a normalized character: one configs/characters/
        <key>.yaml, the avatar PNG alongside (loader globs *.yaml, so the .png is
        ignored by config loading), then reload settings so it's selectable."""
        nonlocal base_settings
        Character(**cdata)  # raises on a malformed card
        char_dir = _char_dir()
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
        split_cards_to_scenarios(root)
        base_settings = load_settings(root)
        return {"ok": True, "key": key, "name": cdata["name"],
                "avatar": (char_dir / f"{key}.png").is_file()}

    @app.post("/api/characters/import")
    def import_character(body: CharacterImportRequest):
        """Import a SillyTavern character card from an uploaded file (PNG with an
        embedded `chara` chunk, or a raw JSON card)."""
        try:
            raw = base64.b64decode(body.data_b64)
            cdata = to_character(extract_card_json(raw))
            avatar = raw if raw.startswith(b"\x89PNG\r\n\x1a\n") else None
            return _write_character(cdata, avatar)
        except Exception as exc:  # malformed card / unreadable PNG / bad JSON
            return JSONResponse({"error": f"could not parse card: {exc}"}, status_code=400)

    @app.post("/api/characters/import-url")
    def import_character_url(body: dict):
        """Import a card from a site URL (Chub, JanitorAI, AICC, Pygmalion, or a
        direct Tavern-PNG link) — the SillyTavern import-from-URL feature."""
        url = (body or {}).get("url", "").strip()
        if not url:
            return JSONResponse({"error": "no url provided"}, status_code=400)
        try:
            card, avatar = fetch_card(url)
            return _write_character(to_character(card), avatar)
        except Exception as exc:
            return JSONResponse({"error": f"import failed: {exc}"}, status_code=400)

    @app.get("/api/models")
    def models() -> dict:
        s = effective_settings()
        text = [{"key": k, "provider": m.provider, "model": m.options.get("model")}
                for k, m in s.models.items() if m.kind == "text"]
        image = [{"key": k, "provider": m.provider} for k, m in s.models.items() if m.kind == "image"]
        return {"text": text, "image": image}

    @app.get("/api/text-models")
    def text_models(kind: str = "text") -> dict:
        """The full model list from the active connection of `kind` (text =
        chat model, image_prompt = image-prompt generator) — so each picker can
        index every model offered by its own connection, not just the saved one."""
        conn = store.active(kind if kind in ("text", "image_prompt") else "text")
        if not conn:
            return {"models": [], "active": None, "connected": False}
        try:
            ms = test_connection(conn.provider, conn.api_key, conn.base_url)
        except Exception as exc:  # noqa: BLE001
            return {"models": [], "active": conn.model, "connected": False, "error": str(exc)}
        return {"models": ms, "active": conn.model, "connected": True, "connection": conn.id}

    @app.post("/api/text/model")
    def set_text_model(body: dict):
        body = body or {}
        kind = body.get("kind") if body.get("kind") in ("text", "image_prompt") else "text"
        conn = store.active(kind)
        if conn is None:
            return JSONResponse({"error": f"no {kind} connection — set one up in Connection"}, status_code=400)
        conn.model = body.get("model")
        store.upsert(conn, make_active=True)
        return {"ok": True, "active": conn.model}

    _ROLE_LABELS = {
        "base": "Character base image",
        "style": "Styled base (style transfer)",
        "sprite": "Outfit / emotion sprites",
        "scene": "Story location backgrounds",
        "chat": "Chat pictures",
    }

    @app.get("/api/image-roles")
    def get_image_roles() -> dict:
        """Per-role image-workflow config: what each generation role uses. Returns the saved
        overrides, what each currently resolves to (effective), the unset fallback (default),
        the available image workflows, and human labels."""
        cfg = load_image_roles()
        return {
            "roles": IMAGE_ROLES,
            "labels": _ROLE_LABELS,
            "config": {r: (cfg.get(r) or "") for r in IMAGE_ROLES},     # saved overrides ('' = unset)
            "effective": {r: _role_model(r) for r in IMAGE_ROLES},      # what runs today
            "default": {r: _role_default(r) for r in IMAGE_ROLES},      # fallback when unset
            "models": [k for k, m in base_settings.models.items() if m.kind == "image"],
        }

    @app.post("/api/image-roles")
    def set_image_roles(body: dict):
        """Save per-role workflow overrides to configs/image_roles.json. Accepts {config:{role:key}}
        (full or partial) or a single {role, model}. Empty string clears a role (back to default).
        Read fresh per request, so it takes effect immediately — no restart."""
        body = body or {}
        cfg = load_image_roles()
        updates = body.get("config")
        if updates is None and body.get("role"):
            updates = {body["role"]: body.get("model") or ""}
        if not isinstance(updates, dict):
            return JSONResponse({"error": "expected {config:{role:key}} or {role,model}"}, status_code=400)
        for role, key in updates.items():
            if role not in IMAGE_ROLES:
                return JSONResponse({"error": f"unknown role '{role}'"}, status_code=400)
            key = (key or "").strip()
            if key and (key not in base_settings.models or base_settings.models[key].kind != "image"):
                return JSONResponse({"error": f"'{key}' is not an image workflow"}, status_code=400)
            if key:
                cfg[role] = key
            else:
                cfg.pop(role, None)                       # empty → unset (back to default)
        path = root / "configs" / "image_roles.json"
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "config": {r: (cfg.get(r) or "") for r in IMAGE_ROLES},
                "effective": {r: _role_model(r) for r in IMAGE_ROLES}}

    # -- connections ------------------------------------------------------
    @app.get("/api/providers")
    def providers(kind: str | None = None) -> list:
        # image_prompt connections use the same (text) providers as chat.
        return list_providers("text" if kind == "image_prompt" else kind)

    @app.get("/api/connections")
    def connections() -> dict:
        return {"active": store.active_map, "connections": [c.masked() for c in store.list()]}

    def _validate(kind: str, provider: str, api_key: str, base_url: str | None) -> dict:
        """Shared validation: text via the provider, image via a ComfyUI ping."""
        if kind == "image":
            if not ping_comfyui(base_url):
                return {"ok": False, "error": "ComfyUI not reachable at that URL — is it running?"}
            ms = image_model_items()
            return {"ok": True, "count": len(ms), "models": ms}
        try:
            ms = test_connection(provider, api_key, base_url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "count": len(ms), "models": ms}

    @app.post("/api/connections/test")
    def conn_test(body: TestRequest):
        r = _validate(body.kind, body.provider, body.api_key, body.base_url)
        return r if r["ok"] else JSONResponse(r, status_code=400)

    @app.post("/api/connections")
    def conn_save(body: SaveConnRequest):
        cid = body.id or body.provider
        existing = store.get(cid)
        # An empty api_key on update PRESERVES the stored key (so auto-saving a model/name
        # change never wipes the credential). A fresh key replaces it.
        api_key = body.api_key or (existing.api_key if existing else "")
        conn = Connection(
            id=cid,
            kind=body.kind,
            provider=body.provider,
            base_url=body.base_url,
            api_key=api_key,
            model=body.model,
        )
        store.upsert(conn, make_active=True)
        return {"ok": True, "active": store.active_map}

    @app.post("/api/connections/{conn_id}/test")
    def conn_test_saved(conn_id: str):
        """Validate + list models for a saved connection using its stored key
        (which never leaves the server)."""
        conn = store.get(conn_id)
        if conn is None:
            return JSONResponse({"ok": False, "error": "no such connection"}, status_code=404)
        r = _validate(conn.kind, conn.provider, conn.api_key, conn.base_url)
        if not r["ok"]:
            return JSONResponse(r, status_code=400)
        return {**r, "provider": conn.provider, "base_url": conn.base_url, "model": conn.model}

    @app.post("/api/connections/{conn_id}/activate")
    def conn_activate(conn_id: str):
        store.set_active(conn_id)
        return {"ok": True, "active": store.active_map}

    @app.delete("/api/connections/{conn_id}")
    def conn_delete(conn_id: str):
        store.remove(conn_id)
        return {"ok": True, "active": store.active_map}

    # -- comfy + run ------------------------------------------------------
    # -- image workflow editing ------------------------------------------
    def _active_comfy_url() -> str:
        conn = store.active("image")
        return (conn.base_url if conn and conn.base_url else comfy_url)

    def _comfy_base_dir() -> Path | None:
        """The managed ComfyUI base directory (holds models/checkpoints, models/
        loras). None for connect-only setups where we don't know the layout."""
        launch = getattr(get_server(comfy_url), "launch", None)
        bd = getattr(launch, "base_directory", None) if launch else None
        return Path(bd) if bd else None

    async def _cancel_job(job) -> dict:
        """Cancel any hub job + run its optional on_cancel hook (e.g. ComfyUI
        interrupt). Shared by the per-category and the generic /jobs cancel."""
        if job is None or job.status != "running":
            return {"ok": False, "status": job.status if job else "idle"}
        job.cancel()
        await job.on_cancel()
        return {"ok": True}

    def _workflow_path(model_key: str) -> Path | None:
        md = base_settings.models.get(model_key)
        if md is None or md.kind != "image":
            return None
        wf = md.options.get("workflow")
        if not wf:
            return None
        path = (root / wf).resolve()
        # Guard against path traversal — must stay inside the project root.
        if root.resolve() not in path.parents:
            return None
        return path

    @app.get("/api/workflow")
    def get_workflow(model: str):
        path = _workflow_path(model)
        if path is None or not path.is_file():
            return JSONResponse({"error": f"no workflow for image model '{model}'"}, status_code=404)
        md = base_settings.models.get(model)
        # Which node fields Loom overwrites at generation time (positive/negative
        # prompt). The positive one is the image description the model writes.
        injects: dict[str, dict[str, str]] = {}
        for role, target in (md.options.get("inputs") or {}).items():
            try:
                injects.setdefault(str(target["node"]), {})[target["field"]] = role
            except (KeyError, TypeError):
                pass
        return {
            "model": model, "path": str(path),
            "json": json.loads(path.read_text(encoding="utf-8")),
            "injects": injects,
        }

    @app.post("/api/workflow")
    def save_workflow(body: WorkflowSaveRequest):
        path = _workflow_path(body.model)
        if path is None:
            return JSONResponse({"error": f"no workflow for image model '{body.model}'"}, status_code=404)
        path.write_text(json.dumps(body.json, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path)}

    @app.post("/api/workflow/duplicate")
    def duplicate_workflow(body: dict):
        """Clone an image model + its workflow JSON into a new editable copy. Copies the
        graph file to workflows/<key>_api.json and APPENDS a new models.yaml entry (textual
        append, so the file's comments survive). Returns the new model key."""
        nonlocal base_settings
        import copy

        body = body or {}
        src = (body.get("model") or "").strip()
        md = base_settings.models.get(src)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image model '{src}'"}, status_code=404)
        src_path = _workflow_path(src)
        if src_path is None or not src_path.is_file():
            return JSONResponse({"error": f"'{src}' has no workflow file to copy"}, status_code=400)

        # New key from the requested name (slugified), made unique against existing models.
        raw = (body.get("name") or f"{src}_copy").strip()
        base_key = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or f"{src}_copy"
        key, i = base_key, 2
        while key in base_settings.models:
            key = f"{base_key}_{i}"; i += 1

        # Copy the workflow graph to a file named after the new key (kept inside the project).
        new_rel = f"./workflows/{key}_api.json"
        new_path = (root / new_rel).resolve()
        if root.resolve() not in new_path.parents:
            return JSONResponse({"error": "bad workflow path"}, status_code=400)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")

        # New model entry = copy of the source, repointed at the new file. Append textually
        # (2-space indent, under the file's top-level `models:`) to preserve all comments.
        entry = {"provider": md.provider, "kind": md.kind,
                 "options": {**copy.deepcopy(md.options), "workflow": new_rel}}
        snippet = yaml.safe_dump({key: entry}, sort_keys=False, allow_unicode=True)
        indented = "".join(("  " + ln) if ln.strip() else ln
                           for ln in snippet.splitlines(keepends=True))
        models_file = root / "configs" / "models.yaml"
        text = models_file.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        models_file.write_text(text + f"\n  # duplicated from {src}\n" + indented, encoding="utf-8")
        base_settings = load_settings(root)
        return {"ok": True, "key": key, "workflow": new_rel}

    @app.post("/api/workflow/test")
    async def test_workflow(body: WorkflowTestRequest):
        """Run the image model's workflow through ComfyUI with a test prompt,
        streaming live progress (SSE) and ending with the rendered image(s).
        Uses the in-editor JSON if provided."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        import httpx

        from ..comfy.generate import stream_generate
        from ..comfy.lora import randomize_seeds
        from ..providers.comfyui_provider import ComfyUIProvider

        md = base_settings.models.get(body.model)
        if md is None or md.kind != "image":
            return JSONResponse({"ok": False, "error": f"no image model '{body.model}'"}, status_code=404)
        opts = dict(md.options)
        conn = store.active("image")
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url

        try:
            provider = ComfyUIProvider(opts)
            if body.json:
                provider.workflow = body.json
            await run_in_threadpool(get_server(provider.base_url).ensure_up)
            prompt = body.prompt or "masterpiece, best quality, highly detailed, 1girl, scenery, soft light"
            # Fresh seed per render, exactly like the LoRA batch pipeline — otherwise
            # every test is the same fixed seed:0 roll (usually a mediocre one).
            graph = randomize_seeds(provider._inject(prompt, None))
            # img2img: upload the source image and point the LoadImage node at it
            # (no-op if the workflow has no LoadImage node).
            if body.init_image:
                import base64 as _b64
                raw = _b64.b64decode(body.init_image.split(",", 1)[-1])

                def _wire():
                    with httpx.Client(base_url=provider.base_url, timeout=60) as client:
                        provider._set_init_image(client, graph, raw)
                await run_in_threadpool(_wire)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

        async def events():
            try:
                async for ev in stream_generate(provider.base_url, graph, provider.output_node, provider.timeout_s):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    # -- image prompt generator config -----------------------------------
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

    def load_promptgen() -> dict:
        path = root / "configs" / "promptgen.json"
        if path.is_file():
            return {**PROMPTGEN_DEFAULT, **json.loads(path.read_text(encoding="utf-8"))}
        return dict(PROMPTGEN_DEFAULT)

    @app.get("/api/promptgen")
    def get_promptgen() -> dict:
        return load_promptgen()

    @app.post("/api/promptgen")
    def save_promptgen(body: dict):
        cfg = {**PROMPTGEN_DEFAULT, **(body or {})}
        path = root / "configs" / "promptgen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    # -- chat model system prompt ----------------------------------------
    # A global system prompt for the chat model, layered on top of the selected
    # character's own system. Empty by default (character governs entirely).
    CHATGEN_DEFAULT = {"system": ""}

    def load_chatgen() -> dict:
        path = root / "configs" / "chatgen.json"
        if path.is_file():
            return {**CHATGEN_DEFAULT, **json.loads(path.read_text(encoding="utf-8"))}
        return dict(CHATGEN_DEFAULT)

    @app.get("/api/chatgen")
    def get_chatgen() -> dict:
        return load_chatgen()

    @app.post("/api/chatgen")
    def save_chatgen(body: dict):
        cfg = {**CHATGEN_DEFAULT, **(body or {})}
        path = root / "configs" / "chatgen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    # -- prompt sets (saveable / selectable prompt arrays) ---------------
    import re as _re

    def _sets_dir() -> Path:
        return root / "configs" / "prompt_sets"

    @app.get("/api/lora/sets")
    def lora_sets() -> dict:
        d = _sets_dir()
        names = sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
        return {"sets": names}

    @app.get("/api/lora/sets/{name}")
    def lora_set(name: str):
        safe = _re.sub(r"[^\w\-]+", "_", name)
        path = _sets_dir() / f"{safe}.json"
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/lora/sets")
    def save_lora_set(body: PromptSetRequest):
        safe = _re.sub(r"[^\w\-]+", "_", body.name).strip("_") or "set"
        d = _sets_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.json").write_text(
            json.dumps({"name": safe, "prompts": body.prompts}, indent=2), encoding="utf-8")
        return {"ok": True, "name": safe}

    # -- LoRA dataset builder --------------------------------------------
    @app.post("/api/lora/prompts")
    def lora_prompts(body: LoraPromptsRequest):
        pg = load_promptgen()
        provider = text_provider_for(body.model or pg.get("model"))
        if provider is None:
            return JSONResponse({"error": "no text connection — set one up in Connection first"}, status_code=400)
        schema = {
            "type": "object",
            "properties": {"prompts": {"type": "array", "items": {"type": "string"}}},
            "required": ["prompts"], "additionalProperties": False,
        }
        instruction = (
            f"Produce exactly {body.count} distinct image prompts for a LoRA training set. "
            f"Each must depict a DIFFERENT subject in a DIFFERENT scene with DIFFERENT lighting. "
            f"Theme: {body.theme}. Each prompt is comma-separated Stable-Diffusion tags "
            f"(subject, appearance, setting, lighting, composition). Return them in `prompts`."
        )
        try:
            res = provider.generate_text(system=pg.get("system"), prompt=instruction, emits=schema)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        prompts = [p for p in (res.data.get("prompts") or []) if isinstance(p, str) and p.strip()]
        return {"prompts": prompts[: body.count]}

    # Random characters sampled straight from danbooru_character.csv — no LLM.
    # Bias toward characters with enough solo posts that the base model knows
    # them, then drop each into a varied scene/lighting/framing.
    _DAN_SCENES = [
        "concert stage", "bamboo forest at night", "manor garden", "snowy forest",
        "rocky wasteland", "fountain plaza", "ruined city wall", "ancient overgrown ruins",
        "ship deck", "dim library", "sunset clubroom", "golden wheat field",
        "misty battlefield meadow", "city street at dusk", "student council room",
        "flower field", "wisteria garden", "training grounds", "guild hall",
        "riverside town", "desert highway", "laboratory interior", "cozy kitchen",
        "empty classroom", "rooftop at night", "seaside cliff", "autumn shrine path",
        "neon alley", "cherry blossom courtyard", "mountain lake",
    ]
    _DAN_LIGHT = [
        "neon stage lighting", "moonlight", "soft morning light", "pale winter light",
        "dramatic glow", "bright daylight", "overcast light", "soft mystical light",
        "tropical sunlight", "lamplight", "warm window light", "golden hour",
        "dawn light", "blue hour", "candlelight", "dappled light", "torchlight",
        "clear daylight", "harsh sun", "cool lab light",
    ]
    _DAN_FRAME = ["upper body", "full body", "cowboy shot", "portrait", "dynamic pose"]

    @app.post("/api/lora/danbooru")
    def lora_danbooru(body: dict):
        import csv as _csv
        import random as _random

        count = max(1, min(int((body or {}).get("count") or 30), 200))
        min_solo = int((body or {}).get("min_solo") or 400)
        path = root / "danbooru_character.csv"
        if not path.is_file():
            return JSONResponse({"error": "danbooru_character.csv not found in project root"}, status_code=404)

        pool: list[str] = []
        with path.open(encoding="utf-8", newline="") as fh:
            for r_ in _csv.DictReader(fh):
                try:
                    if int(r_.get("solo_count") or 0) < min_solo:
                        continue
                except ValueError:
                    continue
                trig = (r_.get("trigger") or "").strip()
                if trig:
                    pool.append(trig)
        if not pool:
            return JSONResponse({"error": f"no characters with solo_count ≥ {min_solo}"}, status_code=400)

        def esc(t: str) -> str:
            return t.replace("(", "\\(").replace(")", "\\)")

        picks = _random.sample(pool, min(count, len(pool)))
        prompts = [
            f"{esc(t)}, solo, {_random.choice(_DAN_SCENES)}, "
            f"{_random.choice(_DAN_LIGHT)}, {_random.choice(_DAN_FRAME)}"
            for t in picks
        ]
        return {"prompts": prompts, "name": "random danbooru prompt"}

    @app.post("/api/lora/generate")
    async def lora_generate(body: LoraGenRequest):
        from fastapi.concurrency import run_in_threadpool

        from ..providers.comfyui_provider import ComfyUIProvider
        from .jobs import LoraJob, current

        running = current()
        if running and running.status == "running":
            return JSONResponse(
                {"error": "a batch is already running — check or cancel it first",
                 "running": True}, status_code=409)

        md = base_settings.models.get(body.model)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image model '{body.model}'"}, status_code=404)
        opts = dict(md.options)
        conn = store.active("image")
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        try:
            provider = ComfyUIProvider(opts)
            await run_in_threadpool(get_server(provider.base_url).ensure_up)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        job = LoraJob(provider.base_url, provider, body.prompts, body.variations,
                      provider.output_node, provider.timeout_s)
        job.start()
        return {"ok": True, "id": job.id, "total": job.total}

    @app.get("/api/lora/stream")
    def lora_stream():
        """Replay + live SSE for the current/last batch. 204 if there's none."""
        from fastapi.responses import StreamingResponse

        from .jobs import current

        job = current()
        if job is None:
            from starlette.responses import Response
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/lora/job")
    def lora_job():
        """Lightweight status (no images) so the UI can tell on mount whether a
        batch is in flight and reattach to it."""
        from .jobs import current

        job = current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/lora/cancel")
    async def lora_cancel():
        from .jobs import current

        return await _cancel_job(current())

    @app.post("/api/lora/save")
    def lora_save(body: LoraSaveRequest):
        import base64 as _b64
        import re

        name = re.sub(r"[^\w\-]+", "_", body.name or "loraset").strip("_") or "loraset"
        out = root / "datasets" / name
        out.mkdir(parents=True, exist_ok=True)
        saved = 0
        for item in body.images:
            src = item.get("src") or ""
            if not isinstance(src, str) or "," not in src:
                continue
            data = _b64.b64decode(src.split(",", 1)[1])
            (out / f"{saved:03d}.png").write_bytes(data)
            caption = item.get("caption")
            if caption:
                (out / f"{saved:03d}.txt").write_text(caption, encoding="utf-8")
            saved += 1
        (out / "loraset.json").write_text(
            json.dumps({"name": name, "count": saved, "base_model": body.base_model}, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "path": str(out), "count": saved}

    # -- dataset viewer + vision captioning ------------------------------
    def _dataset_dir(name: str) -> Path | None:
        safe = _re.sub(r"[^\w\-]+", "_", name or "").strip("_")
        d = (root / "datasets" / safe).resolve()
        if not safe or root.resolve() not in d.parents or not d.is_dir():
            return None
        return d

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

    def load_captioner() -> dict:
        path = root / "configs" / "captioner.json"
        cfg = dict(CAPTIONER_DEFAULT)
        if path.is_file():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg

    @app.get("/api/captioner")
    def get_captioner() -> dict:
        return load_captioner()

    @app.post("/api/captioner")
    def set_captioner(body: dict):
        cfg = {**CAPTIONER_DEFAULT, **(body or {})}
        path = root / "configs" / "captioner.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.get("/api/lora/datasets/{name}")
    def lora_dataset(name: str) -> dict:
        d = _dataset_dir(name)
        if d is None:
            return JSONResponse({"error": f"dataset not found: {name}"}, status_code=404)
        images = []
        for png in sorted(d.glob("*.png")):
            txt = png.with_suffix(".txt")
            images.append({"file": png.name,
                           "caption": txt.read_text(encoding="utf-8") if txt.is_file() else ""})
        return {"name": d.name, "count": len(images), "images": images}

    @app.get("/api/lora/datasets/{name}/img/{file}")
    def lora_dataset_image(name: str, file: str):
        from starlette.responses import FileResponse, Response

        d = _dataset_dir(name)
        if d is None or not _re.fullmatch(r"[\w\-]+\.png", file):
            return Response(status_code=404)
        path = (d / file).resolve()
        if d not in path.parents or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="image/png")

    class CaptionEdit(BaseModel):
        dataset: str
        file: str
        caption: str

    @app.post("/api/lora/caption/edit")
    def lora_caption_edit(body: CaptionEdit):
        d = _dataset_dir(body.dataset)
        if d is None or not _re.fullmatch(r"[\w\-]+\.png", body.file):
            return JSONResponse({"error": "bad dataset/file"}, status_code=404)
        (d / body.file).with_suffix(".txt").write_text(body.caption or "", encoding="utf-8")
        return {"ok": True}

    @app.post("/api/lora/caption")
    async def lora_caption(body: dict):
        """Re-caption a dataset with a vision model (routed through OpenRouter).
        Runs as a resident job so it survives navigation and shows in Activity."""
        import base64 as _b64

        from fastapi.concurrency import run_in_threadpool

        from .caption_job import CaptionJob, current as cap_current

        running = cap_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a captioning run is already going", "running": True},
                                status_code=409)

        d = _dataset_dir((body or {}).get("dataset", ""))
        if d is None:
            return JSONResponse({"error": "dataset not found"}, status_code=404)
        cfg = load_captioner()
        model = (body or {}).get("model") or cfg.get("model")
        if not model:
            # Don't silently fall back to the chat model — it's usually text-only.
            return JSONResponse({"error": "pick a vision-capable model in the Caption model box first"},
                                status_code=400)
        provider = text_provider_for(model)
        if provider is None:
            return JSONResponse({"error": "no text connection — connect OpenRouter first"}, status_code=400)
        if not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "selected model can't caption"}, status_code=400)

        pngs = sorted(d.glob("*.png"))
        system = cfg.get("system")

        async def producer(job):
            total = len(pngs)
            for i, png in enumerate(pngs):
                if job.cancelling:
                    break
                try:
                    uri = "data:image/png;base64," + _b64.b64encode(png.read_bytes()).decode()
                    res = await run_in_threadpool(
                        lambda: provider.generate_text(
                            system=system, prompt="Caption this image.", images=[uri]))
                    caption = (res.text or "").strip().replace("\n", " ")
                    png.with_suffix(".txt").write_text(caption, encoding="utf-8")
                    yield {"type": "caption", "file": png.name, "caption": caption, "index": i, "total": total}
                except Exception as exc:  # noqa: BLE001
                    yield {"type": "error", "file": png.name, "index": i, "total": total, "error": str(exc)}

        job = CaptionJob(d.name, "vlm", len(pngs))
        job.start(producer)
        return {"ok": True, "id": job.id, "total": len(pngs)}

    # -- WD14 local tagger (booru tags, runs in the trainer venv) --------
    @app.get("/api/lora/wd14/status")
    def wd14_status() -> dict:
        import subprocess

        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return {"available": False, "reason": "trainer venv not set up — WD14 runs in it"}
        try:
            r = subprocess.run([py, "-c", "import onnxruntime"], capture_output=True, timeout=40)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)}
        if r.returncode == 0:
            return {"available": True, "python": py}
        return {"available": False, "reason": "onnxruntime not installed in the trainer venv",
                "need_install": True}

    @app.post("/api/lora/wd14/install")
    async def wd14_install():
        from fastapi.responses import StreamingResponse

        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return JSONResponse({"error": "set up the trainer first — WD14 uses its venv"}, status_code=400)

        async def events():
            import asyncio
            from .train_job import _utf8_env
            proc = await asyncio.create_subprocess_exec(
                py, "-m", "pip", "install", "onnxruntime", env=_utf8_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            assert proc.stdout
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                yield f"data: {json.dumps({'type': 'log', 'line': raw.decode('utf-8', 'replace').rstrip()})}\n\n"
            code = await proc.wait()
            yield f"data: {json.dumps({'type': 'done', 'code': code})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/lora/caption/wd14")
    async def lora_caption_wd14(body: dict):
        from .caption_job import CaptionJob, current as cap_current

        running = cap_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a captioning run is already going", "running": True},
                                status_code=409)

        d = _dataset_dir((body or {}).get("dataset", ""))
        if d is None:
            return JSONResponse({"error": "dataset not found"}, status_code=404)
        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return JSONResponse({"error": "trainer venv not set up — WD14 runs in it"}, status_code=400)

        script = root / "scripts" / "wd14_tag.py"
        repo = (body or {}).get("repo") or "SmilingWolf/wd-vit-tagger-v3"
        gt = float((body or {}).get("general_thresh") or 0.35)
        ct = float((body or {}).get("character_thresh") or 0.85)
        cmd = [py, str(script), "--dataset-dir", str(d), "--repo", repo,
               "--general-thresh", str(gt), "--character-thresh", str(ct)]
        total = len(sorted(d.glob("*.png")))

        async def producer(job):
            import asyncio
            from .train_job import _utf8_env
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=_utf8_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            job.proc = proc
            assert proc.stdout
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        ev = {"type": "log", "line": line}
                    if ev.get("type") != "done":  # CaptionJob emits the terminal event
                        yield ev
                else:
                    yield {"type": "log", "line": line}
                if job.cancelling:
                    break
            await proc.wait()

        job = CaptionJob(d.name, "wd14", total)
        job.start(producer)
        return {"ok": True, "id": job.id, "total": total}

    @app.get("/api/lora/caption/stream")
    def lora_caption_stream():
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from .caption_job import current as cap_current

        job = cap_current()
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/lora/caption/job")
    def lora_caption_job() -> dict:
        from .caption_job import current as cap_current

        job = cap_current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/lora/caption/cancel")
    async def lora_caption_cancel():
        from .caption_job import current as cap_current

        return await _cancel_job(cap_current())

    # -- LoRA training (kohya sd-scripts) --------------------------------
    TRAINER_DEFAULT = {"sd_scripts_dir": "", "python": "", "checkpoints_dir": "", "loras_dir": ""}

    def load_trainer() -> dict:
        path = root / "configs" / "trainer.json"
        cfg = dict(TRAINER_DEFAULT)
        if path.is_file():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg

    def _checkpoints_dir(cfg: dict) -> Path | None:
        if cfg.get("checkpoints_dir"):
            return Path(cfg["checkpoints_dir"])
        bd = _comfy_base_dir()
        return (bd / "models" / "checkpoints") if bd else None

    def _loras_out_dir(cfg: dict) -> Path:
        # Default to ComfyUI's loras folder so a trained LoRA is immediately
        # usable (shows up in Chain LoRA). Fall back to the project's loras/.
        if cfg.get("loras_dir"):
            return Path(cfg["loras_dir"])
        bd = _comfy_base_dir()
        return (bd / "models" / "loras") if bd else (root / "loras")

    @app.get("/api/trainer")
    def get_trainer() -> dict:
        from ..train import kohya

        cfg = load_trainer()
        st = kohya.trainer_status(cfg.get("sd_scripts_dir"), cfg.get("python"))
        ck = _checkpoints_dir(cfg)
        return {**cfg, **st, "defaults": kohya.DEFAULTS,
                "checkpoints_dir_resolved": str(ck) if ck else None,
                "loras_out_dir": str(_loras_out_dir(cfg)),
                "setup_script": "scripts/setup_trainer.ps1"}

    @app.post("/api/trainer")
    def set_trainer(body: dict):
        cfg = load_trainer()
        for k in ("sd_scripts_dir", "python", "checkpoints_dir", "loras_dir"):
            if k in (body or {}):
                cfg[k] = (body[k] or "").strip()
        path = root / "configs" / "trainer.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.get("/api/trainer/detect")
    def trainer_detect() -> dict:
        from ..train import kohya

        return kohya.detect_cuda()

    @app.post("/api/trainer/setup")
    async def trainer_setup(body: dict):
        """Install/repair the kohya trainer from the UI — rebuilds the venv with
        the right Python + CUDA torch. Streams via the same /train/stream."""
        import shutil

        from .train_job import TrainJob, current as train_current

        running = train_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a run/setup is already going — check or cancel it",
                                 "running": True}, status_code=409)

        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return JSONResponse({"error": "PowerShell not found on PATH"}, status_code=400)
        script = root / "scripts" / "setup_trainer.ps1"
        if not script.is_file():
            return JSONResponse({"error": "scripts/setup_trainer.ps1 is missing"}, status_code=404)
        from ..train import kohya
        cuda = (body or {}).get("cuda") or "auto"
        if cuda == "auto":
            cuda = kohya.detect_cuda()["cuda"]
        if cuda not in ("cu121", "cu124", "cu128"):
            cuda = "cu124"

        # The install location is deterministic, so write the trainer config now —
        # it's correct the moment setup finishes (no reliance on the script's own
        # config write, which needs a newer PowerShell).
        sd_dir = root / "trainer" / "sd-scripts"
        cfg = load_trainer()
        cfg["sd_scripts_dir"] = str(sd_dir)
        cfg["python"] = str(sd_dir / "venv" / "Scripts" / "python.exe")
        (root / "configs").mkdir(parents=True, exist_ok=True)
        (root / "configs" / "trainer.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-Recreate", "-Cuda", cuda]
        job = TrainJob(cmd, cwd=str(root), output_name="trainer setup", output_path="")
        job.start()
        return {"ok": True, "id": job.id}

    @app.get("/api/lora/datasets")
    def lora_datasets() -> dict:
        d = root / "datasets"
        out = []
        if d.is_dir():
            for sub in sorted(d.iterdir()):
                if not sub.is_dir():
                    continue
                imgs = sorted(sub.glob("*.png"))
                if not imgs:
                    continue
                caps = sum(1 for i in imgs if i.with_suffix(".txt").is_file())
                out.append({"name": sub.name, "count": len(imgs), "captions": caps})
        return {"datasets": out}

    @app.post("/api/train/start")
    async def train_start(body: dict):
        from ..train import kohya
        from .train_job import TrainJob, current as train_current

        running = train_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a training run is already going — check or cancel it",
                                 "running": True}, status_code=409)

        cfg = load_trainer()
        st = kohya.trainer_status(cfg.get("sd_scripts_dir"), cfg.get("python"))
        if not st["installed"]:
            return JSONResponse({"error": "trainer not set up: " + "; ".join(st["issues"]),
                                 "needs_setup": True}, status_code=400)

        dataset = (body or {}).get("dataset")
        ddir = root / "datasets" / (dataset or "")
        if not dataset or not ddir.is_dir() or not any(ddir.glob("*.png")):
            return JSONResponse({"error": f"dataset not found or empty: {dataset}"}, status_code=404)

        ckname = (body or {}).get("base_model")
        ckdir = _checkpoints_dir(cfg)
        if not ckdir:
            return JSONResponse({"error": "can't resolve the checkpoints folder — set checkpoints_dir "
                                          "in the trainer config"}, status_code=400)
        ckpt = (ckdir / ckname) if ckname else None
        if not ckpt or not ckpt.is_file():
            return JSONResponse({"error": f"checkpoint not found: {ckpt}"}, status_code=404)

        p = kohya.merge_params(body or {})
        import re as _re2
        name = _re2.sub(r"[^\w\-]+", "_", (body or {}).get("output_name") or dataset).strip("_") or "lora"
        out_dir = _loras_out_dir(cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        work = ddir / "_train"
        work.mkdir(exist_ok=True)
        toml = work / "dataset.toml"
        kohya.write_dataset_toml(toml, ddir, p["resolution"], p["train_batch_size"], p["num_repeats"])
        cmd = kohya.build_command(st["python"], cfg["sd_scripts_dir"], ckpt, toml, out_dir, name, p)

        job = TrainJob(cmd, cwd=cfg["sd_scripts_dir"], output_name=name,
                       output_path=str(out_dir / f"{name}.safetensors"))
        job.start()
        return {"ok": True, "id": job.id, "output_name": name,
                "out": str(out_dir / f"{name}.safetensors")}

    @app.post("/api/train/anima-config")
    def train_anima_config(body: dict):
        """Anima is a DiT — kohya can't train it. Generate the native Anima
        trainer's config (Anima_lora_configs.toml + dataset TOML) from our dataset
        + the scanned model files, and hand back the command to run it there.
        Loom doesn't run this trainer (it's a separate, GUI-equipped install)."""
        from ..train import anima as animamod
        from ..train import kohya
        body = body or {}
        dataset = body.get("dataset")
        ddir = root / "datasets" / (dataset or "")
        if not dataset or not ddir.is_dir():
            return JSONResponse({"error": f"dataset not found: {dataset}"}, status_code=404)
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        dit = body.get("dit")
        te = body.get("text_encoder") or "qwen_3_06b_base.safetensors"
        vae = body.get("vae") or "qwen_image_vae.safetensors"
        if not dit:
            return JSONResponse({"error": "pick an Anima DiT model"}, status_code=400)
        dit_path = md / "diffusion_models" / dit.replace("\\", "/")
        qwen_path = md / "text_encoders" / te.replace("\\", "/")
        vae_path = md / "vae" / vae.replace("\\", "/")
        missing = [str(x) for x in (dit_path, qwen_path, vae_path) if not x.is_file()]
        if missing:
            return JSONResponse({"error": "missing model file(s): " + ", ".join(missing)}, status_code=404)

        p = animamod.merge_params(body)
        import re as _re3
        name = _re3.sub(r"[^\w\-]+", "_", body.get("output_name") or dataset).strip("_") or "anima-lora"
        work = ddir / "_train"
        work.mkdir(exist_ok=True)
        ds_toml = work / "anima_dataset.toml"
        kohya.write_dataset_toml(ds_toml, ddir, p["resolution"], p["train_batch_size"], p["num_repeats"])
        out_dir = _loras_out_dir(load_trainer())
        out_dir.mkdir(parents=True, exist_ok=True)
        config_path = work / "Anima_lora_configs.toml"
        text = animamod.write_config(config_path, dit_path=dit_path, qwen_path=qwen_path, vae_path=vae_path,
                                     dataset_toml=ds_toml, output_dir=out_dir, output_name=name, p=p)
        return {"ok": True, "config_path": str(config_path), "dataset_toml": str(ds_toml),
                "output": str(out_dir / f"{name}.safetensors"), "config_text": text,
                "command": f'python anima_train_network.py --config_file "{config_path}"'}

    @app.get("/api/train/stream")
    def train_stream():
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from .train_job import current as train_current

        job = train_current()
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/train/job")
    def train_job_status() -> dict:
        from .train_job import current as train_current

        job = train_current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/train/cancel")
    async def train_cancel():
        from .train_job import current as train_current

        return await _cancel_job(train_current())

    # -- the job hub: every workload routes through one registry; Activity is
    # just its view, and these generic endpoints work for any job by id --------
    @app.get("/api/jobs")
    def jobs_list() -> dict:
        from .jobhub import REGISTRY

        snaps = [j.snapshot() for j in reversed(REGISTRY.all())]  # newest first
        return {"jobs": snaps, "running": sum(1 for s in snaps if s["status"] == "running")}

    @app.get("/api/jobs/{job_id}/stream")
    def jobs_stream(job_id: str):
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from .jobhub import REGISTRY

        job = REGISTRY.get(job_id)
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/cancel")
    async def jobs_cancel(job_id: str):
        from .jobhub import REGISTRY

        return await _cancel_job(REGISTRY.get(job_id))

    def _system_stats() -> dict:
        """CPU / RAM (psutil) + GPU (nvidia-smi) for the Activity panel header.
        Any piece that's unavailable comes back as None."""
        import subprocess

        out = {"cpu": None, "mem": None, "gpu": None}
        try:
            import psutil

            out["cpu"] = psutil.cpu_percent(interval=None)  # since last call (polled ~3s)
            vm = psutil.virtual_memory()
            out["mem"] = {"percent": vm.percent, "used": vm.used, "total": vm.total}
        except Exception:  # noqa: BLE001
            pass
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,"
                 "power.draw,power.limit,temperature.gpu,name",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                f = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",", 7)]
                if len(f) >= 8:
                    def num(x):
                        try:
                            return float(x)
                        except ValueError:
                            return None
                    out["gpu"] = {
                        "util": num(f[0]),        # % of time the GPU was busy
                        "mem_util": num(f[1]),    # memory-controller (bandwidth) load
                        "mem_used": num(f[2]), "mem_total": num(f[3]),
                        "power": num(f[4]), "power_limit": num(f[5]),  # compute strain: draw vs cap
                        "temp": num(f[6]), "name": f[7],
                    }
        except Exception:  # noqa: BLE001
            pass
        return out

    # /api/activity is kept as an alias of /api/jobs (the Activity panel reads it).
    @app.get("/api/activity")
    def activity() -> dict:
        return {**jobs_list(), "system": _system_stats()}

    @app.get("/api/server/info")
    def server_info() -> dict:
        import os
        import sys

        return {"uptime_s": round(time.time() - _SERVER_STARTED), "python": sys.version.split()[0], "pid": os.getpid()}

    @app.post("/api/server/restart")
    async def server_restart() -> dict:
        """Re-exec the server process in place (picks up code/config changes).
        Interrupts running jobs; managed ComfyUI keeps running. The response is
        sent first, then the process replaces itself."""
        import asyncio
        import os
        import sys

        async def _restart():
            await asyncio.sleep(0.4)  # let this response flush
            os.execv(sys.executable, [sys.executable, "-m", "loom.cli", *sys.argv[1:]])

        asyncio.create_task(_restart())
        return {"ok": True, "restarting": True}

    @app.get("/api/comfy/models")
    def comfy_models():
        """Signature-classified index of the entire ComfyUI model tree: every
        file tagged with kind (checkpoint/diffusion/vae/clip/lora/…) and arch
        (sdxl/sd15/flux/dit/…) read from its tensor header, not its folder."""
        from ..comfy.scan import scan_models
        bd = _comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "items": []}, status_code=404)
        return scan_models(models_dir)

    @app.post("/api/workflow/check")
    def workflow_check(body: dict):
        """Given an API-format workflow, report which referenced models are
        installed vs missing, matching missing ones to the catalog for download."""
        from ..comfy.catalog import read_catalog
        from ..comfy.workflow_check import check_workflow
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "models dir not found", "refs": [], "missing": []}, status_code=404)
        graph = (body or {}).get("json") or {}
        cat = read_catalog(bd, md).get("entries", [])
        return check_workflow(graph, md, cat)

    # -- drop a model file onto a graph node: file it in the right folder --------
    def _model_kind_folders(kind: str) -> list[str]:
        if kind == "clip":
            return ["text_encoders", "clip"]
        if kind == "sam":
            return ["sams", "sam"]
        return {"checkpoint": ["checkpoints"], "diffusion": ["diffusion_models"], "vae": ["vae"],
                "lora": ["loras"], "controlnet": ["controlnet"], "upscale": ["upscale_models"],
                "ultralytics": ["ultralytics"], "style_models": ["style_models"],
                "ipadapter": ["ipadapter"], "gligen": ["gligen"]}.get(kind, [])

    @app.post("/api/comfy/models/resolve")
    def model_resolve(body: dict):
        """Is a model with this filename already installed for this kind? If so,
        return its folder-relative name (so we reference it instead of uploading)."""
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return {"found": False}
        base = os.path.basename(((body or {}).get("filename") or "").replace("\\", "/"))
        if not base:
            return {"found": False}
        for folder in _model_kind_folders((body or {}).get("kind", "")):
            d = md / folder
            if d.is_dir():
                for p in d.rglob(base):
                    if p.is_file():
                        # native separator so it matches ComfyUI's combo options
                        return {"found": True, "rel": str(p.relative_to(d))}
        return {"found": False}

    @app.post("/api/comfy/models/upload")
    async def model_upload(kind: str, file: UploadFile = File(...)):
        """Stream an uploaded model file into the correct folder for its kind."""
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        folders = _model_kind_folders(kind)
        if not folders:
            return JSONResponse({"error": f"unknown model kind '{kind}'"}, status_code=400)
        base = os.path.basename((file.filename or "").replace("\\", "/"))
        if not base or base.startswith("."):
            return JSONResponse({"error": "bad filename"}, status_code=400)
        folder, rel = folders[0], base
        if kind == "ultralytics":  # ComfyUI splits detectors into bbox/ and segm/
            sub = "segm" if "seg" in base.lower() else "bbox"
            folder, rel = f"ultralytics/{sub}", os.path.join(sub, base)  # native sep
        target = md / folder / base
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with open(tmp, "wb") as out:
                while chunk := await file.read(1 << 20):
                    out.write(chunk)
            tmp.replace(target)
        except Exception as exc:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"ok": True, "rel": rel, "name": base}

    @app.get("/api/comfy/catalog")
    def comfy_catalog():
        """Browse ComfyUI Manager's cached model catalog (URLs + install folders),
        each tagged with whether it's already installed."""
        from ..comfy.catalog import read_catalog
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not bd or not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI base not found", "entries": []}, status_code=404)
        return read_catalog(bd, md)

    @app.post("/api/comfy/catalog/install")
    async def comfy_catalog_install(body: dict):
        """Download a catalog model into its correct folder, streaming progress."""
        from fastapi.responses import StreamingResponse

        import httpx

        from ..comfy.catalog import install_target
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "models dir not found"}, status_code=404)
        url, rel = (body or {}).get("url"), (body or {}).get("rel")
        if not url or not rel:
            return JSONResponse({"error": "url and rel required"}, status_code=400)
        target = install_target(md, rel)
        if target is None:
            return JSONResponse({"error": "path escapes models dir"}, status_code=400)

        async def events():
            tmp = target.with_suffix(target.suffix + ".part")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                    async with client.stream("GET", url) as r:
                        if r.status_code != 200:
                            yield f"data: {json.dumps({'type': 'error', 'error': f'HTTP {r.status_code} from source'})}\n\n"
                            return
                        total = int(r.headers.get("content-length", 0))
                        done, last = 0, 0
                        with open(tmp, "wb") as f:
                            async for chunk in r.aiter_bytes(1 << 20):
                                f.write(chunk)
                                done += len(chunk)
                                if done - last >= (5 << 20):
                                    last = done
                                    yield f"data: {json.dumps({'type': 'progress', 'done': done, 'total': total})}\n\n"
                tmp.replace(target)
                yield f"data: {json.dumps({'type': 'done', 'rel': rel})}\n\n"
            except Exception as exc:  # noqa: BLE001
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/comfy/librarian")
    def librarian_plan():
        """Proposed moves to file misfiled / loose models into arch-correct
        folders (surgical — leaves arch-consistent folders alone)."""
        from ..comfy.librarian import plan_moves
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "moves": []}, status_code=404)
        # LoRA classifications drive the folder layout (<family>/<classification>/).
        classifications = {l.name.replace("\\", "/"): l.type for l in base_settings.loras.library}
        return plan_moves(md, classifications)

    @app.post("/api/comfy/librarian/apply")
    def librarian_apply(body: dict):
        """Execute the chosen moves and rewrite references in Loom configs
        (loras.yaml, character cards, workflows/), then reload settings."""
        nonlocal base_settings
        from ..comfy.librarian import apply_moves
        bd = _comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        res = apply_moves(md, root, (body or {}).get("moves") or [])
        if res.get("moved"):
            base_settings = load_settings(root)  # pick up rewritten lora/checkpoint names
        return res

    @app.get("/api/comfy/choices")
    def comfy_choices():
        """Available files/options from ComfyUI (for the LoRA/checkpoint/sampler
        pickers). Empty lists if ComfyUI is unreachable."""
        import httpx

        out = {"checkpoints": [], "loras": [], "vaes": [], "upscale_models": [],
               "samplers": [], "schedulers": []}
        try:
            r = httpx.get(_active_comfy_url().rstrip("/") + "/object_info", timeout=15)
            r.raise_for_status()
            info = r.json()
        except Exception:  # noqa: BLE001
            return out

        def opts(cls: str, key: str) -> list:
            req = info.get(cls, {}).get("input", {}).get("required", {})
            v = req.get(key)
            return v[0] if isinstance(v, list) and v and isinstance(v[0], list) else []

        out["checkpoints"] = opts("CheckpointLoaderSimple", "ckpt_name")
        out["loras"] = opts("LoraLoader", "lora_name")
        out["vaes"] = opts("VAELoader", "vae_name")
        out["upscale_models"] = opts("UpscaleModelLoader", "model_name")
        out["samplers"] = opts("KSampler", "sampler_name")
        out["schedulers"] = opts("KSampler", "scheduler")
        return out

    @app.get("/api/comfy/object_info")
    def comfy_object_info():
        """Slim node schema from ComfyUI — input names/types (with the multiline
        flag) and output names — so the graph editor can label slots and size
        widgets. Empty dict if ComfyUI is unreachable."""
        import httpx

        try:
            r = httpx.get(_active_comfy_url().rstrip("/") + "/object_info", timeout=20)
            r.raise_for_status()
            info = r.json()
        except Exception:  # noqa: BLE001
            return {}

        slim = {}
        for cls, spec in info.items():
            inp = spec.get("input", {}) or {}
            inputs = []
            for req_flag, group in (("required", inp.get("required", {}) or {}), ("optional", inp.get("optional", {}) or {})):
                for name, val in group.items():
                    t, multiline, widget, default, options = "COMBO", False, True, None, None
                    if isinstance(val, list) and val:
                        spec0 = val[0]
                        o = val[1] if len(val) > 1 and isinstance(val[1], dict) else {}
                        if isinstance(spec0, list):  # COMBO — a list of choices
                            t = "COMBO"
                            options = [x for x in spec0 if isinstance(x, (str, int, float, bool))][:2000]
                            default = o.get("default", options[0] if options else None)
                        elif isinstance(spec0, str):
                            t = spec0
                            if spec0 in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                                multiline = bool(o.get("multiline"))
                                default = o.get("default", "" if spec0 == "STRING" else (False if spec0 == "BOOLEAN" else 0))
                            else:
                                widget = False  # a connection slot, not a widget
                    inputs.append({"name": name, "type": t, "widget": widget, "multiline": multiline,
                                   "default": default, "options": options, "required": req_flag == "required"})
            out_types = spec.get("output", []) or []
            out_names = spec.get("output_name", []) or []
            outputs = []
            for i, ot in enumerate(out_types):
                nm = out_names[i] if i < len(out_names) and out_names[i] else (ot if isinstance(ot, str) else f"out{i}")
                outputs.append({"name": nm, "type": ot if isinstance(ot, str) else "COMBO"})
            slim[cls] = {"inputs": inputs, "outputs": outputs, "category": spec.get("category", "")}
        return slim

    # -- LoRA subsystem (typed library + named, routable stacks) -------------
    @app.get("/api/loras")
    def get_loras():
        """The whole LoRA subsystem config: { library, stacks }."""
        return base_settings.loras.model_dump()

    @app.post("/api/loras")
    def save_loras(body: dict):
        """Persist configs/loras.yaml (library + stacks), then reload."""
        nonlocal base_settings
        try:
            from ..config.schema import LoraConfig
            cfg = LoraConfig(**(body or {}))
            path = root / "configs" / "loras.yaml"
            path.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        base_settings = load_settings(root)
        return {"ok": True}

    @app.post("/api/loras/resolve")
    async def resolve_lora_stack(body: dict):
        """Resolve a stack (by `stack` name, or inline def) against `text` +
        optional `theme`. State LoRAs auto-route by matching the scene to their
        own embedded tags; detail/theme are constant. Returns the effective
        checkpoint + LoRA list plus the per-candidate routing scores."""
        from fastapi.concurrency import run_in_threadpool

        from ..comfy import tags as tagmod
        from ..comfy.stack import resolve_stack
        body = body or {}
        library = [l.model_dump() for l in base_settings.loras.library]
        stack = body.get("stack")
        if isinstance(stack, str):
            sd = next((s for s in base_settings.loras.stacks if s.name == stack), None)
            stack = sd.model_dump() if sd else {}
        elif not isinstance(stack, dict):
            stack = {}

        members = stack.get("loras", [])
        identity = [m for m in members if m.get("role") == "identity"]
        state_cands = [m for m in members if m.get("role") == "state"]

        loras_dir = str(_loras_out_dir(load_trainer()))
        thr = float(body.get("threshold", 0.25))
        try:
            routed = await run_in_threadpool(
                tagmod.route_state, root, loras_dir, body.get("text", ""), state_cands, thr)
        except Exception as exc:  # noqa: BLE001 — embedding/index failure shouldn't 500 the bench
            routed = [{"name": s.get("name"), "weight": s.get("weight", 0.7), "fired": False,
                       "score": 0.0, "matched": [], "why": f"router error: {exc}"} for s in state_cands]

        active = [r for r in routed if r.get("fired")]
        res = resolve_stack({"checkpoint": stack.get("checkpoint"), "identity": identity},
                            library, body.get("theme"), active)
        res["routed"] = routed
        return res

    @app.get("/api/loras/bases")
    def lora_bases():
        """Each image workflow with its base model (checkpoint or UNet) and that
        model's real architecture, classified from its tensor signature via the
        scan. Triage picks one of these; LoRAs are filtered to the matching arch."""
        from ..comfy.scan import classify
        bd = _comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        out = []
        for key, md in base_settings.models.items():
            if md.kind != "image":
                continue
            base_name, folder = None, None
            wf = md.options.get("workflow")
            try:
                p = root / wf if wf else None
                if p and p.is_file():
                    g = json.loads(p.read_text(encoding="utf-8"))
                    for node in g.values():
                        ct = node.get("class_type")
                        if ct == "CheckpointLoaderSimple":
                            base_name, folder = node.get("inputs", {}).get("ckpt_name"), "checkpoints"
                            break
                        if ct in ("UNETLoader", "UnetLoaderGGUF"):
                            base_name, folder = node.get("inputs", {}).get("unet_name"), "diffusion_models"
                            break
            except Exception:  # noqa: BLE001
                pass
            arch = "unknown"
            if base_name and models_dir and folder:
                fp = models_dir / folder / base_name.replace("\\", "/")
                if fp.is_file():
                    arch = classify(str(fp)).get("arch") or "unknown"
            out.append({"key": key, "base": base_name,
                        # only a real checkpoint can drive the minimal triage render
                        "checkpoint": base_name if folder == "checkpoints" else None,
                        "arch": arch})
        return out

    @app.get("/api/loras/clusters")
    async def lora_clusters():
        """Installed LoRAs as a similarity-ordered list (variants folded, nearest
        neighbours per entry), for the Network pane."""
        from fastapi.concurrency import run_in_threadpool

        from ..comfy import tags as tagmod
        loras_dir = str(_loras_out_dir(load_trainer()))
        try:
            return await run_in_threadpool(tagmod.similarity_list, root, loras_dir)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"entries": [], "error": str(exc)}, status_code=500)

    @app.post("/api/loras/tagify")
    def tagify_scene(body: dict):
        """Natural-language scene → comma-separated booru tags via the Prompt Gen
        LLM. This is the prose→tags step; state routing then matches these tags
        against each LoRA's own tags. (The LLM does the understanding; the router
        does the matching.)"""
        pg = load_promptgen()
        provider = text_provider_for((body or {}).get("model") or pg.get("model"))
        if provider is None:
            return JSONResponse({"error": "no text connection — set one up in Connection first"}, status_code=400)
        scene = (body or {}).get("scene", "").strip()
        if not scene:
            return JSONResponse({"error": "scene is empty"}, status_code=400)
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            "required": ["tags"], "additionalProperties": False,
        }
        instruction = (
            "Convert this scene into Danbooru / Stable-Diffusion tags: subject count, "
            "appearance, hair, clothing/outfit, setting, lighting, pose, expression. Use "
            f"canonical lowercase danbooru spellings. Scene: {scene}. Return the `tags` array."
        )
        try:
            res = provider.generate_text(system=pg.get("system"), prompt=instruction, emits=schema)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        # Flatten (models often cram comma-lists into single array items), strip
        # decoration, drop non-latin / symbol-only noise, de-dupe.
        tags, seen = [], set()
        for item in (res.data.get("tags") or []):
            if not isinstance(item, str):
                continue
            for piece in re.split(r"[,\n;]", item):
                t = piece.strip().strip("=-_ ").lower()
                if not t or len(t) > 60 or not re.search(r"[a-z0-9]", t):
                    continue
                if t not in seen:
                    seen.add(t)
                    tags.append(t)
        return {"tags": tags, "text": ", ".join(tags)}

    @app.post("/api/loras/triage-render")
    async def triage_render(body: dict):
        """Render ONE LoRA through a minimal txt2img graph with an explicit
        checkpoint — for classification triage. The caller passes a checkpoint
        matching the LoRA's architecture (its folder); a minimal graph avoids the
        heavy production workflow's custom-node fragility."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ..comfy.generate import stream_generate

        body = body or {}
        ckpt, lora = body.get("checkpoint"), body.get("lora")
        if not ckpt or not lora:
            return JSONResponse({"error": "checkpoint and lora are required"}, status_code=400)
        prompt = body.get("prompt") or "1girl, solo, standing, simple background, looking at viewer"
        neg = body.get("negative") or "lowres, worst quality, bad anatomy, text, watermark"
        w = float(body.get("weight", 0.8))
        steps = int(body.get("steps", 22))
        base = _active_comfy_url()
        graph = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "10": {"class_type": "LoraLoader", "inputs": {"lora_name": lora, "strength_model": w, "strength_clip": w, "model": ["4", 0], "clip": ["4", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["10", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["10", 1]}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "3": {"class_type": "KSampler", "inputs": {"seed": int(body.get("seed", 0)), "steps": steps, "cfg": 6.0, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0, "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "triage", "images": ["8", 0]}},
        }
        try:
            await run_in_threadpool(get_server(base).ensure_up)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        async def events():
            try:
                async for ev in stream_generate(base, graph, "9", 240):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.delete("/api/loras/file")
    def delete_lora_file(name: str):
        """Delete a LoRA file from the loras directory (for pruning redundant /
        overlapping LoRAs). Path-checked to stay inside the loras dir."""
        try:
            loras_dir = _loras_out_dir(load_trainer()).resolve()
            target = (loras_dir / name.replace("\\", "/")).resolve()
            if target != loras_dir and loras_dir not in target.parents:
                return JSONResponse({"error": "path outside loras dir"}, status_code=400)
            if not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            target.unlink()
            return {"ok": True, "deleted": name}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/lora/sample")
    async def lora_sample(body: dict):
        """Generate a sample by hooking a checkpoint + LoRA stack into a real
        image workflow (default: the illustrious graph) — its checkpoint and LoRA
        chain are swapped for the tested set, the prompt is injected at the
        workflow's configured positive node, and it's rendered from its output
        node. Same inject-models mechanism a pipeline uses to apply a character's
        portrait preset; the sample just drives it on demand."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ..comfy.generate import stream_generate
        from ..comfy.stack import inject_models
        from ..providers.comfyui_provider import ComfyUIProvider

        body = body or {}
        checkpoint = body.get("checkpoint")
        loras = body.get("loras")
        if loras is None and body.get("lora"):  # single-lora shorthand
            loras = [{"name": body["lora"], "weight": float(body.get("weight", 1.0))}]
        loras = [l for l in (loras or []) if l.get("name")]
        model_key = body.get("model") or "illustrious"
        prompt = body.get("prompt") or "masterpiece, best quality, 1girl, portrait, detailed"
        negative = body.get("negative")

        md = base_settings.models.get(model_key)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image workflow '{model_key}'"}, status_code=404)
        opts = dict(md.options)
        conn = store.active("image")
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url

        try:
            provider = ComfyUIProvider(opts)
            provider.workflow = inject_models(provider.workflow, checkpoint, loras)
            await run_in_threadpool(get_server(provider.base_url).ensure_up)
            graph = provider._inject(prompt, negative)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        async def events():
            try:
                async for ev in stream_generate(provider.base_url, graph, provider.output_node, provider.timeout_s):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/comfy/embeddings")
    def comfy_embeddings():
        """List textual-inversion embeddings (for the text-encode node's picker).
        Scans the ComfyUI models/embeddings folder directly — ComfyUI's own
        /embeddings route is hijacked to an HTML page by the lora-manager node."""
        base = _comfy_base_dir()
        if base is None:
            return []
        d = base / "models" / "embeddings"
        if not d.is_dir():
            return []
        exts = {".safetensors", ".pt", ".bin", ".ckpt"}
        return sorted(p.stem for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)

    @app.post("/api/comfy/interrupt")
    async def comfy_interrupt():
        """Stop ComfyUI's current execution (used to cancel a test render)."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(_active_comfy_url().rstrip("/") + "/interrupt")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/comfy/up")
    def comfy_up():
        server = get_server(comfy_url)
        try:
            server.ensure_up()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        return {"ok": True, "up": server.is_up(), "launched": server.we_launched_it}

    @app.post("/api/run")
    def run(body: RunRequest):
        settings = effective_settings()
        if body.pipeline not in settings.pipelines:
            return JSONResponse({"error": f"unknown pipeline '{body.pipeline}'"}, status_code=400)

        chat_model = body.chat_model or store.active_id("text")
        image_conn = store.active("image")
        image_model = _role_model("chat", body.image_model)

        # If the active image connection points at a different ComfyUI (e.g. a
        # RunPod URL), override that image model's base_url for this run.
        if image_conn and image_conn.base_url and image_model in settings.models:
            md = settings.models[image_model].model_copy()
            md.options = {**md.options, "base_url": image_conn.base_url}
            settings.models[image_model] = md

        # Image-prompt generator: its own connection + system prompt. The model
        # comes from the dedicated image_prompt connection; it falls back to the
        # chat connection (then the chat model) when none is configured.
        promptgen = load_promptgen()
        ip_conn = store.active("image_prompt") or store.active("text")
        ip_model = (ip_conn.model if ip_conn else None) or promptgen.get("model") or chat_model
        promptgen = {**promptgen, "model": ip_model}

        # Register the image-prompt model id (run through its own connection's
        # credentials) if it isn't already a registered model key.
        if ip_model and ip_model not in settings.models and ip_conn:
            settings.models[ip_model] = ModelDef(
                provider=ip_conn.provider, kind="text",
                options={"model": ip_model, "api_key": ip_conn.api_key, "base_url": ip_conn.base_url},
            )

        # Resolve the focal character: explicit wins, else the scenario's primary
        # cast member — so the reference image / portraits track the right person.
        focal_char = body.character
        if focal_char is None and body.scenario:
            scen = settings.scenarios.get(body.scenario)
            if scen and scen.cast:
                primary = next((m for m in scen.cast if m.primary), scen.cast[0])
                focal_char = primary.character

        # img2img: seed image steps from the character's reference image. Only the
        # img2img workflow (one with a LoadImage node) uses it; others ignore it.
        init_image = None
        if body.use_reference and focal_char:
            ref = _reference_path(focal_char)
            if ref is not None:
                init_image = ref.read_bytes()

        try:
            result = Runner(settings).run(
                body.pipeline, user_message=body.message, character=body.character,
                scenario=body.scenario,
                chat_model=chat_model, image_model=image_model, promptgen=promptgen,
                chat_system=load_chatgen().get("system"), init_image=init_image,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        images = [f"data:image/png;base64,{base64.b64encode(b).decode()}" for b in result.images]
        return {"reply": result.text, "images": images}

    # Serve the built Svelte SPA at "/" if present (prod); otherwise fall back to
    # the bundled single-file page so the server works with no Node build.
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    build_dir = root / "frontend" / "build"
    if build_dir.is_dir():
        class SPAStaticFiles(StaticFiles):
            """Serve real files, but fall back to index.html for unknown paths so
            client-side routes (e.g. /images/lora) survive a refresh/deep-link."""
            async def get_response(self, path, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/", SPAStaticFiles(directory=str(build_dir), html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return INDEX_HTML

    # Clean up any generated characters left storyless by past deletions (e.g. a story removed
    # before this cascade existed) so the library doesn't accumulate orphans.
    try:
        _prune_orphan_characters()
    except Exception:  # noqa: BLE001 — never block startup on cleanup
        pass

    return app


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Loom</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 system-ui,sans-serif; background:#14161a; color:#e6e8ec; }
  header { padding:12px 18px; background:#1b1e24; border-bottom:1px solid #2a2e36;
           display:flex; align-items:center; gap:14px; }
  header h1 { font-size:18px; margin:0; letter-spacing:.5px; }
  .badge { font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid #2a2e36; }
  .badge.up { background:#14331f; color:#7ee0a0; } .badge.down { background:#371a1a; color:#f0a0a0; }
  button { font:inherit; background:#2a6df0; color:#fff; border:0; padding:7px 12px; border-radius:7px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  button.ghost { background:#232730; color:#cfd3da; border:1px solid #2a2e36; }
  button.sm { padding:3px 8px; font-size:12px; }
  main { display:grid; grid-template-columns:320px 1fr; height:calc(100vh - 51px); }
  aside { border-right:1px solid #2a2e36; background:#161920; display:flex; flex-direction:column; min-height:0; }
  .tabs { display:flex; border-bottom:1px solid #2a2e36; }
  .tabs button { flex:1; background:none; color:#8b93a1; border:0; border-bottom:2px solid transparent;
                 border-radius:0; padding:11px 0; }
  .tabs button.active { color:#e6e8ec; border-bottom-color:#2a6df0; }
  .panel { padding:16px; overflow:auto; display:none; } .panel.active { display:block; }
  label { display:block; font-size:12px; color:#8b93a1; margin:10px 0 4px; }
  input, select { width:100%; font:inherit; background:#232730; color:#e6e8ec;
                  border:1px solid #2a2e36; border-radius:7px; padding:8px; }
  .row { display:flex; gap:8px; align-items:center; }
  .status { font-size:13px; margin:10px 0; min-height:18px; }
  .ok { color:#7ee0a0; } .err { color:#f0a0a0; }
  .item { padding:6px 8px; border-radius:7px; cursor:pointer; display:flex; align-items:center; gap:8px; }
  .item:hover { background:#1f2430; } .item.sel { background:#22304a; }
  .item .kind { font-size:11px; color:#8b93a1; margin-left:auto; }
  .chat { display:flex; flex-direction:column; min-height:0; }
  .controls { padding:10px 16px; border-bottom:1px solid #2a2e36; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  .controls .lbl { font-size:12px; color:#8b93a1; } .controls b { color:#cfd3da; font-weight:600; }
  .controls select { width:auto; }
  .log { flex:1; overflow:auto; padding:18px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:720px; } .msg.user { align-self:flex-end; }
  .bubble { padding:10px 14px; border-radius:12px; white-space:pre-wrap; }
  .msg.user .bubble { background:#2a6df0; color:#fff; } .msg.bot .bubble { background:#232730; }
  .msg img { max-width:360px; border-radius:10px; margin-top:8px; display:block; }
  .composer { display:flex; gap:10px; padding:14px 16px; border-top:1px solid #2a2e36; }
  textarea { flex:1; font:inherit; background:#232730; color:#e6e8ec; border:1px solid #2a2e36;
             border-radius:9px; padding:10px; resize:none; height:52px; }
  .hint { font-size:12px; color:#8b93a1; }
</style>
</head>
<body>
<header>
  <h1>Loom</h1>
  <span id="comfyBadge" class="badge down">ComfyUI: …</span>
  <span id="who" style="margin-left:auto;color:#8b93a1;font-size:13px"></span>
</header>
<main>
  <aside>
    <div class="tabs">
      <button data-tab="conn" class="active">Connection</button>
      <button data-tab="chars">Characters</button>
      <button data-tab="image">Image</button>
    </div>

    <div id="tab-conn" class="panel active">
      <label>Provider</label>
      <select id="provider"></select>
      <div id="baseWrap"><label>Base URL</label><input id="baseUrl"/></div>
      <label>API key</label>
      <input id="apiKey" type="password" placeholder="paste your key"/>
      <div class="row" style="margin-top:10px">
        <button id="connectBtn">Connect</button>
        <button id="saveBtn" class="ghost" disabled>Save</button>
      </div>
      <div id="connStatus" class="status"></div>
      <label>Model</label>
      <select id="modelSel"><option value="">— connect first —</option></select>
      <label style="margin-top:16px">Saved connections</label>
      <div id="connList"></div>
    </div>

    <div id="tab-chars" class="panel">
      <div class="hint">Pick the persona the chat model plays.</div>
      <div id="charList" style="margin-top:8px"></div>
    </div>

    <div id="tab-image" class="panel">
      <div class="row" style="justify-content:space-between">
        <span id="comfyBadge2" class="badge down">ComfyUI: …</span>
        <button id="comfyBtn" class="ghost sm">Start ComfyUI</button>
      </div>
      <label style="margin-top:14px">Image model (pipeline)</label>
      <select id="imageSel"></select>
      <div id="imageInfo" class="hint" style="margin-top:8px"></div>
    </div>
  </aside>

  <div class="chat">
    <div class="controls">
      <span><span class="lbl">Pipeline</span> <select id="pipeline"></select></span>
      <span><span class="lbl">Chat:</span> <b id="curChat">—</b></span>
      <span><span class="lbl">Character:</span> <b id="curChar">none</b></span>
      <span><span class="lbl">Image:</span> <b id="curImage">—</b></span>
    </div>
    <div id="logEl" class="log"></div>
    <div class="composer">
      <textarea id="input" placeholder="Message…  (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send">Send</button>
    </div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
const state = { providers: [], activeChar: '', activeImage: '', activeConn: null, activeModel: null };
function esc(s){ return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function add(role, html){ const m=document.createElement('div'); m.className='msg '+role;
  m.innerHTML='<div class="bubble">'+html+'</div>'; $('#logEl').appendChild(m); $('#logEl').scrollTop=1e9; return m; }

// tabs
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); $('#tab-'+b.dataset.tab).classList.add('active');
});

async function loadProviders(){
  state.providers = await (await fetch('/api/providers')).json();
  $('#provider').innerHTML = state.providers.map(p=>`<option value="${p.id}">${p.label}</option>`).join('');
  syncProvider();
}
function syncProvider(){
  const p = state.providers.find(x=>x.id===$('#provider').value);
  if (!p) return;
  $('#baseUrl').value = p.default_base_url;
  $('#baseWrap').style.display = p.base_url_editable ? '' : 'none';
}
$('#provider').onchange = syncProvider;

$('#connectBtn').onclick = async () => {
  const st = $('#connStatus'); st.textContent='Connecting…'; st.className='status';
  $('#connectBtn').disabled=true;
  try{
    const r = await fetch('/api/connections/test',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({provider:$('#provider').value, api_key:$('#apiKey').value, base_url:$('#baseUrl').value})});
    const d = await r.json();
    if(!d.ok){ st.textContent='✗ '+d.error; st.className='status err'; return; }
    st.textContent='✓ Connected — '+d.count+' models'; st.className='status ok';
    $('#modelSel').innerHTML = d.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
    $('#saveBtn').disabled=false;
  }catch(e){ st.textContent='✗ '+e; st.className='status err'; }
  finally{ $('#connectBtn').disabled=false; }
};

$('#saveBtn').onclick = async () => {
  await fetch('/api/connections',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({provider:$('#provider').value, api_key:$('#apiKey').value,
      base_url:$('#baseUrl').value, model:$('#modelSel').value})});
  $('#apiKey').value='';
  await refresh(); await loadConnections();
};

async function loadConnections(){
  const d = await (await fetch('/api/connections')).json();
  $('#connList').innerHTML = d.connections.map(c=>{
    const act = c.id===d.active ? 'sel':'';
    return `<div class="item ${act}" data-act="${c.id}">${c.id}
      <span class="kind">${esc(c.model||'')} ${c.has_key?'🔑':''}</span>
      <button class="ghost sm" data-del="${c.id}">✕</button></div>`;
  }).join('') || '<div class="hint">none yet</div>';
  $('#connList').querySelectorAll('[data-act]').forEach(el=>el.onclick=async ev=>{
    if(ev.target.dataset.del) return;
    await fetch('/api/connections/'+el.dataset.act+'/activate',{method:'POST'}); refresh(); loadConnections();
  });
  $('#connList').querySelectorAll('[data-del]').forEach(el=>el.onclick=async ev=>{
    ev.stopPropagation();
    await fetch('/api/connections/'+el.dataset.del,{method:'DELETE'}); refresh(); loadConnections();
  });
}

async function loadModels(){
  const d = await (await fetch('/api/models')).json();
  $('#imageSel').innerHTML = d.image.map(m=>`<option value="${m.key}">${m.key} (${m.provider})</option>`).join('');
  if(!state.activeImage && d.image[0]) state.activeImage = d.image[0].key;
  if(state.activeImage) $('#imageSel').value = state.activeImage;
  const im = d.image.find(m=>m.key===state.activeImage);
  $('#imageInfo').textContent = im ? `provider: ${im.provider}` : '';
  $('#curImage').textContent = state.activeImage || '—';
}
$('#imageSel').onchange = () => { state.activeImage=$('#imageSel').value; loadModels(); };

async function refresh(){
  const h = await (await fetch('/api/health')).json();
  for(const id of ['#comfyBadge','#comfyBadge2']){ const b=$(id);
    b.textContent='ComfyUI: '+(h.comfyui.up?'up':'down'); b.className='badge '+(h.comfyui.up?'up':'down'); }
  $('#comfyBtn').style.display = h.comfyui.up?'none':'';
  $('#who').textContent = h.profile?.name ? ('You: '+h.profile.name) : '';
  state.activeConn = h.active_connection; state.activeModel = h.active_model;
  $('#curChat').textContent = h.active_model || h.active_connection || '(none — connect)';
  $('#pipeline').innerHTML = h.pipelines.map(p=>`<option>${p}</option>`).join('');
  if(h.defaults?.pipeline) $('#pipeline').value = h.defaults.pipeline;
  if(!state.activeChar && h.defaults?.character) state.activeChar = h.defaults.character;
  $('#curChar').textContent = state.activeChar || 'none';
  $('#charList').innerHTML = ['<div class="item '+(state.activeChar?'':'sel')+'" data-char="">(none)</div>']
    .concat(h.characters.map(c=>`<div class="item ${c===state.activeChar?'sel':''}" data-char="${c}">${c}</div>`)).join('');
  $('#charList').querySelectorAll('[data-char]').forEach(el=>el.onclick=()=>{
    state.activeChar = el.dataset.char; $('#curChar').textContent = state.activeChar||'none'; refresh(); });
}

$('#comfyBtn').onclick = async () => {
  $('#comfyBtn').disabled=true; $('#comfyBtn').textContent='Starting…';
  try{ const d=await (await fetch('/api/comfy/up',{method:'POST'})).json();
    if(!d.ok) add('bot','<span class="err">ComfyUI failed: '+esc(d.error)+'</span>'); }
  finally{ $('#comfyBtn').disabled=false; $('#comfyBtn').textContent='Start ComfyUI'; refresh(); }
};

async function send(){
  const text=$('#input').value.trim(); if(!text) return;
  $('#input').value=''; add('user',esc(text)); const t=add('bot','…');
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({pipeline:$('#pipeline').value, character:state.activeChar||null,
        chat_model: state.activeConn||null, image_model: state.activeImage||null, message:text})});
    const d=await r.json();
    if(d.error){ t.querySelector('.bubble').innerHTML='<span class="err">'+esc(d.error)+'</span>'; }
    else{ let html=esc(d.reply||''); (d.images||[]).forEach(s=>html+='<img src="'+s+'"/>');
      t.querySelector('.bubble').innerHTML = html||'<span class="hint">(no reply)</span>'; }
  }catch(e){ t.querySelector('.bubble').innerHTML='<span class="err">'+esc(''+e)+'</span>'; }
  $('#logEl').scrollTop=1e9;
}
$('#send').onclick=send;
$('#input').addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); send(); }});

loadProviders(); loadConnections(); loadModels(); refresh(); setInterval(refresh, 6000);
</script>
</body>
</html>
"""
