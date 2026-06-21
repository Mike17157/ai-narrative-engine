"""Model-preset manager — the central registry for the "model side" of every function.

A *preset* = model + address mode + base system + inference params. Functions reach a
preset only through their lorebook's binding (Function → Lorebook → Preset), so presets
are never shared by accident. This router is what the Presets manager UI drives; it's a
deliberate sibling of the Lorebooks manager.

  GET    /api/presets            → { presets:[...] }
  POST   /api/presets            → upsert one preset (by id) → full library
  DELETE /api/presets/{id}       → delete a preset (never 'default')
"""
from __future__ import annotations

import re

from fastapi.responses import JSONResponse

from ..services import presets as P


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (name or "").lower()).strip("_") or "preset"


def register(app, ctx):
    @app.get("/api/presets")
    def list_presets() -> dict:
        return P.load_presets(ctx.root)

    @app.post("/api/presets")
    def upsert_preset(body: dict):
        """Create or update one preset (matched by id). A blank id mints a new one from the name."""
        body = dict(body or {})
        lib = P.load_presets(ctx.root)
        pid = (body.get("id") or "").strip()
        if not pid:
            existing = {p["id"] for p in lib["presets"]}
            pid = base = _slug(body.get("name") or "preset")
            n = 2
            while pid in existing:
                pid, n = f"{base}_{n}", n + 1
        body["id"] = pid
        P.upsert_preset(ctx.root, body)
        return {"ok": True, **P.load_presets(ctx.root), "id": pid}

    @app.post("/api/presets/{preset_id}/activate")
    def activate_preset(preset_id: str):
        """Make a preset the one that drives free chat."""
        lib = P.load_presets(ctx.root)
        if not any(p["id"] == preset_id for p in lib["presets"]):
            return JSONResponse({"error": "no such preset"}, status_code=404)
        return {"ok": True, **P.set_active(ctx.root, preset_id)}

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str):
        if preset_id == "default":
            return JSONResponse({"error": "can't delete the default preset"}, status_code=400)
        return {"ok": True, **P.delete_preset(ctx.root, preset_id)}
