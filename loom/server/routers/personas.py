"""Personas — who *you* are in the chat (the {{user}} side).

Server-side (one YAML per persona under configs/personas/, with a <key>.png avatar
alongside), mirroring characters/scenarios. Endpoints cover CRUD + avatar serving,
plus the one-click generation flow:

  POST /api/personas/describe           → summary prose + appearance tags from the description
  POST /api/personas/<key>/image-candidate → ONE full-body portrait candidate (client fires 3×)

The describe + image-candidate pair is non-persisting: the client previews, then saves
via the field-update endpoint (and the avatar endpoint once a candidate is picked) — the
exact candidate-picker pattern the character studio uses.
"""
from __future__ import annotations

import base64
import re

from fastapi import UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from ..services.images import _clean_reference_png, _randomize_seeds, _render
from ..services.prompts import (
    PERSONA_SCHEMA,
    _PERSONA_FRAMING,
    _PERSONA_SYSTEM,
    _safe_image_tags,
    _snap_prompt,
)


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (name or "").lower()).strip("_") or "persona"


def _persona_payload(ctx, key, p) -> dict:
    """One persona as the frontend expects it: its fields + a served avatar URL (if any)."""
    avatar = f"/api/personas/{key}/avatar" if ctx.persona_avatar_path(key) else None
    return {"key": key, "name": p.name, "description": p.description, "summary": p.summary,
            "appearance": p.appearance, "fields": p.fields or {}, "avatar": avatar}


