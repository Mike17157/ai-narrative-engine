from __future__ import annotations

import base64
import re

import yaml
from fastapi import UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ...cards import extract_card_json, to_character
from ...card_sources import fetch_card
from ..services.emotions import EMOTION_KEYS
from ..services.images import _clean_reference_png, _randomize_seeds, _render
from ..services.jobs_util import _start_stream_job
from ..services.prompts import (
    _DESCRIBE_SYSTEM,
    _EXPRESSION_SYSTEM,
    _FULLBODY_FRAMING,
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
                "image": c.image.model_dump(),
                "avatar": f"/api/characters/{k}/avatar" if (char_dir / f"{k}.png").is_file() else None,
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
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.char_dir() / f"{safe}.yaml"
        if not path.is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            loras = [{"name": l["name"], "weight": float(l.get("weight", 1.0))}
                     for l in (body.get("loras") or []) if l.get("name")]
            data["image"] = {"checkpoint": (body.get("checkpoint") or None),
                             "loras": loras, "stack": (body.get("stack") or None)}
            from ...config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        ctx.reload_settings()
        return {"ok": True}

    @app.get("/api/characters/{key}/avatar")
    def character_avatar(key: str):
        """Serve the imported card PNG as the character's avatar (404 if none)."""
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.char_dir() / f"{safe}.png"
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
        if not (ctx.char_dir() / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        data = await file.read()
        # normalize whatever was uploaded to PNG via Pillow if available; else store raw
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001 — Pillow missing or odd format; store as-is
            pass
        (ctx.char_dir() / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.delete("/api/characters/{key}/reference")
    def clear_character_reference(key: str):
        """Drop the dedicated reference, falling back to the avatar."""
        safe = re.sub(r"[^\w\-]+", "", key)
        p = ctx.char_dir() / f"{safe}.ref.png"
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
        render_prompt = f"{prompt}, neutral expression, {_FULLBODY_FRAMING}"
        try:
            from ...comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(prompt=render_prompt)
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
        render_prompt = f"{outfit.get('prompt','')}, {expr_tags}, {_PORTRAIT_FRAMING}"
        try:
            from ...comfy.server import get_server
            get_server(provider.base_url).ensure_up()
            result = provider.generate_image(prompt=render_prompt, init_image=base_png.read_bytes())
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
        outfit carries its own expression range (the runtime picks the closest by emotion vector
        similarity). Returns the portrait payload."""
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

    @app.post("/api/characters/{key}/card")
    def update_character_card(key: str, body: dict):
        """Edit the character card's text (name / persona / greeting / appearance
        + any extra fields), persist to its YAML, and reload settings."""
        if key not in ctx.base_settings.characters:
            return JSONResponse({"error": "no such character"}, status_code=404)
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.char_dir() / f"{safe}.yaml"
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
            from ...config.schema import Character
            Character(**data)  # validate
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        ctx.reload_settings()
        return {"ok": True}

    @app.post("/api/characters/{key}/reference/from-url")
    async def set_reference_from_url(key: str, body: dict):
        """Download an image (e.g. a card-link URL) and store it as the character's
        reference / base image (<key>.ref.png)."""
        import httpx

        safe = re.sub(r"[^\w\-]+", "", key)
        if not (ctx.char_dir() / f"{safe}.yaml").is_file():
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
        (ctx.char_dir() / f"{safe}.ref.png").write_bytes(data)
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
        # The sprite workflow is txt2img (illustrious fork) — identity comes from the
        # prompt, so include the character's core appearance alongside attire+expression.
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        # Per-outfit expression prompt (legacy global as fallback for old data).
        expr = ((outfit.get("expression_prompts") or {}).get(emotion)
                or (m.get("expression_prompts") or {}).get(emotion) or emotion or "")
        # Every outfit picture is FULL BODY (the expression sprite shows the whole look + the face).
        prompt = _regionize_prompt(_snap_prompt(_safe_image_tags(
            ", ".join(p for p in (appearance, attire, expr, _FULLBODY_FRAMING) if p))))
        model = ctx.role_model("sprite", body.get("image_model"))
        provider, model_id = ctx.image_provider(model)
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
    def render_emotions(key: str, body: dict):
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
        model = ctx.role_model("sprite", body.get("image_model"))
        provider, model_id = ctx.image_provider(model)
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        appearance = (ch.fields or {}).get("appearance") or ""
        canon = m.get("expression_prompts") or {}

        def work(emit, cancelled):
            from concurrent.futures import ThreadPoolExecutor

            from ...comfy.server import get_server
            try:
                get_server(provider.base_url).ensure_up()
            except Exception:  # noqa: BLE001
                pass
            done = 0
            for o in outfits:
                if cancelled():
                    break
                oid = o.get("id")
                attire = o.get("attire_prompt") or o.get("prompt") or ""
                odir = ctx.portrait_dir(key, create=True) / oid
                odir.mkdir(parents=True, exist_ok=True)
                emit({"type": "phase",
                      "label": f"Rendering {o.get('name') or oid} — {len(EMOTION_KEYS)} emotions"})

                def _one(emo, _attire=attire, _odir=odir, _o=o):
                    if cancelled():
                        return None
                    expr = canon.get(emo) or (_o.get("expression_prompts") or {}).get(emo) or emo
                    prompt = _regionize_prompt(_snap_prompt(_safe_image_tags(
                        ", ".join(p for p in (appearance, _attire, expr, _FULLBODY_FRAMING) if p))))
                    try:
                        prov2, _mid = ctx.image_provider(model)   # own workflow+seed per thread
                        _randomize_seeds(prov2.workflow)
                        res = prov2.generate_image(prompt=prompt)
                        png = res.images[0] if res.images else None
                    except Exception:  # noqa: BLE001 — one sprite failing must not sink the batch
                        png = None
                    if png:
                        (_odir / f"{emo}.png").write_bytes(png)
                        return emo
                    return None

                # ≤3 concurrent submissions — ComfyUI queues them; don't flood the server.
                with ThreadPoolExecutor(max_workers=3) as ex:
                    for emo in ex.map(_one, EMOTION_KEYS):
                        if emo:
                            o.setdefault("expressions", {})[emo] = f"{emo}.png"
                            done += 1
                            emit({"type": "item", "name": emo, "text": o.get("name") or oid})
                ctx.save_portrait_manifest(key, m)   # persist this outfit's sprites
            return {"ok": True, "rendered": done, "outfits": len(outfits)}

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
        r = ctx.compose_outfit_prompt(
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
        appearance = (ch.fields or {}).get("appearance") or ""
        attire = outfit.get("attire_prompt") or outfit.get("prompt") or ""
        prompt = _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(p for p in (appearance, attire, _FULLBODY_FRAMING) if p))))
        model = ctx.role_model("sprite", (body or {}).get("image_model"))
        provider, model_id = ctx.image_provider(model)
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

        ch = ctx.base_settings.characters.get(key)
        if ch is None:
            return JSONResponse({"error": "no such character"}, status_code=404)
        fields = ch.fields or {}
        out = await run_in_threadpool(lambda: ctx.compose_base_prompt(
            ch.name, ch.system or "", fields.get("appearance", ""), fields.get("role", ""),
            (body or {}).get("model")))
        if "error" in out:
            return JSONResponse({"error": out["error"]}, status_code=500)
        # Persist the derived numeric stature (used for sprite scaling, not a prompt tag) so it
        # survives regardless of how the UI saves the prompt. Best-effort YAML field update.
        height_cm = (out.get("features") or {}).get("height_cm")
        if height_cm:
            try:
                from ...config.schema import Character
                safe = re.sub(r"[^\w\-]+", "", key)
                path = ctx.char_dir() / f"{safe}.yaml"
                if path.is_file():
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    data.setdefault("fields", {})["height_cm"] = int(height_cm)
                    Character(**data)  # validate
                    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                    encoding="utf-8")
                    ctx.reload_settings()
            except Exception:  # noqa: BLE001 — never sink the response over a metadata write
                pass
        return out

    @app.post("/api/characters/{key}/base-candidate")
    async def base_candidate(key: str, body: dict):
        """Generate ONE candidate base image for a character from their description.
        If a style source (another character's reference, e.g. the story's primary)
        is given, render in THAT art style via the IPAdapter style flow; otherwise
        plain txt2img. Returns a data URI (not saved) — the UI lets you pick a winner."""
        ch = ctx.base_settings.characters.get(key)
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
            ref = ctx.reference_path(body["style_from"])
            if ref is not None and (not ctx.reference_path(key) or body["style_from"] != key):
                style_bytes = ref.read_bytes()
        # Pick the workflow by role: 'style' when a style image is in play (config-overridable
        # via image_roles), else 'base'. An explicit image_model in the request always wins.
        # The init image only matters to a style/img2img graph; ComfyUIProvider ignores it on a
        # plain txt2img workflow (no LoadImage node), so passing it through is safe.
        model = ctx.role_model("style" if style_bytes else "base", (body or {}).get("image_model"))
        provider, model_id = ctx.image_provider(model)
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
        if not (ctx.char_dir() / f"{safe}.yaml").is_file():
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
        (ctx.char_dir() / f"{safe}.ref.png").write_bytes(data)
        return {"ok": True}

    @app.post("/api/characters/{key}/expand-background")
    def expand_background(key: str, body: dict):
        """Rewrite a character's persona into a THOROUGH, labelled background
        (Identity / History / Personality / Relationships / Voice). Works for any
        character (incl. imported); uses the character's attached story as context."""
        safe = re.sub(r"[^\w\-]+", "", key)
        path = ctx.char_dir() / f"{safe}.yaml"
        ch = ctx.base_settings.characters.get(key)
        if ch is None or not path.is_file():
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
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["system"] = bg
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        ctx.reload_settings()
        return {"ok": True, "system": bg}

    @app.delete("/api/characters/{key}")
    def delete_character(key: str):
        """Delete a character (yaml + avatar/ref + portraits). First strips it from
        every scenario/story cast so the config still validates on reload."""
        import shutil
        safe = re.sub(r"[^\w\-]+", "", key)
        cdir = ctx.char_dir()
        if not (cdir / f"{safe}.yaml").is_file():
            return JSONResponse({"error": "no such character"}, status_code=404)
        # remove cast references in scenarios + stories (don't delete those files)
        for d in (ctx.scenario_dir(), ctx.story_dir()):
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
        shutil.rmtree(ctx.portrait_dir(key), ignore_errors=True)
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
