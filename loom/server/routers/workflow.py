from __future__ import annotations

import json
import re

import yaml
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...comfy.server import get_server


class WorkflowSaveRequest(BaseModel):
    model: str
    json: dict


class WorkflowTestRequest(BaseModel):
    model: str
    json: dict | None = None       # test the in-editor workflow (unsaved) if given
    prompt: str | None = None      # test positive prompt
    init_image: str | None = None  # base64/data-URL source image for img2img (LoadImage)
    width: int | None = None       # latent canvas override (per-pose aspect, for sprite tests)
    height: int | None = None


def register(app, ctx):
    @app.get("/api/workflow")
    def get_workflow(model: str):
        path = ctx.workflow_path(model)
        if path is None or not path.is_file():
            return JSONResponse({"error": f"no workflow for image model '{model}'"}, status_code=404)
        md = ctx.base_settings.models.get(model)
        # Which node fields Loom overwrites at generation time (positive/negative
        # prompt). The positive one is the image description the model writes.
        injects: dict[str, dict[str, str]] = {}
        for role, target in (md.options.get("inputs") or {}).items():
            try:
                injects.setdefault(str(target["node"]), {})[target["field"]] = role
            except (KeyError, TypeError):
                pass
        sections = {}
        key_nodes = []
        description = ""
        recipes = {}
        meta_path = path.with_suffix(".meta.json")
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                sections = meta.get("sections", {})
                key_nodes = meta.get("key_nodes", [])
                description = meta.get("description", "")
                recipes = meta.get("recipes", {})
            except Exception:  # noqa: BLE001
                pass
        return {
            "model": model, "path": str(path),
            "json": json.loads(path.read_text(encoding="utf-8")),
            "injects": injects,
            "sections": sections,
            "key_nodes": key_nodes,
            "description": description,
            "recipes": recipes,
        }

    @app.post("/api/workflow")
    def save_workflow(body: WorkflowSaveRequest):
        path = ctx.workflow_path(body.model)
        if path is None:
            return JSONResponse({"error": f"no workflow for image model '{body.model}'"}, status_code=404)
        path.write_text(json.dumps(body.json, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path)}

    @app.post("/api/workflow/duplicate")
    def duplicate_workflow(body: dict):
        """Clone an image model + its workflow JSON into a new editable copy. Copies the
        graph file to workflows/<key>_api.json and APPENDS a new models.yaml entry (textual
        append, so the file's comments survive). Returns the new model key."""
        import copy

        body = body or {}
        src = (body.get("model") or "").strip()
        md = ctx.base_settings.models.get(src)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image model '{src}'"}, status_code=404)
        src_path = ctx.workflow_path(src)
        if src_path is None or not src_path.is_file():
            return JSONResponse({"error": f"'{src}' has no workflow file to copy"}, status_code=400)

        # New key from the requested name (slugified), made unique against existing models.
        raw = (body.get("name") or f"{src}_copy").strip()
        base_key = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or f"{src}_copy"
        key, i = base_key, 2
        while key in ctx.base_settings.models:
            key = f"{base_key}_{i}"; i += 1

        # Copy the workflow graph to a file named after the new key (kept inside the project).
        new_rel = f"./workflows/{key}_api.json"
        new_path = (ctx.root / new_rel).resolve()
        if ctx.root.resolve() not in new_path.parents:
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
        models_file = ctx.root / "configs" / "models.yaml"
        text = models_file.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        models_file.write_text(text + f"\n  # duplicated from {src}\n" + indented, encoding="utf-8")
        ctx.reload_settings()
        return {"ok": True, "key": key, "workflow": new_rel}

    @app.post("/api/workflow/test")
    async def test_workflow(body: WorkflowTestRequest):
        """Run the image model's workflow through ComfyUI with a test prompt,
        streaming live progress (SSE) and ending with the rendered image(s).
        Uses the in-editor JSON if provided."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        import httpx

        from ...comfy.generate import stream_generate
        from ...comfy.lora import randomize_seeds
        from ...providers.comfyui_provider import ComfyUIProvider

        md = ctx.base_settings.models.get(body.model)
        if md is None or md.kind != "image":
            return JSONResponse({"ok": False, "error": f"no image model '{body.model}'"}, status_code=404)
        opts = dict(md.options)
        conn = ctx.store.active("image")
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
            latent = (body.width, body.height) if (body.width and body.height) else None
            graph, out_node = provider._inject(prompt, None, latent=latent)
            graph = randomize_seeds(graph)
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
                collect_from = out_node or provider.output_node
                async for ev in stream_generate(provider.base_url, graph, collect_from, provider.timeout_s):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    # Representative emotion spread for the Sprite test grid (neutral doubles as the base-image read).
    _REP_EMOTIONS = ["neutral", "joy", "sadness", "anger", "fear", "surprise", "desire", "disgust"]

    @app.post("/api/test/prompts")
    def test_prompts(body: dict):
        """Compose test-render prompts the SAME way production does, so the Test grid mirrors real
        output. mode 'sprite' → one full-body, pose+expression cell per emotion (the framing/pose the
        real sprite render uses); mode 'scene' → the bare subject (scene workflow carries its framing).
        Subject is the typed `subject`, or a picked `character`'s appearance. Returns {cells}."""
        from ..services.emotions import EMOTION_HINTS, EMOTION_LABELS
        from ..services.prompts import _regionize_prompt, _safe_image_tags, _snap_prompt
        body = body or {}
        mode = body.get("mode") or "sprite"
        # subject: a picked character's appearance wins over typed text (truest to production)
        subject = (body.get("subject") or "").strip()
        ck = (body.get("character") or "").strip()
        if ck:
            c = ctx.base_settings.characters.get(ck)
            if c is not None:
                subject = ((c.fields or {}).get("appearance") or (c.fields or {}).get("base_prompt") or subject)
        subject = subject or "1girl, solo"

        if mode == "scene":
            return {"cells": [{"key": "scene", "label": "Scene",
                               "prompt": _regionize_prompt(_snap_prompt(_safe_image_tags(subject)))}]}

        # sprite: mirror production — subject + expression + pose tags + the pose's framing crop, and
        # report the pose's latent (so the test renders at the same crop+canvas as the real sprite).
        emos = body.get("emotions") or _REP_EMOTIONS
        cells = []
        for emo in emos:
            expr = "" if emo == "neutral" else EMOTION_HINTS.get(emo, "")
            # body language comes from the picked character's composed poses (per-character); a typed
            # subject has no persona → framing only.
            parts = [subject, expr, ctx.pose_tags(ck or None, emo), ctx.pose_framing(emo)]
            prompt = _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(p for p in parts if p))))
            w, h = ctx.pose_latent(emo)
            cells.append({"key": emo, "label": EMOTION_LABELS.get(emo, "Neutral" if emo == "neutral" else emo),
                          "prompt": prompt, "width": w, "height": h})
        return {"cells": cells}

    @app.post("/api/workflow/check")
    def workflow_check(body: dict):
        """Given an API-format workflow, report which referenced models are
        installed vs missing, matching missing ones to the catalog for download."""
        from ...comfy.catalog import read_catalog
        from ...comfy.workflow_check import check_workflow
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "models dir not found", "refs": [], "missing": []}, status_code=404)
        graph = (body or {}).get("json") or {}
        cat = read_catalog(bd, md).get("entries", [])
        return check_workflow(graph, md, cat)
