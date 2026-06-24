from __future__ import annotations

import json

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...connections import Connection, list_providers, ping_comfyui, test_connection
from ..context import IMAGE_ROLES, _ROLE_LABELS, _parse_role_entry
from ..services import config_files


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


def register(app, ctx):
    @app.get("/api/hardware")
    def hardware() -> dict:
        """Local GPU sampled at runtime + whether a RunPod cloud target is configured.
        Drives the local/cloud-only placement labels on image workflows."""
        rp = ctx.runpod_config
        return {"gpu": ctx.gpu_info(),
                "cloud": bool(rp.get("enabled") and rp.get("api_key") and rp.get("serverless_endpoint_id"))}

    @app.get("/api/models")
    def models() -> dict:
        import json as _json

        from ...comfy.hardware import classify_placement
        from ...comfy.workflow_check import classify_workflow

        s = ctx.effective_settings()
        fams = ctx.image_families()
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        vram = ctx.gpu_info().get("vram_gb")
        flagged = ctx.runpod_models()

        def _describe(k: str) -> dict:
            """Type/media/needs_init (nodes + filename) + local/cloud placement (probed
            VRAM vs the workflow's model sizes). Resilient if the graph can't be read."""
            path = ctx.workflow_path(k)
            graph = None
            if path is not None and path.is_file():
                try:
                    graph = _json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    graph = None
            out = classify_workflow(graph, path.name if path else k)
            out.update(classify_placement(graph or {}, models_dir, vram, manual_cloud=(k in flagged)))
            return out

        text = [{"key": k, "provider": m.provider, "model": m.options.get("model")}
                for k, m in s.models.items() if m.kind == "text"]
        image = [{"key": k, "provider": m.provider, "family": fams.get(k, "unknown"), **_describe(k)}
                 for k, m in s.models.items() if m.kind == "image"]
        return {"text": text, "image": image}

    @app.get("/api/text-models")
    def text_models() -> dict:
        conn = ctx.store.active("text")
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
        conn = ctx.store.active("text")
        if conn is None:
            return JSONResponse({"error": f"no {kind} connection — set one up in Connection"}, status_code=400)
        conn.model = body.get("model")
        ctx.store.upsert(conn, make_active=True)
        return {"ok": True, "active": conn.model}

    @app.get("/api/image-roles")
    def get_image_roles() -> dict:
        """Per-role image-workflow config: what each generation role uses. Returns the saved
        overrides, what each currently resolves to (effective), the unset fallback (default),
        the available image workflows, and human labels."""
        cfg = config_files.load_image_roles(ctx.root)
        return {
            "roles": IMAGE_ROLES,
            "labels": _ROLE_LABELS,
            "config": {r: (_parse_role_entry(cfg.get(r))[0] or "") for r in IMAGE_ROLES},  # model key only
            "effective": {r: ctx.role_model(r) for r in IMAGE_ROLES},   # what runs today
            "default": {r: ctx.role_default(r) for r in IMAGE_ROLES},   # fallback when unset
            "models": [k for k, m in ctx.base_settings.models.items() if m.kind == "image"],
            "families": ctx.image_families(),   # {workflow: family} so pickers group by container
        }

    @app.post("/api/image-roles")
    def set_image_roles(body: dict):
        """Save per-role workflow overrides to configs/image_roles.json. Accepts {config:{role:key}}
        (full or partial) or a single {role, model}. Empty string clears a role (back to default).
        Read fresh per request, so it takes effect immediately — no restart."""
        body = body or {}
        cfg = config_files.load_image_roles(ctx.root)
        updates = body.get("config")
        if updates is None and body.get("role"):
            updates = {body["role"]: body.get("model") or ""}
        if not isinstance(updates, dict):
            return JSONResponse({"error": "expected {config:{role:key}} or {role,model}"}, status_code=400)
        for role, key in updates.items():
            if role not in IMAGE_ROLES:
                return JSONResponse({"error": f"unknown role '{role}'"}, status_code=400)
            key = (key or "").strip()
            if key and (key not in ctx.base_settings.models or ctx.base_settings.models[key].kind != "image"):
                return JSONResponse({"error": f"'{key}' is not an image workflow"}, status_code=400)
            if key:
                # Preserve extra opts (e.g. output_variant) already on the role entry.
                existing = cfg.get(role)
                if isinstance(existing, dict):
                    cfg[role] = {**existing, "model": key}
                else:
                    cfg[role] = key
            else:
                cfg.pop(role, None)                       # empty → unset (back to default)
        path = ctx.root / "configs" / "image_roles.json"
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "config": {r: (_parse_role_entry(cfg.get(r))[0] or "") for r in IMAGE_ROLES},
                "effective": {r: ctx.role_model(r) for r in IMAGE_ROLES}}

    # -- connections ------------------------------------------------------
    @app.get("/api/providers")
    def providers(kind: str | None = None) -> list:
        return list_providers(kind if kind == "image" else "text")

    @app.get("/api/connections")
    def connections() -> dict:
        return {"active": ctx.store.active_map, "connections": [c.masked() for c in ctx.store.list()]}

    def _validate(kind: str, provider: str, api_key: str, base_url: str | None) -> dict:
        """Shared validation: text via the provider, image via a ComfyUI ping."""
        if kind == "image":
            if not ping_comfyui(base_url):
                return {"ok": False, "error": "ComfyUI not reachable at that URL — is it running?"}
            ms = ctx.image_model_items()
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
        existing = ctx.store.get(cid)
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
        ctx.store.upsert(conn, make_active=True)
        return {"ok": True, "active": ctx.store.active_map}

    @app.post("/api/connections/{conn_id}/test")
    def conn_test_saved(conn_id: str):
        """Validate + list models for a saved connection using its stored key
        (which never leaves the server)."""
        conn = ctx.store.get(conn_id)
        if conn is None:
            return JSONResponse({"ok": False, "error": "no such connection"}, status_code=404)
        r = _validate(conn.kind, conn.provider, conn.api_key, conn.base_url)
        if not r["ok"]:
            return JSONResponse(r, status_code=400)
        return {**r, "provider": conn.provider, "base_url": conn.base_url, "model": conn.model}

    @app.post("/api/connections/{conn_id}/activate")
    def conn_activate(conn_id: str):
        ctx.store.set_active(conn_id)
        return {"ok": True, "active": ctx.store.active_map}

    @app.delete("/api/connections/{conn_id}")
    def conn_delete(conn_id: str):
        ctx.store.remove(conn_id)
        return {"ok": True, "active": ctx.store.active_map}

    @app.get("/api/chatgen")
    def get_chatgen() -> dict:
        return config_files.load_chatgen(ctx.root)

    @app.post("/api/chatgen")
    def save_chatgen(body: dict):
        cfg = {**config_files.CHATGEN_DEFAULT, **(body or {})}
        path = ctx.root / "configs" / "chatgen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.get("/api/promptgen")
    def get_promptgen() -> dict:
        return config_files.load_promptgen(ctx.root)

    @app.post("/api/promptgen")
    def save_promptgen(body: dict):
        cfg = {**config_files.PROMPTGEN_DEFAULT, **(body or {})}
        path = ctx.root / "configs" / "promptgen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}
