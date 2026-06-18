from __future__ import annotations

import re

from fastapi.responses import JSONResponse


def register(app, ctx):
    @app.get("/api/scenarios")
    def list_scenarios() -> list:
        return [ctx.scenario_summary(k, s) for k, s in ctx.base_settings.scenarios.items()]

    @app.get("/api/scenarios/{key}")
    def get_scenario(key: str):
        scn = ctx.base_settings.scenarios.get(key)
        if scn is None:
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        return {**scn.model_dump(), "key": key, "cast": ctx.scenario_cast(scn)}

    @app.post("/api/scenarios")
    def create_scenario(body: dict):
        body = body or {}
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        key = re.sub(r"[^\w\-]+", "_", name.lower()).strip("_") or "scenario"
        base = key
        n = 2
        while (ctx.scenario_dir() / f"{key}.yaml").is_file():
            key, n = f"{base}_{n}", n + 1
        data = {
            "name": name, "setting": body.get("setting", ""),
            "openings": [o for o in (body.get("openings") or []) if o],
            "lorebook": body.get("lorebook") or {},
            "cast": body.get("cast") or [],
            "background": body.get("background"),
        }
        try:
            ctx.save_scenario(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.post("/api/scenarios/{key}")
    def update_scenario(key: str, body: dict):
        scn = ctx.base_settings.scenarios.get(key)
        if scn is None:
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        body = body or {}
        data = scn.model_dump()
        for f in ("name", "setting", "openings", "lorebook", "cast", "background", "fields"):
            if f in body:
                data[f] = body[f]
        try:
            ctx.save_scenario(key, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        return {"ok": True, "key": key}

    @app.delete("/api/scenarios/{key}")
    def delete_scenario(key: str):
        safe = re.sub(r'[^\w\-]+', '', key)
        p = ctx.scenario_dir() / f"{safe}.yaml"
        if not p.is_file():
            return JSONResponse({"error": "no such scenario"}, status_code=404)
        p.unlink()
        ctx.reload_settings()
        return {"ok": True}