def register(app, ctx):
    @app.get("/api/personas")
    def list_personas() -> list:
        return [_persona_payload(ctx, k, p) for k, p in ctx.base_settings.personas.items()]

    @app.post("/api/personas")
    def create_persona(body: dict):
        """Create a persona (name + optional description). Returns the new key."""
        body = body or {}
        name = (body.get("name") or "New persona").strip() or "New persona"
        key, base, n = _slug(name), _slug(name), 2
        while (ctx.persona_dir() / f"{key}.yaml").is_file():
            key, n = f"{base}_{n}", n + 1
        data = {"name": name, "description": body.get("description", "")}
        try:
            return ctx.write_persona(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)

    @app.post("/api/personas/import")
    def import_personas(body: dict):
        """Bulk-create personas from a client migration (idempotent by name slug).
        Accepts [{ name, description, summary?, appearance?, avatar?(data URI) }, ...].
        Returns { keys: [{ name, key }] } so the client can remap the active id."""
        items = body.get("personas") if body else None
        if not isinstance(items, list):
            return JSONResponse({"error": "expected { personas: [...] }"}, status_code=400)
        existing = {p.name for p in ctx.base_settings.personas.values()}
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name or name in existing:
                continue
            key, base, n = _slug(name), _slug(name), 2
            while (ctx.persona_dir() / f"{key}.yaml").is_file():
                key, n = f"{base}_{n}", n + 1
            data = {"name": name, "description": it.get("description", "") or ""}
            for fld in ("summary", "appearance"):
                v = it.get(fld)
                if v:
                    data[fld] = v
            try:
                ctx.write_persona(key, data)
                existing.add(name)
            except Exception:  # noqa: BLE001 — never abort the whole batch over one row
                continue
            # Carry the avatar across if the migration supplied one (a localStorage data URI).
            av = it.get("avatar")
            if isinstance(av, str) and av.startswith("data:"):
                try:
                    raw = base64.b64decode(av.split(",", 1)[1])
                    ctx.save_persona_avatar(key, _clean_reference_png(raw))
                except Exception:  # noqa: BLE001
                    pass
            out.append({"name": name, "key": key})
        return {"ok": True, "keys": out}

    @app.post("/api/personas/describe")
    async def describe_persona(body: dict):
        """Generate the persona's short SUMMARY (chat blurb) + APPEARANCE booru tags from
        the user's long-form description, via ONE structured call to the image-prompt /
        chat text model. Does NOT persist — the client previews, then saves.

        Registered BEFORE the {key} routes so the static 'describe' path isn't captured as a key."""
        from fastapi.concurrency import run_in_threadpool

        description = ((body or {}).get("description") or "").strip()
        if not description:
            return JSONResponse({"error": "description required"}, status_code=400)
        provider = ctx.ip_provider()
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "connect an image-prompt or chat model first "
                                          "(⚙ Models)"}, status_code=400)
        name = (body or {}).get("name") or "the user"
        prompt = f"NAME: {name}\n\nSELF-DESCRIPTION:\n{description[:4000]}\n\nProduce the summary + appearance."
        try:
            data = await run_in_threadpool(
                lambda: provider.generate_text(system=_PERSONA_SYSTEM, prompt=prompt,
                                               emits=PERSONA_SCHEMA).data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"describe failed: {exc}"}, status_code=500)
        if not isinstance(data, dict):
            data = {}
        summary = (str(data.get("summary") or "").strip())
        appearance_raw = (str(data.get("appearance") or "").strip())
        # Snap the appearance onto real booru tags + clean literal-model traps, so the render is pure.
        appearance = _snap_prompt(_safe_image_tags(appearance_raw)) if appearance_raw else ""
        return {"summary": summary, "appearance": appearance}

    @app.post("/api/personas/{key}")
    def update_persona(key: str, body: dict):
        """Update editable fields (name/description/summary/appearance). Name changes do
        NOT rename the file (the key is stable) — only the displayed name."""
        if key not in ctx.base_settings.personas:
            return JSONResponse({"error": "no such persona"}, status_code=404)
        body = body or {}
        p = ctx.base_settings.personas[key]
        data = {"name": (body.get("name") or p.name).strip() or p.name,
                "description": body.get("description", p.description),
                "summary": body.get("summary", p.summary),
                "appearance": body.get("appearance", p.appearance)}
        # Preserve the lossless extras unless the caller is explicitly replacing them.
        if "fields" in body:
            data["fields"] = body.get("fields") or {}
        elif p.fields:
            data["fields"] = p.fields
        try:
            return ctx.write_persona(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)

    @app.delete("/api/personas/{key}")
    def delete_persona(key: str):
        if not ctx.delete_persona(key):
            return JSONResponse({"error": "no such persona"}, status_code=404)
        return {"ok": True}

    @app.get("/api/personas/{key}/avatar")
    def persona_avatar(key: str):
        path = ctx.persona_avatar_path(key)
        if path is None:
            return JSONResponse({"error": "no avatar"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/personas/{key}/avatar")
    async def set_persona_avatar_upload(key: str, file: UploadFile = File(...)):
        """Upload/replace a persona's avatar PNG (the manual 'change picture' path)."""
        if key not in ctx.base_settings.personas:
            return JSONResponse({"error": "no such persona"}, status_code=404)
        data = await file.read()
        try:
            data = _clean_reference_png(data)
        except Exception:  # noqa: BLE001 — store as-is if Pillow can't normalize
            pass
        url = ctx.save_persona_avatar(key, data)
        return {"ok": True, "avatar": url}

    @app.post("/api/personas/{key}/avatar/from-data")
    def set_persona_avatar_data(key: str, body: dict):
        """Set the avatar from a base64 data URI (used when a generated candidate is picked)."""
        if key not in ctx.base_settings.personas:
            return JSONResponse({"error": "no such persona"}, status_code=404)
        uri = (body or {}).get("data", "")
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        try:
            png = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad image data"}, status_code=400)
        try:
            png = _clean_reference_png(png)
        except Exception:  # noqa: BLE001
            pass
        url = ctx.save_persona_avatar(key, png)
        return {"ok": True, "avatar": url}

    @app.post("/api/personas/{key}/image-candidate")
    async def persona_image_candidate(key: str, body: dict):
        """Render ONE full-body portrait candidate for a persona from its appearance tags.
        Fresh seed each call (the client fires 3× for a candidate picker). Returns a data
        URI (not saved) — the UI's 'Use' posts the chosen one to /avatar/from-data."""
        p = ctx.base_settings.personas.get(key)
        if p is None:
            return JSONResponse({"error": "no such persona"}, status_code=404)
        appearance = (p.appearance or "").strip()
        if not appearance:
            return JSONResponse({"error": "generate the appearance first (✨)"}, status_code=400)
        # Compose the full-body prompt: the persona's appearance + the identity framing. The
        # appearance tags already carry sex/body/hair/eyes/outfit; framing anchors a full-body shot.
        prompt = _snap_prompt(_safe_image_tags(f"{appearance}, {_PERSONA_FRAMING}"))
        provider, model_id = ctx.role_image_provider("base", (body or {}).get("image_model"))
        if provider is None:
            return JSONResponse({"error": model_id}, status_code=400)
        _randomize_seeds(provider.workflow)
        try:
            png = await _render(provider, prompt,
                                out_prefix=ctx.output_prefix_for(model_id, "persona", key))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"render failed: {exc}"}, status_code=500)
        if png is None:
            return JSONResponse({"error": "image model returned no image"}, status_code=500)
        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(),
                "model": model_id}
