from __future__ import annotations

import json

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...comfy.server import get_server


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
        graph = json.loads(path.read_text(encoding="utf-8"))
        return {
            "model": model, "path": str(path),
            "json": graph,
            "injects": injects,
            "sections": sections,
            "key_nodes": key_nodes,
            "description": description,
            "recipes": recipes,
        }

    @app.post("/api/workflow/test")
    async def test_workflow(body: WorkflowTestRequest):
        """Run the image model's workflow with a test prompt against local ComfyUI,
        streaming live progress (SSE) and ending with the rendered image(s). Uses the
        in-editor JSON if given."""
        from fastapi.responses import StreamingResponse

        provider, model_id = ctx.image_provider(body.model)
        if provider is None:
            return JSONResponse({"ok": False, "error": model_id}, status_code=404)
        if body.json:
            provider.workflow = body.json

        prompt = body.prompt or "masterpiece, best quality, highly detailed, 1girl, scenery, soft light"
        latent = (body.width, body.height) if (body.width and body.height) else None
        init_raw = None
        if body.init_image:
            import base64 as _b64
            init_raw = _b64.b64decode(body.init_image.split(",", 1)[-1])

        from fastapi.concurrency import run_in_threadpool

        import httpx

        from ...comfy.generate import stream_generate
        from ...comfy.lora import randomize_seeds

        async def events_local():
            try:
                # Fresh seed per render (else every test is the same fixed seed:0 roll).
                graph, out_node = provider._inject(prompt, None, latent=latent)
                graph = randomize_seeds(graph)
                await run_in_threadpool(get_server(provider.base_url).ensure_up)
                if init_raw is not None:   # img2img: upload + point LoadImage at it
                    def _wire():
                        with httpx.Client(base_url=provider.base_url, timeout=60) as client:
                            provider._set_init_image(client, graph, init_raw)
                    await run_in_threadpool(_wire)
                collect_from = out_node or provider.output_node
                async for ev in stream_generate(provider.base_url, graph, collect_from, provider.timeout_s):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events_local(), media_type="text/event-stream")

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
