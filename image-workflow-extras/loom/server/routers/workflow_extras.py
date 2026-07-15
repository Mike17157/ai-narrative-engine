"""Split out of loom/server/routers/workflow.py: the endpoints that only the
removed complex-workflow UI (graph editor / WorkflowImport / WorkflowTester
"cloud" branch) and RunPod routing used. /api/workflow (GET), /api/workflow/test
(local-only) and /api/test/prompts stayed behind in the main app.

Not wired into the app — reference/restore material only.
"""

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
    provider: str | None = None    # 'cloud' | 'local' | None — where to run; None honours the
    #                                per-workflow default (configs/runpod_models.json)


_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿가-힣぀-ヿ]")


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


_TRANSLATE_SYSTEM = (
    "You are a translation engine for AI image/video generation prompts. Translate the user's "
    "text into natural English. If it is a list of comma-separated tags or keywords, preserve that "
    "structure — translate each term and keep the separators. Output ONLY the translation: no "
    "quotes, no transliteration, no commentary, no notes."
)


def register(app, ctx):
    @app.post("/api/translate")
    def translate(body: dict):
        """Translate Chinese/Korean (or any non-English) text → English via a cheap text model.
        Accepts {text} or {texts:[…]}. Non-CJK strings pass through unchanged so the caller can
        send a whole batch blindly. Used by the workflow importer to localize foreign prompts."""
        body = body or {}
        single = isinstance(body.get("text"), str)
        texts = [body["text"]] if single else list(body.get("texts") or [])
        if not texts:
            return JSONResponse({"error": "no text"}, status_code=400)
        # Use the active chat connection (deepseek by default) — same provider the rest of
        # the app authors with, so no separate key/config is needed.
        provider = ctx.author_provider((body.get("model") or "").strip() or None)
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no chat connection — connect a chat model first"}, status_code=503)

        out: list[str] = []
        for t in texts:
            if not isinstance(t, str) or not t.strip() or not _has_cjk(t):
                out.append(t if isinstance(t, str) else "")
                continue
            try:
                res = provider.generate_text(system=_TRANSLATE_SYSTEM, prompt=t)
                out.append((res.text or "").strip() or t)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": f"translation failed: {exc}"}, status_code=500)
        return {"ok": True, "translation": out[0]} if single else {"ok": True, "translations": out}

    @app.get("/api/runpod-models")
    def list_runpod_models():
        """Image models + whether each runs on RunPod serverless (vs local ComfyUI)."""
        from ..services import config_files as _cf
        flagged = set(_cf.load_runpod_models(ctx.root))
        rp = ctx.runpod_config
        models = [{"key": k, "name": (md.options.get("title") or k), "runpod": k in flagged}
                  for k, md in ctx.base_settings.models.items() if md.kind == "image"]
        return {"models": sorted(models, key=lambda m: m["key"]),
                "configured": bool(rp.get("api_key") and rp.get("serverless_endpoint_id")),
                "endpoint_id": rp.get("serverless_endpoint_id", "")}

    @app.put("/api/runpod-models")
    def set_runpod_model(body: dict):
        """Flip one image model between local ComfyUI and RunPod serverless."""
        from ..services import config_files as _cf
        body = body or {}
        key = str(body.get("key") or "")
        md = ctx.base_settings.models.get(key)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image model '{key}'"}, status_code=404)
        flagged = set(_cf.load_runpod_models(ctx.root))
        if body.get("runpod"):
            flagged.add(key)
        else:
            flagged.discard(key)
        return {"ok": True, "models": _cf.save_runpod_models(ctx.root, list(flagged))}

    @app.post("/api/wan/render")
    async def wan_render(body: dict):
        """Render the `wan` workflow as a still OR a video via one flag. mode='image' →
        num_frames=1 + SaveImage; mode='video' → num_frames (forced to 4n+1) + a
        dynamically-added VHS_VideoCombine → mp4. Streams progress (SSE); the final event
        carries images:[] (data-url png) or videos:[] (data-url mp4)."""
        import copy
        import random

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ...comfy.generate import stream_generate
        from ...providers import _workflow as wfmod

        body = body or {}
        md = ctx.base_settings.models.get("wan")
        path = ctx.workflow_path("wan")
        if md is None or path is None or not path.is_file():
            return JSONResponse({"error": "no 'wan' image model configured"}, status_code=404)
        graph = json.loads(path.read_text(encoding="utf-8"))

        prompt = (body.get("prompt") or "").strip() or "a photograph, natural light"
        mode = body.get("mode") or "image"
        width = max(64, int(body.get("width") or 512))
        height = max(64, int(body.get("height") or 512))
        steps = max(1, int(body.get("steps") or 20))
        seed = int(body.get("seed") or 0) or random.randint(1, 2 ** 31)

        graph["4"]["inputs"]["positive_prompt"] = prompt
        if body.get("negative"):
            graph["4"]["inputs"]["negative_prompt"] = str(body["negative"])
        graph["5"]["inputs"]["width"] = width
        graph["5"]["inputs"]["height"] = height
        graph["6"]["inputs"]["steps"] = steps
        graph["6"]["inputs"]["seed"] = seed

        if mode == "video":
            nf = max(5, int(body.get("num_frames") or 25))
            nf = nf - ((nf - 1) % 4)                 # Wan needs 4n+1 frames
            fps = max(1, int(body.get("fps") or 16))
            graph["5"]["inputs"]["num_frames"] = nf
            graph.pop("8", None)                     # drop the still SaveImage
            graph["9"] = {"class_type": "VHS_VideoCombine", "inputs": {
                "images": ["7", 0], "frame_rate": fps, "loop_count": 0, "filename_prefix": "wan",
                "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 19,
                "save_metadata": False, "trim_to_audio": False, "pingpong": False, "save_output": True}}
            out_node = "9"
        else:
            graph["5"]["inputs"]["num_frames"] = 1
            out_node = "8"

        wfmod.localize_model_paths(graph)

        # Honour the per-workflow RunPod flag — same chokepoint as the rest of the app.
        from ...providers.runpod_serverless_provider import RunPodServerlessProvider
        from ..services.render_stream import serverless_render_events
        provider, _ = ctx.image_provider("wan")

        # --- RunPod serverless: hand the worker the graph we just tuned (prompt/frames
        # already injected); the provider polls /status and returns the mp4/png. --------
        if isinstance(provider, RunPodServerlessProvider):
            provider.workflow = graph
            provider.output_node = out_node

            async def events_cloud():
                try:
                    async for ev in serverless_render_events(provider, prompt=prompt):
                        yield f"data: {json.dumps(ev)}\n\n"
                except Exception as exc:  # noqa: BLE001
                    yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(events_cloud(), media_type="text/event-stream")

        # --- local ComfyUI: ensure_up runs inside the stream so the wait is cancellable.
        base = provider.base_url if provider else (
            (conn.base_url if (conn := ctx.store.active("image")) and conn.base_url else None)
            or md.options.get("base_url") or "http://127.0.0.1:8188")

        async def events():
            try:
                await run_in_threadpool(get_server(base).ensure_up)
                async for ev in stream_generate(base, graph, out_node, float(md.options.get("timeout_s", 600))):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

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

        raw = (body.get("name") or f"{src}_copy").strip()
        base_key = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or f"{src}_copy"
        key, i = base_key, 2
        while key in ctx.base_settings.models:
            key = f"{base_key}_{i}"; i += 1

        new_rel = f"./workflows/{key}_api.json"
        new_path = (ctx.root / new_rel).resolve()
        if ctx.root.resolve() not in new_path.parents:
            return JSONResponse({"error": "bad workflow path"}, status_code=400)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")

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

    @app.post("/api/workflow/convert")
    def convert_workflow(body: dict):
        """Convert a ComfyUI UI/editor-format workflow ({nodes,links}) → API format, using the
        live ComfyUI node definitions (/object_info) to map widgets→inputs. Returns
        {ok, json, unknown_nodes}. unknown_nodes are class types not installed (mapping is
        best-effort for those)."""
        import httpx

        from ...comfy.workflow_check import ui_to_api

        body = body or {}
        ui = body.get("json")
        if not isinstance(ui, dict) or not isinstance(ui.get("nodes"), list):
            return JSONResponse({"error": "not a UI-format workflow (expected {nodes:[…]})"}, status_code=400)
        conn = ctx.store.active("image")
        base = (conn.base_url if conn and conn.base_url else None) or "http://127.0.0.1:8188"
        try:
            get_server(base).ensure_up()
            with httpx.Client(base_url=base, timeout=30) as client:
                oi = client.get("/object_info").json()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not read ComfyUI node definitions: {exc}"}, status_code=502)
        graph, unknown = ui_to_api(ui, oi)
        return {"ok": True, "json": graph, "unknown_nodes": unknown}

    @app.post("/api/workflow/import")
    def import_workflow(body: dict):
        """Register a dropped API-format workflow JSON as a new image model. Saves the
        graph to workflows/<key>_api.json and APPENDS a models.yaml entry (textual append,
        comments preserved). The positive-prompt inject node + output node are auto-detected
        (suggest_io) unless the caller overrides them. No backend restart needed —
        ctx.reload_settings() picks up the new model immediately."""
        from ...comfy.workflow_check import suggest_io

        body = body or {}
        graph = body.get("json")
        if not isinstance(graph, dict) or not graph:
            return JSONResponse({"error": "no workflow json"}, status_code=400)

        io = suggest_io(graph)
        pos = body.get("positive") or io.get("positive")
        out = body.get("output_node") or io.get("output_node")
        if not pos or not pos.get("node"):
            return JSONResponse({"error": "could not find a positive-prompt text node — pick one",
                                 "io": io}, status_code=400)
        if not out:
            return JSONResponse({"error": "could not find an output (SaveImage) node — pick one",
                                 "io": io}, status_code=400)

        raw = (body.get("name") or "imported_workflow").strip()
        base_key = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or "imported_workflow"
        key, i = base_key, 2
        while key in ctx.base_settings.models:
            key = f"{base_key}_{i}"; i += 1

        new_rel = f"./workflows/{key}_api.json"
        new_path = (ctx.root / new_rel).resolve()
        if ctx.root.resolve() not in new_path.parents:
            return JSONResponse({"error": "bad workflow path"}, status_code=400)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

        conn = ctx.store.active("image")
        base_url = (conn.base_url if conn and conn.base_url else None) or "http://127.0.0.1:8188"
        entry = {"provider": "comfyui", "kind": "image", "options": {
            "base_url": base_url,
            "workflow": new_rel,
            "inputs": {"positive": {"node": str(pos["node"]), "field": pos.get("field") or "text"}},
            "output_node": str(out),
            "timeout_s": int(body.get("timeout_s") or 600),
        }}
        snippet = yaml.safe_dump({key: entry}, sort_keys=False, allow_unicode=True)
        indented = "".join(("  " + ln) if ln.strip() else ln
                           for ln in snippet.splitlines(keepends=True))
        models_file = ctx.root / "configs" / "models.yaml"
        text = models_file.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        models_file.write_text(text + "\n  # imported workflow\n" + indented, encoding="utf-8")
        ctx.reload_settings()
        return {"ok": True, "key": key, "workflow": new_rel,
                "inputs": entry["options"]["inputs"], "output_node": str(out)}

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
