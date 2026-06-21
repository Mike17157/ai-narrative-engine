"""Image-preset manager — the central registry for the "look side" of image generation.

An *image preset* = a named LoRA stack (+ optional base checkpoint) injected at render time,
replacing the anima workflow's baked ImpactSwitch styles. A deliberate sibling of the model
Presets and Lorebooks managers.

  GET    /api/image-presets               → { active, presets:[...] }
  POST   /api/image-presets               → upsert one preset (blank id → mint from name)
  POST   /api/image-presets/{id}/activate → set the global-default preset
  DELETE /api/image-presets/{id}          → delete a preset (never 'none')
"""
from __future__ import annotations

import re

from fastapi.responses import JSONResponse

from ..services import image_presets as IP


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (name or "").lower()).strip("_") or "preset"


def register(app, ctx):
    @app.get("/api/image-presets")
    def list_image_presets() -> dict:
        return IP.load_image_presets(ctx.root)

    @app.post("/api/image-presets")
    def upsert_image_preset(body: dict):
        """Create or update one preset (matched by id). A blank id mints a new one from the name."""
        body = dict(body or {})
        lib = IP.load_image_presets(ctx.root)
        pid = (body.get("id") or "").strip()
        if not pid:
            existing = {p["id"] for p in lib["presets"]}
            pid = base = _slug(body.get("name") or "preset")
            n = 2
            while pid in existing:
                pid, n = f"{base}_{n}", n + 1
        body["id"] = pid
        IP.upsert_image_preset(ctx.root, body)
        return {"ok": True, **IP.load_image_presets(ctx.root), "id": pid}

    @app.post("/api/image-presets/{preset_id}/activate")
    def activate_image_preset(preset_id: str):
        """Make a preset the global-default look applied when no per-render override is given."""
        lib = IP.load_image_presets(ctx.root)
        if not any(p["id"] == preset_id for p in lib["presets"]):
            return JSONResponse({"error": "no such preset"}, status_code=404)
        return {"ok": True, **IP.set_active(ctx.root, preset_id)}

    @app.delete("/api/image-presets/{preset_id}")
    def delete_image_preset(preset_id: str):
        if preset_id == "none":
            return JSONResponse({"error": "can't delete the 'none' preset"}, status_code=400)
        return {"ok": True, **IP.delete_image_preset(ctx.root, preset_id)}
