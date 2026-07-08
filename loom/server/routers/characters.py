from __future__ import annotations

import base64
import re

import yaml
from fastapi import UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ...cards import extract_card_json, to_character
from ...card_sources import fetch_card
from ..services import config_files
from ..services.emotions import EMOTION_KEYS, EMOTION_LABELS
from ..services.images import _clean_reference_png, _randomize_seeds, _render
from ..services.batch_images import render_batch
from ..services.jobs_util import _start_stream_job
from ..services.prompts import (
    _DESCRIBE_SYSTEM,
    _EXPRESSION_SYSTEM,
    _OUTFIT_SYSTEM,
    _PORTRAIT_FRAMING,
    _base_prompt,
    _gen_text,
    _persona_text,
    _regionize_prompt,
    _safe_image_tags,
    _snap_prompt,
)


class CharacterImportRequest(BaseModel):
    data_b64: str            # PNG or JSON card bytes, base64-encoded
    filename: str = ""


def register(app, ctx):
    @app.get("/api/poses")
    def get_poses() -> dict:
        """Global shot GEOMETRY per emotion (+ neutral): camera framing (cowboy 3/4 vs full body) and
        latent aspect. Body language itself is generated per character (not global), so this is geometry
        only. Returns each emotion's effective framing/aspect + whether it overrides the default."""
        from ..services.poses import FRAMING_TAGS, ASPECT_DIMS, geometry_default, resolve_geometry
        ov = config_files.load_poses(ctx.root)
        labels = EMOTION_LABELS
        keys = EMOTION_KEYS  # neutral is now a first-class key in the taxonomy
        def row(k):
            g = resolve_geometry(k, ov)
            return {"key": k, "label": labels.get(k, k), "framing": g["framing"], "aspect": g["aspect"],
                    "custom": g != geometry_default(k)}
        return {"poses": [row(k) for k in keys],
                "framings": list(FRAMING_TAGS.keys()), "aspects": list(ASPECT_DIMS.keys())}

    @app.get("/api/pose-library")
    def get_pose_library() -> dict:
        """The curated body-language pose palette generated poses pick from — real Danbooru pose
        tags grouped by body facet. Returns {facets:[{key,desc,tags}], count}."""
        lib = ctx.load_pose_library()
        facets = [{"key": k, "desc": (v or {}).get("desc", ""), "tags": list((v or {}).get("tags") or [])}
                  for k, v in lib.items() if isinstance(v, dict) and v.get("tags")]
        return {"facets": facets, "count": sum(len(f["tags"]) for f in facets)}

    @app.post("/api/poses")
    def set_poses(body: dict):
        """Save shot-geometry overrides. Accepts one {key, framing?, aspect?} or {config:{key:entry}}.
        An entry equal to the built-in geometry default is dropped. Read fresh per render — no restart."""
        from ..services.poses import FRAMING_TAGS, ASPECT_DIMS, geometry_default
        body = body or {}
        updates = body.get("config")
        if updates is None and body.get("key"):
            updates = {body["key"]: {kk: body[kk] for kk in ("framing", "aspect") if kk in body}}
        if not isinstance(updates, dict):
            return JSONResponse({"error": "expected {key,framing,aspect} or {config:{key:entry}}"}, status_code=400)
        valid = set(EMOTION_KEYS)  # neutral is now included in EMOTION_KEYS
        ov = config_files.load_poses(ctx.root)
        for k, v in updates.items():
            if k not in valid:
                return JSONResponse({"error": f"unknown emotion '{k}'"}, status_code=400)
            entry = {}
            if (v or {}).get("framing") in FRAMING_TAGS:
                entry["framing"] = v["framing"]
            if (v or {}).get("aspect") in ASPECT_DIMS:
                entry["aspect"] = v["aspect"]
            # keep only the fields that differ from the geometry default
            d = geometry_default(k)
            entry = {kk: vv for kk, vv in entry.items() if vv != d.get(kk)}
            if entry:
                ov[k] = entry
            else:
                ov.pop(k, None)
        config_files.save_poses(ctx.root, ov)
        return {"ok": True}

    @app.get("/api/characters/{key}/poses")
    def get_character_poses(key: str):
        """A character's per-emotion body-language tags (composed from persona, stored on the manifest).
        Empty until composed (regenerate / wardrobe). Editable as prose → tags."""
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        pp = ctx.portrait_manifest(key).get("pose_prompts") or {}
        return {"poses": [{"key": k, "label": EMOTION_LABELS[k], "tags": (pp.get(k) or "")} for k in EMOTION_KEYS]}

    @app.post("/api/characters/{key}/poses")
    def set_character_pose(key: str, body: dict):
        """Edit ONE emotion's body-language for a character. `tags` is snapped to booru tags (prose ok);
        empty clears it. Or {compose:true} (re)generates the whole set from the persona."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        m = ctx.portrait_manifest(key)
        pp = dict(m.get("pose_prompts") or {})
        if body.get("compose"):
            ctx.ensure_fleshed(key)          # thin seed → disciplined prose first
            c = ctx.base_settings.characters.get(key)
            from loom.stories.pipeline import compose_poses as _compose_poses
            from ..services import config_files as _cfiles
            _w_cfg = ctx.load_story_builder()
            _w_prov = ctx.stage_provider("wardrobe")
            pp = _compose_poses(_w_prov, _persona_text(c), ctx.load_pose_library())
        else:
            emo = (body.get("emotion") or "").strip()
            if emo not in EMOTION_KEYS:
                return JSONResponse({"error": f"unknown emotion '{emo}'"}, status_code=400)
            tags = _snap_prompt(_safe_image_tags((body.get("tags") or "").strip()))
            if tags:
                pp[emo] = tags
            else:
                pp.pop(emo, None)
        m["pose_prompts"] = pp
        ctx.save_portrait_manifest(key, m)
        return {"ok": True, "poses": {k: pp.get(k, "") for k in EMOTION_KEYS}}

    @app.post("/api/characters/{key}/generate-all")
    def generate_all(key: str, body: dict):
        """Headless end-to-end: flesh → base prompt → base image → one outfit → expressions + poses →
        full emotion sprite set, all saved to disk. Streamed job; same orchestrator the CLI uses."""
        from ..services.full_gen import generate_full_character
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        ch = ctx.base_settings.characters[key]
        job = _start_stream_job("character", "Generate full character", ch.name,
                                f"characters/{key}",
                                lambda emit, cancelled: generate_full_character(ctx, key, emit, cancelled))
        return {"job": job.id}

    @app.post("/api/characters/{key}/flesh")
    async def flesh_character_route(key: str, body: dict):
        """Flesh a thin character seed into a thorough, disciplined-prose sheet (rewrites the persona in
        place) — the front door for thin→rich: appearance/pose/expression all parse from this prose."""
        from fastapi.concurrency import run_in_threadpool
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        out = await run_in_threadpool(ctx.flesh_character, key, (body or {}).get("instruction", ""))
        if "error" in out:
            return JSONResponse({"error": out["error"]}, status_code=500)
        return out

    # An IMPORTED card (SillyTavern/Chub) is a different kind of thing from a character we
    # author in the pipeline — external reference material. New imports carry fields.imported;
    # pre-existing ones are detected by their raw card-field signature (we never set these).
    _IMPORT_SIG = {"description", "personality", "scenario", "first_mes", "mes_example",
                   "character_book", "spec", "spec_version", "character_version", "extensions"}

    def _is_imported(c) -> bool:
        f = c.fields or {}
        return bool(f.get("imported")) or any(k in f for k in _IMPORT_SIG)

    @app.get("/api/characters")
    def characters() -> list:
        char_dir = ctx.char_dir()
        # Which story owns each GENERATED character — from the explicit `story` tag,
        # else derived from story cast membership (so pre-tag NPCs still group).
        owner: dict[str, str] = {}
        for sk, st in ctx.base_settings.stories.items():
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
                "playable": bool(getattr(c, "playable", False)),
                "imported": _is_imported(c),
                "home_scenes": [s.model_dump() for s in getattr(c, "home_scenes", []) or []],
                "image": c.image.model_dump(),
                "avatar": f"/api/characters/{k}/avatar" if (ctx.char_asset_dir(k) / f"{k}.png").is_file() else None,
                "reference": f"/api/characters/{k}/reference" if ctx.reference_path(k) else None,
                "images": ctx.character_images(k, c),
                # Story this character is attached to (generated NPCs); None = library.
                "story": story_of(k, c),
                "generated": bool((c.fields or {}).get("_generated")),
            }
            for k, c in ctx.base_settings.characters.items()
        ]

    @app.post("/api/characters/{key}/image")
    def set_character_image(key: str, body: dict):
        """Persist a character's portrait preset (checkpoint + base LoRAs + an
        optional named routing stack) into its YAML, then reload settings."""
        data = ctx._read_character_data(key)   # owning story DB (embedded) or global YAML
        if data is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        try:
            loras = [{"name": l["name"], "weight": float(l.get("weight", 1.0))}
                     for l in (body.get("loras") or []) if l.get("name")]
            data["image"] = {"checkpoint": (body.get("checkpoint") or None),
                             "loras": loras, "stack": (body.get("stack") or None)}
            ctx._write_character_data(key, data)   # validates + routes + reloads
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True}

    @app.get("/api/characters/{key}/avatar")
    def character_avatar(key: str):
        """Serve the imported card PNG as the character's avatar (404 if none)."""
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.char_asset_dir(key) / f"{safe}.png"
        if not path.is_file():
            return JSONResponse({"error": "no avatar"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/characters/{key}/reference")
    def character_reference(key: str):
        """Serve the character's reference image (dedicated ref, else avatar)."""
        path = ctx.reference_path(key)
        if path is None:
            return JSONResponse({"error": "no reference"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/characters/{key}/reference")
    async def set_character_reference(key: str, file: UploadFile = File(...)):
        """Upload/replace a dedicated reference image (<key>.ref.png) for img2img."""
        safe = re.sub(r"[^\w\-]+", "", key)
        if key not in ctx.base_settings.characters:   # embedded (DB) or global — not a YAML-file check
            return JSONResponse({"error": "no such character"}, status_code=404)
        data = await file.read()
        # normalize whatever was uploaded to PNG via Pillow if available; else store raw
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001 — Pillow missing or odd format; store as-is
            pass
        d = ctx.char_asset_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.delete("/api/characters/{key}/reference")
    def clear_character_reference(key: str):
        """Drop the dedicated reference, falling back to the avatar."""
        safe = re.sub(r"[^\w\-]+", "", key)
        p = ctx.char_asset_dir(key) / f"{safe}.ref.png"
        if p.is_file():
            p.unlink()
        return {"ok": True}

    @app.get("/api/characters/{key}/portraits")
    def get_portraits(key: str):
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        return ctx.portrait_payload(key)

    @app.get("/api/characters/{key}/portraits/img/{oid}/{file}")
    def portrait_image(key: str, oid: str, file: str):
        if not re.fullmatch(r"[\w\-]+", oid) or not re.fullmatch(r"[\w\-]+\.png", file):
            return JSONResponse({"error": "bad path"}, status_code=404)
        p = (ctx.portrait_dir(key) / oid / file).resolve()
        if ctx.portrait_dir(key).resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    @app.post("/api/characters/{key}/normalize-identity")
    def normalize_identity(key: str, body: dict | None = None):
        """Backfill a CLEAN, clothing-free canonical `appearance` (fields.appearance) — the
        identity anchor every render leads with. Grounded on the character's best rendered base
        image via vision (falls back to the reference image, else rewrites the existing text to
        strip clothing). Fixes drift from an empty OR clothing-polluted appearance. → {appearance}."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        from ..services.prompts import _IDENTITY_SYSTEM
        # Source image: a rendered outfit base (grounded in what actually renders), else the reference.
        img_uri = None
        m = ctx.portrait_manifest(key)
        for o in (m.get("outfits") or []):
            f = ctx.portrait_dir(key) / str(o.get("id")) / "base.png"
            if o.get("base") and f.is_file():
                img_uri = "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode()
                break
        if img_uri is None:
            ref = ctx.reference_path(key)
            if ref is not None:
                img_uri = "data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()
        prov = ctx.ip_provider()
        cur = (c.fields or {}).get("appearance") or ""
        txt_prompt = (f"Persona:\n{_persona_text(c)}\n\nExisting appearance notes "
                      f"(rewrite as pure identity, strip ALL clothing/accessories):\n{cur or c.system or ''}")
        appearance = ""
        # Prefer vision (grounded on the base render); on ANY failure (model lacks image support,
        # etc.) fall back to a TEXT rewrite that strips clothing — always produces a clean anchor.
        if img_uri and prov is not None and hasattr(prov, "generate_text"):
            try:
                appearance = _gen_text(prov, _IDENTITY_SYSTEM,
                                       f"Persona:\n{_persona_text(c)}\n\nWrite this character's canonical identity.",
                                       images=[img_uri])
            except Exception:  # noqa: BLE001 — vision unsupported → text fallback below
                appearance = ""
        if not appearance:
            # A mechanical rewrite — use a fast non-thinking model, not the (possibly reasoning)
            # vision provider, so this stays quick.
            tprov = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"}) \
                or (prov if (prov is not None and hasattr(prov, "generate_text")) else None)
            if tprov is None or not hasattr(tprov, "generate_text"):
                return JSONResponse({"error": "no text/vision model available"}, status_code=400)
            try:
                appearance = _gen_text(tprov, _IDENTITY_SYSTEM, txt_prompt)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"identity extraction failed: {exc}"}, status_code=500)
        if not appearance or len(appearance) < 15:
            return JSONResponse({"error": "no usable identity produced"}, status_code=502)
        data = ctx._read_character_data(key)
        if data is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        data["fields"] = {**(data.get("fields") or {}), "appearance": appearance}
        ctx._write_character_data(key, data)   # validates + routes + reloads
        return {"appearance": appearance}

    @app.post("/api/prompt/mutate")
    def prompt_mutate(body: dict):
        """Rewrite an image prompt per a plain-English instruction — the Base Studio's mutate
        box, powered by DeepSeek V4 Pro (non-thinking; a prompt edit needs no reasoning chain).
        Body: { prompt, instruction } → { prompt }."""
        body = body or {}
        prompt = (body.get("prompt") or "").strip()
        instruction = (body.get("instruction") or "").strip()
        if not prompt or not instruction:
            return JSONResponse({"error": "need prompt + instruction"}, status_code=400)
        prov = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if prov is None or not hasattr(prov, "generate_text"):
            return JSONResponse({"error": "no text model available"}, status_code=400)
        system = ("You revise IMAGE-GENERATION prompts. Apply the INSTRUCTION to the PROMPT: "
                  "change exactly what it asks for, keep every other detail (identity, garments, "
                  "colours, pose) intact, keep the same prose format and similar length. "
                  "Output ONLY the revised prompt — no preamble, no quotes.")
        try:
            out = _gen_text(prov, system, f"PROMPT:\n{prompt}\n\nINSTRUCTION: {instruction}")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"mutate failed: {exc}"}, status_code=500)
        if not out:
            return JSONResponse({"error": "the model returned nothing"}, status_code=502)
        return {"prompt": out}

    @app.get("/api/style")
    def style_get():
        """The active BROADCAST art style ({} = the built-in default anchor)."""
        import json as _json
        f = ctx.root / "configs" / "image_style.json"
        try:
            return _json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        except (ValueError, OSError):
            return {}

    @app.post("/api/style")
    def style_broadcast(body: dict):
        """STYLE BROADCASTING: distill ONE chosen image into a reusable style card and make it
        the GLOBAL anchor every character render opens with (bases + sprites; sprites then lock
        to their base via img2img, so the whole cast converges on the chosen style as images are
        re-rendered). Body: { image: dataURI | /api/characters/.../portraits/img/... , source? }
        or { reset: true } to return to the built-in anchor. Returns {style}."""
        import json as _json
        from datetime import date
        body = body or {}
        f = ctx.root / "configs" / "image_style.json"
        if body.get("reset"):
            f.unlink(missing_ok=True)
            return {"style": "", "reset": True}
        img = (body.get("image") or "").strip()
        if img.startswith("data:image"):
            uri = img.split("?")[0]
        else:
            mm = re.match(r"^/api/characters/([\w\-]+)/portraits/img/([\w\-]+)/([\w\-]+\.png)", img)
            if not mm:
                return JSONResponse({"error": "give a data URI or a portrait image URL"}, status_code=400)
            p = ctx.portrait_dir(mm.group(1)) / mm.group(2) / mm.group(3)
            if not p.is_file():
                return JSONResponse({"error": "image not found on disk"}, status_code=404)
            uri = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        provider = ctx.ip_provider()
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect an image-prompt (vision) model first (⚙ Models)"}, status_code=400)
        from ..services.prompts import _STYLE_DISTILL_SYSTEM
        try:
            style = _gen_text(provider, _STYLE_DISTILL_SYSTEM,
                              "Write the reusable art-style specification for this image.",
                              images=[uri])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"style distillation failed: {exc}"}, status_code=500)
        if not style or len(style) < 40:
            return JSONResponse({"error": "the vision model returned no usable style"}, status_code=502)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_json.dumps({"style": style, "source": body.get("source") or img[:120],
                                  "saved": date.today().isoformat()}, indent=2, ensure_ascii=False),
                     encoding="utf-8")
        return {"style": style}

    @app.post("/api/characters/{key}/portraits/describe")
    def portrait_describe(key: str, body: dict | None = None):
        """Vision-caption the reference image into a canonical appearance prompt."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider = ctx.ip_provider()
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect an image-prompt model first (⚙ Models)"}, status_code=400)
        ref = ctx.reference_path(key)
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
        m = ctx.portrait_manifest(key)
        m["appearance"] = appearance
        ctx.save_portrait_manifest(key, m)
        return {"appearance": appearance}

    @app.put("/api/characters/{key}/portraits/appearance")
    def portrait_set_appearance(key: str, body: dict):
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = ctx.portrait_manifest(key)
        m["appearance"] = (body or {}).get("appearance", "")
        ctx.save_portrait_manifest(key, m)
        return {"ok": True}

    @app.post("/api/characters/{key}/portraits/outfit")
    def portrait_add_outfit(key: str, body: dict):
        """Create an outfit: rewrite the appearance prompt for the instruction
        (or use the appearance as-is for a blank/base outfit), render its base
        image through the active image model with the reference for identity."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        name = (body.get("name") or "Outfit").strip()
        instruction = (body.get("instruction") or "").strip()
        m = ctx.portrait_manifest(key)
        appearance = m.get("appearance", "")
        if not appearance:
            return JSONResponse({"error": "describe the appearance first"}, status_code=400)

        # Compose the outfit prompt.
        if instruction:
            ipp = ctx.ip_provider()
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

        provider, model_id = ctx.image_provider(body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        # Outfit base is a FULL-BODY whole-look image (not a bust). TXT2IMG by design: identity comes
        # from the appearance tags in the prompt, NOT img2img off the reference — seeding from the ref
        # made every outfit inherit the reference's POSE (the bending-over problem).
        render_prompt = f"{prompt}, neutral expression, {ctx.pose_tags(key, 'neutral')}, {ctx.pose_framing('neutral')}"
        try:
            from ...comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(prompt=render_prompt, latent=ctx.pose_latent('neutral'),
                                             out_prefix=ctx.output_prefix_for(model_id, "outfit", key))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if not result.images:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)

        oid = re.sub(r"[^\w\-]+", "-", name.lower()).strip("-") or "outfit"
        existing = {o["id"] for o in m["outfits"]}
        base_oid, n = oid, 2
        while oid in existing:
            oid, n = f"{base_oid}-{n}", n + 1
        (ctx.portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (ctx.portrait_dir(key) / oid / "base.png").write_bytes(result.images[0])
        outfit = {"id": oid, "name": name, "instruction": instruction, "prompt": prompt,
                  "base": "base.png", "expressions": {}}
        m["outfits"].append(outfit)
        ctx.save_portrait_manifest(key, m)
        return ctx.portrait_payload(key)

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/expression")
    def portrait_render_expression(key: str, oid: str, body: dict):
        """Render ONE emotion expression for an outfit (the frontend loops over
        the set for live progress). Persona + emotion -> expression tags, then
        img2img seeded from the outfit base so only the face changes."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        emotion = ((body or {}).get("emotion") or "").strip()
        if not emotion:
            return JSONResponse({"error": "emotion required"}, status_code=400)
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        base_png = ctx.portrait_dir(key) / oid / "base.png"
        if not base_png.is_file():
            return JSONResponse({"error": "outfit has no base image"}, status_code=400)

        ipp = ctx.ip_provider()
        if ipp is None:
            return JSONResponse({"error": "connect an image-prompt model first (⚙ Models)"}, status_code=400)
        try:
            expr_tags = _gen_text(
                ipp, _EXPRESSION_SYSTEM,
                f"Persona:\n{_persona_text(c)}\n\nEmotion: {emotion}\n\n"
                f"How does THIS character express '{emotion}'?")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"expression tags failed: {exc}"}, status_code=500)

        provider, model_id = ctx.image_provider((body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        render_prompt = f"{outfit.get('prompt','')}, {expr_tags}, {ctx.pose_tags(key, emotion, outfit_id=oid)}, {_PORTRAIT_FRAMING}"
        try:
            from ...comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(prompt=render_prompt, init_image=base_png.read_bytes(),
                                             out_prefix=ctx.output_prefix_for(model_id, "sprite", key))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if not result.images:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)

        emo_safe = re.sub(r"[^\w\-]+", "-", emotion.lower()).strip("-") or "emotion"
        (ctx.portrait_dir(key) / oid / f"{emo_safe}.png").write_bytes(result.images[0])
        outfit.setdefault("expressions", {})[emo_safe] = f"{emo_safe}.png"
        ctx.save_portrait_manifest(key, m)
        return {"emotion": emo_safe, "tags": expr_tags,
                "url": f"/api/characters/{key}/portraits/img/{oid}/{emo_safe}.png"}

    @app.delete("/api/characters/{key}/portraits/outfit/{oid}")
    def portrait_delete_outfit(key: str, oid: str):
        m = ctx.portrait_manifest(key)
        if ctx.portrait_outfit(m, oid) is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        m["outfits"] = [o for o in m["outfits"] if o.get("id") != oid]
        ctx.save_portrait_manifest(key, m)
        import shutil
        d = (ctx.portrait_dir(key) / oid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        return ctx.portrait_payload(key)

    @app.patch("/api/characters/{key}/portraits/outfit/{oid}")
    def portrait_patch_outfit(key: str, oid: str, body: dict):
        """Update an outfit's editable fields: name, instruction, attire_prompt.
        Returns the updated portrait payload."""
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        body = body or {}
        if "name" in body:
            outfit["name"] = (body["name"] or "").strip() or outfit.get("name", oid)
        if "instruction" in body:
            outfit["instruction"] = (body["instruction"] or "").strip()
        if "attire_prompt" in body:
            outfit["attire_prompt"] = (body["attire_prompt"] or "").strip()
            outfit["prompt"] = outfit["attire_prompt"]
        ctx.save_portrait_manifest(key, m)
        return ctx.portrait_payload(key)

    @app.delete("/api/characters/{key}/portraits/outfit/{oid}/expression/{emo}")
    def portrait_delete_expression(key: str, oid: str, emo: str):
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        (outfit.get("expressions") or {}).pop(emo, None)         # rendered image
        (outfit.get("expression_prompts") or {}).pop(emo, None)  # the expression definition
        ctx.save_portrait_manifest(key, m)
        f = (ctx.portrait_dir(key) / oid / f"{emo}.png")
        if f.is_file():
            f.unlink()
        return ctx.portrait_payload(key)

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/expression-prompt")
    def portrait_set_expression_prompt(key: str, oid: str, body: dict):
        """Add or edit ONE expression (emotion + optional face prompt) for THIS outfit only — each
        outfit carries its own expression range. Returns the portrait payload."""
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        body = body or {}
        emo = re.sub(r"[^\w\-]+", "-", (body.get("emotion") or "").lower()).strip("-")
        if not emo:
            return JSONResponse({"error": "emotion required"}, status_code=400)
        outfit.setdefault("expression_prompts", {})[emo] = (body.get("prompt") or "").strip()
        ctx.save_portrait_manifest(key, m)
        return ctx.portrait_payload(key)

    @app.post("/api/characters/{key}/portraits/affect")
    def portrait_compose_affect(key: str, body: dict):
        """Author or re-author the character's EMOTIONAL EXPRESSION RANGE — the set of emotion
        keys this character can display as portrait sprites. ONE cheap 'emotion'-stage LLM call.

        Stored as affect.range = [key1, key2, ...] (plain list of strings, circumplex-sorted).
        portrait_payload enriches this to [{emotion, label, valence, arousal}] before serving it.

        Body:
          {compose: true}  → (re)compose from the character's persona (default).
          {range: [...]}   → set the range explicitly as a list of key strings (or legacy dicts),
                             bypassing the model — for manual art-direction.
          {outfit_id: ...} → target ONE outfit: store the range on that outfit (register-specific,
                             composed with its attire in view) instead of the character default.
        Returns the full portrait payload."""
        c = ctx.base_settings.characters.get(key)
        if c is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        m = ctx.portrait_manifest(key)
        outfit = None
        if body.get("outfit_id"):
            outfit = next((o for o in m.get("outfits", []) if o.get("id") == body["outfit_id"]), None)
            if outfit is None:
                return JSONResponse({"error": "no such outfit"}, status_code=404)
        explicit = body.get("range") if isinstance(body.get("range"), list) else None
        if explicit:
            from ..services.emotions import EMOTION_KEYS
            import math
            from ..services.emotions import EMOTION_COORDS
            seen: set = set()
            cleaned = []
            for item in explicit:
                # Accept both string keys and legacy {emotion, ...} dicts.
                key_str = item if isinstance(item, str) else (item.get("emotion") if isinstance(item, dict) else None)
                if key_str in EMOTION_KEYS and key_str not in seen:
                    seen.add(key_str)
                    cleaned.append(key_str)
            cleaned.sort(key=lambda k: math.atan2(EMOTION_COORDS[k][1], EMOTION_COORDS[k][0]))
            if not cleaned:
                return JSONResponse({"error": "no valid emotion keys in range"}, status_code=400)
            affect = {"range": cleaned}
        else:
            ctx.ensure_fleshed(key)
            c = ctx.base_settings.characters.get(key)
            from ..services.prompts import _persona_text
            from loom.stories.pipeline import compose_affect_range as _compose_affect_range
            from ..services import config_files as _cfiles
            nsfw = bool(body.get("nsfw"))
            _emo_cfg = ctx.load_story_builder()
            _emo_prov = ctx.stage_provider("emotion", body.get("model"))
            attire = (outfit.get("attire_prompt") or outfit.get("prompt") or "") if outfit else ""
            # v4pro (non-thinking) is the pipeline's known-good structured-output model; the default
            # emotion-stage model can't do enum `emits`, so it silently fell back to the full set.
            _emo_prov = (ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
                         if not body.get("model") else _emo_prov)
            affect = _compose_affect_range(_emo_prov, _persona_text(c), nsfw=nsfw,
                                           systems=(_emo_cfg.get("systems") or {}), attire=attire)
            if not (isinstance(affect, dict) and affect.get("range")):
                return JSONResponse({"error": "could not compose affect range "
                                              "(emotion model may not support structured output)"},
                                    status_code=500)
        if outfit is not None:
            outfit["range"] = affect["range"]   # register-specific range lives on the outfit
        else:
            m["affect"] = affect                # character default range
        ctx.save_portrait_manifest(key, m)
        return ctx.portrait_payload(key)

    @app.post("/api/characters/{key}/card")
    def update_character_card(key: str, body: dict):
        """Edit the character card's text (name / persona / greeting / appearance
        + any extra fields), persist to its YAML, and reload settings."""
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        data = ctx._read_character_data(key)   # owning story DB or global library
        if data is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        try:
            if "name" in body:
                data["name"] = (body.get("name") or "").strip() or data.get("name") or key
            if "system" in body:
                data["system"] = body.get("system") or ""
            if "greeting" in body:
                data["greeting"] = body.get("greeting") or None
            if "playable" in body:
                data["playable"] = bool(body.get("playable"))
            if isinstance(body.get("home_scenes"), list):
                data["home_scenes"] = body["home_scenes"]
            if isinstance(body.get("fields"), dict):
                data["fields"] = {**(data.get("fields") or {}), **body["fields"]}
            ctx._write_character_data(key, data)   # validates + routes (DB or YAML) + reloads
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True}

    @app.post("/api/characters/create")
    def create_character(body: dict):
        """Create a brand-new character card from scratch (used by the persona wizard).
        Minimal input — name + optional persona/greeting/fields/playable — persisted as a
        fresh configs/characters/<key>.yaml via write_character (which derives a unique key
        and reloads settings). Returns {ok, key, name}."""
        body = body or {}
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        cdata = {
            "name": name,
            "system": body.get("system") or "",
            "greeting": body.get("greeting") or None,
            "fields": body.get("fields") if isinstance(body.get("fields"), dict) else {},
            "playable": bool(body.get("playable")),
        }
        try:
            return ctx.write_character(cdata, None)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not create: {exc}"}, status_code=400)

    @app.post("/api/characters/{key}/reference/from-url")
    async def set_reference_from_url(key: str, body: dict):
        """Download an image (e.g. a card-link URL) and store it as the character's
        reference / base image (<key>.ref.png)."""
        import httpx

        safe = re.sub(r"[^\w\-]+", "", key)
        if key not in ctx.base_settings.characters:   # embedded (DB) or global — not a YAML-file check
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
        d = ctx.char_asset_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.post("/api/characters/{key}/sprite-candidate")
    async def sprite_candidate(key: str, body: dict):
        """Render ONE sprite candidate for (outfit × emotion) from the wardrobe plan:
        prompt = the outfit's attire + the emotion's expression prompt, img2img from
        the character's base reference (IPAdapter identity). Fresh seed per call;
        returns a data URI (not saved)."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        oid, emotion = body.get("outfit_id"), body.get("emotion")
        m = ctx.portrait_manifest(key)
        outfit = next((o for o in m.get("outfits", []) if o.get("id") == oid), None)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        # The sprite workflow is txt2img (anima) — identity comes from the
        # prompt, so include the character's core appearance alongside attire+expression.
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        # Per-outfit expression prompt (legacy global as fallback for old data).
        expr = ((outfit.get("expression_prompts") or {}).get(emotion)
                or (m.get("expression_prompts") or {}).get(emotion) or emotion or "")
        # TXT2IMG at the FIXED sprite seed (44) — identity + outfit + style held constant in the
        # prompt keep the whole set in one latent region; only pose/face vary. The per-emotion
        # prompt is authored by v4pro (appearance + clothing + pose + facial expression), with the
        # deterministic `sprite_prompt` as fallback. A re-roll (body.reroll) uses a random seed so
        # it actually differs.
        from ..services.prompts import (sprite_prompt, compose_sprite_prompt,
                                         face_wildcard)
        from ..services.images import _set_seeds, SPRITE_SEED
        from ..services.emotions import EMOTION_LABELS, EMOTION_HINTS
        _style = ctx.art_style(char_key=key)   # L0: story art style → global anchor
        provider, model_id = ctx.role_image_provider("sprite", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        # Face-focused prompt for the FaceDetailer pass (only used by detailer workflows).
        _wc = face_wildcard(appearance, EMOTION_LABELS.get(emotion, emotion), emotion, expr)
        for _n in provider.workflow.values():
            if isinstance(_n, dict) and _n.get("class_type") == "FaceDetailer":
                _n.setdefault("inputs", {})["wildcard"] = _wc
        prompt = ""
        _v4 = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
        if _v4 is not None and hasattr(_v4, "generate_text"):
            try:
                prompt = compose_sprite_prompt(_v4, style=_style, appearance=appearance, attire=attire,
                                               emotion_label=EMOTION_LABELS.get(emotion, emotion),
                                               hint=(EMOTION_HINTS.get(emotion) or expr or emotion))
            except Exception:  # noqa: BLE001 — fall back to deterministic assembly
                prompt = ""
        if not prompt:
            prompt = sprite_prompt(appearance, attire, expr, ctx.pose_tags(key, emotion),
                                   ctx.pose_framing(emotion), emotion=emotion, style=_style)
        if body.get("reroll"):
            _randomize_seeds(provider.workflow)
        else:
            _set_seeds(provider.workflow, SPRITE_SEED)
        try:
            png = await _render(provider, prompt,
                                out_prefix=ctx.output_prefix_for(model_id, "sprite", key),
                                latent=ctx.pose_latent(emotion))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/characters/{key}/sprite-stack")
    async def sprite_stack(key: str, body: dict):
        """The IMAGE CARD, inspectable: every layer that stacks into one sprite's prompt, tagged
        with the tab that authored it (overview → cast), plus the final composed prompt and the
        face-detailer pass. Dry-run — nothing renders. Same inputs as sprite-candidate.
        Body: {outfit_id, emotion, compose?: false} — compose:false skips the LLM final."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        oid, emotion = body.get("outfit_id"), body.get("emotion") or "neutral"
        m = ctx.portrait_manifest(key)
        outfit = next((o for o in m.get("outfits", []) if o.get("id") == oid), None)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        from ..services.prompts import sprite_prompt, compose_sprite_prompt, face_wildcard
        from ..services.emotions import EMOTION_LABELS, EMOTION_HINTS
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        expr = ((outfit.get("expression_prompts") or {}).get(emotion)
                or (m.get("expression_prompts") or {}).get(emotion) or emotion or "")
        owner = ctx._char_owner(key)
        st = ctx.base_settings.stories.get(owner) if owner else None
        story_styled = bool(st and (st.art_style or "").strip())
        style = ctx.art_style(char_key=key)
        pose, framing = ctx.pose_tags(key, emotion, outfit_id=oid), ctx.pose_framing(emotion)
        label = EMOTION_LABELS.get(emotion, emotion)
        layers = [
            {"id": "style",    "label": "Art style",   "source": "overview" if story_styled else "global",
             "text": style},
            {"id": "identity", "label": "Identity",    "source": "cast", "text": appearance},
            {"id": "outfit",   "label": "Outfit",      "source": "cast", "text": attire},
            {"id": "emotion",  "label": f"Emotion — {label}", "source": "cast",
             "text": EMOTION_HINTS.get(emotion) or expr},
            {"id": "pose",     "label": "Pose & framing", "source": "cast",
             "text": f"{pose} · {framing}".strip(" ·")},
        ]
        final, composed_by = "", "deterministic"
        if body.get("compose", True):
            _v4 = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
            if _v4 is not None and hasattr(_v4, "generate_text"):
                try:
                    final = compose_sprite_prompt(_v4, style=style, appearance=appearance, attire=attire,
                                                  emotion_label=label,
                                                  hint=(EMOTION_HINTS.get(emotion) or expr or emotion))
                    composed_by = "deepseek-v4-pro"
                except Exception:  # noqa: BLE001
                    final = ""
        if not final:
            final = sprite_prompt(appearance, attire, expr, pose, framing, emotion=emotion, style=style)
            composed_by = "deterministic"
        layers.append({"id": "final", "label": f"Final prompt ({composed_by})", "source": "compose",
                       "text": final})
        layers.append({"id": "face", "label": "Face pass (detailer)", "source": "cast",
                       "text": face_wildcard(appearance, label, emotion, expr)})
        return {"layers": layers, "story": owner, "outfit": oid, "emotion": emotion}

    @app.post("/api/characters/{key}/sprite/select")
    def sprite_select(key: str, body: dict):
        """Save a chosen sprite candidate into the outfit's expression grid."""
        body = body or {}
        oid, emotion = body.get("outfit_id"), body.get("emotion")
        m = ctx.portrait_manifest(key)
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
        (ctx.portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (ctx.portrait_dir(key) / oid / f"{emo}.png").write_bytes(png)
        outfit.setdefault("expressions", {})[emo] = f"{emo}.png"
        ctx.save_portrait_manifest(key, m)
        return {"ok": True, "url": f"/api/characters/{key}/portraits/img/{oid}/{emo}.png"}

    @app.post("/api/characters/{key}/portraits/render-emotions")
    async def render_emotions(key: str, body: dict):   # async: _start_stream_job needs the loop
        """Render the FULL fixed emotion taxonomy as sprites UPFRONT — for one outfit
        (body.outfit_id) or ALL outfits. ONE full-body image per (outfit × emotion), saved straight
        into the manifest; streamed as a job (phase per outfit, item per emotion). The per-cell
        3-candidate redo stays available via sprite-candidate/sprite-select. Returns {job}."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        only = body.get("outfit_id")
        m = ctx.portrait_manifest(key)
        outfits = [o for o in m.get("outfits", []) if (not only or o.get("id") == only)]
        if not outfits:
            return JSONResponse({"error": "no outfits to render"}, status_code=400)
        provider, model_id = ctx.role_image_provider("sprite", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        appearance = (ch.fields or {}).get("appearance") or ""
        canon = m.get("expression_prompts") or {}
        oprefix = ctx.output_prefix_for(model_id, "sprite", key)   # organized output path (once)

        def work(emit, cancelled):
            from concurrent.futures import ThreadPoolExecutor

            from ...comfy.server import get_server
            try:
                get_server(provider.base_url).ensure_up()
            except Exception:  # noqa: BLE001
                pass
            done = 0

            from ..services.prompts import (sprite_prompt, compose_sprite_prompt,
                                             face_wildcard, _persona_text)
            from ..services.emotions import CORE_KEYS, EMOTION_LABELS, EMOTION_HINTS, INTIMACY_KEYS
            from ..services.images import SPRITE_SEED
            from loom.stories.pipeline import compose_affect_range as _compose_affect_range
            _style = ctx.art_style(char_key=key)   # L0: story art style → global anchor
            _mature = bool(body.get("mature"))
            _emo_pool = set(EMOTION_KEYS)
            # Character-default range (fallback when an outfit has no register-specific range yet).
            _char_range = [(r.get("emotion") if isinstance(r, dict) else r)
                           for r in ((m.get("affect") or {}).get("range") or [])]
            _char_range = [e for e in _char_range if e]
            _persona = _persona_text(ch)
            _emo_systems = (ctx.load_story_builder().get("systems") or {})
            # v4pro (non-thinking) — the known-good structured model; the default emotion-stage
            # model can't do enum `emits` and silently falls back to the full set.
            _v4 = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "none"})
            _dirty = False   # did we auto-compose any outfit range → persist it

            def _emos_for(o):
                # Per-OUTFIT emotion set — the register-specific range someone shows in THIS outfit.
                # body.emotions override > stored outfit range > auto-composed from attire > char
                # default > CORE. INTIMACY keys dropped unless the caller opts in (mature).
                nonlocal _dirty
                rng = body.get("emotions") or o.get("range")
                if not rng:
                    attire = o.get("attire_prompt") or o.get("prompt") or ""
                    rng = (_compose_affect_range(_v4, _persona, nsfw=_mature,
                                                 systems=_emo_systems, attire=attire).get("range")
                           if attire else None) or _char_range or CORE_KEYS
                    o["range"] = rng          # store so the pane + next render match this outfit
                    _dirty = True
                return [e for e in rng if e in _emo_pool and (_mature or e not in INTIMACY_KEYS)]

            # Build the (outfit × emotion) job list first (prompts filled in the parallel pass).
            all_jobs = []
            for o in outfits:
                if cancelled():
                    break
                oid = o.get("id")
                attire = o.get("attire_prompt") or o.get("prompt") or ""
                is_unified = o.get("unified", False)
                odir = ctx.portrait_dir(key, create=True) / oid
                odir.mkdir(parents=True, exist_ok=True)
                for emo in _emos_for(o):
                    all_jobs.append({"outfit": o, "emotion": emo, "attire": attire,
                                     "is_unified": is_unified, "out_dir": odir,
                                     "latent": ctx.pose_latent(emo)})
            if _dirty:
                ctx.save_portrait_manifest(key, m)

            if cancelled():
                return {"ok": True, "rendered": 0, "outfits": 0}

            # PROMPT COMPOSITION — v4pro writes each emotion's full prompt (appearance + clothing +
            # pose + facial expression) IN PARALLEL; deterministic sprite_prompt is the fallback.
            emit({"type": "phase", "label": f"Composing {len(all_jobs)} emotion prompts (v4pro)…"})
            def _compose(j):
                o, emo = j["outfit"], j["emotion"]
                app_ = "" if j["is_unified"] else appearance
                if _v4 is not None and hasattr(_v4, "generate_text"):
                    try:
                        return compose_sprite_prompt(_v4, style=_style, appearance=app_,
                                                     attire=j["attire"],
                                                     emotion_label=EMOTION_LABELS.get(emo, emo),
                                                     hint=(EMOTION_HINTS.get(emo)
                                                           or canon.get(emo) or emo))
                    except Exception:  # noqa: BLE001
                        pass
                return sprite_prompt(app_, j["attire"],
                                     canon.get(emo) or (o.get("expression_prompts") or {}).get(emo) or emo,
                                     ctx.pose_tags(key, emo, outfit_id=o.get("id")),
                                     ctx.pose_framing(emo), emotion=emo, style=_style)
            with ThreadPoolExecutor(max_workers=8) as ex:
                for j, p in zip(all_jobs, ex.map(_compose, all_jobs)):
                    j["prompt"] = p

            if cancelled():
                return {"ok": True, "rendered": 0, "outfits": 0}

            total = len(all_jobs)
            emit({"type": "phase", "label": f"Rendering {total} sprites across {len(outfits)} outfits"})
            emit({"type": "progress", "done": 0, "total": total})   # arm the bar immediately

            batch_prompts = [{"prompt": j["prompt"], "latent": j["latent"],
                              "wildcard": face_wildcard("" if j["is_unified"] else appearance,
                                                        EMOTION_LABELS.get(j["emotion"], j["emotion"]),
                                                        j["emotion"], canon.get(j["emotion"], ""))}
                             for j in all_jobs]
            saved = {"n": 0}
            img_base = f"/api/characters/{key}/portraits/img"

            def _on_result(idx, png):
                # STREAM: persist + announce each sprite the instant it lands, so the pane fills
                # cell-by-cell instead of a burst at the end. Runs serially in the fan-out thread.
                job = all_jobs[idx]
                if not png:
                    return
                oid, emo = job["outfit"].get("id"), job["emotion"]
                (job["out_dir"] / f"{emo}.png").write_bytes(png)
                job["outfit"].setdefault("expressions", {})[emo] = f"{emo}.png"
                ctx.save_portrait_manifest(key, m)
                saved["n"] += 1
                emit({"type": "item", "name": emo, "text": job["outfit"].get("name") or oid})
                emit({"type": "sprite", "outfit": oid, "emotion": emo,
                      "url": f"{img_base}/{oid}/{emo}.png"})

            render_batch(
                provider, batch_prompts, ctx=ctx, out_prefix_template=oprefix,
                cancel=cancelled, seed=SPRITE_SEED,   # fixed seed → consistent set
                on_progress=lambda d, t: emit({"type": "progress", "done": d, "total": t}),
                on_result=_on_result,
                force_local=True,   # the krea2 sprite detailer lives only on local ComfyUI
            )
            return {"ok": True, "rendered": saved["n"], "outfits": len(outfits)}

        job = _start_stream_job("sprites", "Render emotions", ch.name,
                                body.get("screen") or f"characters/{key}", work)
        return {"job": job.id}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/recompose")
    def portrait_outfit_recompose(key: str, oid: str, body: dict):
        """Re-run the robust 2-step outfit-prompt pipeline (`_compose_outfit_prompt`: best-guess →
        danbooru_character.csv clothing retrieval → choose-from-real refine) and SAVE it as the
        outfit's attire_prompt. Regenerating the outfit IMAGE calls this first, so the render always
        rides a fresh, complete, colour-consistent prompt — never a stale stored one. {attire_prompt}."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        # Use the brief concept (if stored) as the seed — not the full prose prompt.
        brief_concept = outfit.get("concept") or ""
        from loom.stories.pipeline import compose_outfit_prompt as _compose_outfit_prompt
        from ..services import config_files as _cfiles
        _w_cfg = ctx.load_story_builder()
        _w_prov = ctx.stage_provider("wardrobe", (body or {}).get("model"))
        r = _compose_outfit_prompt(
            _w_prov, ch.system or "", (ch.fields or {}).get("appearance", ""),
            outfit.get("name", ""), brief_concept)
        attire = r.get("attire") if isinstance(r, dict) else ""
        if not attire:
            return JSONResponse({"error": "could not compose outfit prompt (author model may "
                                          "not support structured output)"}, status_code=500)
        outfit["attire_prompt"] = attire
        outfit["prompt"] = attire
        if r.get("unified"):
            outfit["unified"] = True
        ctx.save_portrait_manifest(key, m)
        return {"ok": True, "attire_prompt": attire}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/candidate")
    async def portrait_outfit_candidate(key: str, oid: str, body: dict):
        """Render ONE FULL-BODY candidate for an outfit — the whole-look reference (character
        appearance + the outfit's attire, FULL-BODY framing, txt2img). Fresh seed each call; returns
        a data URI (not saved). The UI recomposes the prompt first, then generates 3 and lets you
        pick. Distinct from the face-focused emotion sprites."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        # Base Studio overrides: a one-off `attire` prompt and/or an explicit `style` card
        # (else the active global anchor). Persisting a mutated attire is the caller's call.
        attire = ((body or {}).get("attire") or "").strip()             or outfit.get("attire_prompt") or outfit.get("prompt") or ""
        is_unified = outfit.get("unified", False)
        provider, model_id = ctx.role_image_provider("sprite", (body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        # The base is just the NEUTRAL sprite — render it through the SAME `sprite_prompt` builder
        # as the emotions (identical scaffold: style anchor + appearance + attire + neutral pose),
        # or the base and its emotion sprites come out in different art styles (measured mismatch).
        from ..services.prompts import sprite_prompt
        _style = ((body or {}).get("style") or "").strip() or ctx.art_style(char_key=key)
        appearance = "" if is_unified else ((ch.fields or {}).get("appearance") or "")
        prompt = sprite_prompt(appearance, attire, "",
                               ctx.pose_tags(key, "neutral"), ctx.pose_framing("neutral"),
                               emotion="neutral", style=_style)
        _randomize_seeds(provider.workflow)
        # txt2img — identity from the appearance tags (the model is consistent without img2img).
        try:
            png = await _render(provider, prompt, out_prefix=ctx.output_prefix_for(model_id, "outfit", key),
                                latent=ctx.pose_latent("neutral"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode()}

    @app.post("/api/characters/{key}/portraits/outfit/{oid}/base")
    def portrait_outfit_set_base(key: str, oid: str, body: dict):
        """Save a chosen FULL-BODY candidate as the outfit's base image (base.png)."""
        m = ctx.portrait_manifest(key)
        outfit = ctx.portrait_outfit(m, oid)
        if outfit is None:
            return JSONResponse({"error": "no such outfit"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        (ctx.portrait_dir(key, create=True) / oid).mkdir(parents=True, exist_ok=True)
        (ctx.portrait_dir(key) / oid / "base.png").write_bytes(png)
        outfit["base"] = "base.png"
        ctx.save_portrait_manifest(key, m)
        return {"ok": True, "url": f"/api/characters/{key}/portraits/img/{oid}/base.png"}

    @app.get("/api/characters/{key}/base-prompt")
    def base_prompt(key: str):
        """The exact positive prompt that base-candidate will send for this character
        (so the UI can show/edit it before generating). Also returns the raw appearance
        field for context."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        default = _base_prompt(ch)
        saved = _safe_image_tags(((ch.fields or {}).get("base_prompt") or "").strip())
        return {"prompt": saved or default, "default": default, "saved": bool(saved),
                "appearance": (ch.fields or {}).get("appearance") or "",
                "name": ch.name}

    @app.post("/api/characters/{key}/base-prompt/generate")
    async def generate_base_prompt(key: str, body: dict):
        """Regenerate a character's base-image prompt via the shared appearance authority
        (_compose_base_prompt): FEATURES_SCHEMA draft → co-occurrence enrichment →
        _assemble_base_prompt. Returns {prompt, features, companions}. The SAME function runs at
        cast generation, so a regenerated cast already matches this."""
        from fastapi.concurrency import run_in_threadpool

        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        # Auto-flesh a thin seed first, so the appearance is parsed from disciplined prose (thin→rich).
        await run_in_threadpool(ctx.ensure_fleshed, key)
        ch = ctx.base_settings.characters.get(key)
        fields = ch.fields or {}
        from loom.stories.pipeline import compose_base_prompt as _compose_base_prompt
        from ..services import config_files as _cfiles
        _bp_cfg = ctx.load_story_builder()
        _bp_prov = ctx.stage_provider("base_image", (body or {}).get("model"))
        out = await run_in_threadpool(lambda: _compose_base_prompt(
            _bp_prov, ch.name, ch.system or "", fields.get("appearance", ""),
            fields.get("role", ""), systems=(_bp_cfg.get("systems") or {})))
        if "error" in out:
            return JSONResponse({"error": out["error"]}, status_code=500)
        # Persist the derived numeric stature (used for sprite scaling, not a prompt tag) so it
        # survives regardless of how the UI saves the prompt. Best-effort YAML field update.
        height_cm = (out.get("features") or {}).get("height_cm")
        if height_cm:
            try:
                data = ctx._read_character_data(key)   # owning story DB (embedded) or global YAML
                if data is not None:
                    data.setdefault("fields", {})["height_cm"] = int(height_cm)
                    ctx._write_character_data(key, data)
            except Exception:  # noqa: BLE001 — never sink the response over a metadata write
                pass
        return out

    @app.post("/api/characters/{key}/base-candidate")
    async def base_candidate(key: str, body: dict):
        """Generate ONE candidate base image for a character from their description.
        Plain txt2img (the image pipeline is Anima-only — no IPAdapter style flow).
        Returns a data URI (not saved) — the UI lets you pick a winner."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        body = body or {}
        # The chosen workflow carries its own quality/style. The UI may pass an edited `prompt`
        # (see GET .../base-prompt); else use the character's own appearance (NOT a hardcoded
        # fallback — that mis-genders e.g. Darek).
        prompt = (body.get("prompt") or "").strip() or _base_prompt(ch)
        provider, model_id = ctx.role_image_provider("base", body.get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)  # fresh seed each call → a batch of 4 varies
        try:
            png = await _render(provider, prompt,
                                out_prefix=ctx.output_prefix_for(model_id, "base", key),
                                latent=ctx.pose_latent("neutral"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(),
                "model": model_id}

    @app.post("/api/characters/{key}/reference/from-data")
    def set_reference_from_data(key: str, body: dict):
        """Set the character's reference/base image from a base64 data URI (used to
        accept a chosen base-image candidate)."""
        safe = re.sub(r"[^\w\-]+", "", key)
        if key not in ctx.base_settings.characters:   # embedded (DB) or global — not a YAML-file check
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
        d = ctx.char_asset_dir(key); d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.post("/api/characters/{key}/expand-background")
    def expand_background(key: str, body: dict):
        """Rewrite a character's persona into a THOROUGH, labelled background
        (Identity / History / Personality / Relationships / Voice). Works for any
        character (incl. imported); uses the character's attached story as context."""
        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        provider = ctx.author_provider((body or {}).get("model"))
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect a chat model first"}, status_code=400)
        story_ctx = ""
        sk = (ch.fields or {}).get("story") or (body or {}).get("story")
        st = ctx.base_settings.stories.get(sk) if sk else None
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
        data = ctx._read_character_data(key) or {}
        data["system"] = bg
        ctx._write_character_data(key, data)   # owning story DB (embedded) or global YAML
        return {"ok": True, "system": bg}

    @app.delete("/api/characters/{key}")
    def delete_character(key: str):
        """Delete a character: strip it from every story cast, drop its embedded record from the
        owning story DB (+ any global YAML), and remove its avatar/ref/portraits."""
        import shutil

        from ...stories import story_db as _SDB
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        safe = re.sub(r"[^\w\-]+", "", key)
        # Resolve the asset dirs BEFORE dropping the DB record — removal flips ownership, so
        # char_asset_dir/portrait_dir would otherwise point at the global pool, not the story folder.
        adir = ctx.char_asset_dir(key)
        pdir = ctx.portrait_dir(key)
        # strip cast references across stories (routed: DB or YAML) + drop the embedded record
        for skey, st in list(ctx.base_settings.stories.items()):
            kept = [m for m in st.cast if m.character != key]
            if len(kept) != len(st.cast):
                ctx.update_story_fields(skey, {"cast": [m.model_dump() for m in kept]})
            db = ctx._story_file(skey)
            if db is not None and key in _SDB.character_keys(db):
                _SDB.delete_character(db, key)
        # on-disk binaries (in the story folder for owned chars, else the global library) + any YAML
        for fn in (f"{safe}.yaml", f"{safe}.png", f"{safe}.ref.png"):
            f = adir / fn
            if f.is_file():
                f.unlink()
        shutil.rmtree(pdir, ignore_errors=True)
        ctx.reload_settings()
        return {"ok": True}

    @app.post("/api/characters/import")
    def import_character(body: CharacterImportRequest):
        """Import a SillyTavern character card from an uploaded file (PNG with an
        embedded `chara` chunk, or a raw JSON card)."""
        try:
            raw = base64.b64decode(body.data_b64)
            cdata = to_character(extract_card_json(raw))
            avatar = raw if raw.startswith(b"\x89PNG\r\n\x1a\n") else None
            return ctx.write_character(cdata, avatar)
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
            return ctx.write_character(to_character(card), avatar)
        except Exception as exc:
            return JSONResponse({"error": f"import failed: {exc}"}, status_code=400)

    @app.post("/api/characters/{key}/portraits/wardrobe")
    def portraits_apply_wardrobe(key: str, body: dict):
        """Merge a planned wardrobe into the character's portrait manifest.

        Additive by default — new outfits are appended; expression/pose/affect are composed from
        the persona when absent.  With `replace: true` existing outfits (and their sprite
        directories) are deleted first and the persona-derived prompt caches are cleared so
        everything is recomposed fresh."""
        from loom.stories.pipeline import apply_manifest
        if ctx.base_settings.characters.get(key) is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        try:
            return apply_manifest(ctx, key, body or {})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
